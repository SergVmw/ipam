import asyncio
from datetime import timedelta
from ipaddress import ip_address, ip_network

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Ip, IpEvent, ScanRun, Subnet, UsageSnapshot, Vlan, utcnow
from ..schemas import SubnetIn, SubnetUpdate
from ..scanner.engine import busy_ids, scan_subnet_now, spawn
from ..security import get_current_user, require_role
from ..service import (
    ZEROS,
    audit,
    check_cidr_in_net,
    check_overlap,
    cond_free_by_subnet,
    finish_subnet_counts,
    ip_dict,
    last_scans,
    resync_subnet_ips,
    subnet_dict,
    usage_counts,
)

router = APIRouter(prefix="/api/subnets", tags=["subnets"])


def _norm_tags(raw: str | None) -> str | None:
    """'Prod, finance ,prod' -> 'prod,finance' (None/пусто -> None)."""
    if not raw:
        return None
    seen: list[str] = []
    for t in raw.split(","):
        t = t.strip().lower()
        if t and t not in seen:
            seen.append(t)
    return ",".join(seen) or None


async def _vlan_map(db: AsyncSession) -> dict[int, Vlan]:
    rows = (await db.execute(select(Vlan))).scalars().all()
    return {v.id: v for v in rows}


@router.get("")
async def list_subnets(vlan_id: int | None = None, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    q = select(Subnet).order_by(Subnet.name)
    if vlan_id is not None:
        q = q.where(Subnet.vlan_id == vlan_id)
    subnets = (await db.execute(q)).scalars().all()
    if not subnets:
        return []
    counts: dict[int, dict] = {}
    for st, sid, n in (await db.execute(
        select(Ip.state, Ip.subnet_id, func.count())
        .where(Ip.subnet_id.in_([s.id for s in subnets]))
        .group_by(Ip.state, Ip.subnet_id)
    )).all():
        c = counts.setdefault(sid, {"free": 0, "used": 0, "reserved": 0, "offline": 0, "cond_free": 0})
        if st in c:
            c[st] = n
    # единое правило: занято = used + reserved; offline < 3 дн. — «усл. осв.»
    cond_by = await cond_free_by_subnet(db, [s.id for s in subnets])
    for sid_, c in counts.items():
        c["cond_free"] = cond_by.get(sid_, 0)
        finish_subnet_counts(c)
    vlans = await _vlan_map(db)
    scans = await last_scans(db, [s.id for s in subnets])
    out = []
    for s in subnets:
        d = subnet_dict(s, counts.get(s.id, dict(ZEROS)), vlans.get(s.vlan_id))
        d.update(scans.get(s.id, {"last_scan_at": None, "last_error": None}))
        out.append(d)
    return out


@router.post("", status_code=201)
async def create_subnet(data: SubnetIn, db=Depends(get_db), user=Depends(require_role("admin", "operator"))):
    await check_overlap(db, data.cidr)
    if data.vlan_id is not None and not await db.get(Vlan, data.vlan_id):
        raise HTTPException(422, "VLAN не найден")
    s = Subnet(
        name=data.name,
        cidr=data.cidr,
        vlan_id=data.vlan_id,
        gateway=check_cidr_in_net(data.cidr, data.gateway),
        dhcp_start=check_cidr_in_net(data.cidr, data.dhcp_start),
        dhcp_end=check_cidr_in_net(data.cidr, data.dhcp_end),
        scan_enabled=data.scan_enabled,
        scan_interval_s=data.scan_interval_s,
        scan_method=(data.scan_method or None),
        tags=_norm_tags(data.tags),
        descr=data.descr,
    )
    if s.scan_enabled:
        s.next_scan_at = utcnow() + timedelta(seconds=s.scan_interval_s or 3600)
    db.add(s)
    await db.flush()
    # вложенность разрешена: IP унаследованы от родительской сети переезжают сюда,
    # адреса, не покрытые подсетями, — материализуются
    await resync_subnet_ips(db, s.id, s.cidr, s.gateway, s.dhcp_start, s.dhcp_end)
    audit(db, user, "subnet_create", s.name, {"cidr": s.cidr})
    await db.commit()
    await db.refresh(s)
    return {"id": s.id}


@router.get("/{sid}")
async def get_subnet(sid: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    s = await db.get(Subnet, sid)
    if not s:
        raise HTTPException(404, "Сеть не найдена")
    counts = await usage_counts(db, sid)
    vlans = await _vlan_map(db)
    d = subnet_dict(s, counts, vlans.get(s.vlan_id))
    scans = await last_scans(db, [sid])
    d.update(scans.get(sid, {"last_scan_at": None, "last_error": None}))
    d["busy"] = sid in busy_ids
    return d


@router.put("/{sid}")
async def update_subnet(sid: int, data: SubnetUpdate, db=Depends(get_db), user=Depends(require_role("admin", "operator"))):
    s = await db.get(Subnet, sid)
    if not s:
        raise HTTPException(404, "Сеть не найдена")
    if data.vlan_id and not await db.get(Vlan, data.vlan_id):
        raise HTTPException(422, "VLAN не найден")
    changes: dict = {}
    if data.name is not None:
        s.name = data.name
        changes["name"] = data.name
    if data.vlan_id is not None:
        new_vlan = data.vlan_id or None  # 0 = снять VLAN
        if new_vlan != s.vlan_id:
            s.vlan_id = new_vlan
            changes["vlan_id"] = s.vlan_id
    if any(v is not None for v in (data.gateway, data.dhcp_start, data.dhcp_end)):
        s.gateway = check_cidr_in_net(s.cidr, data.gateway if data.gateway is not None else s.gateway)
        s.dhcp_start = check_cidr_in_net(s.cidr, data.dhcp_start if data.dhcp_start is not None else s.dhcp_start)
        s.dhcp_end = check_cidr_in_net(s.cidr, data.dhcp_end if data.dhcp_end is not None else s.dhcp_end)
        lo = int(ip_address(s.dhcp_start)) if s.dhcp_start else None
        hi = int(ip_address(s.dhcp_end)) if s.dhcp_end else None
        rows = (await db.execute(select(Ip).where(Ip.subnet_id == sid))).scalars().all()
        for r in rows:
            r.is_gateway = r.ip == s.gateway
            r.in_dhcp = bool(lo is not None and hi is not None and lo <= r.ip_int <= hi)
        changes["gateway/dhcp"] = {"gateway": s.gateway, "dhcp": [s.dhcp_start, s.dhcp_end]}
    if data.scan_enabled is not None:
        s.scan_enabled = data.scan_enabled
        changes["scan_enabled"] = data.scan_enabled
    if data.scan_interval_s is not None:
        s.scan_interval_s = data.scan_interval_s
        changes["scan_interval_s"] = data.scan_interval_s
    if data.scan_method is not None:
        s.scan_method = data.scan_method or None
        changes["scan_method"] = s.scan_method or "default"
    if data.tags is not None:
        s.tags = _norm_tags(data.tags)
        changes["tags"] = s.tags
    if data.descr is not None:
        s.descr = data.descr
        changes["descr"] = data.descr
    if s.scan_enabled:
        s.next_scan_at = utcnow() + timedelta(seconds=s.scan_interval_s or 3600)
    else:
        s.next_scan_at = None
    if changes:
        audit(db, user, "subnet_update", s.name, changes)
    await db.commit()
    return {"ok": True}


@router.delete("/{sid}", status_code=204)
async def delete_subnet(sid: int, db=Depends(get_db), user=Depends(require_role("admin"))):
    s = await db.get(Subnet, sid)
    if not s:
        raise HTTPException(404, "Сеть не найдена")
    # вложенность: адреса возвращаем ближайшей родительской сети (иначе пропадут по каскаду)
    from ipaddress import ip_network as _ipn
    from sqlalchemy import update as sa_update
    parents = []
    try:
        me = _ipn(s.cidr)
        for o in (await db.execute(select(Subnet.id, Subnet.cidr))).all():
            if o.id == sid:
                continue
            try:
                if me.subnet_of(_ipn(o.cidr)):
                    parents.append((int(o.cidr.split("/")[1]), o.id))
            except ValueError:
                continue
        if parents:
            parents.sort(reverse=True)  # самая мелкая (наибольший префикс) — ближайшая
            await db.execute(sa_update(Ip).where(Ip.subnet_id == sid).values(subnet_id=parents[0][1]))
    except ValueError:
        pass
    await db.execute(delete(Ip).where(Ip.subnet_id == sid))
    await db.execute(delete(IpEvent).where(IpEvent.subnet_id == sid))
    await db.execute(delete(UsageSnapshot).where(UsageSnapshot.subnet_id == sid))
    await db.execute(delete(ScanRun).where(ScanRun.subnet_id == sid))
    audit(db, user, "subnet_delete", s.name, {"cidr": s.cidr})
    await db.execute(delete(Subnet).where(Subnet.id == sid))
    await db.commit()


@router.post("/{sid}/scan", status_code=202)
async def trigger_scan(sid: int, db=Depends(get_db), user=Depends(require_role("admin", "operator"))):
    s = await db.get(Subnet, sid)
    if not s:
        raise HTTPException(404, "Сеть не найдена")
    if sid in busy_ids:
        raise HTTPException(409, "Скан уже выполняется")
    spawn(scan_subnet_now(sid))
    return {"started": True, "subnet_id": sid}


@router.get("/{sid}/scan-runs")
async def scan_runs(sid: int, limit: int = 5, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    runs = (await db.execute(
        select(ScanRun).where(ScanRun.subnet_id == sid).order_by(ScanRun.started_at.desc()).limit(min(limit, 50))
    )).scalars().all()
    return [{
        "id": r.id,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "alive": r.alive,
        "new_ips": r.new_ips,
        "freed_ips": r.freed_ips,
        "error": r.error,
    } for r in runs]


@router.get("/{sid}/ips")
async def list_ips(
    sid: int,
    state: str | None = None,
    q: str | None = None,
    net: str | None = None,
    page: int = 0,
    size: int = 1000,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    s = await db.get(Subnet, sid)
    if not s:
        raise HTTPException(404, "Сеть не найдена")
    query = select(Ip).where(Ip.subnet_id == sid)
    if state:
        query = query.where(Ip.state == state)
    if net:
        try:
            n = ip_network(net)
        except ValueError:
            raise HTTPException(422, "Некорректный диапазон")
        query = query.where(Ip.ip_int >= int(n.network_address), Ip.ip_int <= int(n.broadcast_address))
    if q:
        like = f"%{q}%"
        query = query.where(or_(Ip.ip.like(like), Ip.hostname.ilike(like), Ip.owner.ilike(like), Ip.mac.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    size = max(1, min(size, 100000))
    rows = (await db.execute(query.order_by(Ip.ip_int).offset(page * size).limit(size))).scalars().all()
    return {"items": [ip_dict(r) for r in rows], "total": total}


@router.get("/{sid}/blocks")
async def blocks(sid: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    """Сводка по блокам /24 — для крупных сетей (> /21)."""
    s = await db.get(Subnet, sid)
    if not s:
        raise HTTPException(404, "Сеть не найдена")
    from collections import defaultdict

    net = ip_network(s.cidr)
    block_bits = min(24, net.prefixlen)
    mask = (~((1 << (32 - block_bits)) - 1)) & 0xFFFFFFFF
    agg: dict[int, dict] = defaultdict(lambda: {"free": 0, "used": 0, "reserved": 0, "offline": 0})
    for ip_int, st in (await db.execute(select(Ip.ip_int, Ip.state).where(Ip.subnet_id == sid))).all():
        if st in agg[ip_int & mask]:
            agg[ip_int & mask][st] += 1
    out = []
    for base in sorted(agg):
        c = agg[base]
        total = sum(c.values())
        # единое правило: занято = used + reserved, offline — не в заполняемость
        out.append({
            "cidr": str(ip_network(base, block_bits)),
            "free": c["free"] + c["offline"],
            "used": c["used"],
            "reserved": c["reserved"],
            "offline": c["offline"],
            "total": total,
            "pct": round((c["used"] + c["reserved"]) / total * 100, 1) if total else 0.0,
        })
    return out
