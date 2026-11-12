from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import notify_client as notify, services
from ..db import get_session
from ..models import CartItem, Category, Product, Wishlist
from ..schemas import CategoryIn, ProductIn

public = APIRouter(tags=["catalog"])
admin = APIRouter(prefix="/admin", tags=["admin-catalog"])

SORTS = {
    "popular": lambda: [Product.featured.desc(), Product.sold_count.desc(), Product.id.desc()],
    "newest": lambda: [Product.created_at.desc(), Product.id.desc()],
    "price_asc": lambda: [Product.price.asc(), Product.id.asc()],
    "price_desc": lambda: [Product.price.desc(), Product.id.asc()],
    "name": lambda: [func.lower(Product.name).asc()],
    "discount": lambda: [case((Product.compare_price > Product.price,
                               (Product.compare_price - Product.price) / Product.compare_price), else_=0).desc(),
                         Product.id.desc()],
}


def _filters(stmt, *, q="", category="", brand="", min_price=None, max_price=None, in_stock=False,
             on_sale=False, featured=False):
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Product.name).like(like), func.lower(Product.brand).like(like),
                              func.lower(Product.description).like(like)))
    if category:
        stmt = stmt.where(Product.category_id.in_(select(Category.id).where(Category.slug == category)))
    if brand:
        stmt = stmt.where(Product.brand.in_([b.strip() for b in brand.split(",") if b.strip()]))
    if min_price is not None:
        stmt = stmt.where(Product.price >= min_price)
    if max_price is not None:
        stmt = stmt.where(Product.price <= max_price)
    if in_stock:
        stmt = stmt.where(Product.stock_qty > 0)
    if on_sale:
        stmt = stmt.where(Product.compare_price > Product.price)
    if featured:
        stmt = stmt.where(Product.featured.is_(True))
    return stmt


async def _page(session: AsyncSession, stmt, order_by, page: int, page_size: int) -> dict:
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await session.scalars(stmt.order_by(*order_by).offset((page - 1) * page_size).limit(page_size))).all()
    cats = await services.category_map(session)
    return {"items": [services.product_out(p, cats) for p in rows], "total": total or 0,
            "page": page, "page_size": page_size}


# ------------------------------------------------------------------ public
@public.get("/categories")
async def list_categories(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(Category, func.count(Product.id))
        .outerjoin(Product, (Product.category_id == Category.id) & (Product.status == "ACTIVE"))
        .group_by(Category.id).order_by(Category.sort_order, Category.id))).all()
    return [{"id": c.id, "name": c.name, "slug": c.slug, "icon": c.icon, "sort_order": c.sort_order,
             "product_count": n} for c, n in rows]


@public.get("/products")
async def list_products(q: str = "", category: str = "", brand: str = "", min_price: float | None = None,
                        max_price: float | None = None, in_stock: bool = False, on_sale: bool = False,
                        featured: bool = False, sort: str = "popular", page: int = Query(1, ge=1),
                        page_size: int = Query(12, ge=1, le=60), session: AsyncSession = Depends(get_session)):
    stmt = _filters(select(Product).where(Product.status == "ACTIVE"), q=q, category=category, brand=brand,
                    min_price=min_price, max_price=max_price, in_stock=in_stock, on_sale=on_sale, featured=featured)
    return await _page(session, stmt, SORTS.get(sort, SORTS["popular"])(), page, page_size)


@public.get("/products/facets")
async def facets(q: str = "", category: str = "", session: AsyncSession = Depends(get_session)):
    """Brands (with counts) and price range for the filter panel."""
    base = _filters(select(Product).where(Product.status == "ACTIVE"), q=q, category=category).subquery()
    brands = (await session.execute(
        select(base.c.brand, func.count()).where(base.c.brand != "").group_by(base.c.brand)
        .order_by(func.count().desc(), base.c.brand))).all()
    lo, hi = (await session.execute(select(func.min(base.c.price), func.max(base.c.price)))).one()
    return {"brands": [{"name": b, "count": n} for b, n in brands],
            "price_min": float(lo or 0), "price_max": float(hi or 0)}


@public.get("/products/{product_id}")
async def get_product(product_id: int, session: AsyncSession = Depends(get_session)):
    p = await session.get(Product, product_id)
    if not p or p.status != "ACTIVE":
        raise HTTPException(404, "Product not found")
    return services.product_out(p, await services.category_map(session))


@public.get("/products/{product_id}/related")
async def related(product_id: int, session: AsyncSession = Depends(get_session)):
    p = await session.get(Product, product_id)
    if not p:
        raise HTTPException(404, "Product not found")
    stmt = select(Product).where(Product.status == "ACTIVE", Product.id != p.id)
    if p.category_id:
        stmt = stmt.where(Product.category_id == p.category_id)
    rows = (await session.scalars(stmt.order_by(Product.sold_count.desc()).limit(4))).all()
    cats = await services.category_map(session)
    return [services.product_out(r, cats) for r in rows]


