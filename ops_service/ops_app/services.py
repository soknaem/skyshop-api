"""Business logic of the operation service: catalog helpers, orders, payments, stock, outbox."""
import asyncio
import json
import logging
import re
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import khqr_client
from . import notify_client as notify
from .config import settings
from .db import SessionLocal
from .deps import User
from .hub import payment_hub, user_hub
from .models import (Cart, CartItem, Category, EventOutbox, Order, OrderItem, Payment, Product, Wishlist,
                     utcnow)
from .seed_data import CATEGORIES, PRODUCTS, TEST_ITEM_KHR

log = logging.getLogger("ops")
WATCHERS: dict[int, asyncio.Task] = {}
MIN_AMOUNT = {"KHR": Decimal("100"), "USD": Decimal("0.01")}
STATUS_FLOW = {
    "PICKUP": ["PROCESSING", "READY", "COMPLETED"],
    "DELIVERY": ["PROCESSING", "OUT_FOR_DELIVERY", "COMPLETED"],
}


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def new_order_no() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "ORD" + datetime.now().strftime("%y%m%d") + "".join(secrets.choice(alphabet) for _ in range(6))


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "category"


# ------------------------------------------------------------------ products
def first_image(p: Product) -> str:
    imgs = p.images or []
    return imgs[0] if imgs else (p.image_url or "")


def product_out(p: Product, cats: dict[int, Category]) -> dict:
    cat = cats.get(p.category_id) if p.category_id else None
    price, old = float(p.price), float(p.compare_price) if p.compare_price else None
    return {
        "id": p.id, "name": p.name, "description": p.description, "price": price,
        "compare_price": old, "discount_percent": round((1 - price / old) * 100) if old and old > price else 0,
        "stock_qty": p.stock_qty, "brand": p.brand or "",
        "category": {"id": cat.id, "name": cat.name, "slug": cat.slug} if cat else None,
        "category_id": p.category_id, "warranty": p.warranty or "", "featured": bool(p.featured),
        "sold_count": p.sold_count or 0, "specs": p.specs or {},
        "images": p.images or ([p.image_url] if p.image_url else []), "image_url": first_image(p),
        "status": p.status, "in_stock": p.stock_qty > 0 and p.status == "ACTIVE",
        "created_at": aware(p.created_at).isoformat() if p.created_at else None,
    }


async def category_map(session: AsyncSession) -> dict[int, Category]:
    return {c.id: c for c in (await session.scalars(select(Category))).all()}


# ---------------------------------------------------------------------- cart
async def get_cart(session: AsyncSession, user_id: int, create: bool = False) -> Cart | None:
    cart = await session.scalar(select(Cart).where(Cart.user_id == user_id))
    if not cart and create:
        cart = Cart(user_id=user_id)
        session.add(cart)
        await session.flush()
    return cart


async def cart_view(session: AsyncSession, cart: Cart | None) -> dict:
    items, total, count = [], Decimal("0"), 0
    if cart:
        rows = (await session.execute(
            select(CartItem, Product).join(Product, Product.id == CartItem.product_id)
            .where(CartItem.cart_id == cart.id).order_by(CartItem.id))).all()
        for ci, p in rows:
            sub = Decimal(p.price) * ci.qty
            items.append({
                "product_id": p.id, "name": p.name, "brand": p.brand or "", "image_url": first_image(p),
                "price": float(p.price), "qty": ci.qty, "stock_qty": p.stock_qty, "subtotal": float(sub),
                "available": p.status == "ACTIVE" and p.stock_qty >= ci.qty,
            })
            total += sub
            count += ci.qty
    return {"items": items, "total": float(total), "count": count, "currency": settings.shop_currency}


# ------------------------------------------------------------- order views
def payment_view(p: Payment | None) -> dict | None:
    if p is None:
        return None
    return {
        "public_id": p.public_id, "status": p.status, "amount": float(p.amount),
        "currency": p.currency, "expires_at": aware(p.expires_at).isoformat(),
        "qr_image": p.qr_image if p.status == "PENDING" else None, "mock": settings.mock_mode,
    }


