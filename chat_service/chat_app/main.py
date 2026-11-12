"""Live chat support. Customers (logged in or guests) talk to the shop admins in real time over WebSocket.

Customer socket : /ws/chat        (gateway adds X-User-Id / X-User-Name for logged-in users, ?guest=<id> for guests)
Admin socket    : /ws/admin/chat  (gateway only lets ADMIN tokens in)
"""
import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import notify_client as notify
from .config import settings
from .db import Base, SessionLocal, engine, ensure_database
from .models import Conversation, Message, utcnow

log = logging.getLogger("chat")
OPEN_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}
GUEST_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_TEXT = 1000
OFFLINE_NOTICE_GAP = timedelta(minutes=30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_database()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="Chat Service", version="1.0", lifespan=lifespan)


@app.middleware("http")
async def internal_key_guard(request: Request, call_next):
    if request.url.path not in OPEN_PATHS and request.headers.get("x-internal-key") != settings.internal_key:
        return JSONResponse({"detail": "Forbidden: call this service through the gateway"}, status_code=403)
    return await call_next(request)


@app.get("/health")
async def health():
    return {"service": "chat", "status": "ok", "admins_online": len(admins)}


# --------------------------------------------------------------- connections
customers: dict[int, set[WebSocket]] = {}   # conversation id -> sockets
admins: set[WebSocket] = set()


async def _send(ws: WebSocket, data: dict) -> bool:
    try:
        await ws.send_text(json.dumps(data))
        return True
    except Exception:
        return False


async def to_admins(data: dict) -> None:
    for ws in list(admins):
        if not await _send(ws, data):
            admins.discard(ws)


async def to_customer(conv_id: int, data: dict) -> None:
    for ws in list(customers.get(conv_id, ())):
        if not await _send(ws, data):
            customers[conv_id].discard(ws)


def conv_dict(c: Conversation) -> dict:
    return {"id": c.id, "customer_name": c.customer_name, "is_guest": c.user_id is None, "status": c.status,
            "unread": c.unread_admin, "last_message": c.last_message,
            "last_message_at": c.last_message_at.isoformat(), "online": bool(customers.get(c.id))}


def msg_dict(m: Message) -> dict:
    return {"id": m.id, "conversation_id": m.conversation_id, "sender": m.sender, "sender_name": m.sender_name,
            "text": m.text, "created_at": m.created_at.isoformat()}


async def add_message(session: AsyncSession, conv: Conversation, sender: str, name: str, text: str) -> Message:
    m = Message(conversation_id=conv.id, sender=sender, sender_name=name, text=text)
    session.add(m)
    if sender != "SYSTEM":  # automatic notices must not replace the preview in the admin inbox
        conv.last_message, conv.last_message_at = text[:200], utcnow()
    if sender == "CUSTOMER":
        conv.unread_admin += 1
    elif sender == "ADMIN":
        conv.unread_customer += 1
    await session.flush()
    return m


async def broadcast_online_status() -> None:
    online = bool(admins)
    for conv_id in list(customers):
        await to_customer(conv_id, {"type": "support_status", "online": online})


