import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..ldap_auth import ldap_auth
from ..models import User, UserPref, utcnow
from ..schemas import LoginIn
from ..security import create_token, get_current_user, verify_password
from ..settings_store import get_all

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
async def login(data: LoginIn, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.username == data.username))).scalar_one_or_none()
    rt = await get_all(db)

    if user is not None and user.provider == "local":
        if verify_password(data.password, user.password_hash or ""):
            return {"token": create_token(user), "username": user.username,
                    "role": user.role, "display_name": user.display_name}
        raise HTTPException(401, "Неверный логин или пароль")

    # доменный пользователь: allow-list (пустой список = все доменные могут входить)
    allow = {x.strip().lower() for x in (rt.get("ldap_allow_list") or "").split(",") if x.strip()}
    if allow and data.username.strip().lower() not in allow:
        raise HTTPException(403, "Вход разрешён только для указанных пользователей")

    # доменный пользователь (уже есть запись) или первый вход доменного юзера
    ok, err, info = await asyncio.to_thread(ldap_auth, rt, data.username, data.password)
    if not ok:
        raise HTTPException(401, err or "Неверный логин или пароль")

    if user is None:
        user = User(username=data.username, password_hash="",
                    role=(rt.get("ldap_default_role") or "viewer"), provider="ldap",
                    display_name=(info or {}).get("display_name"),
                    created_at=utcnow())
        db.add(user)
    else:
        # имя из AD обновляем при каждом успешном входе
        dn = (info or {}).get("display_name")
        if dn and user.display_name != dn:
            user.display_name = dn
        # почта из AD: если у пользователя ещё не задана — сохраняем автоматически
        mail = (info or {}).get("mail")
        if mail:
            pref = (await db.execute(
                select(UserPref).where(UserPref.user_id == user.id, UserPref.key == "email")
            )).scalar_one_or_none()
            if pref is None:
                db.add(UserPref(user_id=user.id, key="email", value=str(mail)))
            elif not (pref.value or "").strip():
                pref.value = str(mail)
    await db.commit()
    await db.refresh(user)
    return {"token": create_token(user), "username": user.username,
            "role": user.role, "display_name": user.display_name}


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return {"username": user.username, "role": user.role,
            "provider": user.provider, "display_name": user.display_name}
