import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import quote

import httpx
import jwt
import websockets
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .ratelimit import limiter
from .security import verify_access_token

log = logging.getLogger("gateway")
logging.basicConfig(level=logging.INFO)

SERVICES = {"auth": settings.auth_url, "ops": settings.ops_url, "reports": settings.report_url,
            "media": settings.media_url, "notify": settings.notify_url}
AUTH_PUBLIC = {"login", "register", "refresh", "logout", "providers", "telegram/start", "forgot-password",
               "reset-password", "reset-password/check"}
SENSITIVE_PATHS = ("/api/auth/login", "/api/auth/register", "/api/auth/forgot-password", "/api/auth/reset-password")
OPS_LOGIN_REQUIRED = {"cart", "checkout", "orders", "my", "payments", "wishlist"}  # buying needs a login
FORWARD_HEADERS = {"content-type", "accept"}
client: httpx.AsyncClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    client = httpx.AsyncClient(timeout=30)
    yield
    await client.aclose()


app = FastAPI(title="API Gateway", version="2.0", lifespan=lifespan,
              description="Single public entry. Routes /api/auth, /api/ops, /api/reports, /api/media; /media/* serves "
                          "images; verifies JWT and role; WebSocket: /ws/payments/{id}, /ws/chat, "
                          "/ws/admin/orders, /ws/admin/chat, /ws/notifications")
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Request-ID"])


def error(status: int, code: str, message: str, request_id: str = "") -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "request_id": request_id}},
                        status_code=status, headers={"X-Request-ID": request_id} if request_id else None)


@app.exception_handler(StarletteHTTPException)
async def http_exc(request: Request, exc: StarletteHTTPException):
    return error(exc.status_code, "http_error", str(exc.detail), getattr(request.state, "request_id", ""))


@app.exception_handler(RequestValidationError)
async def validation_exc(request: Request, exc: RequestValidationError):
    return error(422, "validation_error", "Invalid request", getattr(request.state, "request_id", ""))


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    request.state.request_id = rid
    ip = request.client.host if request.client else "unknown"
    path = request.url.path
    if request.method != "OPTIONS" and not path.startswith("/media/") and path != "/health":
        if path in SENSITIVE_PATHS and not limiter.allow(f"login:{ip}", settings.login_rate_limit_per_min):
            return error(429, "rate_limited", "Too many attempts. Try again in a minute.", rid)
        if not limiter.allow(f"all:{ip}", settings.rate_limit_per_min):
            return error(429, "rate_limited", "Too many requests. Slow down.", rid)
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    if not path.startswith("/media/"):
        log.info("%s %s -> %s (%.0f ms) [%s]", request.method, path, response.status_code,
                 (time.perf_counter() - start) * 1000, rid)
    return response


# ----------------------------------------------------------------- routing
def target_path(service: str, path: str) -> str:
    return f"/auth/{path}" if service == "auth" else f"/{path}"


def required_role(service: str, path: str) -> str | None:
    """None = public, 'AUTH' = any logged-in user, 'ADMIN' = admin only."""
    first = path.split("/", 1)[0]
    if first == "internal":
        raise HTTPException(404, "Not found")
    if service == "auth":
        if first == "admin":
            return "ADMIN"
        public = path in AUTH_PUBLIC or path.startswith("oauth/") or path.startswith("telegram/status/")
        return None if public else "AUTH"
    if service == "media":
        return "AUTH" if path == "avatar" else "ADMIN"   # customers may only upload their own profile picture
    if service == "reports":
        return "ADMIN"
    if service == "notify":
        return "ADMIN" if first == "admin" else "AUTH"
    if first == "admin":
        return "ADMIN"
    return "AUTH" if first in OPS_LOGIN_REQUIRED else None  # browsing is public


async def authenticate(request_headers: dict) -> dict:
    auth = request_headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "Please log in first")
    try:
        return await verify_access_token(auth[7:], client)
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Your session expired. Please log in again.")
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid token")
    except httpx.HTTPError:
        raise HTTPException(503, "Auth service unavailable")


def identity_headers(claims: dict) -> dict:
    """Header values must be latin-1, so names (Khmer, etc.) are percent-encoded."""
    return {"X-User-Id": str(claims["sub"]), "X-User-Role": claims.get("role", ""),
            "X-User-Name": quote(claims.get("name", "") or ""), "X-User-Email": quote(claims.get("email", "") or "")}


