"""Google and Facebook login (authorization-code flow). The website redirects the browser;
this service only builds the URL and exchanges the code on the server (secrets never reach the browser)."""
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException

from .config import settings

TIMEOUT = 10


def enabled() -> dict[str, bool]:
    return {
        "google": bool(settings.google_client_id and settings.google_client_secret),
        "facebook": bool(settings.facebook_app_id and settings.facebook_app_secret),
        "telegram": bool(settings.telegram_bot_token and settings.telegram_bot_username),
    }


def authorize_url(provider: str, redirect_uri: str, state: str) -> str:
    if not enabled().get(provider):
        raise HTTPException(501, f"{provider.title()} login is not configured on the server")
    if provider == "google":
        q = urlencode({"client_id": settings.google_client_id, "redirect_uri": redirect_uri,
                       "response_type": "code", "scope": "openid email profile", "state": state,
                       "prompt": "select_account"})
        return f"https://accounts.google.com/o/oauth2/v2/auth?{q}"
    if provider == "facebook":
        q = urlencode({"client_id": settings.facebook_app_id, "redirect_uri": redirect_uri,
                       "state": state, "scope": "email,public_profile", "response_type": "code"})
        return f"https://www.facebook.com/v19.0/dialog/oauth?{q}"
    raise HTTPException(404, "Unknown provider")


async def fetch_profile(provider: str, code: str, redirect_uri: str) -> dict:
    """Returns {provider_id, name, email, email_verified, avatar}."""
    if not enabled().get(provider):
        raise HTTPException(501, f"{provider.title()} login is not configured on the server")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            if provider == "google":
                tok = await c.post("https://oauth2.googleapis.com/token", data={
                    "code": code, "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret, "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code"})
                tok.raise_for_status()
                info = (await c.get("https://openidconnect.googleapis.com/v1/userinfo",
                                    headers={"Authorization": f"Bearer {tok.json()['access_token']}"}))
                info.raise_for_status()
                d = info.json()
                return {"provider_id": d["sub"], "name": d.get("name", ""), "email": d.get("email", ""),
                        "email_verified": bool(d.get("email_verified")), "avatar": d.get("picture", "")}
            tok = await c.get("https://graph.facebook.com/v19.0/oauth/access_token", params={
                "client_id": settings.facebook_app_id, "client_secret": settings.facebook_app_secret,
                "redirect_uri": redirect_uri, "code": code})
            tok.raise_for_status()
            me = await c.get("https://graph.facebook.com/me", params={
                "fields": "id,name,email,picture.type(large)", "access_token": tok.json()["access_token"]})
            me.raise_for_status()
            d = me.json()
            return {"provider_id": d["id"], "name": d.get("name", ""), "email": d.get("email", ""),
                    "email_verified": False, "avatar": (d.get("picture") or {}).get("data", {}).get("url", "")}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, f"{provider.title()} sign-in failed. Please try again.")
