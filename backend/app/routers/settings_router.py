import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import SettingsIn
from ..security import require_role
from ..service import audit
from ..settings_store import get_all, set_many
from ..scanner.mail import send_test_mail

log = logging.getLogger("ipam.settings")
router = APIRouter(prefix="/api/settings", tags=["settings"])

MASK = "****"


def _clean(d: dict) -> dict:
    d["smtp_password"] = MASK if d["smtp_password"] else ""
    d["ldap_bind_password"] = MASK if d["ldap_bind_password"] else ""
    try:
        links = json.loads(d.get("ui_links") or "[]")
        d["ui_links"] = links if isinstance(links, list) else []
    except Exception:
        d["ui_links"] = []
    return d


@router.get("")
async def read_settings(db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    return _clean(await get_all(db))


@router.put("")
async def update_settings(data: SettingsIn, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    updates = data.model_dump(exclude_none=True)
    if updates.get("smtp_password") in (MASK, ""):
        updates.pop("smtp_password", None)  # маскируемое/пустое = не менять
    if updates.get("ldap_bind_password") in (MASK, ""):
        updates.pop("ldap_bind_password", None)
    if "ui_links" in updates:
        updates["ui_links"] = json.dumps(updates["ui_links"], ensure_ascii=False)
    if not updates:
        return {"ok": True}
    await set_many(db, updates)
    audit(db, user, "settings_update", None, {k: ("***" if "password" in k else v) for k, v in updates.items()})
    await db.commit()
    return _clean(await get_all(db))


@router.post("/test-mail")
async def test_mail(db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    d = await get_all(db)
    ok, err = await send_test_mail(d)
    if not ok:
        raise HTTPException(400, err)
    audit(db, user, "settings_test_mail", None)
    await db.commit()
    return {"ok": True}
