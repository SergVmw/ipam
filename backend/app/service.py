import json
from ipaddress import ip_address, ip_network

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog, Ip, ScanRun, Subnet, User, utcnow

ZEROS = {"free": 0, "used": 0, "reserved": 0, "offline": 0, "cond_free": 0, "total": 0, "occupied": 0, "pct": 0.0}

# IP, не отвечающий меньше COND_FREE_DAYS дней, — «условно освобождён»;
# без ответа ≥ COND_FREE_DAYS дней — отображается как свободный.
# Должно совпадать с COND_FREE_DAYS в frontend/src/util.ts
COND_FREE_DAYS = 3


def check_cidr_in_net(cidr: str, ip: str | None) -> str | None:
    """Проверка IP в пределах сети; None/'' -> None."""
    if ip in (None, ""):
        return None
    try:
        net = ip_network(cidr)
        addr = ip_address(ip)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Некорректный IP: {ip!r}")
    if addr not in net:
        raise HTTPException(status_code=422, detail=f"{ip} вне сети {cidr}")
    return str(addr)


async def check_overlap(db: AsyncSession, cidr: str) -> None:
    new_net = ip_network(cidr)
    existing = (await db.execute(select(Subnet.cidr))).scalars().all()
    for other in existing:
        try:
            if new_net.overlaps(ip_network(other)):
                raise HTTPException(status_code=409, detail=f"Сеть пересекается с {other}")
        except ValueError:
            continue


def materialize_ips(subnet_id: int, cidr: str, gateway: str | None, dhcp_start: str | None, dhcp_end: str | None) -> list[dict]:
    """Все адреса сети (кроме network/broadcast) с флагами gateway/dhcp."""
    net = ip_network(cidr)
    lo = int(ip_address(dhcp_start)) if dhcp_start else None
    hi = int(ip_address(dhcp_end)) if dhcp_end else None
    rows = []
    for addr in net.hosts():
        s = str(addr)
        rows.append({
            "ip": s,
            "ip_int": int(addr),
            "subnet_id": subnet_id,
            "state": "free",
            "is_gateway": s == gateway,
            "in_dhcp": bool(lo is not None and hi is not None and lo <= int(addr) <= hi),
        })
    return rows


def finish_counts(c: dict) -> dict:
    # «свободно» уже включает offline ≥ COND_FREE_DAYS дн. (перенесено из offline в free);
    # cond_free — offline < COND_FREE_DAYS дн. «занято» = used + reserved (offline не в счёт)
    cond = c.get("cond_free", 0)
    c["total"] = c["free"] + c["used"] + c["reserved"] + cond
    c["occupied"] = c["used"] + c["reserved"]
    c["pct"] = round(c["occupied"] / c["total"] * 100, 1) if c["total"] else 0.0
    return c


async def cond_free_by_subnet(db: AsyncSession, subnet_ids: list[int] | None = None) -> dict[int, int]:
    """offline-IP с последним ответом < COND_FREE_DAYS дней, по сетям.

    Единое правило «условно освобождён» для всех списков (overview/subnets):
    такие адреса НЕ считаются занятыми, ≥ COND_FREE_DAYS дней — свободны.
    """
    from datetime import timedelta
    q = select(Ip.subnet_id, func.count()).where(
        Ip.state == "offline",
        Ip.last_seen >= utcnow() - timedelta(days=COND_FREE_DAYS),
    ).group_by(Ip.subnet_id)
    if subnet_ids:
        q = q.where(Ip.subnet_id.in_(subnet_ids))
    return {sid: n for sid, n in (await db.execute(q)).all()}


def finish_subnet_counts(c: dict) -> dict:
    """Завершает счётчики сети по единому правилу:
    «занято» = used + reserved; offline < 3 дн. — cond_free; offline ≥ 3 дн. — free."""
    c["total"] = c["free"] + c["used"] + c["reserved"] + c["offline"]
    c["free"] += c["offline"] - c.get("cond_free", 0)
    c["occupied"] = c["used"] + c["reserved"]
    c["pct"] = round(c["occupied"] / c["total"] * 100, 1) if c["total"] else 0.0
    return c


async def usage_counts(db: AsyncSession, subnet_id: int) -> dict:
    rows = (await db.execute(
        select(Ip.state, func.count()).where(Ip.subnet_id == subnet_id).group_by(Ip.state)
    )).all()
    c = {"free": 0, "used": 0, "reserved": 0, "offline": 0, "cond_free": 0}
    for state, n in rows:
        if state in c:
            c[state] = n
    # offline: без ответа < COND_FREE_DAYS дн. → «условно освобождён»; ≥ COND_FREE_DAYS дн. → «свободно»
    from datetime import timedelta
    cutoff = utcnow() - timedelta(days=COND_FREE_DAYS)
    cond_free = (await db.execute(
        select(func.count()).where(
            Ip.subnet_id == subnet_id,
            Ip.state == "offline",
            Ip.last_seen >= cutoff,
        )
    )).scalar() or 0
    c["cond_free"] = cond_free
    c["free"] += c["offline"] - cond_free  # offline ≥ 3 дн. считаем свободными
    return finish_counts(c)


async def last_scans(db: AsyncSession, subnet_ids: list[int]) -> dict[int, dict]:
    if not subnet_ids:
        return {}
    runs = (await db.execute(
        select(ScanRun)
        .where(ScanRun.subnet_id.in_(subnet_ids))
        .order_by(ScanRun.started_at.desc())
        .limit(2000)
    )).scalars().all()
    out: dict[int, dict] = {}
    for r in runs:
        if r.subnet_id not in out:
            out[r.subnet_id] = {
                "last_scan_at": r.started_at.isoformat() if r.started_at else None,
                "last_error": r.error,
                "last_alive": r.alive,
            }
    return out


def subnet_dict(s: Subnet, counts: dict, vlan) -> dict:
    c = {**ZEROS, **counts}
    return {
        "id": s.id,
        "cidr": s.cidr,
        "name": s.name,
        "vlan_id": s.vlan_id,
        "vlan_name": vlan.name if vlan else None,
        "vlan_color": vlan.color if vlan else None,
        "gateway": s.gateway,
        "dhcp_start": s.dhcp_start,
        "dhcp_end": s.dhcp_end,
        "scan_enabled": s.scan_enabled,
        "scan_interval_s": s.scan_interval_s,
        "scan_method": s.scan_method,
        "tags": [t for t in (s.tags or "").split(",") if t],
        "next_scan_at": s.next_scan_at.isoformat() if s.next_scan_at else None,
        "descr": s.descr,
        **{k: c[k] for k in ("total", "used", "free", "reserved", "offline", "cond_free", "pct")},
        "last_scan_at": None,
        "last_error": None,
    }


def ip_dict(r: Ip) -> dict:
    return {
        "ip": r.ip,
        "state": r.state,
        "hostname": r.hostname,
        "hostname_manual": r.hostname_manual,
        "mac": r.mac,
        "mac_vendor": r.mac_vendor,
        "owner": r.owner,
        "note": r.note,
        "is_gateway": r.is_gateway,
        "in_dhcp": r.in_dhcp,
        "first_seen": r.first_seen.isoformat() if r.first_seen else None,
        "last_seen": r.last_seen.isoformat() if r.last_seen else None,
    }


def audit(db: AsyncSession, user: User | None, action: str, target: str | None, detail: dict | None = None) -> None:
    db.add(AuditLog(
        user_id=user.id if user else None,
        action=action,
        target=target,
        detail=json.dumps(detail or {}, ensure_ascii=False),
        at=utcnow(),
    ))
