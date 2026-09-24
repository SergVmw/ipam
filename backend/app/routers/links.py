import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import FiberLink, Location
from ..schemas import LinkIn, RouteIn
from ..security import get_current_user, require_role
from ..service import audit

router = APIRouter(prefix="/api/links", tags=["links"])


def _usage_parse(raw: str | None) -> list[dict] | None:
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except Exception:
        return None
    if not isinstance(d, list):
        return None
    out = []
    for x in d:
        if isinstance(x, dict) and str(x.get("name", "")).strip() and isinstance(x.get("count"), int):
            e = {"name": str(x["name"]).strip(), "count": int(x["count"])}
            e["speed"] = float(x["speed"]) if isinstance(x.get("speed"), (int, float)) else None
            mode = x.get("speed_mode")
            e["speed_mode"] = mode if mode in ("all", "pair") else None
            extra = x.get("extra")
            e["extra"] = str(extra).strip() if isinstance(extra, str) and extra.strip() else None
            out.append(e)
    return out or None


def _usage_dump(usage: list | None) -> str | None:
    if not usage:
        return None
    clean = [
        {"name": (x.name or "").strip(), "count": int(x.count),
         "speed": float(x.speed) if x.speed is not None else None,
         "speed_mode": x.speed_mode if x.speed_mode in ("all", "pair") else None,
         "extra": (x.extra or "").strip() or None}
        for x in usage if (x.name or "").strip()
    ]
    return json.dumps(clean, ensure_ascii=False) if clean else None


def _route_parse(raw: str | None) -> dict | None:
    """fiber_link.route: {"via": [id,...], "segs": [км|null,...]}."""
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    via = d.get("via")
    segs = d.get("segs")
    via = [int(x) for x in via if isinstance(x, (int, float))] if isinstance(via, list) else []
    segs = [float(x) if isinstance(x, (int, float)) else None for x in segs] if isinstance(segs, list) else []
    if not via and not segs:
        return None
    return {"via": via, "segs": segs}


def _route_dump(route: "RouteIn | None") -> str | None:
    if not route or not route.via:
        return None
    segs = list(route.segs)
    segs = (segs + [None] * (len(route.via) + 1))[: len(route.via) + 1]
    return json.dumps(
        {"via": list(route.via), "segs": segs}, ensure_ascii=False
    )


async def _check_route(db: AsyncSession, route: "RouteIn | None", a_id: int, b_id: int) -> None:
    """Проверка промежуточных точек: существуют, без дублей, не совпадают с А/Б;
    segs — ровно len(via)+1 значений (км, ≥0 или null)."""
    if not route:
        return
    via = list(route.via)
    if len(set(via)) != len(via):
        raise HTTPException(422, "Промежуточные точки: есть дубликаты")
    if a_id in via or b_id in via:
        raise HTTPException(422, "Точка А и точка Б не могут быть промежуточными")
    for vid in via:
        if not await db.get(Location, vid):
            raise HTTPException(422, f"Промежуточная точка (id={vid}) не найдена")
    segs = list(route.segs)
    if len(segs) != len(via) + 1:
        raise HTTPException(422, "Длины участков: количество не совпадает с числом точек маршрута")
    for s in segs:
        if s is not None and s < 0:
            raise HTTPException(422, "Длина участка: число ≥ 0 (км)")


def _loc_dict(l: Location) -> dict:
    return {
        "id": l.id, "name": l.name, "address": l.address,
        "lat": l.lat, "lng": l.lng, "is_transit": bool(l.is_transit),
    }


def _dict(f: FiberLink, a: Location, b: Location, locs: dict[int, Location]) -> dict:
    rt = _route_parse(f.route)
    route = None
    if rt:
        route = {
            "via": [_loc_dict(locs[v]) for v in rt["via"] if v in locs],
            "segs": rt["segs"],
        }
    return {
        "id": f.id,
        "name": f.name,
        "a": _loc_dict(a),
        "b": _loc_dict(b),
        "capacity": f.capacity,
        "fibers": f.fibers,
        "length": f.length,
        "route": route,
        "fiber_usage": _usage_parse(f.fiber_usage),
        "is_active": f.is_active,
        "descr": f.descr,
    }


async def _check_locations(db: AsyncSession, a_id: int, b_id: int) -> tuple[Location, Location]:
    if a_id == b_id:
        raise HTTPException(422, "Точка А и точка Б должны быть разными")
    a = await db.get(Location, a_id)
    b = await db.get(Location, b_id)
    if not a or not b:
        raise HTTPException(422, "Указанное местоположение не найдено")
    return a, b


@router.get("")
async def list_links(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    locs = {l.id: l for l in (await db.execute(select(Location))).scalars().all()}
    links = (await db.execute(select(FiberLink).order_by(FiberLink.name))).scalars().all()
    out = []
    for f in links:
        a, b = locs.get(f.a_id), locs.get(f.b_id)
        if a and b:
            out.append(_dict(f, a, b, locs))
    return out


@router.post("", status_code=201)
async def create_link(data: LinkIn, db=Depends(get_db), user=Depends(require_role("admin", "operator"))):
    exists = (await db.execute(select(FiberLink).where(FiberLink.name == data.name))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, f"Линия «{data.name}» уже существует")
    a, b = await _check_locations(db, data.a_id, data.b_id)
    await _check_route(db, data.route, a.id, b.id)
    f = FiberLink(name=data.name, a_id=a.id, b_id=b.id, capacity=data.capacity,
                  fibers=data.fibers, length=data.length,
                  route=_route_dump(data.route),
                  fiber_usage=_usage_dump(data.fiber_usage),
                  is_active=data.is_active, descr=data.descr)
    db.add(f)
    via_names = [(await db.get(Location, v)).name for v in data.route.via] if (data.route and data.route.via) else None
    audit(db, user, "link_create", data.name, {"a": a.name, "b": b.name, "via": via_names})
    await db.commit()
    await db.refresh(f)
    return {"id": f.id}


@router.put("/{link_id}")
async def update_link(link_id: int, data: LinkIn, db=Depends(get_db), user=Depends(require_role("admin", "operator"))):
    f = await db.get(FiberLink, link_id)
    if not f:
        raise HTTPException(404, "Линия связи не найдена")
    clash = (await db.execute(
        select(FiberLink).where(FiberLink.name == data.name, FiberLink.id != link_id)
    )).scalar_one_or_none()
    if clash:
        raise HTTPException(409, f"Линия «{data.name}» уже существует")
    a, b = await _check_locations(db, data.a_id, data.b_id)
    await _check_route(db, data.route, a.id, b.id)
    f.name = data.name
    f.a_id = a.id
    f.b_id = b.id
    f.capacity = data.capacity
    f.fibers = data.fibers
    f.length = data.length
    f.route = _route_dump(data.route)
    f.fiber_usage = _usage_dump(data.fiber_usage)
    f.is_active = data.is_active
    f.descr = data.descr
    via_names = [(await db.get(Location, v)).name for v in data.route.via] if (data.route and data.route.via) else None
    audit(db, user, "link_update", data.name, {"a": a.name, "b": b.name, "via": via_names})
    await db.commit()
    return {"ok": True}


@router.delete("/{link_id}", status_code=204)
async def delete_link(link_id: int, db=Depends(get_db), user=Depends(require_role("admin", "operator"))):
    f = await db.get(FiberLink, link_id)
    if not f:
        raise HTTPException(404, "Линия связи не найдена")
    audit(db, user, "link_delete", f.name)
    await db.delete(f)
    await db.commit()
