from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Ip, utcnow
from ..schemas import IpUpdate
from ..security import require_role
from ..service import audit, ip_dict

router = APIRouter(prefix="/api/ips", tags=["ips"])


@router.put("/{ip}")
async def update_ip(ip: str, data: IpUpdate, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin", "operator"))):
    row = (await db.execute(select(Ip).where(Ip.ip == ip))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "IP не найден")
    changes: dict = {}
    if data.state is not None:
        if data.state not in ("free", "reserved", "used"):
            raise HTTPException(422, "state: free | reserved | used")
        if data.state != row.state:
            row.state = data.state
            row.misses = 0
            if data.state == "used" and not row.last_seen:
                row.last_seen = utcnow()
            if data.state == "free":
                row.last_seen = None
            changes["state"] = data.state
    if data.owner is not None:
        row.owner = data.owner or None
        changes["owner"] = row.owner
    if data.note is not None:
        row.note = data.note or None
        changes["note"] = row.note
    if data.clear_hostname:
        row.hostname = None
        row.hostname_manual = False
        changes["hostname"] = None
    elif data.hostname is not None:
        row.hostname = data.hostname or None
        row.hostname_manual = bool(data.hostname)
        changes["hostname"] = row.hostname
    if changes:
        audit(db, user, "ip_update", row.ip, changes)
    await db.commit()
    await db.refresh(row)
    return ip_dict(row)
