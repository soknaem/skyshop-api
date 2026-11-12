"""Notification service.

* Other services post events to /internal/events (best effort, they never wait for the result).
* Each event can become in-app notifications (stored + pushed live over WebSocket) and/or a Telegram message
  (to the shop owner for important operations, or to a customer who logged in with Telegram).
"""
import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import telegram
from .config import settings
from .db import Base, SessionLocal, ensure_database, engine, get_session
from .models import Notification, Preference, utcnow

log = logging.getLogger("notify")
logging.basicConfig(level=logging.INFO)
OPEN_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}
KEY = {"X-Internal-Key": settings.internal_key}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_database()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="Notification Service", version="1.0", lifespan=lifespan)


@app.middleware("http")
async def internal_key_guard(request: Request, call_next):
    if request.url.path not in OPEN_PATHS and request.headers.get("x-internal-key") != settings.internal_key:
        return JSONResponse({"detail": "Forbidden: call this service through the gateway"}, status_code=403)
    return await call_next(request)


@app.get("/health")
async def health():
    return {"service": "notify", "status": "ok", "telegram": bool(settings.telegram_bot_token and settings.alert_chat_ids)}


# ----------------------------------------------------------------- live push
class Hub:
    def __init__(self) -> None:
        self.sockets: dict[int, set[WebSocket]] = {}

    async def push(self, user_id: int, data: dict) -> None:
        for ws in list(self.sockets.get(user_id, ())):
            try:
                await ws.send_text(json.dumps(data))
            except Exception:
                self.sockets[user_id].discard(ws)


hub = Hub()


def row(n: Notification) -> dict:
    return {"id": n.id, "type": n.type, "title": n.title, "body": n.body, "url": n.url, "level": n.level,
            "read": n.read_at is not None, "created_at": n.created_at.isoformat()}


async def unread_count(session: AsyncSession, user_id: int) -> int:
    return await session.scalar(select(func.count()).select_from(Notification)
                                .where(Notification.user_id == user_id, Notification.read_at.is_(None))) or 0


@app.websocket("/ws/notifications")
async def notifications_ws(ws: WebSocket):
    uid = ws.headers.get("x-user-id", "")
    if ws.headers.get("x-internal-key") != settings.internal_key or not uid.isdigit():
        await ws.close(code=1008)
        return
    user_id = int(uid)
    await ws.accept()
    hub.sockets.setdefault(user_id, set()).add(ws)
    try:
        async with SessionLocal() as s:
            await ws.send_text(json.dumps({"type": "hello", "unread": await unread_count(s, user_id)}))
        while True:
            await ws.receive_text()   # keep-alive; the client may send "ping"
    except WebSocketDisconnect:
        pass
    finally:
        hub.sockets.get(user_id, set()).discard(ws)


# ------------------------------------------------------------ contacts (auth)
_admin_cache: dict = {"at": 0.0, "ids": []}


async def admin_ids() -> list[int]:
    if time.time() - _admin_cache["at"] < 60 and _admin_cache["ids"]:
        return _admin_cache["ids"]
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(f"{settings.auth_url}/auth/internal/admins", headers=KEY)
            r.raise_for_status()
            _admin_cache.update(at=time.time(), ids=[a["id"] for a in r.json()])
    except Exception as e:
        log.warning("could not load admins from auth: %s", e)
    return _admin_cache["ids"]


async def contact(user_id: int) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(f"{settings.auth_url}/auth/internal/users/{user_id}/contact", headers=KEY)
            return r.json() if r.status_code == 200 else None
    except Exception:
        return None


# --------------------------------------------------------------- events in
class EventIn(BaseModel):
    audience: Literal["user", "admins", "none"] = "none"
    user_id: int | None = None
    type: str = Field(max_length=40)
    title: str = Field(max_length=160)
    body: str = ""
    url: str = Field(default="", max_length=300)
    level: Literal["info", "success", "warning"] = "info"
    store: bool = True            # keep as an in-app notification
    telegram: bool = False        # also send a Telegram message


async def _telegram_for(ev: EventIn, text: str) -> None:
    if ev.audience == "user" and ev.user_id:
        c = await contact(ev.user_id)
        if not c or not c.get("telegram_chat_id"):
            return
        async with SessionLocal() as s:
            pref = await s.get(Preference, ev.user_id)
        if pref is not None and not pref.telegram_enabled:
            return
        await telegram.send(c["telegram_chat_id"], text)
    else:
        await telegram.send_to_admins(text)


