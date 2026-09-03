"""Пайплайн скана: sweep -> PTR -> diff с БД -> события -> снапшот занятости."""
import asyncio
import json
import logging
from datetime import timedelta

from sqlalchemy import func, select

from ..config import settings
from ..db import SessionLocal
from ..models import Ip, IpEvent, ScanRun, Subnet, UsageSnapshot, utcnow
from ..settings_store import get_all
from .dns import resolve_ptrs
from .logbuffer import scan_log
from .mail import send_scan_mail
from .ping import sweep

log = logging.getLogger("ipam.scanner")

busy_ids: set[int] = set()
_scan_sem = asyncio.Semaphore(settings.SCAN_CONCURRENCY)
_bg_tasks: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    """create_task с удержанием ссылки (защита от GC)."""
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return t


def add_event(db, ip: str | None, subnet_id: int, type: str, detail: dict | None = None) -> None:
    db.add(IpEvent(ip=ip, subnet_id=subnet_id, type=type,
                   detail=json.dumps(detail or {}, ensure_ascii=False), at=utcnow()))


async def scan_subnet_now(subnet_id: int, trigger: str = "manual") -> None:
    busy_ids.add(subnet_id)
    try:
        async with _scan_sem:
            async with SessionLocal() as db:
                subnet = await db.get(Subnet, subnet_id)
                if subnet is None:
                    return
                started = utcnow()
                rt = await get_all(db)  # runtime-настройки (Настройки в UI)
                error: str | None = None
                alive_map: dict[str, dict] = {}
                diag: dict = {}  # диагностика sweep для лога сканера (метод, exit, stderr, alive_ips)
                try:
                    # метод: свой у сети (если задан) иначе глобальный из Настроек
                    method = subnet.scan_method or str(rt.get("scan_method", "auto"))
                    alive_map = await sweep(subnet.cidr, method=method,
                                            timeout_ms=int(rt["scan_timeout_ms"]), rate=int(rt["scan_rate"]),
                                            ports=settings.probe_ports, diag=diag)
                except Exception as e:
                    error = str(e)
                    log.warning("scan %s failed: %s", subnet.cidr, e)
                alive = set(alive_map.keys())

                rows = {r.ip: r for r in (await db.execute(
                    select(Ip).where(Ip.subnet_id == subnet_id)
                )).scalars()}

                new_ips = freed_ips = 0
                new_list: list[str] = []
                freed_list: list[str] = []
                need_ptr: list[str] = []
                for ip_str, row in rows.items():
                    if ip_str in alive:
                        row.misses = 0
                        row.last_seen = started
                        # MAC + vendor (даёт только nmap на L2-сегменте)
                        info = alive_map.get(ip_str) or {}
                        mac = (info.get("mac") or "").lower() or None
                        vendor = (info.get("vendor") or "").strip() or None
                        if mac:
                            if row.mac != mac:
                                if row.mac is not None:
                                    add_event(db, ip_str, subnet_id, "mac_changed", {"old": row.mac, "new": mac})
                                row.mac = mac
                            if vendor and row.mac_vendor != vendor:
                                row.mac_vendor = vendor
                        if row.state != "used":
                            row.state = "used"
                            if row.first_seen is None:
                                row.first_seen = started
                            new_ips += 1
                            new_list.append(ip_str)
                            add_event(db, ip_str, subnet_id, "ip_seen")
                        if not row.hostname and not row.hostname_manual:
                            need_ptr.append(ip_str)
                    else:
                        row.misses = (row.misses or 0) + 1
                        if row.state == "used" and row.misses >= settings.FREE_AFTER_MISSES:
                            row.state = "offline"
                            freed_ips += 1
                            freed_list.append(ip_str)
                            add_event(db, ip_str, subnet_id, "ip_freed")

                # конфликт: один MAC жив на нескольких IP (видно только при nmap на L2)
                mac_to_ips: dict[str, list[str]] = {}
                for ip_str, info in alive_map.items():
                    mac = (info or {}).get("mac")
                    if mac:
                        mac_to_ips.setdefault(mac, []).append(ip_str)
                for mac, mac_ips in mac_to_ips.items():
                    if len(mac_ips) > 1:
                        add_event(db, None, subnet_id, "conflict", {"mac": mac, "ips": sorted(mac_ips)})

                # "зарезервировано, но живо" — событие раз в 24 ч на подсеть
                reserved_alive = [ip for ip in alive if ip in rows and rows[ip].state == "reserved"]
                if reserved_alive:
                    since = started - timedelta(hours=24)
                    last = (await db.execute(
                        select(func.max(IpEvent.at)).where(
                            IpEvent.subnet_id == subnet_id,
                            IpEvent.type == "reserved_alive",
                            IpEvent.at >= since,
                        )
                    )).scalar()
                    if last is None:
                        add_event(db, None, subnet_id, "reserved_alive",
                                  {"count": len(reserved_alive), "ips": sorted(reserved_alive)[:20]})

                # hostname по PTR — только для пустых и не-ручных; можно выключить в Настройках
                ptrs = {}
                if rt["resolve_dns"]:
                    servers = [x.strip() for x in str(rt["dns_servers"]).split(",") if x.strip()] or None
                    ptrs = await resolve_ptrs(need_ptr, servers)
                for ip_str, name in ptrs.items():
                    row = rows.get(ip_str)
                    if row is not None and not row.hostname and not row.hostname_manual:
                        row.hostname = name
                        add_event(db, ip_str, subnet_id, "hostname_changed", {"hostname": name})

                db.add(ScanRun(subnet_id=subnet_id, started_at=started, finished_at=utcnow(),
                               alive=len(alive), new_ips=new_ips, freed_ips=freed_ips, error=error))
                st = (await db.execute(
                    select(Ip.state, func.count()).where(Ip.subnet_id == subnet_id).group_by(Ip.state)
                )).all()
                c = {"free": 0, "used": 0, "reserved": 0, "offline": 0}
                for s_, n in st:
                    if s_ in c:
                        c[s_] = n
                db.add(UsageSnapshot(subnet_id=subnet_id, at=started, total=sum(c.values()), **c))
                # лог сканера (in-memory, удержание 1 час): анализ работы fping/nmap/TCP-пробы
                scan_log.add({
                    "at": started.isoformat(),
                    "subnet_id": subnet.id,
                    "cidr": subnet.cidr,
                    "name": subnet.name,
                    "trigger": trigger,
                    "method": diag.get("method"),
                    "method_requested": diag.get("method_requested"),
                    "params": {
                        "timeout_ms": int(rt["scan_timeout_ms"]),
                        "rate": int(rt["scan_rate"]),
                        "ports": diag.get("ports"),
                    },
                    "hosts_total": len(rows),
                    "alive": len(alive),
                    "new": new_ips,
                    "freed": freed_ips,
                    "duration_ms": diag.get("duration_ms"),
                    "exit_code": diag.get("exit_code"),
                    "stderr": diag.get("stderr_tail"),
                    "alive_ips": diag.get("alive_ips"),
                    "error": error,
                    "counts": c,
                })
                await db.commit()
                log.info("scan %s done: alive=%s new=%s freed=%s error=%s",
                         subnet.cidr, len(alive), new_ips, freed_ips, error)
                # почтовое уведомление (если включено в Настройках)
                if rt["mail_enabled"] and (new_ips or freed_ips or reserved_alive):
                    spawn(send_scan_mail(
                        rt, subnet.cidr, subnet.name, len(alive),
                        [{"ip": i, "hostname": rows[i].hostname, "owner": rows[i].owner} for i in new_list],
                        freed_list, sorted(reserved_alive),
                    ))
    finally:
        busy_ids.discard(subnet_id)
