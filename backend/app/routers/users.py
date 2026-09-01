from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User
from ..schemas import UserIn, UserUpdate
from ..security import hash_password, require_role
from ..service import audit

router = APIRouter(prefix="/api/users", tags=["users"])
ROLES = ("admin", "operator", "viewer")


async def _count_admins(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(User).where(User.role == "admin"))).scalar() or 0


def _user_dict(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role, "provider": u.provider,
            "display_name": u.display_name,
            "created_at": u.created_at.isoformat() if u.created_at else None}


@router.get("")
async def list_users(db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    rows = (await db.execute(select(User).order_by(User.username))).scalars().all()
    return [_user_dict(u) for u in rows]


@router.post("", status_code=201)
async def create_user(data: UserIn, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    if data.role not in ROLES:
        raise HTTPException(422, "role: admin | operator | viewer")
    exists = (await db.execute(select(User).where(User.username == data.username))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "Пользователь с таким именем уже существует")
    if data.provider == "ldap":
        u = User(username=data.username, password_hash="", role=data.role, provider="ldap")
    else:
        if not data.password:
            raise HTTPException(422, "Для локального пользователя нужен пароль")
        u = User(username=data.username, password_hash=hash_password(data.password), role=data.role, provider="local")
    db.add(u)
    audit(db, user, "user_create", data.username, {"role": data.role, "provider": data.provider})
    await db.commit()
    await db.refresh(u)
    return _user_dict(u)


@router.put("/{uid}")
async def update_user(uid: int, data: UserUpdate, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    u = await db.get(User, uid)
    if not u:
        raise HTTPException(404, "Пользователь не найден")
    changes: dict = {}
    if data.role is not None:
        if data.role not in ROLES:
            raise HTTPException(422, "role: admin | operator | viewer")
        if u.role == "admin" and data.role != "admin" and await _count_admins(db) <= 1:
            raise HTTPException(409, "Нельзя понизить последнего администратора")
        u.role = data.role
        changes["role"] = data.role
    if data.username is not None and data.username != u.username:
        if u.provider == "ldap":
            raise HTTPException(409, "Имя доменного пользователя менять нельзя (оно из домена)")
        if u.id == user.id:
            raise HTTPException(409, "Нельзя менять собственное имя входа")
        clash = (await db.execute(select(User).where(User.username == data.username, User.id != uid))).scalar_one_or_none()
        if clash:
            raise HTTPException(409, "Имя входа уже занято")
        u.username = data.username
        changes["username"] = data.username
    if data.password:
        if u.provider == "ldap":
            raise HTTPException(409, "Пароль доменного пользователя задаётся в домене")
        u.password_hash = hash_password(data.password)
        changes["password"] = "***"
    if changes:
        audit(db, user, "user_update", u.username, changes)
    await db.commit()
    return _user_dict(u)


@router.delete("/{uid}", status_code=204)
async def delete_user(uid: int, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    u = await db.get(User, uid)
    if not u:
        raise HTTPException(404, "Пользователь не найден")
    if u.id == user.id:
        raise HTTPException(409, "Нельзя удалить собственную учётную запись")
    if u.role == "admin" and await _count_admins(db) <= 1:
        raise HTTPException(409, "Нельзя удалить последнего администратора")
    audit(db, user, "user_delete", u.username, {"provider": u.provider})
    await db.delete(u)
    await db.commit()
