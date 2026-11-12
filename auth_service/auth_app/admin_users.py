"""Admin user management. The gateway only lets ADMIN tokens reach /auth/admin/*."""
import secrets
import string
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import notify_client as notify, security
from .db import get_session
from .deps import Actor, actor
from .models import User
from .schemas import AdminResetPassword, AdminUserCreate, AdminUserPatch

router = APIRouter(prefix="/auth/admin/users", tags=["admin-users"])


def out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "name": u.name or u.username, "email": u.email, "phone": u.phone,
            "avatar_url": u.avatar_url, "provider": u.provider, "role": u.role, "is_active": u.is_active,
            "last_login": u.last_login.isoformat() if u.last_login else None, "created_at": u.created_at.isoformat()}


def alert(by: Actor, title: str, body: str) -> None:
    notify.fire(audience="none", type="admin.users", title=title, body=f"{body}\nBy: {by.name or by.id}",
                url="/admin/users", telegram=True, store=False)


async def _active_admins(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(User).where(User.role == "ADMIN", User.is_active.is_(True))) or 0


async def _get(session: AsyncSession, user_id: int) -> User:
    u = await session.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    return u


@router.get("")
async def list_users(q: str = "", role: str = "", status: str = "", provider: str = "", page: int = Query(1, ge=1),
                     page_size: int = Query(10, ge=1, le=100), session: AsyncSession = Depends(get_session)):
    stmt = select(User)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(User.name).like(like), func.lower(User.email).like(like),
                              func.lower(User.username).like(like), User.phone.like(f"%{q}%")))
    if role in ("ADMIN", "CUSTOMER"):
        stmt = stmt.where(User.role == role)
    if status == "active":
        stmt = stmt.where(User.is_active.is_(True))
    elif status == "disabled":
        stmt = stmt.where(User.is_active.is_(False))
    if provider:
        stmt = stmt.where(User.provider == provider)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await session.scalars(stmt.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [out(u) for u in rows], "total": total or 0, "page": page, "page_size": page_size}


@router.get("/summary")
async def summary(session: AsyncSession = Depends(get_session)):
    week = datetime.now(timezone.utc) - timedelta(days=7)
    total = await session.scalar(select(func.count()).select_from(User)) or 0
    admins = await session.scalar(select(func.count()).select_from(User).where(User.role == "ADMIN")) or 0
    disabled = await session.scalar(select(func.count()).select_from(User).where(User.is_active.is_(False))) or 0
    new7 = await session.scalar(select(func.count()).select_from(User).where(User.created_at >= week)) or 0
    by = dict((await session.execute(select(User.provider, func.count()).group_by(User.provider))).all())
    return {"total": total, "admins": admins, "customers": total - admins, "disabled": disabled,
            "active": total - disabled, "new_7d": new7, "by_provider": by}


@router.get("/{user_id}")
async def get_user(user_id: int, session: AsyncSession = Depends(get_session)):
    return out(await _get(session, user_id))


@router.post("", status_code=201)
async def create_user(body: AdminUserCreate, by: Actor = Depends(actor), session: AsyncSession = Depends(get_session)):
    email = body.email.lower()
    if await session.scalar(select(User).where(User.provider == "local",
                                               or_(User.username == email, func.lower(User.email) == email))):
        raise HTTPException(409, "An account with this email already exists")
    u = User(username=email, email=email, name=body.name.strip(), phone=body.phone.strip(), role=body.role,
             password_hash=security.hash_password(body.password), is_active=True)
    session.add(u)
    await session.commit()
    alert(by, "User created", f"{u.name} ({u.email}) as {u.role}")
    return out(u)


@router.patch("/{user_id}")
async def update_user(user_id: int, body: AdminUserPatch, by: Actor = Depends(actor),
                      session: AsyncSession = Depends(get_session)):
    u = await _get(session, user_id)
    changes = []
    revoke = False
    if body.is_active is not None and body.is_active != u.is_active:
        if u.id == by.id:
            raise HTTPException(409, "You cannot disable your own account")
        if not body.is_active and u.role == "ADMIN" and await _active_admins(session) <= 1:
            raise HTTPException(409, "You cannot disable the last active admin")
        u.is_active, revoke = body.is_active, True
        changes.append("enabled" if body.is_active else "DISABLED")
    if body.role is not None and body.role != u.role:
        if u.id == by.id:
            raise HTTPException(409, "You cannot change your own role")
        if u.role == "ADMIN" and body.role != "ADMIN" and u.is_active and await _active_admins(session) <= 1:
            raise HTTPException(409, "You cannot remove the last admin")
        changes.append(f"role {u.role} to {body.role}")
        u.role, revoke = body.role, True
    if body.name is not None:
        u.name = body.name.strip()
    if body.phone is not None:
        u.phone = body.phone.strip()
    if revoke:
        u.token_valid_after = datetime.now(timezone.utc)   # old refresh tokens stop working
    await session.commit()
    if changes:
        alert(by, "User changed", f"{u.name} ({u.email or u.username}): {', '.join(changes)}")
    return out(u)


@router.post("/{user_id}/reset-password")
async def admin_reset_password(user_id: int, body: AdminResetPassword, by: Actor = Depends(actor),
                               session: AsyncSession = Depends(get_session)):
    u = await _get(session, user_id)
    if u.provider != "local":
        raise HTTPException(409, f"This user signs in with {u.provider.title()}; there is no password to reset")
    temp = body.password or "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    u.password_hash = security.hash_password(temp)
    u.token_valid_after = datetime.now(timezone.utc)
    await session.commit()
    alert(by, "Password reset by admin", f"{u.name} ({u.email})")
    notify.fire(audience="user", user_id=u.id, type="security.password_changed", level="warning", title="Your password was changed by an admin",
                body="If you did not expect this, contact support.", url="/settings")
    return {"ok": True, "temporary_password": None if body.password else temp}


@router.delete("/{user_id}", status_code=204)
async def delete_user(user_id: int, by: Actor = Depends(actor), session: AsyncSession = Depends(get_session)):
    u = await _get(session, user_id)
    if u.id == by.id:
        raise HTTPException(409, "You cannot delete your own account")
    if u.role == "ADMIN" and u.is_active and await _active_admins(session) <= 1:
        raise HTTPException(409, "You cannot delete the last admin")
    name = f"{u.name} ({u.email or u.username})"
    await session.delete(u)
    await session.commit()
    alert(by, "User deleted", name)