async def order_view(session: AsyncSession, order: Order) -> dict:
    items = (await session.scalars(
        select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.id))).all()
    pay = await session.scalar(
        select(Payment).where(Payment.order_id == order.id).order_by(Payment.id.desc()).limit(1))
    return {
        "id": order.id, "order_no": order.order_no, "status": order.status,
        "fulfillment": order.fulfillment, "fulfillment_status": order.fulfillment_status,
        "customer_name": order.customer_name, "email": order.email, "phone": order.phone,
        "shipping_address": order.shipping_address,
        "subtotal": float(order.subtotal if order.subtotal is not None else order.total_amount),
        "delivery_fee": float(order.delivery_fee or 0),
        "total_amount": float(order.total_amount), "currency": order.currency,
        "created_at": aware(order.created_at).isoformat(),
        "paid_at": aware(order.paid_at).isoformat() if order.paid_at else None,
        "items": [{"product_id": i.product_id, "product_name": i.product_name, "image_url": i.image_url or "",
                   "unit_price": float(i.unit_price), "qty": i.qty, "subtotal": float(i.subtotal)} for i in items],
        "payment": payment_view(pay),
        "shop": {"address": settings.shop_address, "hours": settings.shop_hours, "name": settings.shop_name},
    }


async def my_orders(session: AsyncSession, user_id: int) -> list[dict]:
    orders = (await session.scalars(
        select(Order).where(Order.user_id == user_id).order_by(Order.id.desc()).limit(100))).all()
    ids = [o.id for o in orders]
    items = (await session.scalars(select(OrderItem).where(OrderItem.order_id.in_(ids)))).all() if ids else []
    by: dict[int, list[OrderItem]] = {}
    for i in items:
        by.setdefault(i.order_id, []).append(i)
    return [{
        "order_no": o.order_no, "status": o.status, "fulfillment": o.fulfillment,
        "fulfillment_status": o.fulfillment_status, "total_amount": float(o.total_amount),
        "currency": o.currency, "created_at": aware(o.created_at).isoformat(),
        "item_count": sum(i.qty for i in by.get(o.id, [])),
        "preview": [{"name": i.product_name, "image_url": i.image_url or ""} for i in by.get(o.id, [])[:3]],
    } for o in orders]


async def get_order_for(session: AsyncSession, order_no: str, user: User) -> Order:
    order = await session.scalar(select(Order).where(Order.order_no == order_no))
    if not order or not (user.is_admin or order.user_id == user.id):
        raise HTTPException(404, "Order not found")  # same answer for "not yours": no information leak
    return order


# ------------------------------------------------------------------ payment
async def create_payment(session: AsyncSession, order: Order) -> Payment:
    attempt = await session.scalar(
        select(func.count()).select_from(Payment).where(Payment.order_id == order.id)) or 0
    bill = order.order_no if attempt == 0 else f"{order.order_no}{attempt}"
    try:
        data = await asyncio.to_thread(khqr_client.create, float(order.total_amount), bill)
    except Exception as e:  # bad token, no network, wrong account...
        log.exception("KHQR create failed")
        raise HTTPException(502, f"Could not create the KHQR code: {e}")
    pay = Payment(
        public_id=uuid.uuid4().hex, order_id=order.id, md5=data["md5"], tran_id=data["tran_id"],
        qr_string=data["qr"], qr_image=data["image"], amount=order.total_amount,
        currency=order.currency, status="PENDING",
        expires_at=utcnow() + timedelta(minutes=settings.payment_ttl_min),
        raw_response={"mock_paid": False} if settings.mock_mode else None,
    )
    session.add(pay)
    await session.flush()
    return pay


async def _check_stock(session: AsyncSession, lines: list[tuple[int, int]]) -> None:
    for pid, qty in lines:
        p = await session.get(Product, pid)
        if not p or p.status != "ACTIVE":
            raise HTTPException(409, f"'{p.name if p else 'A product'}' is no longer available")
        if p.stock_qty < qty:
            raise HTTPException(409, f"Only {p.stock_qty} left of '{p.name}'")


