import json

from fastapi import APIRouter, Depends
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Ip, Subnet, Vlan
from ..security import get_current_user
from ..settings_store import get_all
from ..service import ZEROS, cond_free_by_subnet, finish_subnet_counts, last_scans, subnet_dict

router = APIRouter(prefix="/api", tags=["overview"])


@router.get("/meta")
async def meta(db: AsyncSession = Depends(get_db)):
    """Публичное: для UI (часовой пояс, оформление)."""
    d = await get_all(db)
    try:
        ui_links = json.loads(d.get("ui_links") or "[]")
        if not isinstance(ui_links, list):
            ui_links = []
    except Exception:
        ui_links = []
    return {
        "tz_offset_min": int(d.get("tz_offset_min", 0)),
        "ui_logo": d.get("ui_logo") or "",
        "copyright": d.get("copyright") or "",
        "admin_email": d.get("admin_email") or "",
        "ui_links": ui_links,
        "show_no_dns": bool(d.get("show_no_dns", True)),
        "search_mode": d.get("search_mode") or "page",
        "org_name": d.get("org_name") or "",
    }


@router.get("/search")
async def search(q: str = "", limit: int = 8, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    """Глобальный поиск: по именам/CIDR сетей, VLAN (имя/vid) и IP (hostname/owner/IP)."""
    q = q.strip()
    if len(q) < 2:
        return {"subnets": [], "vlans": [], "ips": []}
    like = f"%{q}%"
    limit = max(1, min(limit, 100))
    subnets = (await db.execute(
        select(Subnet).where(or_(Subnet.name.ilike(like), Subnet.cidr.ilike(like),
                                  Subnet.tags.ilike(like))).limit(limit)
    )).scalars().all()
    vlans = (await db.execute(
        select(Vlan).where(or_(Vlan.name.ilike(like), cast(Vlan.vid, String).like(like))).limit(limit)
    )).scalars().all()
    ips = (await db.execute(
        select(Ip, Subnet)
        .join(Subnet, Ip.subnet_id == Subnet.id)
        .where(or_(Ip.hostname.ilike(like), Ip.owner.ilike(like), Ip.ip.like(like)))
        .limit(limit)
    )).all()
    return {
        "subnets": [{"id": s.id, "name": s.name, "cidr": s.cidr} for s in subnets],
        "vlans": [{"id": v.id, "vid": v.vid, "name": v.name, "color": v.color} for v in vlans],
        "ips": [{
            "ip": ip_row.ip, "hostname": ip_row.hostname, "owner": ip_row.owner,
            "subnet_id": s.id, "subnet_name": s.name, "cidr": s.cidr,
        } for ip_row, s in ips],
    }


@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    vlans = (await db.execute(select(Vlan).order_by(Vlan.vid))).scalars().all()
    subnets = (await db.execute(select(Subnet).order_by(Subnet.name))).scalars().all()
    counts_by: dict[int, dict] = {}
    for st, sid, n in (await db.execute(
        select(Ip.state, Ip.subnet_id, func.count()).group_by(Ip.state, Ip.subnet_id)
    )).all():
        c = counts_by.setdefault(sid, {"free": 0, "used": 0, "reserved": 0, "offline": 0, "cond_free": 0})
        if st in c:
            c[st] = n
    # единое правило: занято = used + reserved; offline < 3 дн. — «усл. осв.»
    cond_by = await cond_free_by_subnet(db)
    for sid_, c in counts_by.items():
        c["cond_free"] = cond_by.get(sid_, 0)
        finish_subnet_counts(c)
    vlan_map = {v.id: v for v in vlans}
    scans = await last_scans(db, [s.id for s in subnets])
    by_vlan: dict[int | None, list] = {}
    for s in subnets:
        d = subnet_dict(s, counts_by.get(s.id, dict(ZEROS)), vlan_map.get(s.vlan_id))
        d.update(scans.get(s.id, {"last_scan_at": None, "last_error": None}))
        by_vlan.setdefault(s.vlan_id, []).append(d)
    totals = {"subnets": len(subnets), "ips": 0, "used": 0, "pct": 0.0}
    for c in counts_by.values():
        totals["ips"] += c["total"]
        totals["used"] += c["occupied"]
    if totals["ips"]:
        totals["pct"] = round(totals["used"] / totals["ips"] * 100, 1)
    out_vlans = [{
        "id": v.id, "vid": v.vid, "name": v.name, "color": v.color, "descr": v.descr,
        "subnets": by_vlan.get(v.id, []),
    } for v in vlans]
    return {"vlans": out_vlans, "unassigned": by_vlan.get(None, []), "totals": totals}
