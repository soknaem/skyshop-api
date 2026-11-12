import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import migrate, services
from .config import settings
from .db import Base, SessionLocal, engine
from .routers import cart, catalog, shop, wishlist, ws

logging.basicConfig(level=logging.INFO)
OPEN_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await migrate.run(engine)
    async with SessionLocal() as session:
        await services.seed(session)
    await services.resume_watchers()
    dispatcher = asyncio.create_task(services.outbox_dispatcher())
    yield
    dispatcher.cancel()
    for t in list(services.WATCHERS.values()):
        t.cancel()
    await engine.dispose()


app = FastAPI(title="Operation Service", version="2.0", lifespan=lifespan)


@app.middleware("http")
async def internal_key_guard(request: Request, call_next):
    if request.url.path not in OPEN_PATHS and request.headers.get("x-internal-key") != settings.internal_key:
        return JSONResponse({"detail": "Forbidden: call this service through the gateway"}, status_code=403)
    return await call_next(request)


@app.get("/health")
async def health():
    return {"service": "operation", "status": "ok", "mock_mode": settings.mock_mode}


app.include_router(catalog.public)
app.include_router(catalog.admin)
app.include_router(cart.router)
app.include_router(wishlist.router)
app.include_router(shop.router)
app.include_router(ws.router)
