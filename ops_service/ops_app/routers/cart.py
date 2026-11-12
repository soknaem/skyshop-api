from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import services
from ..db import get_session
from ..deps import User, current_user
from ..models import CartItem, Product
from ..schemas import CartItemIn, CartItemPatch

router = APIRouter(prefix="/cart", tags=["cart"])


@router.get("")
async def get_cart(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return await services.cart_view(session, await services.get_cart(session, user.id))


async def _set_qty(session: AsyncSession, user: User, product_id: int, qty: int):
    product = await session.get(Product, product_id)
    if not product or product.status != "ACTIVE":
        raise HTTPException(404, "Product not found")
    cart = await services.get_cart(session, user.id, create=True)
    item = await session.scalar(select(CartItem).where(CartItem.cart_id == cart.id, CartItem.product_id == product_id))
    if qty <= 0:
        if item:
            await session.delete(item)
    else:
        if qty > product.stock_qty:
            raise HTTPException(409, f"Only {product.stock_qty} left of '{product.name}'")
        if item:
            item.qty = qty
        else:
            session.add(CartItem(cart_id=cart.id, product_id=product_id, qty=qty))
    await session.commit()
    return cart


@router.post("/items")
async def add_item(body: CartItemIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    cart = await services.get_cart(session, user.id)
    item = None
    if cart:
        item = await session.scalar(select(CartItem).where(CartItem.cart_id == cart.id,
                                                           CartItem.product_id == body.product_id))
    cart = await _set_qty(session, user, body.product_id, (item.qty if item else 0) + body.qty)
    return await services.cart_view(session, cart)


@router.patch("/items/{product_id}")
async def update_item(product_id: int, body: CartItemPatch, user: User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    cart = await _set_qty(session, user, product_id, body.qty)
    return await services.cart_view(session, cart)


@router.delete("/items/{product_id}")
async def remove_item(product_id: int, user: User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    cart = await services.get_cart(session, user.id)
    if cart:
        await session.execute(delete(CartItem).where(CartItem.cart_id == cart.id, CartItem.product_id == product_id))
        await session.commit()
    return await services.cart_view(session, cart)