# ------------------------------------------------------------------- admin
@admin.get("/products")
async def admin_products(q: str = "", category: str = "", page: int = Query(1, ge=1),
                         page_size: int = Query(10, ge=1, le=100), session: AsyncSession = Depends(get_session)):
    stmt = _filters(select(Product), q=q, category=category)
    return await _page(session, stmt, [Product.id.desc()], page, page_size)


def _alert(title: str, body: str, by: str, url: str = "/admin/products") -> None:
    notify.fire(audience="none", type="admin.catalog", title=title, body=f"{body}\nBy: {unquote(by) or 'admin'}",
                url=url, telegram=True, store=False)


@admin.post("/products", status_code=201)
async def admin_create(body: ProductIn, x_user_name: str = Header(default=""), session: AsyncSession = Depends(get_session)):
    data = body.model_dump()
    data["image_url"] = (data["images"] or [""])[0]
    p = Product(**data)
    session.add(p)
    await session.commit()
    _alert("Product created", f"{p.name} · stock {p.stock_qty}", x_user_name, f"/admin/products/{p.id}/edit")
    return services.product_out(p, await services.category_map(session))


@admin.get("/products/{product_id}")
async def admin_get(product_id: int, session: AsyncSession = Depends(get_session)):
    p = await session.get(Product, product_id)
    if not p:
        raise HTTPException(404, "Product not found")
    return services.product_out(p, await services.category_map(session))


@admin.put("/products/{product_id}")
async def admin_update(product_id: int, body: ProductIn, session: AsyncSession = Depends(get_session)):
    p = await session.get(Product, product_id)
    if not p:
        raise HTTPException(404, "Product not found")
    data = body.model_dump()
    data["image_url"] = (data["images"] or [""])[0]
    for k, v in data.items():
        setattr(p, k, v)
    await session.commit()
    return services.product_out(p, await services.category_map(session))


@admin.delete("/products/{product_id}", status_code=204)
async def admin_delete(product_id: int, x_user_name: str = Header(default=""), session: AsyncSession = Depends(get_session)):
    p = await session.get(Product, product_id)
    if not p:
        raise HTTPException(404, "Product not found")
    name = p.name
    await session.execute(delete(CartItem).where(CartItem.product_id == product_id))
    await session.execute(delete(Wishlist).where(Wishlist.product_id == product_id))
    await session.delete(p)  # old orders keep their own name/price snapshot
    await session.commit()
    _alert("Product deleted", name, x_user_name)


async def _unique_slug(session: AsyncSession, base: str, skip_id: int | None = None) -> str:
    slug, n = base, 2
    while True:
        existing = await session.scalar(select(Category.id).where(Category.slug == slug))
        if existing is None or existing == skip_id:
            return slug
        slug, n = f"{base}-{n}", n + 1


@admin.get("/categories")
async def admin_categories(session: AsyncSession = Depends(get_session)):
    return await list_categories(session)


@admin.post("/categories", status_code=201)
async def admin_category_create(body: CategoryIn, session: AsyncSession = Depends(get_session)):
    c = Category(name=body.name.strip(), icon=body.icon, sort_order=body.sort_order,
                 slug=await _unique_slug(session, services.slugify(body.slug or body.name)))
    session.add(c)
    await session.commit()
    return {"id": c.id, "name": c.name, "slug": c.slug, "icon": c.icon, "sort_order": c.sort_order}


@admin.put("/categories/{category_id}")
async def admin_category_update(category_id: int, body: CategoryIn, session: AsyncSession = Depends(get_session)):
    c = await session.get(Category, category_id)
    if not c:
        raise HTTPException(404, "Category not found")
    c.name, c.icon, c.sort_order = body.name.strip(), body.icon, body.sort_order
    c.slug = await _unique_slug(session, services.slugify(body.slug or body.name), skip_id=c.id)
    await session.commit()
    return {"id": c.id, "name": c.name, "slug": c.slug, "icon": c.icon, "sort_order": c.sort_order}


@admin.delete("/categories/{category_id}", status_code=204)
async def admin_category_delete(category_id: int, x_user_name: str = Header(default=""), session: AsyncSession = Depends(get_session)):
    c = await session.get(Category, category_id)
    if not c:
        raise HTTPException(404, "Category not found")
    name = c.name
    await session.execute(update(Product).where(Product.category_id == c.id).values(category_id=None))
    await session.delete(c)
    await session.commit()
    _alert("Category deleted", name, x_user_name, "/admin/categories")
