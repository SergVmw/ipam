"""Runtime-настройки: env-дефолты + переопределения из БД (раздел «Настройки»)."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings as env
from .models import AppSetting

# ключ: тип значения
KEYS = {
    "dns_servers": str,
    "resolve_dns": bool,
    "scan_method": str,
    "scan_rate": int,
    "scan_timeout_ms": int,
    "tz_offset_min": int,
    "ui_logo": str,
    "copyright": str,
    "admin_email": str,
    "ldap_enabled": bool,
    "ldap_url": str,
    "ldap_base_dn": str,
    "ldap_user_dn_template": str,
    "ldap_search_filter": str,
    "ldap_bind_dn": str,
    "ldap_bind_password": str,
    "ldap_default_role": str,
    "ldap_allow_list": str,
    "mail_enabled": bool,
    "smtp_host": str,
    "smtp_port": int,
    "smtp_user": str,
    "smtp_password": str,
    "smtp_starttls": bool,
    "mail_from": str,
    "mail_to": str,
    "ui_links": str,
    "show_no_dns": bool,
    "search_mode": str,
    "org_name": str,
    "agent_report_interval_min": int,
}


def defaults() -> dict:
    return {
        "dns_servers": env.DNS_SERVERS,
        "resolve_dns": env.RESOLVE_DNS,
        "scan_method": "auto",
        "scan_rate": env.SCAN_RATE,
        "scan_timeout_ms": env.SCAN_TIMEOUT_MS,
        "tz_offset_min": 0,
        "ui_logo": "",
        "copyright": "",
        "admin_email": "",
        "ldap_enabled": False,
        "ldap_url": "",
        "ldap_base_dn": "",
        "ldap_user_dn_template": "{username}",
        "ldap_search_filter": "(sAMAccountName={username})",
        "ldap_bind_dn": "",
        "ldap_bind_password": "",
        "ldap_default_role": "viewer",
        "ldap_allow_list": "",  # пустое = все доменные могут входить; иначе список логинов через запятую
        "mail_enabled": env.MAIL_ENABLED,
        "smtp_host": env.SMTP_HOST,
        "smtp_port": env.SMTP_PORT,
        "smtp_user": env.SMTP_USER,
        "smtp_password": env.SMTP_PASSWORD,
        "smtp_starttls": env.SMTP_STARTTLS,
        "mail_from": env.MAIL_FROM,
        "mail_to": env.MAIL_TO,
        "ui_links": "[]",
        "show_no_dns": True,
        "search_mode": "page",
        "org_name": "",
        # глобальный троттлинг отчётов агентов (мин): повторный отчёт не чаще
        # этого интервала; агент забирает его с /api/agent/config
        "agent_report_interval_min": 15,
    }


def _coerce(key: str, raw: str):
    t = KEYS[key]
    if t is bool:
        return raw.lower() in ("1", "true", "yes", "on")
    if t is int:
        return int(raw)
    return raw


async def get_all(db: AsyncSession) -> dict:
    d = defaults()
    rows = (await db.execute(select(AppSetting))).scalars().all()
    for r in rows:
        if r.key in KEYS:
            try:
                d[r.key] = _coerce(r.key, r.value)
            except ValueError:
                pass
    return d


async def set_many(db: AsyncSession, updates: dict) -> None:
    existing = {r.key: r for r in (await db.execute(select(AppSetting))).scalars()}
    for k, v in updates.items():
        if k not in KEYS:
            continue
        raw = "true" if v is True else ("false" if v is False else str(v))
        if k in existing:
            existing[k].value = raw
        else:
            db.add(AppSetting(key=k, value=raw))
