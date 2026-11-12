import asyncio
import hashlib
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import mailer, migrate, notify_client as notify, oauth, security, telegram_login
from .config import settings
from .db import Base, SessionLocal, engine, get_session
from .admin_users import router as admin_users_router
from .deps import Actor, actor
from .models import PasswordReset, RevokedToken, User
from .schemas import (ChangePasswordIn, ForgotIn, LoginIn, OAuthCallbackIn, ProfileIn, RefreshIn, RegisterIn, ResetIn,
                      TokenOut, UserOut)

OPEN_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}
log = logging.getLogger("auth")


@asynccontextmanager
async def lifespan(app: FastAPI):
    security.load_keys()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await migrate.run(engine)
    async with SessionLocal() as session:
        admin = await session.scalar(select(User).where(User.username == settings.admin_username))
        if not admin:
            session.add(User(username=settings.admin_username, email="admin@shop.local", name="Administrator",
                             password_hash=security.hash_password(settings.admin_password), role="ADMIN"))
        await session.execute(delete(RevokedToken).where(RevokedToken.expires_at < datetime.now(timezone.utc)))
        await session.commit()
    poller = None
    if oauth.enabled()["telegram"]:
        poller = asyncio.create_task(telegram_login.poller())
    yield
    if poller:
        poller.cancel()
    await engine.dispose()


app = FastAPI(title="Auth Service", version="3.0", lifespan=lifespan)
app.include_router(admin_users_router)


@app.middleware("http")
async def internal_key_guard(request: Request, call_next):
    """Only the gateway (which knows INTERNAL_KEY) may call this service."""
    if request.url.path not in OPEN_PATHS and request.headers.get("x-internal-key") != settings.internal_key:
        return JSONResponse({"detail": "Forbidden: call this service through the gateway"}, status_code=403)
    return await call_next(request)


def _user_out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, name=u.name or u.username, email=u.email, phone=u.phone,
                   avatar_url=u.avatar_url, provider=u.provider, role=u.role)


def _tokens(user: User) -> TokenOut:
    access, expires_in = security.make_access_token(user)
    return TokenOut(access_token=access, refresh_token=security.make_refresh_token(user),
                    expires_in=expires_in, user=_user_out(user))


@app.get("/health")
async def health():
    return {"service": "auth", "status": "ok"}


@app.get("/auth/public-key")
async def public_key():
    """The gateway and other services verify JWTs with this key only."""
    return {"algorithm": "RS256", "public_key": security.public_key_pem()}


@app.get("/auth/providers")
async def providers():
    return {**oauth.enabled(), "telegram_bot": settings.telegram_bot_username}


# ------------------------------------------------------------ email + password
@app.post("/auth/register", response_model=TokenOut, status_code=201)
async def register(body: RegisterIn, session: AsyncSession = Depends(get_session)):
    email = body.email.lower()
    if await session.scalar(select(User).where(or_(User.username == email, func.lower(User.email) == email),
                                               User.provider == "local")):
        raise HTTPException(409, "An account with this email already exists")
    user = User(username=email, email=email, name=body.name.strip(), phone=body.phone.strip(),
                password_hash=security.hash_password(body.password), role="CUSTOMER", is_active=True,
                last_login=datetime.now(timezone.utc))
    session.add(user)
    await session.commit()
    notify.fire(audience="admins", type="user.registered", title="New customer registered",
                body=f"{user.name} ({user.email})", url="/admin/users", telegram=True)
    return _tokens(user)


@app.post("/auth/login", response_model=TokenOut)
async def login(body: LoginIn, session: AsyncSession = Depends(get_session)):
    ident = body.username.strip().lower()
    user = await session.scalar(select(User).where(
        User.provider == "local", or_(func.lower(User.username) == ident, func.lower(User.email) == ident)))
    if not user or not user.is_active or not security.verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Wrong email or password")
    user.last_login = datetime.now(timezone.utc)
    await session.commit()
    if user.role == "ADMIN":
        notify.fire(audience="none", type="security.admin_login", title="Admin signed in", body=user.name or user.username,
                    url="/admin/dashboard", telegram=True, store=False)
    return _tokens(user)


