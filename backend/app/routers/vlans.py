from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Subnet, Vlan
from ..schemas import VlanIn
from ..security import get_current_user, require_role
from ..service import audit
from .subnets import _norm_tags

router = APIRouter(prefix="/api/vlans", tags=["vlans"])


@router.get("")
async def list_vlans(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    vlans = (await db.execute(select(Vlan).order_by(Vlan.vid))).scalars().all()
    counts = dict((await db.execute(
        select(Subnet.vlan_id, func.count()).where(Subnet.vlan_id.is_not(None)).group_by(Subnet.vlan_id)
    )).all())
    return [{
        "id": v.id, "vid": v.vid, "name": v.name, "color": v.color, "descr": v.descr,
        "tags": [t for t in (v.tags or "").split(",") if t.strip()],
        "subnets_count": counts.get(v.id, 0),
    } for v in vlans]


@router.post("", status_code=201)
async def create_vlan(data: VlanIn, db=Depends(get_db), user=Depends(require_role("admin"))):
    exists = (await db.execute(select(Vlan).where(or_(Vlan.vid == data.vid, Vlan.name == data.name)))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, f"VLAN с vid={data.vid} или именем '{data.name}' уже существует")
    v = Vlan(vid=data.vid, name=data.name, color=data.color, descr=data.descr,
             tags=_norm_tags(data.tags))
    db.add(v)
    audit(db, user, "vlan_create", data.name, {"vid": data.vid})
    await db.commit()
    await db.refresh(v)
    return {"id": v.id}


@router.put("/{vlan_id}")
async def update_vlan(vlan_id: int, data: VlanIn, db=Depends(get_db), user=Depends(require_role("admin"))):
    v = await db.get(Vlan, vlan_id)
    if not v:
        raise HTTPException(404, "VLAN не найден")
    clash = (await db.execute(
        select(Vlan).where(or_(Vlan.vid == data.vid, Vlan.name == data.name), Vlan.id != vlan_id)
    )).scalar_one_or_none()
    if clash:
        raise HTTPException(409, "VLAN с таким vid или именем уже существует")
    v.vid = data.vid
    v.name = data.name
    v.color = data.color
    v.descr = data.descr
    v.tags = _norm_tags(data.tags)
    audit(db, user, "vlan_update", data.name, {"vid": data.vid})
    await db.commit()
    return {"ok": True}


@router.delete("/{vlan_id}", status_code=204)
async def delete_vlan(vlan_id: int, db=Depends(get_db), user=Depends(require_role("admin"))):
    v = await db.get(Vlan, vlan_id)
    if not v:
        raise HTTPException(404, "VLAN не найден")
    await db.execute(update(Subnet).where(Subnet.vlan_id == vlan_id).values(vlan_id=None))
    audit(db, user, "vlan_delete", v.name, {"vid": v.vid})
    await db.delete(v)
    await db.commit()
