import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from .. import services
from ..config import settings
from ..db import SessionLocal
from ..hub import payment_hub, user_hub
from ..models import Order, Payment

router = APIRouter()


@router.websocket("/ws/payments/{public_id}")
async def payment_ws(ws: WebSocket, public_id: str):
    """Pushes payment status to the browser (via the gateway) the moment it changes."""
    if ws.headers.get("x-internal-key") != settings.internal_key:
        await ws.close(code=1008)
        return
    async with SessionLocal() as s:
        pay = await s.scalar(select(Payment).where(Payment.public_id == public_id))
        if not pay:
            await ws.close(code=1008)
            return
        order = await s.get(Order, pay.order_id)
        status, order_no, pid = pay.status, order.order_no, pay.id
    await ws.accept()
    queue = payment_hub.subscribe(public_id)
    try:
        await ws.send_json({"type": "status", "status": status, "order_no": order_no})
        if status != "PENDING":
            return
        services.ensure_watcher(pid)
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=20)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping"})
                continue
            await ws.send_json(msg)
            if msg.get("status") in ("PAID", "EXPIRED", "SUPERSEDED"):
                return
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        payment_hub.unsubscribe(public_id, queue)
        try:
            await ws.close()
        except Exception:
            pass


@router.websocket("/ws/me")
async def my_updates_ws(ws: WebSocket):
    """Live updates for one customer (the gateway adds X-User-Id after verifying the JWT)."""
    uid = ws.headers.get("x-user-id", "")
    if ws.headers.get("x-internal-key") != settings.internal_key or not uid.isdigit():
        await ws.close(code=1008)
        return
    await ws.accept()
    queue = user_hub.subscribe(uid)
    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=20)
            except asyncio.TimeoutError:
                msg = {"type": "ping"}
            await ws.send_json(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        user_hub.unsubscribe(uid, queue)
        try:
            await ws.close()
        except Exception:
            pass
