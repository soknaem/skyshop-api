from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import services
from ..config import settings
from ..db import get_session
from ..deps import User, current_user
from ..models import Order, Payment
from ..schemas import CheckoutIn, StatusIn

router = APIRouter(tags=["shop"])


@router.get("/config")
async def shop_config():
    return {"currency": settings.shop_currency, "mock_mode": settings.mock_mode,
            "payment_ttl_min": settings.payment_ttl_min, "shop_name": settings.shop_name,
            "shop_address": settings.shop_address, "shop_hours": settings.shop_hours,
            "delivery_fee": settings.delivery_fee}


@router.post("/checkout", status_code=201)
async def checkout(body: CheckoutIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    cart = await services.get_cart(session, user.id)
    order = await services.create_order(session, cart, user, body.model_dump())
    await session.commit()
    pay = await session.scalar(select(Payment).where(Payment.order_id == order.id))
    services.ensure_watcher(pay.id)
    return await services.order_view(session, order)


@router.get("/orders/{order_no}")
async def get_order(order_no: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return await services.order_view(session, await services.get_order_for(session, order_no, user))


@router.get("/my/orders")
async def my_orders(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return await services.my_orders(session, user.id)


async def _payment(session: AsyncSession, public_id: str, user: User) -> tuple[Payment, Order]:
    pay = await session.scalar(select(Payment).where(Payment.public_id == public_id))
    order = await session.get(Order, pay.order_id) if pay else None
    if not pay or not (user.is_admin or order.user_id == user.id):
        raise HTTPException(404, "Payment not found")
    return pay, order


@router.get("/payments/{public_id}/status")
async def payment_status(public_id: str, user: User = Depends(current_user),
                         session: AsyncSession = Depends(get_session)):
    """Fallback for clients without WebSocket: also checks KHQR once."""
    pay, order = await _payment(session, public_id, user)
    return {"status": await services.poll_once(session, pay), "order_no": order.order_no}


@router.post("/payments/{public_id}/regenerate")
async def regenerate(public_id: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    order = await services.regenerate_payment(session, public_id, user)
    return await services.order_view(session, order)


@router.post("/payments/{public_id}/mock-pay")
async def mock_pay(public_id: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """Demo mode only: pretend the customer paid."""
    if not settings.mock_mode:
        raise HTTPException(403, "Mock payment is disabled (MOCK_MODE=false)")
    pay, _ = await _payment(session, public_id, user)
    if pay.status == "PENDING":
        pay.raw_response = {**(pay.raw_response or {}), "mock_paid": True}
        await session.commit()
        await services.poll_once(session, pay)
    return {"status": pay.status}


@router.patch("/admin/orders/{order_no}/status")
async def admin_set_status(order_no: str, body: StatusIn, session: AsyncSession = Depends(get_session)):
    order = await services.set_fulfillment_status(session, order_no, body.status)
    return {"order_no": order.order_no, "fulfillment_status": order.fulfillment_status}
