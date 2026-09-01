import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import IpEvent
from ..security import get_current_user

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events")
async def events(subnet_id: int | None = None, type: str | None = None, limit: int = 100,
                 db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    q = select(IpEvent)
    if subnet_id is not None:
        q = q.where(IpEvent.subnet_id == subnet_id)
    if type:
        q = q.where(IpEvent.type == type)
    rows = (await db.execute(q.order_by(IpEvent.at.desc(), IpEvent.id.desc()).limit(min(max(limit, 1), 500)))).scalars().all()
    out = []
    for r in rows:
        detail = None
        if r.detail:
            try:
                detail = json.loads(r.detail)
            except Exception:
                detail = r.detail
        out.append({
            "id": r.id,
            "ip": r.ip,
            "subnet_id": r.subnet_id,
            "type": r.type,
            "detail": detail,
            "at": r.at.isoformat() if r.at else None,
        })
    return out