async def create_order(session: AsyncSession, cart: Cart | None, user: User, body: dict) -> Order:
    rows = (await session.execute(
        select(CartItem, Product).join(Product, Product.id == CartItem.product_id)
        .where(CartItem.cart_id == (cart.id if cart else -1)).order_by(CartItem.id))).all()
    if not rows:
        raise HTTPException(400, "Your cart is empty")
    fulfillment = body["fulfillment"]
    if fulfillment == "DELIVERY":
        if len(body.get("phone") or "") < 6:
            raise HTTPException(422, "A phone number is required for delivery")
        if len(body.get("shipping_address") or "") < 5:
            raise HTTPException(422, "A delivery address is required")
    await _check_stock(session, [(ci.product_id, ci.qty) for ci, _ in rows])
    subtotal = sum((Decimal(p.price) * ci.qty for ci, p in rows), Decimal("0"))
    fee = Decimal(str(settings.delivery_fee)) if fulfillment == "DELIVERY" else Decimal("0")
    total = subtotal + fee
    if total < MIN_AMOUNT.get(settings.shop_currency, Decimal("0.01")):
        raise HTTPException(400, f"Minimum payment is {MIN_AMOUNT[settings.shop_currency]} {settings.shop_currency}")
    order = Order(
        order_no=new_order_no(), user_id=user.id, cart_id=cart.id, fulfillment=fulfillment,
        fulfillment_status="PENDING",
        customer_name=body.get("full_name") or user.name or None, email=body.get("email") or user.email or None,
        phone=body.get("phone"), shipping_address=body.get("shipping_address") if fulfillment == "DELIVERY" else None,
        subtotal=subtotal, delivery_fee=fee, total_amount=total, currency=settings.shop_currency, status="PENDING",
    )
    session.add(order)
    await session.flush()
    for ci, p in rows:  # price snapshot
        session.add(OrderItem(order_id=order.id, product_id=p.id, product_name=p.name, image_url=first_image(p),
                              unit_price=p.price, qty=ci.qty, subtotal=Decimal(p.price) * ci.qty))
    await create_payment(session, order)
    return order


async def regenerate_payment(session: AsyncSession, public_id: str, user: User) -> Order:
    old = await session.scalar(select(Payment).where(Payment.public_id == public_id))
    if not old:
        raise HTTPException(404, "Payment not found")
    order = await session.scalar(select(Order).where(Order.id == old.order_id).with_for_update())
    if not (user.is_admin or order.user_id == user.id):
        raise HTTPException(404, "Payment not found")
    if order.status == "PAID":
        raise HTTPException(409, "This order is already paid")
    items = (await session.scalars(select(OrderItem).where(OrderItem.order_id == order.id))).all()
    await _check_stock(session, [(i.product_id, i.qty) for i in items])
    if old.status == "PENDING":
        old.status = "SUPERSEDED"
    order.status = "PENDING"
    await session.flush()
    new = await create_payment(session, order)
    await session.commit()
    payment_hub.publish(old.public_id, {"type": "status", "status": "SUPERSEDED", "order_no": order.order_no})
    ensure_watcher(new.id)
    return order


# --------------------------------------------------------------- mark paid
def _json_safe(obj):
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return None


def order_event_payload(order: Order, items: list[OrderItem], now: datetime) -> dict:
    return {
        "order_id": order.id, "order_no": order.order_no, "user_id": order.user_id,
        "fulfillment": order.fulfillment, "fulfillment_status": order.fulfillment_status,
        "customer_name": order.customer_name or "Customer", "email": order.email or "", "phone": order.phone or "",
        "shipping_address": order.shipping_address or "",
        "subtotal": str(order.subtotal if order.subtotal is not None else order.total_amount),
        "delivery_fee": str(order.delivery_fee or 0), "total_amount": str(order.total_amount),
        "currency": order.currency, "status": "PAID", "created_at": aware(order.created_at).isoformat(),
        "paid_at": now.isoformat(),
        "items": [{"product_id": i.product_id, "product_name": i.product_name, "image_url": i.image_url or "",
                   "unit_price": str(i.unit_price), "qty": i.qty, "subtotal": str(i.subtotal)} for i in items],
    }


async def mark_paid(session: AsyncSession, payment: Payment, details) -> bool:
    """Returns True when this call is the one that marked the order paid.
    Runs in ONE transaction: order PAID, stock deducted, popularity counted, cart cleared, event queued."""
    order = await session.scalar(select(Order).where(Order.id == payment.order_id).with_for_update())
    now = utcnow()
    payment.status = "PAID"
    payment.paid_at = now
    payment.raw_response = {**(payment.raw_response or {}), "details": _json_safe(details)}
    if order.status == "PAID":  # a second QR of the same order was paid: only record it
        payment.raw_response = {**payment.raw_response, "duplicate_payment": True}
        return False
    order.status = "PAID"
    order.fulfillment_status = "PROCESSING"
    order.paid_at = now
    items = (await session.scalars(select(OrderItem).where(OrderItem.order_id == order.id))).all()
    for it in items:
        p = await session.scalar(select(Product).where(Product.id == it.product_id).with_for_update())
        if p:
            p.stock_qty = max(0, p.stock_qty - it.qty)
            p.sold_count = (p.sold_count or 0) + it.qty
    if order.cart_id:
        await session.execute(delete(CartItem).where(CartItem.cart_id == order.cart_id))
    session.add(EventOutbox(event_type="order.paid", payload=order_event_payload(order, list(items), now)))
    return True


