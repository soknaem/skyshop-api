import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import migrate
from .config import settings
from .db import Base, engine, get_session
from .models import DailySales, OrderItemSnapshot, OrderSnapshot

log = logging.getLogger("report")
OPEN_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await migrate.run(engine)
    yield
    await engine.dispose()


app = FastAPI(title="Report Service", version="2.0", lifespan=lifespan)


@app.middleware("http")
async def internal_key_guard(request: Request, call_next):
    if request.url.path not in OPEN_PATHS and request.headers.get("x-internal-key") != settings.internal_key:
        return JSONResponse({"detail": "Forbidden: call this service through the gateway"}, status_code=403)
    return await call_next(request)


@app.get("/health")
async def health():
    return {"service": "report", "status": "ok"}


# ------------------------------------------------------------- live admin feed
class AdminHub:
    def __init__(self) -> None:
        self.sockets: set[WebSocket] = set()

    async def broadcast(self, message: dict) -> None:
        for ws in list(self.sockets):
            try:
                await ws.send_json(message)
            except Exception:
                self.sockets.discard(ws)


admin_hub = AdminHub()


@app.websocket("/ws/admin/orders")
async def admin_orders_ws(ws: WebSocket):
    """The gateway only forwards admins here. Sends a message for every new paid order."""
    if ws.headers.get("x-internal-key") != settings.internal_key:
        await ws.close(code=1008)
        return
    await ws.accept()
    admin_hub.sockets.add(ws)
    try:
        while True:
            await ws.receive_text()  # keeps the connection open; client may send "ping"
    except WebSocketDisconnect:
        pass
    finally:
        admin_hub.sockets.discard(ws)


# ------------------------------------------------------------ internal event
class ItemIn(BaseModel):
    product_id: int
    product_name: str
    image_url: str = ""
    unit_price: Decimal
    qty: int
    subtotal: Decimal


class OrderPaidEvent(BaseModel):
    order_id: int
    order_no: str
    user_id: int | None = None
    fulfillment: str = "DELIVERY"
    fulfillment_status: str = "PROCESSING"
    customer_name: str = "Customer"
    email: str = ""
    phone: str = ""
    shipping_address: str = ""
    subtotal: Decimal | None = None
    delivery_fee: Decimal | None = None
    total_amount: Decimal
    currency: str
    status: str = "PAID"
    created_at: datetime
    paid_at: datetime
    items: list[ItemIn]


def _list_row(o: OrderSnapshot) -> dict:
    return {"id": o.id, "order_no": o.order_no, "customer_name": o.customer_name or "Customer",
            "phone": o.phone or "", "fulfillment": o.fulfillment, "fulfillment_status": o.fulfillment_status,
            "total_amount": float(o.total_amount), "currency": o.currency,
            "order_date": o.paid_at.isoformat() if o.paid_at else None}


@app.post("/internal/events/order-paid", status_code=201)
async def order_paid(ev: OrderPaidEvent, session: AsyncSession = Depends(get_session)):
    """Idempotent: the same event delivered twice creates only one snapshot."""
    if await session.scalar(select(OrderSnapshot.id).where(OrderSnapshot.order_no == ev.order_no)):
        return {"status": "duplicate"}
    snap = OrderSnapshot(
        order_id=ev.order_id, order_no=ev.order_no, user_id=ev.user_id, fulfillment=ev.fulfillment,
        fulfillment_status=ev.fulfillment_status, customer_name=ev.customer_name, email=ev.email or None,
        phone=ev.phone or None, shipping_address=ev.shipping_address or None, subtotal=ev.subtotal,
        delivery_fee=ev.delivery_fee, total_amount=ev.total_amount, currency=ev.currency, status=ev.status,
        created_at=ev.created_at, paid_at=ev.paid_at)
    session.add(snap)
    await session.flush()
    for it in ev.items:
        session.add(OrderItemSnapshot(snapshot_id=snap.id, **it.model_dump()))
    day = ev.paid_at.astimezone(timezone.utc).date()
    row = await session.get(DailySales, day, with_for_update=True)
    if row:
        row.orders_count += 1
        row.revenue = Decimal(row.revenue) + ev.total_amount
    else:
        session.add(DailySales(sale_date=day, orders_count=1, revenue=ev.total_amount))
    try:
        await session.commit()
    except IntegrityError:  # two events arrived at the very same moment
        await session.rollback()
        return {"status": "duplicate"}
    await admin_hub.broadcast({"type": "order_paid", "order": _list_row(snap)})
    return {"status": "stored"}


class OrderUpdatedEvent(BaseModel):
    order_no: str
    fulfillment_status: str


