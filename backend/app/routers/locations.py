import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import FiberLink, Location
from ..schemas import LocationIn
from ..security import get_current_user, require_role
from ..service import audit

router = APIRouter(prefix="/api/locations", tags=["locations"])


def _dict(l: Location, links_count: int = 0) -> dict:
    return {
        "id": l.id,
        "name": l.name,
        "address": l.address,
        "lat": l.lat,
        "lng": l.lng,
        "is_transit": bool(l.is_transit),
        "descr": l.descr,
        "links_count": links_count,
    }


def _via_ids(route_raw: str | None) -> list[int]:
    """Id промежуточных точек из fiber_link.route (JSON)."""
    if not route_raw:
        return []
    try:
        d = json.loads(route_raw)
        via = d.get("via") if isinstance(d, dict) else None
        return [int(x) for x in via if isinstance(x, (int, float))] if isinstance(via, list) else []
    except Exception:
        return []


async def _links_count(db: AsyncSession) -> dict[int, int]:
    out: dict[int, int] = {}
    for col in (FiberLink.a_id, FiberLink.b_id):
        for lid, n in (await db.execute(select(col, func.count()).group_by(col))).all():
            out[lid] = out.get(lid, 0) + n
    for raw in (await db.execute(select(FiberLink.route))).scalars().all():
        for vid in _via_ids(raw):
            out[vid] = out.get(vid, 0) + 1
    return out


@router.get("")
async def list_locations(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    locs = (await db.execute(select(Location).order_by(Location.name))).scalars().all()
    counts = await _links_count(db)
    return [_dict(l, counts.get(l.id, 0)) for l in locs]


@router.post("", status_code=201)
async def create_location(data: LocationIn, db=Depends(get_db), user=Depends(require_role("admin", "operator"))):
    exists = (await db.execute(select(Location).where(Location.name == data.name))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, f"Местоположение «{data.name}» уже существует")
    l = Location(name=data.name, address=data.address, lat=data.lat, lng=data.lng,
                 is_transit=data.is_transit, descr=data.descr)
    db.add(l)
    audit(db, user, "location_create", data.name, {"address": data.address})
    await db.commit()
    await db.refresh(l)
    return {"id": l.id}


@router.put("/{loc_id}")
async def update_location(loc_id: int, data: LocationIn, db=Depends(get_db), user=Depends(require_role("admin", "operator"))):
    l = await db.get(Location, loc_id)
    if not l:
        raise HTTPException(404, "Местоположение не найдено")
    clash = (await db.execute(
        select(Location).where(Location.name == data.name, Location.id != loc_id)
    )).scalar_one_or_none()
    if clash:
        raise HTTPException(409, f"Местоположение «{data.name}» уже существует")
    l.name = data.name
    l.address = data.address
    l.lat = data.lat
    l.lng = data.lng
    l.is_transit = data.is_transit
    l.descr = data.descr
    audit(db, user, "location_update", data.name, {"address": data.address})
    await db.commit()
    return {"ok": True}


@router.delete("/{loc_id}", status_code=204)
async def delete_location(loc_id: int, db=Depends(get_db), user=Depends(require_role("admin", "operator"))):
    l = await db.get(Location, loc_id)
    if not l:
        raise HTTPException(404, "Местоположение не найдено")
    n = (await db.execute(select(func.count()).where(or_(FiberLink.a_id == loc_id, FiberLink.b_id == loc_id)))).scalar() or 0
    if not n:
        # используется как ПРОМЕЖУТОЧНАЯ точка хотя бы одной линии
        for raw in (await db.execute(select(FiberLink.route))).scalars().all():
            if loc_id in _via_ids(raw):
                n = 1
                break
    if n:
        raise HTTPException(409, f"Местоположение используется {n} линиями связи — сначала удалите/измените линии")
    audit(db, user, "location_delete", l.name)
    await db.delete(l)
    await db.commit()