# --------------------------------------------------------------- social login
async def _social_user(session: AsyncSession, provider: str, p: dict) -> User:
    user = await session.scalar(select(User).where(User.provider == provider, User.provider_id == p["provider_id"]))
    if not user and p.get("email") and p.get("email_verified"):
        # link to an existing normal CUSTOMER account that owns the same verified email
        user = await session.scalar(select(User).where(
            User.provider == "local", User.role == "CUSTOMER", func.lower(User.email) == p["email"].lower()))
    if not user:
        user = User(username=f"{provider}_{p['provider_id']}", email=p.get("email", ""),
                    name=p.get("name") or provider.title() + " user", avatar_url=p.get("avatar", ""),
                    provider=provider, provider_id=p["provider_id"], role="CUSTOMER", is_active=True,
                    password_hash=security.hash_password(secrets.token_urlsafe(24)))
        session.add(user)
    else:
        if p.get("avatar") and not user.avatar_url:
            user.avatar_url = p["avatar"]
        if not user.name and p.get("name"):
            user.name = p["name"]
    if user.is_active is False:
        raise HTTPException(403, "This account is disabled")
    user.last_login = datetime.now(timezone.utc)
    await session.commit()
    return user


@app.get("/auth/oauth/{provider}/url")
async def oauth_url(provider: str, redirect_uri: str, state: str):
    return {"url": oauth.authorize_url(provider, redirect_uri, state)}


@app.post("/auth/oauth/{provider}/callback", response_model=TokenOut)
async def oauth_callback(provider: str, body: OAuthCallbackIn, session: AsyncSession = Depends(get_session)):
    if provider not in ("google", "facebook"):
        raise HTTPException(404, "Unknown provider")
    profile = await oauth.fetch_profile(provider, body.code, body.redirect_uri)
    return _tokens(await _social_user(session, provider, profile))


@app.post("/auth/telegram/start")
async def telegram_start():
    if not oauth.enabled()["telegram"]:
        raise HTTPException(501, "Telegram login is not configured on the server")
    token, url = telegram_login.start_login()
    return {"token": token, "url": url}


@app.get("/auth/telegram/status/{token}")
async def telegram_status(token: str, session: AsyncSession = Depends(get_session)):
    state, profile = telegram_login.status(token)
    if state != "confirmed":
        return {"status": state}
    user = await _social_user(session, "telegram", profile)
    return {"status": "confirmed", "tokens": _tokens(user).model_dump()}


# ----------------------------------------------------------- tokens + profile
@app.post("/auth/refresh", response_model=TokenOut)
async def refresh(body: RefreshIn, session: AsyncSession = Depends(get_session)):
    """Rotation: the used refresh token is blacklisted and a new pair is issued."""
    try:
        data = security.decode_token(body.refresh_token, "refresh")
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired refresh token")
    if await session.get(RevokedToken, data["jti"]):
        raise HTTPException(401, "Refresh token already used")
    user = await session.get(User, int(data["sub"]))
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or disabled")
    if user.token_valid_after and data["iat"] < int(user.token_valid_after.timestamp()):
        raise HTTPException(401, "Your session ended. Please log in again.")  # password changed, role changed or disabled
    session.add(RevokedToken(jti=data["jti"], expires_at=datetime.fromtimestamp(data["exp"], timezone.utc)))
    await session.commit()
    return _tokens(user)


@app.post("/auth/logout")
async def logout(body: RefreshIn, session: AsyncSession = Depends(get_session)):
    try:
        data = security.decode_token(body.refresh_token, "refresh")
    except jwt.PyJWTError:
        return {"ok": True}
    if not await session.get(RevokedToken, data["jti"]):
        session.add(RevokedToken(jti=data["jti"], expires_at=datetime.fromtimestamp(data["exp"], timezone.utc)))
        await session.commit()
    return {"ok": True}


async def _me(x_user_id: str | None, session: AsyncSession) -> User:
    if not x_user_id:
        raise HTTPException(401, "Not authenticated")
    user = await session.get(User, int(x_user_id))
    if not user or not user.is_active:
        raise HTTPException(401, "User not found")
    return user


@app.get("/auth/me", response_model=UserOut)
async def me(x_user_id: str | None = Header(default=None), session: AsyncSession = Depends(get_session)):
    return _user_out(await _me(x_user_id, session))


@app.patch("/auth/me", response_model=UserOut)
async def update_me(body: ProfileIn, x_user_id: str | None = Header(default=None),
                    session: AsyncSession = Depends(get_session)):
    user = await _me(x_user_id, session)
    user.name, user.phone = body.name.strip(), body.phone.strip()
    if body.avatar_url is not None:
        if body.avatar_url and not body.avatar_url.startswith("/media/"):
            raise HTTPException(422, "Profile pictures must be uploaded to this shop")
        user.avatar_url = body.avatar_url
    await session.commit()
    return _user_out(user)


