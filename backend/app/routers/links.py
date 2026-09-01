import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import FiberLink, Location
from ..schemas import LinkIn
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
            out.append(e)
    return out or None


def _usage_dump(usage: list | None) -> str | None:
    if not usage:
        return None
    clean = [
        {"name": (x.name or "").strip(), "count": int(x.count),
         "speed": float(x.speed) if x.speed is not None else None}
        for x in usage if (x.name or "").strip()
    ]
    return json.dumps(clean, ensure_ascii=False) if clean else None


def _loc_dict(l: Location) -> dict:
    return {
        "id": l.id, "name": l.name, "address": l.address,
        "lat": l.lat, "lng": l.lng,
    }


def _dict(f: FiberLink, a: Location, b: Location) -> dict:
    return {
        "id": f.id,
        "name": f.name,
        "a": _loc_dict(a),
        "b": _loc_dict(b),
        "capacity": f.capacity,
        "fibers": f.fibers,
        "length": f.length,
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
            out.append(_dict(f, a, b))
    return out


@router.post("", status_code=201)
async def create_link(data: LinkIn, db=Depends(get_db), user=Depends(require_role("admin", "operator"))):
    exists = (await db.execute(select(FiberLink).where(FiberLink.name == data.name))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, f"Линия «{data.name}» уже существует")
    a, b = await _check_locations(db, data.a_id, data.b_id)
    f = FiberLink(name=data.name, a_id=a.id, b_id=b.id, capacity=data.capacity,
                  fibers=data.fibers, length=data.length,
                  fiber_usage=_usage_dump(data.fiber_usage),
                  is_active=data.is_active, descr=data.descr)
    db.add(f)
    audit(db, user, "link_create", data.name, {"a": a.name, "b": b.name})
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
    f.name = data.name
    f.a_id = a.id
    f.b_id = b.id
    f.capacity = data.capacity
    f.fibers = data.fibers
    f.length = data.length
    f.fiber_usage = _usage_dump(data.fiber_usage)
    f.is_active = data.is_active
    f.descr = data.descr
    audit(db, user, "link_update", data.name, {"a": a.name, "b": b.name})
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