@app.post("/internal/events/order-updated")
async def order_updated(ev: OrderUpdatedEvent, session: AsyncSession = Depends(get_session)):
    snap = await session.scalar(select(OrderSnapshot).where(OrderSnapshot.order_no == ev.order_no))
    if not snap:
        raise HTTPException(404, "Order not known yet")  # the sender retries until order.paid has arrived
    snap.fulfillment_status = ev.fulfillment_status
    await session.commit()
    await admin_hub.broadcast({"type": "order_updated", "order": _list_row(snap)})
    return {"status": "updated"}


# -------------------------------------------------------------- admin reports
@app.get("/orders")
async def list_orders(q: str = "", fulfillment: str = "", status: str = "", page: int = Query(1, ge=1),
                      page_size: int = Query(10, ge=1, le=100), session: AsyncSession = Depends(get_session)):
    stmt = select(OrderSnapshot)
    if fulfillment:
        stmt = stmt.where(OrderSnapshot.fulfillment == fulfillment)
    if status:
        stmt = stmt.where(OrderSnapshot.fulfillment_status == status)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(OrderSnapshot.order_no).like(like),
                              func.lower(OrderSnapshot.customer_name).like(like),
                              OrderSnapshot.phone.like(f"%{q}%")))
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await session.scalars(stmt.order_by(OrderSnapshot.paid_at.desc())
                                  .offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [_list_row(o) for o in rows], "total": total or 0, "page": page, "page_size": page_size}


@app.get("/orders/{snapshot_id}")
async def order_detail(snapshot_id: int, session: AsyncSession = Depends(get_session)):
    o = await session.get(OrderSnapshot, snapshot_id)
    if not o:
        raise HTTPException(404, "Order not found")
    items = (await session.scalars(select(OrderItemSnapshot)
                                   .where(OrderItemSnapshot.snapshot_id == o.id)
                                   .order_by(OrderItemSnapshot.id))).all()
    return {
        "id": o.id, "order_no": o.order_no, "status": o.status, "customer_name": o.customer_name or "Customer",
        "email": o.email or "", "phone": o.phone or "", "shipping_address": o.shipping_address or "",
        "fulfillment": o.fulfillment, "fulfillment_status": o.fulfillment_status,
        "subtotal": float(o.subtotal if o.subtotal is not None else o.total_amount),
        "delivery_fee": float(o.delivery_fee or 0),
        "total_amount": float(o.total_amount), "currency": o.currency,
        "order_date": o.paid_at.isoformat(), "created_at": o.created_at.isoformat(),
        "items": [{"product_id": i.product_id, "product_name": i.product_name, "image_url": i.image_url or "",
                   "unit_price": float(i.unit_price), "qty": i.qty, "subtotal": float(i.subtotal)}
                  for i in items],
    }


@app.get("/summary")
async def summary(session: AsyncSession = Depends(get_session)):
    total_orders, revenue = (await session.execute(
        select(func.count(OrderSnapshot.id), func.coalesce(func.sum(OrderSnapshot.total_amount), 0)))).one()
    today = datetime.now(timezone.utc).date()
    day = await session.get(DailySales, today)
    currency = await session.scalar(select(OrderSnapshot.currency).limit(1))
    revenue = float(revenue)
    split = dict((await session.execute(
        select(OrderSnapshot.fulfillment, func.count()).group_by(OrderSnapshot.fulfillment))).all())
    open_orders = await session.scalar(select(func.count()).where(OrderSnapshot.fulfillment_status != "COMPLETED"))
    return {"total_orders": total_orders, "total_revenue": revenue,
            "pickup_orders": split.get("PICKUP", 0), "delivery_orders": split.get("DELIVERY", 0),
            "open_orders": open_orders or 0,
            "today_orders": day.orders_count if day else 0,
            "today_revenue": float(day.revenue) if day else 0.0,
            "average_order": round(revenue / total_orders, 2) if total_orders else 0.0,
            "currency": currency}


@app.get("/sales-by-day")
async def sales_by_day(days: int = Query(14, ge=1, le=90), session: AsyncSession = Depends(get_session)):
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    rows = (await session.scalars(select(DailySales).where(DailySales.sale_date >= start))).all()
    by_date: dict[date, DailySales] = {r.sale_date: r for r in rows}
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        r = by_date.get(d)
        out.append({"date": d.isoformat(), "orders": r.orders_count if r else 0,
                    "revenue": float(r.revenue) if r else 0.0})
    return out


@app.get("/top-products")
async def top_products(limit: int = Query(5, ge=1, le=20), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(OrderItemSnapshot.product_name, func.sum(OrderItemSnapshot.qty).label("qty"),
               func.sum(OrderItemSnapshot.subtotal).label("revenue"))
        .group_by(OrderItemSnapshot.product_name)
        .order_by(func.sum(OrderItemSnapshot.qty).desc()).limit(limit))).all()
    return [{"product_name": r[0], "qty": int(r[1]), "revenue": float(r[2])} for r in rows]