@app.post("/auth/change-password", response_model=TokenOut)
async def change_password(body: ChangePasswordIn, x_user_id: str | None = Header(default=None),
                          session: AsyncSession = Depends(get_session)):
    """Only for accounts that sign in with email + password. Other sessions are signed out; this one continues."""
    user = await _me(x_user_id, session)
    if user.provider != "local":
        raise HTTPException(409, f"You sign in with {user.provider.title()}, so there is no password to change")
    if not security.verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Your current password is not correct")
    if body.current_password == body.new_password:
        raise HTTPException(422, "Choose a password that is different from the current one")
    user.password_hash = security.hash_password(body.new_password)
    user.token_valid_after = datetime.now(timezone.utc)
    await session.commit()
    notify.fire(audience="user", user_id=user.id, type="security.password_changed", level="warning",
                title="Your password was changed", body="If this was not you, reset your password right away.", url="/settings")
    if user.role == "ADMIN":
        notify.fire(audience="none", type="security.admin_password", title="Admin password changed", body=user.name or user.username,
                    telegram=True, store=False)
    return _tokens(user)


# ---------------------------------------------------------- forgot / reset password
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@app.post("/auth/forgot-password")
async def forgot_password(body: ForgotIn, session: AsyncSession = Depends(get_session)):
    """Always answers the same, so nobody can find out which emails have an account."""
    email = body.email.lower()
    user = await session.scalar(select(User).where(User.provider == "local", User.is_active.is_(True),
                                                   func.lower(User.email) == email))
    result: dict = {"ok": True, "email_configured": mailer.configured()}
    if not user:
        return result
    old = (await session.scalars(select(PasswordReset).where(PasswordReset.user_id == user.id,
                                                             PasswordReset.used_at.is_(None)))).all()
    for r in old:
        r.used_at = datetime.now(timezone.utc)   # only the newest link works
    raw = secrets.token_urlsafe(32)
    session.add(PasswordReset(user_id=user.id, token_hash=_hash(raw),
                              expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.reset_ttl_min)))
    await session.commit()
    link = f"{settings.site_url}/reset-password?token={raw}"
    subject, text, html = mailer.reset_email(user.name or "there", link)
    sent = await mailer.send_email(user.email, subject, text, html)
    if not sent:
        log.warning("PASSWORD RESET LINK for %s (no SMTP configured): %s", user.email, link)
        if settings.dev_return_reset_link:
            result["dev_link"] = link
    if user.role == "ADMIN":
        notify.fire(audience="none", type="security.reset_requested", title="Admin password reset requested",
                    body=user.name or user.username, telegram=True, store=False)
    return result


@app.get("/auth/reset-password/check")
async def reset_check(token: str, session: AsyncSession = Depends(get_session)):
    r = await session.scalar(select(PasswordReset).where(PasswordReset.token_hash == _hash(token)))
    valid = bool(r and not r.used_at and r.expires_at > datetime.now(timezone.utc))
    return {"valid": valid}


@app.post("/auth/reset-password")
async def reset_password(body: ResetIn, session: AsyncSession = Depends(get_session)):
    r = await session.scalar(select(PasswordReset).where(PasswordReset.token_hash == _hash(body.token)))
    now = datetime.now(timezone.utc)
    if not r or r.used_at or r.expires_at <= now:
        raise HTTPException(400, "This reset link is invalid or has expired. Request a new one.")
    user = await session.get(User, r.user_id)
    if not user or not user.is_active or user.provider != "local":
        raise HTTPException(400, "This reset link is invalid or has expired. Request a new one.")
    user.password_hash = security.hash_password(body.password)
    user.token_valid_after = now
    r.used_at = now
    await session.commit()
    notify.fire(audience="user", user_id=user.id, type="security.password_changed", level="warning",
                title="Your password was reset", body="If this was not you, contact support.", url="/settings")
    if user.role == "ADMIN":
        notify.fire(audience="none", type="security.admin_password", title="Admin password was reset", body=user.name or user.username,
                    telegram=True, store=False)
    return {"ok": True}


# ----------------------------------------------- internal (other services only)
@app.get("/auth/internal/admins")
async def internal_admins(session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(User).where(User.role == "ADMIN", User.is_active.is_(True)))).all()
    return [{"id": u.id, "name": u.name} for u in rows]


@app.get("/auth/internal/users/{user_id}/contact")
async def internal_contact(user_id: int, session: AsyncSession = Depends(get_session)):
    u = await session.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    return {"name": u.name, "email": u.email, "telegram_chat_id": u.provider_id if u.provider == "telegram" else None}
