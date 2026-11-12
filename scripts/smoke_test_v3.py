"""Checks the v3 features through the gateway: passwords, admin user management, notifications, avatars.
Needs MOCK_MODE=true and DEV_RETURN_RESET_LINK=true in .env (so the reset link is returned), services running.

    python scripts/smoke_test_v3.py
"""
import asyncio
import io
import json
import random
import re
import sys
import time

import httpx
import websockets
from PIL import Image

GW, WS = "http://localhost:8000", "ws://localhost:8000"
RUN = random.randint(10000, 99999)


def ok(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        sys.exit(1)


def bearer(tok):
    return {"Authorization": "Bearer " + tok}


async def recv_until(ws, pred, timeout=10):
    async def loop():
        while True:
            m = json.loads(await ws.recv())
            if pred(m):
                return m
    return await asyncio.wait_for(loop(), timeout)


async def main():
    async with httpx.AsyncClient(base_url=GW, timeout=30) as c:
        h = (await c.get("/health")).json()
        ok("7 services healthy (incl. notify)", h["status"] == "ok" and "notify" in h["services"], str(h["services"]))

        # ------------------------------------------------------------ accounts
        email = f"v3{RUN}@test.com"
        r = await c.post("/api/auth/register", json={"name": "Vee Three", "email": email, "password": "secret123"})
        ok("register (min 8 chars)", r.status_code == 201 and r.json()["expires_in"] == 900, f"expires_in={r.json().get('expires_in')}")
        T = r.json(); U = bearer(T["access_token"])
        ok("7-char password rejected", (await c.post("/api/auth/register", json={"name": "Short Pw", "email": f"s{RUN}@test.com", "password": "short12"})).status_code == 422)

        # ---------------------------------------------------- change password
        await asyncio.sleep(1.2)  # tokens are timestamped per second
        r = await c.post("/api/auth/change-password", headers=U, json={"current_password": "wrong-pass", "new_password": "newsecret1"})
        ok("change password: wrong current -> 400", r.status_code == 400)
        r = await c.post("/api/auth/change-password", headers=U, json={"current_password": "secret123", "new_password": "secret123"})
        ok("change password: same password -> 422", r.status_code == 422)
        r = await c.post("/api/auth/change-password", headers=U, json={"current_password": "secret123", "new_password": "short"})
        ok("change password: too short -> 422", r.status_code == 422)
        r = await c.post("/api/auth/change-password", headers=U, json={"current_password": "secret123", "new_password": "newsecret1"})
        ok("change password", r.status_code == 200 and "access_token" in r.json())
        T2 = r.json(); U = bearer(T2["access_token"])
        ok("old refresh token signed out by the change", (await c.post("/api/auth/refresh", json={"refresh_token": T["refresh_token"]})).status_code == 401)
        ok("this session continues (new tokens)", (await c.post("/api/auth/refresh", json={"refresh_token": T2["refresh_token"]})).status_code == 200)
        ok("old password no longer works", (await c.post("/api/auth/login", json={"username": email, "password": "secret123"})).status_code == 401)
        ok("new password works", (await c.post("/api/auth/login", json={"username": email, "password": "newsecret1"})).status_code == 200)

        # ----------------------------------------------------- forgot / reset
        r = await c.post("/api/auth/forgot-password", json={"email": f"nobody{RUN}@test.com"})
        ok("forgot password: unknown email gets the SAME answer (no account leak)", r.status_code == 200 and r.json()["ok"] is True and "dev_link" not in r.json())
        r1 = await c.post("/api/auth/forgot-password", json={"email": email})
        ok("forgot password: link created (dev mode returns it)", "dev_link" in r1.json(), str(r1.json())[:80])
        link1 = r1.json()["dev_link"]; tok1 = re.search(r"token=([\w-]+)", link1).group(1)
        ok("reset token is valid", (await c.get("/api/auth/reset-password/check", params={"token": tok1})).json()["valid"] is True)
        r2 = await c.post("/api/auth/forgot-password", json={"email": email})
        tok2 = re.search(r"token=([\w-]+)", r2.json()["dev_link"]).group(1)
        ok("a newer link cancels the older one", (await c.get("/api/auth/reset-password/check", params={"token": tok1})).json()["valid"] is False)
        ok("reset: bad token -> 400", (await c.post("/api/auth/reset-password", json={"token": "x" * 30, "password": "another123"})).status_code == 400)
        ok("reset: short password -> 422", (await c.post("/api/auth/reset-password", json={"token": tok2, "password": "abc"})).status_code == 422)
        await asyncio.sleep(1.2)
        ok("reset password", (await c.post("/api/auth/reset-password", json={"token": tok2, "password": "another123"})).status_code == 200)
        ok("reset link is single-use", (await c.post("/api/auth/reset-password", json={"token": tok2, "password": "another456"})).status_code == 400)
        ok("reset signs out older sessions", (await c.post("/api/auth/refresh", json={"refresh_token": T2["refresh_token"]})).status_code == 401)
        ok("login with the reset password", (await c.post("/api/auth/login", json={"username": email, "password": "another123"})).status_code == 200)

        # --------------------------------------------------- admin: user management
        adm = (await c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})).json()
        A = bearer(adm["access_token"]); admin_id = adm["user"]["id"]
        ok("customer cannot open admin users", (await c.get("/api/auth/admin/users", headers=U)).status_code == 403)
        ok("guest cannot open admin users", (await c.get("/api/auth/admin/users")).status_code == 401)
        ok("internal auth endpoints hidden", (await c.get("/api/auth/internal/admins")).status_code == 404)
        s = (await c.get("/api/auth/admin/users/summary", headers=A)).json()
        ok("user summary", s["total"] >= 2 and s["admins"] >= 1 and "local" in s["by_provider"], str(s))
        lst = (await c.get("/api/auth/admin/users", headers=A, params={"q": email})).json()
        ok("search users", lst["total"] == 1 and lst["items"][0]["email"] == email)
        uid = lst["items"][0]["id"]
        ok("filter by role", all(u["role"] == "ADMIN" for u in (await c.get("/api/auth/admin/users", headers=A, params={"role": "ADMIN"})).json()["items"]))
        r = await c.post("/api/auth/admin/users", headers=A, json={"name": "Staff Sam", "email": f"sam{RUN}@test.com", "password": "samsecret1", "role": "ADMIN"})
        ok("admin creates a user", r.status_code == 201 and r.json()["role"] == "ADMIN")
        sam = r.json()
        ok("duplicate email rejected", (await c.post("/api/auth/admin/users", headers=A, json={"name": "Dup", "email": f"sam{RUN}@test.com", "password": "samsecret1"})).status_code == 409)
        r = await c.patch(f"/api/auth/admin/users/{uid}", headers=A, json={"name": "Vee Renamed", "phone": "012000111"})
        ok("admin edits profile", r.json()["name"] == "Vee Renamed" and r.json()["phone"] == "012000111")
        ok("cannot disable yourself", (await c.patch(f"/api/auth/admin/users/{admin_id}", headers=A, json={"is_active": False})).status_code == 409)
        ok("cannot demote yourself", (await c.patch(f"/api/auth/admin/users/{admin_id}", headers=A, json={"role": "CUSTOMER"})).status_code == 409)
        ok("cannot delete yourself", (await c.delete(f"/api/auth/admin/users/{admin_id}", headers=A)).status_code == 409)
        login = (await c.post("/api/auth/login", json={"username": email, "password": "another123"})).json()
        await asyncio.sleep(1.2)
        r = await c.patch(f"/api/auth/admin/users/{uid}", headers=A, json={"is_active": False})
        ok("admin disables a user", r.json()["is_active"] is False)
        ok("disabled user cannot log in", (await c.post("/api/auth/login", json={"username": email, "password": "another123"})).status_code == 401)
        ok("disabled user's session cannot be refreshed", (await c.post("/api/auth/refresh", json={"refresh_token": login["refresh_token"]})).status_code == 401)
        await c.patch(f"/api/auth/admin/users/{uid}", headers=A, json={"is_active": True})
        r = await c.post(f"/api/auth/admin/users/{uid}/reset-password", headers=A, json={})
        temp = r.json()["temporary_password"]
        ok("admin resets a password (temporary one returned once)", r.status_code == 200 and len(temp) == 12)
        ok("login with the temporary password", (await c.post("/api/auth/login", json={"username": email, "password": temp})).status_code == 200)
        ok("admin deletes a user", (await c.delete(f"/api/auth/admin/users/{sam['id']}", headers=A)).status_code == 204)
        ok("deleted user is gone", (await c.get(f"/api/auth/admin/users/{sam['id']}", headers=A)).status_code == 404)

        # --------------------------------------------- avatar + profile settings
        T3 = (await c.post("/api/auth/login", json={"username": email, "password": temp})).json(); U = bearer(T3["access_token"])
        buf = io.BytesIO(); Image.new("RGB", (900, 500), (200, 40, 90)).save(buf, "PNG")
        r = await c.post("/api/media/avatar", headers=U, files={"file": ("me.png", buf.getvalue(), "image/png")})
        ok("customer uploads an avatar", r.status_code == 201 and r.json()["url"].startswith("/media/"))
        av = r.json()["url"]
        ok("avatar is served", (await c.get(av)).status_code == 200)
        ok("customer still cannot use the product image upload", (await c.post("/api/media/upload", headers=U, files={"file": ("me.png", buf.getvalue(), "image/png")})).status_code == 403)
        r = await c.patch("/api/auth/me", headers=U, json={"name": "Vee Renamed", "phone": "012000111", "avatar_url": av})
        ok("profile saved with avatar", r.json()["avatar_url"] == av)
        ok("external avatar link refused", (await c.patch("/api/auth/me", headers=U, json={"name": "Vee Renamed", "avatar_url": "https://evil.example/x.png"})).status_code == 422)
        ok("avatar shows up in the next token", (await c.post("/api/auth/refresh", json={"refresh_token": T3["refresh_token"]})).json()["user"]["avatar_url"] == av)

        # ---------------------------------------------------------- notifications
        T3 = (await c.post("/api/auth/login", json={"username": email, "password": temp})).json(); U = bearer(T3["access_token"])
        base = (await c.get("/api/notify/notifications", headers=A)).json()["unread"]
        cheap = (await c.get("/api/ops/products", params={"sort": "price_asc", "page_size": 1})).json()["items"][0]
        async with websockets.connect(f"{WS}/ws/notifications?token={T3['access_token']}") as live:
            hello = json.loads(await live.recv())
            ok("notifications WebSocket hello", hello["type"] == "hello")
            await c.post("/api/ops/cart/items", headers=U, json={"product_id": cheap["id"], "qty": 1})
            o = (await c.post("/api/ops/checkout", headers=U, json={"fulfillment": "PICKUP"})).json()
            await c.post(f"/api/ops/payments/{o['payment']['public_id']}/mock-pay", headers=U)
            n = await recv_until(live, lambda m: m.get("type") == "notification", 10)
            ok("customer gets 'Payment received' live", n["notification"]["title"] == "Payment received" and o["order_no"] in n["notification"]["body"])
            await asyncio.sleep(1)
            await c.patch(f"/api/ops/admin/orders/{o['order_no']}/status", headers=A, json={"status": "READY"})
            n = await recv_until(live, lambda m: m.get("type") == "notification" and "ready" in m["notification"]["title"].lower(), 10)
            ok("customer gets 'ready for pickup' live", n["notification"]["url"] == f"/order/{o['order_no']}")
        mine = (await c.get("/api/notify/notifications", headers=U)).json()
        ok("customer notification list + unread", len(mine["items"]) >= 2 and mine["unread"] >= 2, f"unread={mine['unread']}")
        ok("admin got 'New paid order'", any(x["title"] == "New paid order" and o["order_no"] in x["body"] for x in (await c.get("/api/notify/notifications", headers=A)).json()["items"]))
        first = mine["items"][0]["id"]
        r = await c.post(f"/api/notify/notifications/{first}/read", headers=U)
        ok("mark one as read", r.json()["unread"] == mine["unread"] - 1)
        ok("mark all as read", (await c.post("/api/notify/notifications/read-all", headers=U)).json()["unread"] == 0)
        ok("clear read notifications", (await c.delete("/api/notify/notifications", headers=U)).json()["unread"] == 0 and (await c.get("/api/notify/notifications", headers=U)).json()["items"] == [])
        other = (await c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})).json()
        ok("notifications are private per user", all(x["id"] != first for x in (await c.get("/api/notify/notifications", headers=bearer(other["access_token"]))).json()["items"]))
        ok("guest cannot read notifications", (await c.get("/api/notify/notifications")).status_code == 401)
        ok("internal notify endpoint hidden", (await c.post("/api/notify/internal/events", json={})).status_code == 404)
        pr = (await c.get("/api/notify/preferences", headers=U)).json()
        ok("preferences (no telegram login -> not available)", pr["telegram_available"] is False and pr["telegram_enabled"] is True)
        ok("save preference", (await c.put("/api/notify/preferences", headers=U, json={"telegram_enabled": False})).json()["telegram_enabled"] is False)
        st = (await c.get("/api/notify/admin/telegram-status", headers=A)).json()
        ok("telegram status (admin)", "bot_configured" in st and "chat_count" in st, str(st))
        ok("telegram status is admin-only", (await c.get("/api/notify/admin/telegram-status", headers=U)).status_code == 403)
        ok("telegram test explains what is missing", (await c.post("/api/notify/admin/telegram-test", headers=A)).status_code == 400)
    print("\nV3 CHECKS PASSED")


asyncio.run(main())
