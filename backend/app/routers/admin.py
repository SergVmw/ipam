"""Администрирование: очистка БД (только локальный пользователь admin)."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import (Agent, AuditLog, Ip, IpEvent,
                      ScanRun, Subnet, UsageSnapshot, Vlan, utcnow)
from ..security import require_role
from ..service import audit

log = logging.getLogger("ipam.admin")
router = APIRouter(prefix="/api", tags=["admin"])


@router.post("/admin/clear-db")
async def clear_db(db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    """Полная очистка данных: сети, IP, VLAN, агенты, сканы, события.

    Сохраняются: пользователи, настройки (app_setting) и ДОКУМЕНТАЦИЯ
    (разделы, страницы-статьи, файлы на диске) — статьи не удаляются.
    Только для локального пользователя admin (не для LDAP-админа).
    """
    if user.username != "admin" or user.provider != "local":
        raise HTTPException(403, "Доступно только локальному пользователю admin")

    # Документация НЕ удаляется: статьи/разделы/файлы — независимый контент
    # (у doc_* нет FK к сетям/IP/VLAN), поэтому очистка их не затрагивает.

    # порядок с учётом FK (postgres): сначала «дети»
    for model in (IpEvent, Ip, ScanRun, UsageSnapshot,
                  Subnet, Vlan, Agent, AuditLog):
        await db.execute(sa_delete(model))

    audit(db, user, "db_clear", None, {"by": user.username, "docs_kept": True})
    await db.commit()
    log.warning("БД очищена администратором %s (документация сохранена)", user.username)
    return {"cleared": True, "kept": ["docs"]}