@app.api_route("/api/{service}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(service: str, path: str, request: Request):
    if service not in SERVICES:
        raise HTTPException(404, "Unknown service")
    role_needed = required_role(service, path)
    headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARD_HEADERS}
    headers["X-Internal-Key"] = settings.internal_key
    headers["X-Request-ID"] = request.state.request_id
    if role_needed:
        claims = await authenticate(dict(request.headers))
        if role_needed == "ADMIN" and claims.get("role") != "ADMIN":
            raise HTTPException(403, "Admin only")
        headers.update(identity_headers(claims))
    if service == "media" and int(request.headers.get("content-length") or 0) > (settings.max_upload_mb + 1) * 1024 * 1024:
        raise HTTPException(413, f"Image is too large (max {settings.max_upload_mb} MB)")
    try:
        upstream = await client.request(
            request.method, SERVICES[service] + target_path(service, path),
            params=request.query_params, content=await request.body(), headers=headers)
    except httpx.ConnectError:
        raise HTTPException(503, f"The {service} service is not reachable")
    except httpx.TimeoutException:
        raise HTTPException(504, f"The {service} service timed out")
    if upstream.status_code >= 400:
        try:
            detail = upstream.json().get("detail", "Request failed")
        except Exception:
            detail = "Request failed"
        if isinstance(detail, list):
            detail = "; ".join(f"{'.'.join(str(x) for x in d.get('loc', [])[1:])}: {d.get('msg')}" for d in detail)
        return error(upstream.status_code, "upstream_error", str(detail), request.state.request_id)
    return Response(upstream.content, status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type"))


@app.get("/media/{filename}")
async def media_file(filename: str):
    """Public image URLs (cached for a year; file names are random)."""
    try:
        r = await client.get(f"{settings.media_url}/files/{filename}", headers={"X-Internal-Key": settings.internal_key})
    except httpx.HTTPError:
        raise HTTPException(503, "Media service is not reachable")
    if r.status_code != 200:
        raise HTTPException(404, "Image not found")
    return Response(r.content, media_type=r.headers.get("content-type"),
                    headers={"Cache-Control": "public, max-age=31536000, immutable",
                             "Cross-Origin-Resource-Policy": "cross-origin"})


@app.get("/health")
async def health():
    targets = {**SERVICES, "chat": settings.chat_url}

    async def ping(name: str, url: str):
        try:
            r = await client.get(f"{url}/health", timeout=3)
            return name, ("ok" if r.status_code == 200 else "error")
        except Exception:
            return name, "down"
    results = dict(await asyncio.gather(*(ping(n, u) for n, u in targets.items())))
    return {"gateway": "ok", "services": results,
            "status": "ok" if all(v == "ok" for v in results.values()) else "degraded"}


# --------------------------------------------------------------- websockets
async def relay(client_ws: WebSocket, upstream_url: str, extra_headers: dict | None = None) -> None:
    """Pipe messages both ways between the browser and an internal service."""
    headers = {"X-Internal-Key": settings.internal_key, **(extra_headers or {})}
    try:
        async with websockets.connect(upstream_url, additional_headers=headers) as up:
            async def to_upstream():
                try:
                    while True:
                        await up.send(await client_ws.receive_text())
                except WebSocketDisconnect:
                    await up.close()

            async def to_client():
                try:
                    async for msg in up:
                        await client_ws.send_text(msg if isinstance(msg, str) else msg.decode())
                except (WebSocketDisconnect, RuntimeError):
                    await up.close()  # the browser left first

            tasks = [asyncio.create_task(to_upstream()), asyncio.create_task(to_client())]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)  # collect results so nothing is left unhandled
    except Exception as e:
        log.warning("websocket relay ended: %s", e)
    finally:
        try:
            await client_ws.close()
        except Exception:
            pass


def _ws_url(base: str, path: str) -> str:
    return base.replace("http://", "ws://").replace("https://", "wss://") + path


async def _claims_or_none(token: str) -> dict | None:
    try:
        return await verify_access_token(token, client) if token else None
    except Exception:
        return None


@app.websocket("/ws/payments/{public_id}")
async def ws_payment(ws: WebSocket, public_id: str):
    await ws.accept()
    await relay(ws, _ws_url(settings.ops_url, f"/ws/payments/{public_id}"))


@app.websocket("/ws/admin/orders")
async def ws_admin_orders(ws: WebSocket, token: str = ""):
    """Browsers cannot set headers on WebSocket, so the JWT comes as ?token=..."""
    claims = await _claims_or_none(token)
    if not claims or claims.get("role") != "ADMIN":
        await ws.close(code=1008)
        return
    await ws.accept()
    await relay(ws, _ws_url(settings.report_url, "/ws/admin/orders"))


@app.websocket("/ws/me")
async def ws_me(ws: WebSocket, token: str = ""):
    """Live updates for the logged-in customer (order status changes)."""
    claims = await _claims_or_none(token)
    if not claims:
        await ws.close(code=1008)
        return
    await ws.accept()
    await relay(ws, _ws_url(settings.ops_url, "/ws/me"), identity_headers(claims))


@app.websocket("/ws/notifications")
async def ws_notifications(ws: WebSocket, token: str = ""):
    claims = await _claims_or_none(token)
    if not claims:
        await ws.close(code=1008)
        return
    await ws.accept()
    await relay(ws, _ws_url(settings.notify_url, "/ws/notifications"), identity_headers(claims))


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket, token: str = "", guest: str = ""):
    """Customer chat. Logged-in users are recognised by token, visitors by a random guest id."""
    claims = await _claims_or_none(token)
    await ws.accept()
    headers = identity_headers(claims) if claims else {}
    await relay(ws, _ws_url(settings.chat_url, f"/ws/chat?guest={quote(guest)}"), headers)


@app.websocket("/ws/admin/chat")
async def ws_admin_chat(ws: WebSocket, token: str = ""):
    claims = await _claims_or_none(token)
    if not claims or claims.get("role") != "ADMIN":
        await ws.close(code=1008)
        return
    await ws.accept()
    await relay(ws, _ws_url(settings.chat_url, "/ws/admin/chat"), identity_headers(claims))
