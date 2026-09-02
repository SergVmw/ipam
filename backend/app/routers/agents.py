import asyncio
import json
import secrets
from datetime import timedelta
from ipaddress import ip_address, ip_network

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent_installer import install_agent
from ..config import settings
from ..db import SessionLocal, get_db
from ..models import Agent, AgentReport, Ip, IpEvent, Subnet, utcnow
from ..schemas import AgentIn, AgentUpdate
from ..security import get_current_user, require_role
from ..service import audit

router = APIRouter(prefix="/api", tags=["agents"])

INSTALL_STATE: dict[int, dict] = {}
FORCE_POLL_STATE: dict[int, dict] = {}
_bg_tasks: set = set()

POLL_FILE_DEFAULT = "/var/lib/ipam_agent/force_poll"


def _agent_dict(a: Agent) -> dict:
    return {
        "id": a.id, "name": a.name, "key": a.key,
        "subnet_ids": [int(x) for x in (a.subnet_ids or "").split(",") if x.strip()] if a.subnet_ids else None,
        "enabled": a.enabled,
        "last_report_at": a.last_report_at.isoformat() if a.last_report_at else None,
        "last_hosts": a.last_hosts,
        "descr": a.descr,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "ssh_host": a.ssh_host, "ssh_port": a.ssh_port, "ssh_user": a.ssh_user,
    "ssh_password": "****" if a.ssh_password else "",
    "poll_file": a.poll_file or POLL_FILE_DEFAULT,
    "report_interval_min": a.report_interval_min,
    "last_install_at": a.last_install_at.isoformat() if a.last_install_at else None,
        "install_log": (json.loads(a.install_log) if a.install_log else None),
    }


