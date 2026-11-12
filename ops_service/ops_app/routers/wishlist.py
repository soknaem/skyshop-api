from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import services
from ..db import get_session
from ..deps import User, current_user
from ..models import Product, Wishlist

router = APIRouter(prefix="/wishlist", tags=["wishlist"])


@router.get("")
async def list_wishlist(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(
        select(Product).join(Wishlist, Wishlist.product_id == Product.id)
        .where(Wishlist.user_id == user.id, Product.status == "ACTIVE").order_by(Wishlist.id.desc()))).all()
    cats = await services.category_map(session)
    return [services.product_out(p, cats) for p in rows]


@router.get("/ids")
async def wishlist_ids(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return list((await session.scalars(select(Wishlist.product_id).where(Wishlist.user_id == user.id))).all())


@router.post("/{product_id}", status_code=201)
async def add(product_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    if not await session.get(Product, product_id):
        raise HTTPException(404, "Product not found")
    exists = await session.scalar(select(Wishlist.id).where(Wishlist.user_id == user.id,
                                                            Wishlist.product_id == product_id))
    if not exists:
        session.add(Wishlist(user_id=user.id, product_id=product_id))
        await session.commit()
    return {"saved": True}


@router.delete("/{product_id}")
async def remove(product_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    await session.execute(delete(Wishlist).where(Wishlist.user_id == user.id, Wishlist.product_id == product_id))
    await session.commit()
    return {"saved": False}
