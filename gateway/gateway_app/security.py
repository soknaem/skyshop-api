"""JWT verification. The gateway only needs the PUBLIC key of the auth service."""
import asyncio

import httpx
import jwt

from .config import settings

_public_key: str | None = None
_lock = asyncio.Lock()


async def _fetch_public_key(client: httpx.AsyncClient) -> str:
    global _public_key
    async with _lock:
        r = await client.get(f"{settings.auth_url}/auth/public-key",
                             headers={"X-Internal-Key": settings.internal_key}, timeout=5)
        r.raise_for_status()
        _public_key = r.json()["public_key"]
        return _public_key


async def verify_access_token(token: str, client: httpx.AsyncClient) -> dict:
    """Returns the claims, raises jwt.PyJWTError if invalid, httpx.HTTPError if auth is down."""
    key = _public_key or await _fetch_public_key(client)
    try:
        data = jwt.decode(token, key, algorithms=["RS256"])
    except jwt.InvalidSignatureError:
        # the auth service may have a new key pair: refresh once
        key = await _fetch_public_key(client)
        data = jwt.decode(token, key, algorithms=["RS256"])
    if data.get("type") != "access":
        raise jwt.InvalidTokenError("not an access token")
    return data