@router.get("/agents")
async def list_agents(db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    rows = (await db.execute(select(Agent).order_by(Agent.name))).scalars().all()
    return [_agent_dict(a) for a in rows]


@router.post("/agents", status_code=201)
async def create_agent(data: AgentIn, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    exists = (await db.execute(select(Agent).where(Agent.name == data.name))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "Агент с таким именем уже существует")
    if data.subnet_ids:
        cnt = (await db.execute(select(Subnet.id).where(Subnet.id.in_(data.subnet_ids)))).all()
        if len(cnt) != len(set(data.subnet_ids)):
            raise HTTPException(422, "Указаны несуществующие сети")
    a = Agent(name=data.name, key=secrets.token_hex(24),
              subnet_ids=",".join(str(i) for i in data.subnet_ids) if data.subnet_ids else None,
              enabled=data.enabled, descr=data.descr, created_at=utcnow(),
              report_interval_min=data.report_interval_min or None,
              ssh_host=data.ssh_host, ssh_port=data.ssh_port,
              ssh_user=data.ssh_user, ssh_password=data.ssh_password,
              poll_file=data.poll_file or None)
    db.add(a)
    audit(db, user, "agent_create", data.name, {"ssh": data.ssh_host})
    await db.commit()
    await db.refresh(a)
    return _agent_dict(a)


@router.put("/agents/{aid}")
async def update_agent(aid: int, data: AgentUpdate, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    a = await db.get(Agent, aid)
    if not a:
        raise HTTPException(404, "Агент не найден")
    changes: dict = {}
    if data.name is not None and data.name != a.name:
        clash = (await db.execute(select(Agent).where(Agent.name == data.name, Agent.id != aid))).scalar_one_or_none()
        if clash:
            raise HTTPException(409, "Имя уже занято")
        a.name = data.name
        changes["name"] = data.name
    if data.subnet_ids is not None:
        if data.subnet_ids:
            cnt = (await db.execute(select(Subnet.id).where(Subnet.id.in_(data.subnet_ids)))).all()
            if len(cnt) != len(set(data.subnet_ids)):
                raise HTTPException(422, "Указаны несуществующие сети")
        a.subnet_ids = ",".join(str(i) for i in data.subnet_ids) if data.subnet_ids else None
        changes["subnet_ids"] = a.subnet_ids
    if data.enabled is not None:
        a.enabled = data.enabled
        changes["enabled"] = data.enabled
    if data.descr is not None:
        a.descr = data.descr
        changes["descr"] = data.descr
    if data.ssh_host is not None:
        a.ssh_host = data.ssh_host
        changes["ssh_host"] = data.ssh_host
    if data.ssh_port is not None:
        a.ssh_port = data.ssh_port
        changes["ssh_port"] = data.ssh_port
    if data.ssh_user is not None:
        a.ssh_user = data.ssh_user
        changes["ssh_user"] = data.ssh_user
    if data.ssh_password is not None and data.ssh_password not in ("****",):
        a.ssh_password = data.ssh_password or None
        changes["ssh_password"] = "***"
    if data.poll_file is not None:
        a.poll_file = data.poll_file or None
        changes["poll_file"] = a.poll_file
    if data.report_interval_min is not None:
        a.report_interval_min = data.report_interval_min or None  # 0 = глобальная настройка
        changes["report_interval_min"] = a.report_interval_min
    if data.regenerate_key:
        a.key = secrets.token_hex(24)
        changes["key"] = "*** (пересоздан)"
    if changes:
        audit(db, user, "agent_update", a.name, changes)
    await db.commit()
    await db.refresh(a)
    return _agent_dict(a)


@router.post("/agents/{aid}/install", status_code=202)
async def install_start(aid: int, request: Request, ipam_url: str | None = None,
                        db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    a = await db.get(Agent, aid)
    if not a:
        raise HTTPException(404, "Агент не найден")
    if not (a.ssh_host and a.ssh_user):
        raise HTTPException(422, "Укажите SSH-хост и пользователя (и пароль) в карточке агента")
    if INSTALL_STATE.get(aid, {}).get("state") == "running":
        raise HTTPException(409, "Установка уже выполняется")
    base = (ipam_url or str(request.base_url)).rstrip("/")

    async def runner():
        INSTALL_STATE[aid] = {"state": "running", "steps": [], "finished_at": None}
        try:
            steps = await asyncio.to_thread(
                install_agent, a.ssh_host, a.ssh_port, a.ssh_user, a.ssh_password or "", base, a.key)
            INSTALL_STATE[aid] = {"state": "done", "steps": steps, "finished_at": utcnow().isoformat()}
        except Exception as e:
            INSTALL_STATE[aid] = {"state": "error",
                                  "steps": [{"step": "Ошибка", "ok": False, "detail": str(e)}],
                                  "finished_at": utcnow().isoformat()}
        try:
            async with SessionLocal() as db2:
                a2 = await db2.get(Agent, aid)
                if a2:
                    a2.last_install_at = utcnow()
                    a2.install_log = json.dumps(INSTALL_STATE[aid]["steps"], ensure_ascii=False)
                    await db2.commit()
        except Exception:
            pass

    t = asyncio.create_task(runner())
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    audit(db, user, "agent_install_start", a.name, {"host": a.ssh_host})
    await db.commit()
    return {"started": True}


@router.get("/agents/{aid}/install-state")
async def install_state(aid: int, user=Depends(require_role("admin"))):
    return INSTALL_STATE.get(aid, {"state": "idle", "steps": [], "finished_at": None})


# ---------------------------------------------------------------------------
# Конфиг агента (агент забирает его с ядра перед каждым запуском —
# изменения настроек агента в ядре автоматически прилетают на агента)
# ---------------------------------------------------------------------------
@router.get("/agent/config")
async def agent_config(request: Request, db: AsyncSession = Depends(get_db)):
    key = request.headers.get("X-Agent-Key", "")
    agent = (await db.execute(select(Agent).where(Agent.key == key))).scalar_one_or_none() if key else None
    if agent is None:
        raise HTTPException(401, "Неизвестный агент")
    if not agent.enabled:
        raise HTTPException(403, "Агент отключён")
    networks = ""
    if agent.subnet_ids:
        ids = [int(x) for x in agent.subnet_ids.split(",") if x.strip()]
        rows = (await db.execute(select(Subnet.cidr).where(Subnet.id.in_(ids)))).scalars().all()
        networks = ",".join(rows)
    from ..settings_store import get_all
    st = await get_all(db)
    # интервал: per-agent override (если задан), иначе глобальная настройка
    global_min = max(1, min(1440, int(st.get("agent_report_interval_min") or 15)))
    interval_min = max(1, min(1440, int(agent.report_interval_min or global_min)))
    return {
        "name": agent.name,
        "enabled": True,
        "networks": networks,
        "poll_file": agent.poll_file or POLL_FILE_DEFAULT,
        "report_interval_s": interval_min * 60,
    }


# ---------------------------------------------------------------------------
# Принудительный опрос: ядро по SSH трогает файл-триггер на агенте;
# агент подхватывает его на ближайшем cron-цикле (cron у агента — каждые 5 мин,
# сам скрипт тrottles-ится: если свежий отчёт уже ушёл — цикл пропускает)
# ---------------------------------------------------------------------------
def _ssh_force_poll(host, port, user, password, poll_file) -> str:
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, port=port or 22, username=user, password=password or "",
              timeout=15, allow_agent=False, look_for_keys=False)
    try:
        _in, out, err = c.exec_command(
            f"if [ ! -f /opt/ipam_agent.sh ]; then echo NO_AGENT; exit 0; fi; "
            f"mkdir -p $(dirname '{poll_file}') && touch '{poll_file}' && echo OK",
            timeout=20)
        out_s = out.read().decode(errors="ignore").strip()
        err_s = err.read().decode(errors="ignore").strip()
        if out_s == "OK":
            return "триггер отправлен — агент отработает на ближайшем cron-цикле (каждые 5 мин)"
        if out_s == "NO_AGENT":
            return "на агенте нет /opt/ipam_agent.sh — сначала «Установить»"
        return f"ошибка: {err_s or 'неизвестна'}"
    finally:
        c.close()


@router.post("/agents/{aid}/force-poll", status_code=202)
async def force_poll(aid: int, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    a = await db.get(Agent, aid)
    if not a:
        raise HTTPException(404, "Агент не найден")
    if not (a.ssh_host and a.ssh_user):
        raise HTTPException(422, "Укажите SSH-хост и пользователя агента")
    if FORCE_POLL_STATE.get(aid, {}).get("state") == "running":
        raise HTTPException(409, "Принудительный опрос уже выполняется")
    poll_file = a.poll_file or POLL_FILE_DEFAULT
    host, port, ssh_user, pwd = a.ssh_host, a.ssh_port, a.ssh_user, a.ssh_password
    audit(db, user, "agent_force_poll", a.name, {"host": a.ssh_host})
    await db.commit()

    async def runner():
        FORCE_POLL_STATE[aid] = {"state": "running", "detail": "подключение по SSH…", "finished_at": None}
        try:
            msg = await asyncio.to_thread(_ssh_force_poll, host, port, ssh_user, pwd, poll_file)
            FORCE_POLL_STATE[aid] = {"state": "done", "detail": msg, "finished_at": utcnow().isoformat()}
        except Exception as e:
            FORCE_POLL_STATE[aid] = {"state": "error", "detail": str(e), "finished_at": utcnow().isoformat()}

    t = asyncio.create_task(runner())
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return {"started": True}


@router.get("/agents/{aid}/force-poll-state")
async def force_poll_state(aid: int, user=Depends(require_role("admin"))):
    return FORCE_POLL_STATE.get(aid, {"state": "idle", "detail": "", "finished_at": None})


def _ssh_remove_agent(host, port, user, password) -> tuple[bool, str]:
    """Удалить след агента на хосте: скрипт, env, cfg, состояние, строка cron.

    Best-effort: вызывается при удалении агента из ядра. Ошибка тут не должна
    блокировать удаление записи в ядре — её ловит вызывающий.
    """
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, port=port or 22, username=user, password=password,
              timeout=10, banner_timeout=10, auth_timeout=10,
              allow_agent=False, look_for_keys=False)
    try:
        # cron: убрать только строки с маркером ipam_agent, остальные оставить;
        # если cron-таблица стала пустой — удалить её целиком (crontab -r)
        _in, out, err = c.exec_command(
            "rm -f /opt/ipam_agent.sh /etc/ipam_agent.env /etc/ipam_agent.cfg; "
            "rm -rf /var/lib/ipam_agent; "
            "if command -v crontab >/dev/null 2>&1; then "
            "  crontab -l 2>/dev/null | grep -v ipam_agent > /tmp/ipam_crontab.del 2>/dev/null; "
            "  if [ -s /tmp/ipam_crontab.del ]; then crontab /tmp/ipam_crontab.del 2>/dev/null; "
            "  else crontab -r 2>/dev/null; fi; "
            "  rm -f /tmp/ipam_crontab.del; "
            "fi; echo CLEANED",
            timeout=30)
        out_s = out.read().decode(errors="ignore").strip()
        err_s = err.read().decode(errors="ignore").strip()
        if out_s == "CLEANED":
            return True, "удалено на хосте: скрипт, env, cfg, состояние, строка cron"
        return False, f"ошибка: {err_s or 'неизвестна'}"
    finally:
        c.close()


@router.delete("/agents/{aid}", status_code=200)
async def delete_agent(aid: int, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    a = await db.get(Agent, aid)
    if not a:
        raise HTTPException(404, "Агент не найден")

    # --- 1) удалённая очистка (best-effort): если для агента задан SSH —
    # подключаемся, чистим след установки, отключаемся. Хост недоступен →
    # не блокируем: агент всё равно удаляется в ядре, детали в ответе.
    if a.ssh_host and a.ssh_user and a.ssh_password:
        try:
            ok, msg = await asyncio.to_thread(
                _ssh_remove_agent, a.ssh_host, a.ssh_port, a.ssh_user, a.ssh_password)
            remote = msg if ok else f"хост {a.ssh_host}: {msg} — удалён только в ядре"
        except Exception as e:
            remote = f"хост {a.ssh_host}: {e} — удалён только в ядре"
    else:
        remote = "SSH-доступ не задан — удалён только в ядре"

    # --- 2) удаление в ядре
    audit(db, user, "agent_delete", a.name)
    await db.delete(a)
    await db.commit()
    return {"deleted": True, "remote_cleanup": remote}


def _add_event(db, ip: str | None, subnet_id, etype: str, detail: dict | None = None):
    import json
    db.add(IpEvent(ip=ip, subnet_id=subnet_id, type=etype,
                   detail=json.dumps(detail or {}, ensure_ascii=False), at=utcnow()))


@router.post("/agent/report")
async def agent_report(request: Request, db: AsyncSession = Depends(get_db)):
    """Отчёт агента (X-Agent-Key). Тело: {"hosts": [{"ip", "mac"?, "vendor"?, "hostname"?}]}"""
    key = request.headers.get("X-Agent-Key", "")
    agent = (await db.execute(select(Agent).where(Agent.key == key))).scalar_one_or_none() if key else None
    if agent is None or not agent.enabled:
        raise HTTPException(401, "Неизвестный или отключённый агент")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(422, "Ожидается JSON {\"hosts\": [...]}")
    hosts = data.get("hosts") or []

    subnets = (await db.execute(select(Subnet))).scalars().all()
    ranges = []
    for s in subnets:
        net = ip_network(s.cidr)
        ranges.append((s, int(net.network_address), int(net.broadcast_address)))
    agent_subnet_ids = set(int(x) for x in (agent.subnet_ids or "").split(",") if x.strip()) if agent.subnet_ids else None

    now = utcnow()
    reported: set[str] = set()
    applied = 0
    with_mac = 0
    for h in hosts:
        try:
            ip_str = str(h.get("ip", "")).strip()
            ip_int = int(ip_address(ip_str))
        except (ValueError, TypeError):
            continue
        subnet = next((s for s, lo, hi in ranges if lo <= ip_int <= hi), None)
        if subnet is None:
            continue
        if agent_subnet_ids is not None and subnet.id not in agent_subnet_ids:
            continue
        row = (await db.execute(select(Ip).where(Ip.ip == ip_str))).scalar_one_or_none()
        if row is None:
            continue
        reported.add(ip_str)
        mac = str(h.get("mac") or "").lower()
        vendor = str(h.get("vendor") or "").strip() or None
        if mac:
            with_mac += 1
            if row.mac != mac:
                if row.mac:
                    _add_event(db, ip_str, subnet.id, "mac_changed", {"old": row.mac, "new": mac, "source": "agent"})
                row.mac = mac
            if vendor and row.mac_vendor != vendor:
                row.mac_vendor = vendor
        hostname = str(h.get("hostname") or "").strip()
        if hostname and not row.hostname_manual and not row.hostname:
            row.hostname = hostname
            _add_event(db, ip_str, subnet.id, "hostname_changed", {"hostname": hostname, "source": "agent"})
        if row.state not in ("used", "reserved"):
            row.state = "used"
            if row.first_seen is None:
                row.first_seen = now
            _add_event(db, ip_str, subnet.id, "ip_seen", {"source": "agent"})
        row.misses = 0
        row.last_seen = now
        applied += 1

    # анти-флэппинг: IP в сетях агента, которых нет в отчёте
    if reported:
        target = [s for s, _, _ in ranges if agent_subnet_ids is None or s.id in agent_subnet_ids]
        for s in target:
            rows = (await db.execute(
                select(Ip).where(Ip.subnet_id == s.id, Ip.state.in_(["used", "offline"]))
            )).scalars().all()
            for r in rows:
                if r.ip in reported:
                    continue
                r.misses = (r.misses or 0) + 1
                if r.state == "used" and r.misses >= settings.FREE_AFTER_MISSES:
                    r.state = "offline"
                    _add_event(db, r.ip, s.id, "ip_freed", {"source": "agent"})

    agent.last_report_at = now
    agent.last_hosts = len([h for h in hosts if str(h.get("ip", "")).strip()])

    # --- история отработок: каждое срабатывание агента остаётся в ядре
    # (карточка агента → «отчёты»: видно, когда агент последний раз доложил,
    #  сколько было отчётов сегодня и т.п.). Держим 14 дней.
    db.add(AgentReport(
        agent_id=agent.id, at=now,
        hosts=len([h for h in hosts if str(h.get("ip", "")).strip()]),
        applied=applied, with_mac=with_mac,
    ))
    await db.execute(sa_delete(AgentReport).where(
        AgentReport.agent_id == agent.id,
        AgentReport.at < now - timedelta(days=14),
    ))
    await db.commit()
    return {"ok": True, "reported": len(reported), "applied": applied, "with_mac": with_mac}


@router.get("/agents/{aid}/reports")
async def agent_reports(aid: int, limit: int = 100,
                        db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    """История приёмов отчётов (новые сверху). limit до 500."""
    a = await db.get(Agent, aid)
    if not a:
        raise HTTPException(404, "Агент не найден")
    rows = (await db.execute(
        select(AgentReport).where(AgentReport.agent_id == aid)
        .order_by(AgentReport.at.desc(), AgentReport.id.desc())
        .limit(min(max(limit, 1), 500))
    )).scalars().all()
    return [{"at": r.at.isoformat(), "hosts": r.hosts, "applied": r.applied,
             "with_mac": r.with_mac} for r in rows]
