"""Администрирование: очистка БД (только локальный пользователь admin)."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import (Agent, AuditLog, DocFile, DocPage, DocSection, Ip, IpEvent,
                      ScanRun, Subnet, UsageSnapshot, Vlan, utcnow)
from ..security import require_role
from ..service import audit

log = logging.getLogger("ipam.admin")
router = APIRouter(prefix="/api", tags=["admin"])


@router.post("/admin/clear-db")
async def clear_db(db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    """Полная очистка данных: сети, IP, VLAN, агенты, документы, сканы, события.

    Сохраняются: пользователи и настройки (app_setting). Только для локального
    пользователя admin (не для LDAP-админа).
    """
    if user.username != "admin" or user.provider != "local":
        raise HTTPException(403, "Доступно только локальному пользователю admin")

    # файлы документов на диске
    from pathlib import Path
    from ..routers.docs import _docs_dir
    try:
        for f in _docs_dir().iterdir():
            if f.is_file():
                f.unlink(missing_ok=True)
    except Exception:
        pass

    # порядок с учётом FK (postgres): сначала «дети»
    for model in (IpEvent, Ip, ScanRun, UsageSnapshot,
                  DocFile, DocPage, DocSection,
                  Subnet, Vlan, Agent, AuditLog):
        await db.execute(sa_delete(model))

    audit(db, user, "db_clear", None, {"by": user.username})
    await db.commit()
    log.warning("БД очищена администратором %s", user.username)
    return {"cleared": True}
