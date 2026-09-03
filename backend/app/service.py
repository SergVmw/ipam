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
    """Запрет: дубли и ЧАСТИЧНЫЕ пересечения.
    Вложение разрешено (подсеть внутри master-сети и наоборот) — как в phpIPAM."""
    new_net = ip_network(cidr)
    existing = (await db.execute(select(Subnet.cidr))).scalars().all()
    for other in existing:
        try:
            o = ip_network(other)
        except ValueError:
            continue
        if new_net == o:
            raise HTTPException(status_code=409, detail=f"Сеть {other} уже существует")
        if new_net.subnet_of(o) or o.subnet_of(new_net):
            continue  # вложение: master/подсеть — нормально
        if new_net.overlaps(o):
            raise HTTPException(status_code=409, detail=f"Сеть частично пересекается с {other}")


async def resync_subnet_ips(db: AsyncSession, subnet_id: int, cidr: str,
                            gateway: str | None = None,
                            dhcp_start: str | None = None,
                            dhcp_end: str | None = None) -> None:
    """IP-синхронизация новой сети при допустимой вложенности (Ip.ip уникален
    глобально — адрес живёт ровно в одной сети):
    1) строки, «наследованные» от строгих родительских сетей (крупнее, содержат
       эту) — перенести в эту сеть;
    2) адреса, у которых строки ещё нет (не покрыты подсетями) — материализовать.
    Порядок создания не важен: /24→/29 и /29→/24 дают одинаковый результат."""
    from sqlalchemy import insert, update as sa_update
    net = ip_network(cidr)
    lo, hi = int(net.network_address), int(net.broadcast_address)
    parent_ids = []
    for o in (await db.execute(select(Subnet.id, Subnet.cidr))).all():
        if o.id == subnet_id:
            continue
        try:
            if net.subnet_of(ip_network(o.cidr)):
                parent_ids.append(o.id)
        except ValueError:
            continue
    if parent_ids:
        # переносим ТОЛЬКО «хосты» этой сети (net.hosts() — ровно те адреса,
        # что материализует у автономной сети) — тогда вложенная сеть выглядит
        # так же, как автономная. network/broadcast остаются в родителе.
        host_ints = [int(a) for a in net.hosts()]
        for i in range(0, len(host_ints), 5000):
            chunk = host_ints[i:i + 5000]
            await db.execute(
                sa_update(Ip)
                .where(Ip.subnet_id.in_(parent_ids), Ip.ip_int.in_(chunk))
                .values(subnet_id=subnet_id)
            )
    existing = set((await db.execute(
        select(Ip.ip_int).where(Ip.ip_int >= lo, Ip.ip_int <= hi)
    )).scalars().all())
    rows = materialize_ips(subnet_id, cidr, gateway, dhcp_start, dhcp_end)
    missing = [r for r in rows if r["ip_int"] not in existing]
    for i in range(0, len(missing), 5000):
        await db.execute(insert(Ip), missing[i:i + 5000])


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


# --- Доступность сетей для индикаторов «Я» (ядро) и «А» (агент) в «Сети» ---
# Критерий доступности: хоть ОДИН хост сети достижим. Критерий недоступности:
# все адреса unreachable (живых 0) — но ТОЛЬКО если скан/отчёт отработал.
# Состояния: ok (зелёный) | off/no (серый — недоступно) | err (жёлтый — ошибка скана,
# результат не достоверен) | none (пунктирный — данных нет: не сканировалось / агента нет)

def core_reach_from_scan(scan: dict | None, total: int) -> dict:
    """Индикатор «Я» по последнему скану ЯДРОМ."""
    if not scan or not scan.get("last_scan_at"):
        return {"state": "none", "alive": None, "at": None, "error": None, "total": total}
    at = scan["last_scan_at"]
    alive = scan.get("last_alive")
    if scan.get("last_error"):
        return {"state": "err", "alive": alive, "at": at, "error": scan["last_error"], "total": total}
    state = "ok" if (alive or 0) >= 1 else "off"
    return {"state": state, "alive": alive, "at": at, "error": None, "total": total}


async def agent_reach_by_subnet(db: AsyncSession, subnet_ids: list[int]) -> dict[int, dict]:
    """Индикатор «А» по последним ОТЧЁТАМ АГЕНТОВ, ответственных за сеть.

    Свежесть отчёта: не старше max(45 мин, 3×интервал отчёта агента).
    ok  — свежий отчёт содержит ≥1 хост сети;
    no  — свежий отчёт есть, но хостов из сети 0 (недоступно из агента);
    off — агент за сеть отвечает, но свежих отчётов нет (агент молчит/упал);
    none — ни один включённый агент не отвечает за эту сеть.
    """
    from datetime import timedelta

    from .models import Agent, AgentSubnetReport
    from .settings_store import get_all

    out: dict[int, dict] = {
        sid: {"state": "none", "at": None, "hosts": None, "agent": None} for sid in subnet_ids
    }
    if not subnet_ids:
        return out
    agents = (await db.execute(select(Agent).where(Agent.enabled.is_(True)))).scalars().all()
    if not agents:
        return out
    st = await get_all(db)
    global_min = max(1, min(1440, int(st.get("agent_report_interval_min") or 15)))
    now = utcnow()
    # последние записи (agent, subnet)
    reps = (await db.execute(
        select(AgentSubnetReport).where(AgentSubnetReport.subnet_id.in_(subnet_ids))
    )).scalars().all()
    latest: dict[tuple[int, int], AgentSubnetReport] = {}
    for r in reps:
        k = (r.agent_id, r.subnet_id)
        cur = latest.get(k)
        if cur is None or (r.at, r.id) > (cur.at, cur.id):
            latest[k] = r
    # ответственность: сеть -> агенты
    responsible: dict[int, list[Agent]] = {sid: [] for sid in subnet_ids}
    for a in agents:
        a_ids = set(int(x) for x in (a.subnet_ids or "").split(",") if x.strip()) if a.subnet_ids else None
        for sid in subnet_ids:
            if a_ids is None or sid in a_ids:
                responsible[sid].append(a)
    RANK = {"none": 0, "off": 1, "no": 2, "ok": 3}
    for sid, ags in responsible.items():
        res = out[sid]
        for a in ags:
            interval_min = max(1, min(1440, int(a.report_interval_min or global_min)))
            window = timedelta(minutes=max(45, 3 * interval_min))
            rec = latest.get((a.id, sid))
            if rec is not None and rec.at >= now - window:
                st_, at, hosts = ("ok" if rec.hosts > 0 else "no"), rec.at.isoformat(), rec.hosts
            elif rec is not None:
                st_, at, hosts = "off", rec.at.isoformat(), None
            else:
                # агент отчитывался (в целом), но по этой сети свежей записи нет
                st_, at, hosts = "off", (a.last_report_at.isoformat() if a.last_report_at else None), None
            if RANK[st_] > RANK[res["state"]]:
                res.update(state=st_, at=at, hosts=hosts, agent=a.name)
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