def _money(amount, currency: str) -> str:
    return f"{float(amount):,.0f} {currency}" if currency == "KHR" else f"{currency} {float(amount):,.2f}"


async def announce_paid(session: AsyncSession, payment: Payment) -> None:
    """In-app notifications + Telegram for a newly paid order (best effort, runs after the commit)."""
    order = await session.get(Order, payment.order_id)
    items = (await session.scalars(select(OrderItem).where(OrderItem.order_id == order.id))).all()
    kind = "Pickup" if order.fulfillment == "PICKUP" else "Delivery"
    total = _money(order.total_amount, order.currency)
    notify.fire(audience="user", user_id=order.user_id, type="order.paid", level="success", title="Payment received",
                body=f"Order {order.order_no} · {total}", url=f"/order/{order.order_no}", telegram=True)
    notify.fire(audience="admins", type="order.paid", title="New paid order",
                body=f"{order.order_no} · {total} · {kind} · {sum(i.qty for i in items)} item(s)",
                url=f"/admin/orders?q={order.order_no}", telegram=True)
    for it in items:
        p = await session.get(Product, it.product_id)
        if p and p.stock_qty <= 5 and p.stock_qty + it.qty > 5:   # just crossed the low-stock line
            notify.fire(audience="admins", type="stock.low", level="warning",
                        title="Out of stock" if p.stock_qty == 0 else "Low stock",
                        body=f"{p.name}: {p.stock_qty} left", url=f"/admin/products/{p.id}/edit", telegram=True)


STATUS_TEXT = {"PROCESSING": "We are preparing your order", "READY": "Your order is ready for pickup",
               "OUT_FOR_DELIVERY": "Your order is on the way", "COMPLETED": "Your order is completed"}


async def set_fulfillment_status(session: AsyncSession, order_no: str, status: str) -> Order:
    order = await session.scalar(select(Order).where(Order.order_no == order_no).with_for_update())
    if not order:
        raise HTTPException(404, "Order not found")
    if order.status != "PAID":
        raise HTTPException(409, "Only paid orders can change status")
    if status not in STATUS_FLOW[order.fulfillment]:
        raise HTTPException(422, f"'{status}' is not a valid step for a {order.fulfillment.lower()} order")
    order.fulfillment_status = status
    session.add(EventOutbox(event_type="order.updated",
                            payload={"order_no": order.order_no, "fulfillment_status": status}))
    await session.commit()
    if order.user_id:  # tell the customer right away (their browser may be in another tab)
        user_hub.publish(str(order.user_id), {"type": "order_updated", "order_no": order.order_no,
                                              "fulfillment": order.fulfillment, "fulfillment_status": status})
    notify.fire(audience="user", user_id=order.user_id, type="order.status", level="success",
                title=STATUS_TEXT.get(status, "Order updated"), body=f"Order {order.order_no}",
                url=f"/order/{order.order_no}", telegram=True)
    return order


async def poll_once(session: AsyncSession, pay: Payment) -> str:
    """Check one payment against KHQR and update the database. Returns the payment status."""
    if pay.status != "PENDING":
        return pay.status
    order = await session.get(Order, pay.order_id)
    msg = {"type": "status", "order_no": order.order_no}
    if utcnow() >= aware(pay.expires_at):
        return await _expire(session, pay, order)
    paid_flag = bool((pay.raw_response or {}).get("mock_paid"))
    try:
        st = await asyncio.to_thread(khqr_client.check, pay.md5, aware(pay.created_at).timestamp(), paid_flag)
    except Exception as e:
        log.warning("KHQR check failed: %s", e)
        return "PENDING"
    if st == "PAID":
        details = await asyncio.to_thread(khqr_client.details, pay.md5)
        newly = await mark_paid(session, pay, details)
        await session.commit()
        payment_hub.publish(pay.public_id, {**msg, "status": "PAID"})
        if newly:
            await announce_paid(session, pay)
        return "PAID"
    if st == "EXPIRED":
        return await _expire(session, pay, order)
    if st == "SCANNED":
        payment_hub.publish(pay.public_id, {**msg, "status": "SCANNED"})
    return "PENDING"


