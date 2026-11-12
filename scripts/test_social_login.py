"""Tests the social-login server logic WITHOUT calling Google/Facebook/Telegram (their answers are simulated).

    python scripts/test_social_login.py
"""
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "auth_service"))
os.environ.update({"GOOGLE_CLIENT_ID": "gid", "GOOGLE_CLIENT_SECRET": "gsecret", "FACEBOOK_APP_ID": "fid",
                   "FACEBOOK_APP_SECRET": "fsecret", "TELEGRAM_BOT_TOKEN": "123:dummy", "TELEGRAM_BOT_USERNAME": "my_shop_bot"})

import httpx  # noqa: E402
from auth_app import oauth, telegram_login  # noqa: E402
from auth_app.main import app  # noqa: E402

KEY = {"X-Internal-Key": os.environ.get("INTERNAL_KEY", "change-me-internal-key")}


def ok(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        sys.exit(1)


async def main():
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://auth", headers=KEY) as c:
            p = (await c.get("/auth/providers")).json()
            ok("providers enabled", p["google"] and p["facebook"] and p["telegram"] and p["telegram_bot"] == "my_shop_bot")

            u = (await c.get("/auth/oauth/google/url", params={"redirect_uri": "http://localhost:3000/auth/callback/google", "state": "abc"})).json()["url"]
            q = parse_qs(urlparse(u).query)
            ok("google authorize URL", u.startswith("https://accounts.google.com/") and q["client_id"] == ["gid"] and q["state"] == ["abc"]
               and q["redirect_uri"] == ["http://localhost:3000/auth/callback/google"] and "openid" in q["scope"][0])
            u = (await c.get("/auth/oauth/facebook/url", params={"redirect_uri": "http://localhost:3000/auth/callback/facebook", "state": "xyz"})).json()["url"]
            q = parse_qs(urlparse(u).query)
            ok("facebook authorize URL", u.startswith("https://www.facebook.com/") and q["client_id"] == ["fid"] and "email" in q["scope"][0])

            # --- simulated provider answers
            profiles = {}

            async def fake(provider, code, redirect_uri):
                return profiles[code]
            oauth.fetch_profile = fake  # the route calls oauth.fetch_profile(...)

            profiles["g1"] = {"provider_id": "g-111", "name": "Sok Google", "email": "sok@gmail.com", "email_verified": True, "avatar": "https://x/p.png"}
            r = await c.post("/auth/oauth/google/callback", json={"code": "g1", "redirect_uri": "http://x"})
            ok("google login creates a customer", r.status_code == 200 and r.json()["user"]["role"] == "CUSTOMER" and r.json()["user"]["provider"] == "google", r.text[:300])
            uid = r.json()["user"]["id"]
            r = await c.post("/auth/oauth/google/callback", json={"code": "g1", "redirect_uri": "http://x"})
            ok("second google login = same account", r.json()["user"]["id"] == uid)
            ok("avatar kept", r.json()["user"]["avatar_url"] == "https://x/p.png")

            # a normal customer registered with the same VERIFIED google email gets linked
            r = await c.post("/auth/register", json={"name": "Local Lina", "email": "lina@gmail.com", "password": "secret123"})
            lid = r.json()["user"]["id"]
            profiles["g2"] = {"provider_id": "g-222", "name": "Lina G", "email": "lina@gmail.com", "email_verified": True, "avatar": ""}
            r = await c.post("/auth/oauth/google/callback", json={"code": "g2", "redirect_uri": "http://x"})
            ok("verified email links to the existing customer", r.json()["user"]["id"] == lid)

            # NEVER link to the admin account, even with the admin's email
            profiles["g3"] = {"provider_id": "g-333", "name": "Evil", "email": "admin@shop.local", "email_verified": True, "avatar": ""}
            r = await c.post("/auth/oauth/google/callback", json={"code": "g3", "redirect_uri": "http://x"})
            ok("never links to the admin account", r.json()["user"]["role"] == "CUSTOMER" and r.json()["user"]["username"] == "google_g-333")

            # unverified (facebook) email is not trusted for linking
            profiles["f1"] = {"provider_id": "f-1", "name": "Fb Lina", "email": "lina@gmail.com", "email_verified": False, "avatar": ""}
            r = await c.post("/auth/oauth/facebook/callback", json={"code": "f1", "redirect_uri": "http://x"})
            ok("unverified email does not link accounts", r.json()["user"]["id"] != lid and r.json()["user"]["provider"] == "facebook")

            ok("unknown provider 404", (await c.post("/auth/oauth/twitter/callback", json={"code": "x", "redirect_uri": "http://x"})).status_code == 404)

            # --- telegram deep-link login
            st = (await c.post("/auth/telegram/start")).json()
            ok("telegram start gives a bot link", st["url"] == f"https://t.me/my_shop_bot?start={st['token']}")
            ok("pending until START is pressed", (await c.get(f"/auth/telegram/status/{st['token']}")).json()["status"] == "pending")
            await telegram_login.handle_update({"update_id": 1, "message": {"text": "/start wrong-token", "from": {"id": 5, "first_name": "X"}}})
            ok("wrong token ignored", (await c.get(f"/auth/telegram/status/{st['token']}")).json()["status"] == "pending")
            await telegram_login.handle_update({"update_id": 2, "message": {"text": f"/start {st['token']}", "from": {"id": 777, "first_name": "Chan", "last_name": "Dara", "username": "chandara"}}})
            r = (await c.get(f"/auth/telegram/status/{st['token']}")).json()
            ok("telegram login confirmed -> tokens", r["status"] == "confirmed" and r["tokens"]["user"]["name"] == "Chan Dara" and r["tokens"]["user"]["provider"] == "telegram")
            ok("token is single-use", (await c.get(f"/auth/telegram/status/{st['token']}")).json()["status"] == "expired")
    print("\nSOCIAL LOGIN LOGIC OK")


asyncio.run(main())