@app.post("/internal/events", status_code=202)
async def event(ev: EventIn):
    recipients: list[int] = []
    if ev.audience == "user" and ev.user_id:
        recipients = [ev.user_id]
    elif ev.audience == "admins":
        recipients = await admin_ids()

    if ev.store and recipients:
        async with SessionLocal() as s:
            rows = [Notification(user_id=u, type=ev.type, title=ev.title, body=ev.body, url=ev.url, level=ev.level)
                    for u in recipients]
            s.add_all(rows)
            await s.commit()
            for n in rows:
                await hub.push(n.user_id, {"type": "notification", "notification": row(n), "unread": await unread_count(s, n.user_id)})
    if ev.telegram:
        asyncio.create_task(_telegram_for(ev, telegram.format_message(ev.type, ev.title, ev.body, ev.url)))
    return {"stored": len(recipients) if ev.store else 0}


# ------------------------------------------------------------ user endpoints
def uid_of(x_user_id: str | None = Header(default=None)) -> int:
    if not x_user_id or not x_user_id.isdigit():
        raise HTTPException(401, "Please log in first")
    return int(x_user_id)


@app.get("/notifications")
async def list_notifications(limit: int = Query(30, ge=1, le=100), user_id: int = Depends(uid_of),
                             session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(Notification).where(Notification.user_id == user_id)
                                  .order_by(Notification.id.desc()).limit(limit))).all()
    return {"items": [row(n) for n in rows], "unread": await unread_count(session, user_id)}


@app.post("/notifications/read-all")
async def read_all(user_id: int = Depends(uid_of), session: AsyncSession = Depends(get_session)):
    await session.execute(update(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None))
                          .values(read_at=utcnow()))
    await session.commit()
    return {"unread": 0}


@app.post("/notifications/{notification_id}/read")
async def read_one(notification_id: int, user_id: int = Depends(uid_of), session: AsyncSession = Depends(get_session)):
    await session.execute(update(Notification).where(Notification.id == notification_id, Notification.user_id == user_id,
                                                     Notification.read_at.is_(None)).values(read_at=utcnow()))
    await session.commit()
    return {"unread": await unread_count(session, user_id)}


@app.delete("/notifications")
async def clear_read(user_id: int = Depends(uid_of), session: AsyncSession = Depends(get_session)):
    await session.execute(delete(Notification).where(Notification.user_id == user_id, Notification.read_at.is_not(None)))
    await session.commit()
    return {"unread": await unread_count(session, user_id)}


class PrefIn(BaseModel):
    telegram_enabled: bool


@app.get("/preferences")
async def get_preferences(user_id: int = Depends(uid_of), session: AsyncSession = Depends(get_session)):
    pref = await session.get(Preference, user_id)
    c = await contact(user_id)
    return {"telegram_enabled": pref.telegram_enabled if pref else True,
            "telegram_available": bool(c and c.get("telegram_chat_id")) and bool(settings.telegram_bot_token)}


@app.put("/preferences")
async def set_preferences(body: PrefIn, user_id: int = Depends(uid_of), session: AsyncSession = Depends(get_session)):
    pref = await session.get(Preference, user_id)
    if pref:
        pref.telegram_enabled = body.telegram_enabled
    else:
        session.add(Preference(user_id=user_id, telegram_enabled=body.telegram_enabled))
    await session.commit()
    return {"telegram_enabled": body.telegram_enabled}


# --------------------------------------------------------------------- admin
@app.get("/admin/telegram-status")
async def telegram_status():
    return {"bot_configured": bool(settings.telegram_bot_token), "bot_username": settings.telegram_bot_username,
            "alerts_enabled": settings.telegram_alerts, "chat_count": len(settings.alert_chat_ids)}


@app.post("/admin/telegram-test")
async def telegram_test(x_user_name: str = Header(default="")):
    if not settings.telegram_bot_token:
        raise HTTPException(400, "TELEGRAM_BOT_TOKEN is not set in .env")
    if not settings.alert_chat_ids:
        raise HTTPException(400, "TELEGRAM_ALERT_CHAT_IDS is empty. Send /id to your bot to get your chat id.")
    results = await telegram.send_to_admins(telegram.format_message(
        "test", "Telegram alerts are working", f"Test message from the {settings.shop_name} admin panel.", "/admin/settings"))
    if not any(r["ok"] for r in results):
        raise HTTPException(502, results[0]["error"] if results else "Alerts are switched off (TELEGRAM_ALERTS=false)")
    return {"sent": sum(1 for r in results if r["ok"]), "results": results}