# ------------------------------------------------------------ customer socket
@app.websocket("/ws/chat")
async def customer_ws(ws: WebSocket, guest: str = ""):
    if ws.headers.get("x-internal-key") != settings.internal_key:
        await ws.close(code=1008)
        return
    uid = ws.headers.get("x-user-id", "")
    user_id = int(uid) if uid.isdigit() else None
    name = unquote(ws.headers.get("x-user-name", "")) or "Guest"
    if user_id is None and not GUEST_RE.match(guest):
        await ws.close(code=1008)
        return
    await ws.accept()

    async with SessionLocal() as s:
        q = select(Conversation).where(Conversation.user_id == user_id) if user_id else \
            select(Conversation).where(Conversation.guest_id == guest)
        conv = await s.scalar(q.order_by(Conversation.id.desc()).limit(1))
        fresh = conv is None
        if fresh:
            conv = Conversation(user_id=user_id, guest_id=None if user_id else guest, customer_name=name)
            s.add(conv)
            await s.flush()
            await add_message(s, conv, "SYSTEM", settings.shop_name,
                              f"Hi {name if user_id else 'there'}! Ask us anything about our products, orders or delivery.")
            conv.unread_customer = 0
        elif user_id and conv.customer_name != name:
            conv.customer_name = name
        conv.unread_customer = 0
        await s.commit()
        history = (await s.scalars(select(Message).where(Message.conversation_id == conv.id)
                                   .order_by(Message.id.desc()).limit(60))).all()
        conv_id = conv.id
        await _send(ws, {"type": "init", "conversation_id": conv_id, "support_online": bool(admins),
                         "messages": [msg_dict(m) for m in reversed(history)]})
        if fresh:
            await to_admins({"type": "conversation_updated", "conversation": conv_dict(conv)})

    customers.setdefault(conv_id, set()).add(ws)
    await to_admins({"type": "presence", "conversation_id": conv_id, "online": True})
    stamps: list[float] = []
    try:
        while True:
            try:
                data = json.loads(await ws.receive_text())
            except json.JSONDecodeError:
                continue
            kind = data.get("type")
            if kind == "typing":
                await to_admins({"type": "typing", "conversation_id": conv_id})
            elif kind == "read":
                async with SessionLocal() as s:
                    c = await s.get(Conversation, conv_id)
                    c.unread_customer = 0
                    await s.commit()
            elif kind == "message":
                text = str(data.get("text", "")).strip()[:MAX_TEXT]
                now = time.monotonic()
                stamps[:] = [t for t in stamps if now - t < 60]
                if not text:
                    continue
                if len(stamps) >= 20:
                    await _send(ws, {"type": "error", "message": "You are sending messages too fast."})
                    continue
                stamps.append(now)
                async with SessionLocal() as s:
                    c = await s.get(Conversation, conv_id)
                    if c.status == "CLOSED":
                        c.status = "OPEN"
                    m = await add_message(s, c, "CUSTOMER", c.customer_name, text)
                    notice = None
                    if not admins:
                        last_sys = await s.scalar(select(Message).where(
                            Message.conversation_id == conv_id, Message.sender == "SYSTEM",
                            Message.text.like("Our team is offline%")).order_by(Message.id.desc()).limit(1))
                        if not last_sys or datetime.now(timezone.utc) - last_sys.created_at.astimezone(timezone.utc) > OFFLINE_NOTICE_GAP:
                            notice = await add_message(s, c, "SYSTEM", settings.shop_name,
                                                       "Our team is offline right now. Leave your message and we will reply here as soon as we are back.")
                            c.unread_customer = 0
                            notify.fire(audience="none", type="chat.offline", title="New chat message (no admin online)",
                                        body=f"{c.customer_name}: {text[:140]}", url="/admin/chat", telegram=True, store=False)
                    await s.commit()
                    payload = msg_dict(m)
                    await to_customer(conv_id, {"type": "message", "message": payload})
                    await to_admins({"type": "message", "message": payload, "conversation": conv_dict(c)})
                    if notice:
                        await to_customer(conv_id, {"type": "message", "message": msg_dict(notice)})
    except WebSocketDisconnect:
        pass
    finally:
        customers.get(conv_id, set()).discard(ws)
        if not customers.get(conv_id):
            customers.pop(conv_id, None)
            await to_admins({"type": "presence", "conversation_id": conv_id, "online": False})


# ---------------------------------------------------------------- admin socket
@app.websocket("/ws/admin/chat")
async def admin_ws(ws: WebSocket):
    if ws.headers.get("x-internal-key") != settings.internal_key:
        await ws.close(code=1008)
        return
    admin_name = unquote(ws.headers.get("x-user-name", "")) or "Support"
    await ws.accept()
    admins.add(ws)
    await broadcast_online_status()
    async with SessionLocal() as s:
        rows = (await s.scalars(select(Conversation).order_by(Conversation.last_message_at.desc()).limit(100))).all()
        await _send(ws, {"type": "conversations", "items": [conv_dict(c) for c in rows]})
    try:
        while True:
            try:
                data = json.loads(await ws.receive_text())
            except json.JSONDecodeError:
                continue
            kind, conv_id = data.get("type"), data.get("conversation_id")
            if kind == "history" and isinstance(conv_id, int):
                async with SessionLocal() as s:
                    msgs = (await s.scalars(select(Message).where(Message.conversation_id == conv_id)
                                            .order_by(Message.id.desc()).limit(100))).all()
                    await _send(ws, {"type": "history", "conversation_id": conv_id,
                                     "messages": [msg_dict(m) for m in reversed(msgs)]})
            elif kind == "send" and isinstance(conv_id, int):
                text = str(data.get("text", "")).strip()[:MAX_TEXT]
                if not text:
                    continue
                async with SessionLocal() as s:
                    c = await s.get(Conversation, conv_id)
                    if not c:
                        continue
                    c.status, c.unread_admin = "OPEN", 0
                    m = await add_message(s, c, "ADMIN", admin_name, text)
                    await s.commit()
                    payload = {"type": "message", "message": msg_dict(m), "conversation": conv_dict(c)}
                    await to_customer(conv_id, {"type": "message", "message": msg_dict(m)})
                    await to_admins(payload)
            elif kind == "read" and isinstance(conv_id, int):
                async with SessionLocal() as s:
                    c = await s.get(Conversation, conv_id)
                    if c:
                        c.unread_admin = 0
                        await s.commit()
                        await to_admins({"type": "conversation_updated", "conversation": conv_dict(c)})
            elif kind == "close" and isinstance(conv_id, int):
                async with SessionLocal() as s:
                    c = await s.get(Conversation, conv_id)
                    if c:
                        c.status = "CLOSED"
                        await s.commit()
                        await to_admins({"type": "conversation_updated", "conversation": conv_dict(c)})
            elif kind == "typing" and isinstance(conv_id, int):
                await to_customer(conv_id, {"type": "typing"})
    except WebSocketDisconnect:
        pass
    finally:
        admins.discard(ws)
        await broadcast_online_status()
