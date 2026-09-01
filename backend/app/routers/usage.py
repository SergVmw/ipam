import math
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Subnet, UsageSnapshot, utcnow
from ..security import get_current_user
from ..service import usage_counts

router = APIRouter(prefix="/api/subnets", tags=["usage"])


@router.get("/{sid}/usage")
async def usage(sid: int, days: int = 30, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    s = await db.get(Subnet, sid)
    if not s:
        raise HTTPException(404, "Сеть не найдена")
    since = utcnow() - timedelta(days=max(1, min(days, 365)))
    snaps = (await db.execute(
        select(UsageSnapshot)
        .where(UsageSnapshot.subnet_id == sid, UsageSnapshot.at >= since)
        .order_by(UsageSnapshot.at)
    )).scalars().all()
    if len(snaps) > 500:  # дэксэмплирование для длинных периодов
        step = math.ceil(len(snaps) / 500)
        snaps = snaps[::step]
    series = []
    for sn in snaps:
        occupied = sn.used + sn.reserved  # offline = «свободно/усл. осв.», в заполняемость не входит
        series.append({
            "at": sn.at.isoformat(),
            "pct": round(occupied / sn.total * 100, 1) if sn.total else 0.0,
            "used": sn.used,
            "free": sn.free,
            "reserved": sn.reserved,
            "offline": sn.offline,
        })
    return {"current": await usage_counts(db, sid), "series": series}