async def _expire(session: AsyncSession, pay: Payment, order: Order) -> str:
    pay.status = "EXPIRED"
    if order.status == "PENDING":
        order.status = "EXPIRED"
    await session.commit()
    payment_hub.publish(pay.public_id, {"type": "status", "status": "EXPIRED", "order_no": order.order_no})
    return "EXPIRED"


async def watch_payment(payment_id: int) -> None:
    try:
        while True:
            async with SessionLocal() as session:
                pay = await session.get(Payment, payment_id)
                if not pay or pay.status != "PENDING":
                    return
                if await poll_once(session, pay) != "PENDING":
                    return
            await asyncio.sleep(3)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("payment watcher crashed")
    finally:
        WATCHERS.pop(payment_id, None)


def ensure_watcher(payment_id: int) -> None:
    t = WATCHERS.get(payment_id)
    if t is None or t.done():
        WATCHERS[payment_id] = asyncio.create_task(watch_payment(payment_id))


async def resume_watchers() -> None:
    async with SessionLocal() as session:
        ids = (await session.scalars(select(Payment.id).where(Payment.status == "PENDING"))).all()
    for pid in ids:
        ensure_watcher(pid)


# ------------------------------------------------------------------ outbox
EVENT_PATHS = {"order.paid": "/internal/events/order-paid", "order.updated": "/internal/events/order-updated"}


async def outbox_dispatcher() -> None:
    """Delivers queued events to the report service; retries when it is down."""
    async with httpx.AsyncClient(timeout=5) as client:
        while True:
            try:
                async with SessionLocal() as s:
                    events = (await s.scalars(select(EventOutbox).where(EventOutbox.status == "PENDING")
                                              .order_by(EventOutbox.id).limit(20))).all()
                    for ev in events:
                        try:
                            r = await client.post(f"{settings.report_url}{EVENT_PATHS[ev.event_type]}",
                                                  json=ev.payload,
                                                  headers={"X-Internal-Key": settings.internal_key})
                            r.raise_for_status()
                            ev.status, ev.sent_at = "SENT", utcnow()
                        except Exception as e:
                            ev.attempts += 1
                            ev.last_error = str(e)[:500]
                            if ev.attempts >= 30:
                                ev.status = "FAILED"
                            else:
                                break  # keep the order; retry on the next round
                    await s.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("outbox dispatcher error")
            await asyncio.sleep(2)


# -------------------------------------------------------------------- seed
def _scaled(usd: float) -> Decimal:
    """USD -> shop currency. KHR is rounded to the nearest 100 (4000 KHR per USD)."""
    if settings.shop_currency == "USD":
        return Decimal(str(usd)).quantize(Decimal("0.01"))
    return Decimal(int(round(usd * 4000 / 100.0)) * 100)


async def seed(session: AsyncSession) -> None:
    if not await session.scalar(select(func.count()).select_from(Category)):
        for i, (slug, name, icon) in enumerate(CATEGORIES):
            session.add(Category(slug=slug, name=name, icon=icon, sort_order=i))
        await session.commit()
    if await session.scalar(select(func.count()).select_from(Product)):
        return
    cats = {c.slug: c.id for c in (await session.scalars(select(Category))).all()}
    for n, (slug, name, brand, cat, usd, old, stock, _kind, _hue, featured, sold, warranty, desc, specs) in enumerate(PRODUCTS):
        test_item = slug.endswith("-test")
        price = (Decimal(TEST_ITEM_KHR) if settings.shop_currency == "KHR" else Decimal("0.03")) if test_item else _scaled(usd)
        session.add(Product(
            name=name, brand=brand, category_id=cats.get(cat), description=desc, price=price,
            compare_price=_scaled(old) if old and not test_item else None, stock_qty=stock, warranty=warranty,
            featured=featured, sold_count=sold, specs=specs, images=[f"/products/{slug}.svg"],
            image_url=f"/products/{slug}.svg", status="ACTIVE",
            created_at=utcnow() - timedelta(days=n * 3)))
    await session.commit()
