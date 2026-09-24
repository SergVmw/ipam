"""Личный профиль и настройки текущего пользователя (/api/me/prefs).

Доступно любому авторизованному (admin/operator/viewer) — в отличие от
глобальных «Настроек» (только админ). Храним в user_pref (key/value):
  * ui_layout — личный внешний вид (ipam = «новый» | phpipam = «классический»);
    пусто/не задано = используется глобальная настройка администратора;
  * side_mode — что показывать в левой колонке «классического» вида по умолчанию
    (vlan | subnets);
  * email — почта пользователя.

Если почта не задана, для доменных (LDAP) пользователей предлагаем её
«вытянуть из домена»: либо реальный атрибут mail из AD (если он был получен
при входе), либо username@<domain из ldap_base_dn / имени входа>.
"""
import re

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User, UserPref
from ..schemas import PrefsIn
from ..security import get_current_user
from ..settings_store import get_all

router = APIRouter(prefix="/api", tags=["profile"])

SIDE_DEFAULT = "vlan"   # по умолчанию слева — VLAN
LAYOUT_GLOBAL_DEFAULT = "ipam"
KEYS = ("side_mode", "email", "ui_layout")


def _domain_from_base(base_dn: str) -> str:
    """'DC=corp,DC=example,DC=com' -> 'corp.example.com'."""
    dcs = []
    for part in (base_dn or "").split(","):
        part = part.strip()
        m = re.match(r"(?i)^DC=(.+)$", part)
        if m:
            dcs.append(m.group(1).strip())
    return ".".join(dcs).lower() if dcs else ""


async def _email_suggestion(user: User, rt: dict) -> str | None:
    """Почта «из домена», если у пользователя её ещё нет."""
    if user.provider != "ldap":
        return None
    # 1) домен из ldap_base_dn (Настройки → домен AD/LDAP)
    domain = _domain_from_base(rt.get("ldap_base_dn") or "")
    # 2) иначе берём домен из имени входа вида user@domain
    if not domain and "@" in (user.username or ""):
        domain = (user.username or "").rsplit("@", 1)[1].lower()
    if not domain:
        return None
    if "@" in (user.username or ""):
        return (user.username or "").strip()  # уже похоже на почту
    return f"{user.username}@{domain}"


async def _load_prefs(db: AsyncSession, user: User) -> dict:
    rows = (await db.execute(select(UserPref).where(UserPref.user_id == user.id))).scalars().all()
    d: dict = {"side_mode": SIDE_DEFAULT, "email": "", "ui_layout": ""}  # ui_layout: "" = как у администратора
    for r in rows:
        if r.key in d:
            d[r.key] = r.value
    return d


async def _set_prefs(db: AsyncSession, user: User, updates: dict) -> None:
    if not updates:
        return
    existing = {r.key: r for r in (await db.execute(
        select(UserPref).where(UserPref.user_id == user.id))).scalars()}
    for k, v in updates.items():
        if k not in KEYS:
            continue
        val = str(v).strip() if isinstance(v, str) else str(v)
        if k == "email" and val == "null":
            val = ""
        if k == "ui_layout" and val not in ("ipam", "phpipam"):
            val = ""  # пусто/недопустимо = по настройке администратора
        if k in existing:
            existing[k].value = val
        else:
            db.add(UserPref(user_id=user.id, key=k, value=val))


@router.get("/me/prefs")
async def get_prefs(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rt = await get_all(db)
    p = await _load_prefs(db, user)
    global_layout = (rt.get("ui_layout") or LAYOUT_GLOBAL_DEFAULT)
    personal = p.get("ui_layout") or ""
    effective = personal or global_layout
    # если почта не задана — сразу предлагаем «из домена»
    suggested = None if p.get("email") else await _email_suggestion(user, rt)
    return {
        "username": user.username,
        "role": user.role,
        "provider": user.provider,
        "display_name": user.display_name,
        **p,
        "ui_layout": personal,          # личный выбор ("" = как у админа)
        "ui_layout_global": global_layout,
        "ui_layout_effective": effective,
        "email_suggested": suggested,
    }


@router.put("/me/prefs")
async def put_prefs(data: PrefsIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    updates = data.model_dump(exclude_none=True)
    if updates.get("email") is not None:
        updates["email"] = updates["email"].strip() or ""
    await _set_prefs(db, user, updates)
    await db.commit()
    return await get_prefs(user, db)
