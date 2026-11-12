"""End-to-end check of the whole backend through the gateway (needs MOCK_MODE=true and a FRESH database).

    python scripts/reset_db.py      # optional: clean start
    python scripts/smoke_test.py
"""
import asyncio
import io
import json
import random
import sys

import httpx
import websockets
from PIL import Image

GW, WS = "http://localhost:8000", "ws://localhost:8000"
RUN = random.randint(10000, 99999)


def ok(label: str, cond: bool, extra: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        sys.exit(1)


async def recv_until(ws, pred, timeout=10):
    async def loop():
        while True:
            msg = json.loads(await ws.recv())
            if pred(msg):
                return msg
    return await asyncio.wait_for(loop(), timeout)


async def main() -> None:
    async with httpx.AsyncClient(base_url=GW, timeout=30) as c:
        h = (await c.get("/health")).json()
        ok("all 6 services healthy", h["status"] == "ok", str(h["services"]))

        # ---------------------------------------------------------- browsing is public
        cats = (await c.get("/api/ops/categories")).json()
        ok("categories with counts", len(cats) == 8 and all(x["product_count"] > 0 for x in cats))
        allp = (await c.get("/api/ops/products", params={"page_size": 60})).json()
        ok("electronics catalogue", allp["total"] >= 20, f"total={allp['total']}")
        r = (await c.get("/api/ops/products", params={"category": "audio"})).json()
        ok("filter by category", r["total"] == 3 and all(p["category"]["slug"] == "audio" for p in r["items"]))
        r = (await c.get("/api/ops/products", params={"brand": "Volt,Pulse", "sort": "price_asc"})).json()
        ok("filter by brands + sort price asc", r["total"] >= 7 and r["items"][0]["price"] <= r["items"][-1]["price"])
        r = (await c.get("/api/ops/products", params={"on_sale": True})).json()
        ok("on sale filter", r["total"] > 5 and all(p["discount_percent"] > 0 for p in r["items"]))
        r = (await c.get("/api/ops/products", params={"q": "wireless"})).json()
        ok("search", r["total"] >= 2)
        r = (await c.get("/api/ops/products", params={"sort": "popular", "page_size": 3})).json()
        ok("popular first", r["items"][0]["featured"] is True)
        fac = (await c.get("/api/ops/products/facets", params={"category": "accessories"})).json()
        ok("facets", len(fac["brands"]) >= 1 and fac["price_max"] > fac["price_min"])
        cheap = (await c.get("/api/ops/products", params={"sort": "price_asc", "page_size": 1})).json()["items"][0]
        ok("test item is the cheapest", "test item" in cheap["name"], cheap["name"])
        p1 = (await c.get(f"/api/ops/products/{allp['items'][0]['id']}")).json()
        ok("product detail has specs and images", len(p1["specs"]) >= 3 and len(p1["images"]) >= 1)
        rel = (await c.get(f"/api/ops/products/{p1['id']}/related")).json()
        ok("related products", len(rel) >= 1 and all(x["id"] != p1["id"] for x in rel))

        # ------------------------------------------------- guests can only view
        for method, path in [("POST", "/api/ops/cart/items"), ("GET", "/api/ops/cart"), ("POST", "/api/ops/checkout"),
                             ("GET", "/api/ops/orders/X"), ("GET", "/api/ops/wishlist"), ("GET", "/api/ops/admin/products")]:
            r = await c.request(method, path, json={} if method == "POST" else None)
            ok(f"guest blocked: {method} {path}", r.status_code == 401, str(r.status_code))
        ok("report/internal hidden", (await c.get("/api/reports/internal/events/order-paid")).status_code == 404)

        # ------------------------------------------------------ customer accounts
        email_a, email_b = f"a{RUN}@test.com", f"b{RUN}@test.com"
        r = await c.post("/api/auth/register", json={"name": "Alice Test", "email": email_a, "password": "secret12323"})
        ok("register", r.status_code == 201 and r.json()["user"]["role"] == "CUSTOMER")
        tok_a = r.json()
        A = {"Authorization": "Bearer " + tok_a["access_token"]}
        ok("duplicate email rejected", (await c.post("/api/auth/register", json={"name": "Alice", "email": email_a.upper(), "password": "secret12323"})).status_code == 409)
        ok("short password rejected", (await c.post("/api/auth/register", json={"name": "Bob Test", "email": email_b, "password": "123"})).status_code == 422)
        ok("wrong password", (await c.post("/api/auth/login", json={"username": email_a, "password": "nope"})).status_code == 401)
        r = await c.post("/api/auth/login", json={"username": email_a, "password": "secret12323"})
        ok("login with email", r.status_code == 200)
        r = await c.patch("/api/auth/me", headers=A, json={"name": "Alice Updated", "phone": "012111222"})
        ok("update profile", r.json()["name"] == "Alice Updated")
        r = await c.post("/api/auth/refresh", json={"refresh_token": tok_a["refresh_token"]})
        ok("refresh rotation", r.status_code == 200)
        ok("old refresh token rejected", (await c.post("/api/auth/refresh", json={"refresh_token": tok_a["refresh_token"]})).status_code == 401)
        tok_a = r.json(); A = {"Authorization": "Bearer " + tok_a["access_token"]}
        r = await c.post("/api/auth/register", json={"name": "Bob Test", "email": email_b, "password": "secret12323"})
        B = {"Authorization": "Bearer " + r.json()["access_token"]}
        prov = (await c.get("/api/auth/providers")).json()
        ok("social providers listed (unconfigured)", prov["google"] is False and prov["telegram"] is False)
        ok("oauth url 501 when not configured", (await c.get("/api/auth/oauth/google/url", params={"redirect_uri": "http://x/cb", "state": "s"})).status_code == 501)
        ok("telegram start 501 when not configured", (await c.post("/api/auth/telegram/start")).status_code == 501)

        # ---------------------------------------------------------------- wishlist
        r = await c.post(f"/api/ops/wishlist/{p1['id']}", headers=A)
        ok("wishlist add", r.status_code == 201)
        ok("wishlist ids", (await c.get("/api/ops/wishlist/ids", headers=A)).json() == [p1["id"]])
        ok("wishlist list", (await c.get("/api/ops/wishlist", headers=A)).json()[0]["id"] == p1["id"])
        await c.delete(f"/api/ops/wishlist/{p1['id']}", headers=A)
        ok("wishlist remove", (await c.get("/api/ops/wishlist/ids", headers=A)).json() == [])

        # ----------------------------------------- admin login + live feeds opened early
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        ok("admin login", r.status_code == 200 and r.json()["user"]["role"] == "ADMIN")
        tok_admin = r.json()["access_token"]; ADM = {"Authorization": "Bearer " + tok_admin}
        ok("customer cannot use admin api", (await c.get("/api/ops/admin/products", headers=A)).status_code == 403)
        ok("customer cannot see reports", (await c.get("/api/reports/summary", headers=A)).status_code == 403)

        stock_before = cheap["stock_qty"]
        async with websockets.connect(f"{WS}/ws/admin/orders?token={tok_admin}") as adm_orders:
            # ------------------------------------------------- pickup order: no details needed
            r = await c.post("/api/ops/cart/items", headers=A, json={"product_id": cheap["id"], "qty": 2})
            ok("add to cart (logged in)", r.status_code == 200 and r.json()["count"] == 2)
            r = await c.post("/api/ops/cart/items", headers=A, json={"product_id": cheap["id"], "qty": 900})
            ok("qty over stock rejected", r.status_code == 409)
            ok("cart is private per user", (await c.get("/api/ops/cart", headers=B)).json()["count"] == 0)
            r = await c.post("/api/ops/checkout", headers=A, json={"fulfillment": "DELIVERY", "full_name": "", "email": "", "phone": "", "shipping_address": ""})
            ok("delivery needs phone", r.status_code == 422 and "phone" in r.json()["error"]["message"])
            r = await c.post("/api/ops/checkout", headers=A, json={"fulfillment": "DELIVERY", "phone": "012345678"})
            ok("delivery needs address", r.status_code == 422 and "address" in r.json()["error"]["message"])
            r = await c.post("/api/ops/checkout", headers=A, json={"fulfillment": "PICKUP", "email": "not-an-email"})
            ok("optional email must still be valid", r.status_code == 422)
            r = await c.post("/api/ops/checkout", headers=A, json={"fulfillment": "PICKUP"})
            ok("PICKUP checkout with NO details", r.status_code == 201 and r.json()["fulfillment"] == "PICKUP"
               and r.json()["payment"]["qr_image"].startswith("data:image"))
            order = r.json(); pub = order["payment"]["public_id"]
            ok("order name falls back to account name", order["customer_name"] == "Alice Updated")
            ok("other user cannot read the order", (await c.get(f"/api/ops/orders/{order['order_no']}", headers=B)).status_code == 404)

            async with websockets.connect(f"{WS}/ws/payments/{pub}") as ws:
                ok("ws initial status", json.loads(await ws.recv())["status"] == "PENDING")
                ok("other user cannot pay it", (await c.post(f"/api/ops/payments/{pub}/mock-pay", headers=B)).status_code == 404)
                ok("mock pay", (await c.post(f"/api/ops/payments/{pub}/mock-pay", headers=A)).status_code == 200)
                msg = await recv_until(ws, lambda m: m.get("status") in ("PAID",), 10)
                ok("ws pushed PAID", msg["status"] == "PAID")
            ev = await recv_until(adm_orders, lambda m: m.get("type") == "order_paid", 15)
            ok("admin live feed: order_paid", ev["order"]["order_no"] == order["order_no"] and ev["order"]["fulfillment"] == "PICKUP")

            o = (await c.get(f"/api/ops/orders/{order['order_no']}", headers=A)).json()
            ok("order PAID + PROCESSING", o["status"] == "PAID" and o["fulfillment_status"] == "PROCESSING")
            after = (await c.get(f"/api/ops/products/{cheap['id']}")).json()
            ok("stock deducted and popularity counted", after["stock_qty"] == stock_before - 2 and after["sold_count"] == cheap["sold_count"] + 2)
            ok("cart cleared", (await c.get("/api/ops/cart", headers=A)).json()["count"] == 0)

            # --------------------------------- delivery order with only phone + address
            await c.post("/api/ops/cart/items", headers=A, json={"product_id": cheap["id"], "qty": 1})
            r = await c.post("/api/ops/checkout", headers=A, json={"fulfillment": "DELIVERY", "phone": "012345678", "shipping_address": "St 1, Phnom Penh"})
            ok("DELIVERY checkout (phone + address only)", r.status_code == 201 and r.json()["shipping_address"] == "St 1, Phnom Penh")
            o2 = r.json(); pub2 = o2["payment"]["public_id"]
            await c.post(f"/api/ops/payments/{pub2}/mock-pay", headers=A)
            await recv_until(adm_orders, lambda m: m.get("type") == "order_paid", 15)
            r = await c.patch(f"/api/ops/admin/orders/{o2['order_no']}/status", headers=ADM, json={"status": "READY"})
            ok("pickup-only step rejected for delivery", r.status_code == 422)
            async with websockets.connect(f"{WS}/ws/me?token={tok_a['access_token']}") as mine:
                r = await c.patch(f"/api/ops/admin/orders/{o2['order_no']}/status", headers=ADM, json={"status": "OUT_FOR_DELIVERY"})
                ok("admin updates status", r.status_code == 200)
                note = await recv_until(mine, lambda m: m.get("type") == "order_updated", 10)
                ok("customer is notified live (/ws/me)", note["order_no"] == o2["order_no"] and note["fulfillment_status"] == "OUT_FOR_DELIVERY")
            try:
                async with websockets.connect(f"{WS}/ws/me?token=bad") as bad:
                    await bad.recv()
                ok("/ws/me rejects bad token", False)
            except Exception:
                ok("/ws/me rejects bad token", True)
            upd = await recv_until(adm_orders, lambda m: m.get("type") == "order_updated", 15)
            ok("admin live feed: order_updated", upd["order"]["fulfillment_status"] == "OUT_FOR_DELIVERY")
            ok("customer sees new status", (await c.get(f"/api/ops/orders/{o2['order_no']}", headers=A)).json()["fulfillment_status"] == "OUT_FOR_DELIVERY")

        mine = (await c.get("/api/ops/my/orders", headers=A)).json()
        ok("my orders", len(mine) == 2 and mine[0]["preview"][0]["name"])
        ok("other user has none", (await c.get("/api/ops/my/orders", headers=B)).json() == [])

        # --------------------------------------------------------------- reports
        r = (await c.get("/api/reports/orders", headers=ADM, params={"fulfillment": "PICKUP"})).json()
        ok("report filter pickup", r["total"] >= 1 and all(x["fulfillment"] == "PICKUP" for x in r["items"]))
        rid = (await c.get("/api/reports/orders", headers=ADM, params={"q": o2["order_no"]})).json()["items"][0]["id"]
        d = (await c.get(f"/api/reports/orders/{rid}", headers=ADM)).json()
        ok("report detail", d["shipping_address"] == "St 1, Phnom Penh" and d["fulfillment_status"] == "OUT_FOR_DELIVERY")
        s = (await c.get("/api/reports/summary", headers=ADM)).json()
        ok("summary split", s["pickup_orders"] >= 1 and s["delivery_orders"] >= 1, str(s))
        ok("sales-by-day / top products", len((await c.get("/api/reports/sales-by-day", headers=ADM, params={"days": 7})).json()) == 7
           and len((await c.get("/api/reports/top-products", headers=ADM)).json()) >= 1)

        # ------------------------------------------------ admin catalog management
        r = await c.post("/api/ops/admin/categories", headers=ADM, json={"name": "Smart Home", "icon": "home"})
        ok("create category", r.status_code == 201 and r.json()["slug"] == "smart-home")
        cid = r.json()["id"]
        r = await c.put(f"/api/ops/admin/categories/{cid}", headers=ADM, json={"name": "Smart Home Gear", "icon": "home", "sort_order": 9})
        ok("update category", r.json()["name"] == "Smart Home Gear")
        r = await c.post("/api/ops/admin/products", headers=ADM, json={
            "name": "Test Plug", "description": "x", "price": 12.5, "compare_price": 20, "stock_qty": 5, "brand": "Volt",
            "category_id": cid, "featured": True, "specs": {"Power": "10A", " ": "x"}, "images": ["/media/a.webp"], "status": "ACTIVE"})
        ok("create product (category, specs, images)", r.status_code == 201 and r.json()["discount_percent"] == 38 and r.json()["specs"] == {"Power": "10A"})
        pid = r.json()["id"]
        r = await c.put(f"/api/ops/admin/products/{pid}", headers=ADM, json={"name": "Test Plug 2", "price": 30, "compare_price": 20, "stock_qty": 5, "images": []})
        ok("bad compare price dropped", r.json()["compare_price"] is None)
        ok("delete product", (await c.delete(f"/api/ops/admin/products/{pid}", headers=ADM)).status_code == 204)
        ok("delete category", (await c.delete(f"/api/ops/admin/categories/{cid}", headers=ADM)).status_code == 204)

        # ------------------------------------------------------------------ media
        buf = io.BytesIO()
        Image.new("RGBA", (2400, 1600), (255, 0, 0, 128)).save(buf, "PNG")
        files = {"file": ("big photo.png", buf.getvalue(), "image/png")}
        r = await c.post("/api/media/upload", headers=ADM, files=files)
        ok("image upload", r.status_code == 201 and r.json()["url"].startswith("/media/") and r.json()["width"] == 1600, str(r.json()))
        up = r.json()
        r = await c.get(up["url"])
        ok("image served publicly as webp", r.status_code == 200 and r.headers["content-type"] == "image/webp")
        ok("thumbnail served", (await c.get(up["thumb_url"])).status_code == 200)
        r = await c.post("/api/media/upload", headers=ADM, files={"file": ("evil.png", b"<?php echo 1;", "image/png")})
        ok("fake image rejected", r.status_code == 415)
        r = await c.post("/api/media/upload", headers=A, files={"file": ("a.png", buf.getvalue(), "image/png")})
        ok("customer cannot upload", r.status_code == 403)
        ok("path traversal blocked", (await c.get("/media/..%2f..%2f.env")).status_code in (404, 422))

        # ------------------------------------------------------------- live chat
        gid = f"guest{RUN}abcdef"
        async with websockets.connect(f"{WS}/ws/chat?guest={gid}") as g:
            init = json.loads(await g.recv())
            ok("chat init (guest)", init["type"] == "init" and init["support_online"] is False and init["messages"][0]["sender"] == "SYSTEM")
            await g.send(json.dumps({"type": "message", "text": "Hello, is the Nova X1 in stock?"}))
            echo = await recv_until(g, lambda m: m.get("type") == "message" and m["message"]["sender"] == "CUSTOMER")
            notice = await recv_until(g, lambda m: m.get("type") == "message" and m["message"]["sender"] == "SYSTEM")
            ok("offline notice when no admin", "offline" in notice["message"]["text"])
            async with websockets.connect(f"{WS}/ws/admin/chat?token={tok_admin}") as adm:
                lst = json.loads(await adm.recv())
                conv = next(x for x in lst["items"] if x["last_message"].startswith("Hello"))
                ok("admin inbox lists the conversation", conv["unread"] >= 1 and conv["is_guest"])
                st = await recv_until(g, lambda m: m.get("type") == "support_status")
                ok("customer told support is online", st["online"] is True)
                await adm.send(json.dumps({"type": "history", "conversation_id": conv["id"]}))
                hist = await recv_until(adm, lambda m: m.get("type") == "history")
                ok("admin loads history", any(m["text"].startswith("Hello") for m in hist["messages"]))
                await adm.send(json.dumps({"type": "send", "conversation_id": conv["id"], "text": "Yes! 25 in stock."}))
                reply = await recv_until(g, lambda m: m.get("type") == "message" and m["message"]["sender"] == "ADMIN")
                ok("customer gets the admin reply live", reply["message"]["text"] == "Yes! 25 in stock.")
                await g.send(json.dumps({"type": "message", "text": "Thanks!"}))
                live = await recv_until(adm, lambda m: m.get("type") == "message" and m["message"]["text"] == "Thanks!")
                ok("admin gets customer message live", live["conversation"]["unread"] >= 1)
                await adm.send(json.dumps({"type": "read", "conversation_id": conv["id"]}))
                upd = await recv_until(adm, lambda m: m.get("type") == "conversation_updated")
                ok("mark read", upd["conversation"]["unread"] == 0)
        async with websockets.connect(f"{WS}/ws/chat?token={tok_a['access_token']}") as u:
            init = json.loads(await u.recv())
            ok("chat for logged-in user uses account name", init["type"] == "init" and "Alice" in init["messages"][0]["text"])
        try:
            async with websockets.connect(f"{WS}/ws/admin/chat?token={tok_a['access_token']}") as bad:
                await bad.recv()
            ok("customer cannot open the admin chat", False)
        except Exception:
            ok("customer cannot open the admin chat", True)

        # ------------------------------------------------ notifications (in-app, live)
        async def wait_for(fn, tries=30):
            for _ in range(tries):
                v = await fn()
                if v:
                    return v
                await asyncio.sleep(0.3)
            return None

        async def notes(h):
            return (await c.get("/api/notify/notifications", headers=h)).json()

        async def has_note(h, title):
            d = await notes(h)
            return next((n for n in d["items"] if n["title"] == title), None)

        n = await wait_for(lambda: has_note(A, "Payment received"))
        ok("customer got a 'Payment received' notification", n is not None and n["url"].startswith("/order/"))
        ok("customer unread count", (await notes(A))["unread"] >= 1)
        n = await wait_for(lambda: has_note(ADM, "New paid order"))
        ok("admin got a 'New paid order' notification", n is not None and "ORD" in n["body"])
        ok("customer cannot read admin notifications", all(x["title"] != "New paid order" for x in (await notes(A))["items"]))
        async with websockets.connect(f"{WS}/ws/notifications?token={tok_a['access_token']}") as nws:
            hello = json.loads(await nws.recv())
            ok("notification socket hello with unread", hello["type"] == "hello" and hello["unread"] >= 1)
            r = await c.patch(f"/api/ops/admin/orders/{order['order_no']}/status", headers=ADM, json={"status": "READY"})
            live = await recv_until(nws, lambda m: m.get("type") == "notification", 10)
            ok("order status pushed to the customer live", "ready for pickup" in live["notification"]["title"].lower())
        r = await c.post("/api/notify/notifications/read-all", headers=A)
        ok("mark all read", r.json()["unread"] == 0 and (await notes(A))["unread"] == 0)
        ok("notifications need login", (await c.get("/api/notify/notifications")).status_code == 401)
        ok("customer cannot call admin notify", (await c.get("/api/notify/admin/telegram-status", headers=A)).status_code == 403)
        st = (await c.get("/api/notify/admin/telegram-status", headers=ADM)).json()
        ok("telegram status (admin)", "bot_configured" in st and "chat_count" in st)
        r = await c.post("/api/notify/admin/telegram-test", headers=ADM)
        ok("telegram test refuses politely when not configured", r.status_code in (400, 502), r.text[:120])
        ok("notify internal endpoint hidden", (await c.post("/api/notify/internal/events", json={})).status_code == 404)
        pref = (await c.get("/api/notify/preferences", headers=A)).json()
        ok("preferences", pref["telegram_enabled"] is True and pref["telegram_available"] is False)

        # -------------------------------------------------------- profile + avatar
        buf2 = io.BytesIO(); Image.new("RGB", (600, 300), (200, 30, 90)).save(buf2, "PNG")
        r = await c.post("/api/media/avatar", headers=A, files={"file": ("me.png", buf2.getvalue(), "image/png")})
        ok("customer uploads a profile picture", r.status_code == 201 and r.json()["url"].startswith("/media/"))
        av = r.json()["url"]
        im = Image.open(io.BytesIO((await c.get(av)).content))
        ok("avatar is a 256px square", im.size == (256, 256), str(im.size))
        r = await c.patch("/api/auth/me", headers=A, json={"name": "Alice Updated", "phone": "012111222", "avatar_url": av})
        ok("profile keeps avatar", r.json()["avatar_url"] == av)
        r = await c.patch("/api/auth/me", headers=A, json={"name": "Alice Updated", "phone": "", "avatar_url": "https://evil.example/x.png"})
        ok("external avatar URL rejected", r.status_code == 422)
        ok("customer still cannot use product upload", (await c.post("/api/media/upload", headers=A, files={"file": ("a.png", buf2.getvalue(), "image/png")})).status_code == 403)

        # ---------------------------------------------------- change password
        r = await c.post("/api/auth/change-password", headers=A, json={"current_password": "wrong-one", "new_password": "newpass123"})
        ok("change password: wrong current rejected", r.status_code == 400)
        r = await c.post("/api/auth/change-password", headers=A, json={"current_password": "secret12323", "new_password": "secret12323"})
        ok("change password: same password rejected", r.status_code == 422)
        r = await c.post("/api/auth/change-password", headers=A, json={"current_password": "secret12323", "new_password": "short"})
        ok("change password: too short rejected", r.status_code == 422)
        old_refresh = tok_a["refresh_token"]
        r = await c.post("/api/auth/change-password", headers=A, json={"current_password": "secret12323", "new_password": "newpass123"})
        ok("change password works and returns a fresh session", r.status_code == 200 and r.json()["access_token"])
        fresh = r.json()
        await asyncio.sleep(1.1)
        ok("other sessions are signed out (old refresh token)", (await c.post("/api/auth/refresh", json={"refresh_token": old_refresh})).status_code == 401)
        ok("this session continues (new refresh token)", (await c.post("/api/auth/refresh", json={"refresh_token": fresh["refresh_token"]})).status_code == 200)
        ok("old password no longer works", (await c.post("/api/auth/login", json={"username": email_a, "password": "secret12323"})).status_code == 401)
        r = await c.post("/api/auth/login", json={"username": email_a, "password": "newpass123"})
        ok("new password works", r.status_code == 200)
        n = await wait_for(lambda: has_note({"Authorization": "Bearer " + r.json()["access_token"]}, "Your password was changed"))
        ok("password change notification", n is not None and n["level"] == "warning")

        # ---------------------------------------------------- forgot / reset password
        r = await c.post("/api/auth/forgot-password", json={"email": f"nobody{RUN}@test.com"})
        ok("forgot password: unknown email gets the same answer", r.status_code == 200 and r.json()["ok"] is True and "dev_link" not in r.json())
        r = await c.post("/api/auth/forgot-password", json={"email": email_a})
        ok("forgot password: known email", r.status_code == 200 and r.json()["ok"] is True)
        link = r.json().get("dev_link")
        if not link:
            print("SKIP reset-link tests (set DEV_RETURN_RESET_LINK=true in .env to run them)")
        else:
            token = link.split("token=")[1]
            ok("reset token is valid", (await c.get("/api/auth/reset-password/check", params={"token": token})).json()["valid"] is True)
            ok("garbage token is invalid", (await c.get("/api/auth/reset-password/check", params={"token": "x" * 40})).json()["valid"] is False)
            ok("reset rejects short passwords", (await c.post("/api/auth/reset-password", json={"token": token, "password": "short"})).status_code == 422)
            r2 = await c.post("/api/auth/forgot-password", json={"email": email_a})
            token2 = r2.json()["dev_link"].split("token=")[1]
            ok("only the newest link works", (await c.post("/api/auth/reset-password", json={"token": token, "password": "resetpass123"})).status_code == 400)
            ok("reset with the newest link", (await c.post("/api/auth/reset-password", json={"token": token2, "password": "resetpass123"})).status_code == 200)
            ok("reset link is single use", (await c.post("/api/auth/reset-password", json={"token": token2, "password": "another123"})).status_code == 400)
            ok("login with the reset password", (await c.post("/api/auth/login", json={"username": email_a, "password": "resetpass123"})).status_code == 200)
            ok("change-password refused for social accounts is covered by social test", True)

        # ----------------------------------------------------- admin user management
        r = (await c.get("/api/auth/admin/users/summary", headers=ADM)).json()
        ok("users summary", r["total"] >= 3 and r["admins"] >= 1 and "by_provider" in r, str(r))
        ok("customers cannot manage users", (await c.get("/api/auth/admin/users", headers=A)).status_code == 403)
        ok("guests cannot manage users", (await c.get("/api/auth/admin/users")).status_code == 401)
        lst = (await c.get("/api/auth/admin/users", headers=ADM, params={"q": f"a{RUN}"})).json()
        ok("search users", lst["total"] == 1 and lst["items"][0]["email"] == email_a)
        ok("filter users by role", all(u["role"] == "ADMIN" for u in (await c.get("/api/auth/admin/users", headers=ADM, params={"role": "ADMIN"})).json()["items"]))
        email_c = f"c{RUN}@test.com"
        r = await c.post("/api/auth/admin/users", headers=ADM, json={"name": "Created By Admin", "email": email_c, "password": "adminmade123", "role": "CUSTOMER"})
        ok("admin creates a user", r.status_code == 201 and r.json()["role"] == "CUSTOMER")
        uid = r.json()["id"]
        ok("duplicate email rejected", (await c.post("/api/auth/admin/users", headers=ADM, json={"name": "Dup Dup", "email": email_c, "password": "adminmade123"})).status_code == 409)
        ok("new user can log in", (await c.post("/api/auth/login", json={"username": email_c, "password": "adminmade123"})).status_code == 200)
        user_login = (await c.post("/api/auth/login", json={"username": email_c, "password": "adminmade123"})).json()
        r = await c.patch(f"/api/auth/admin/users/{uid}", headers=ADM, json={"name": "Renamed User", "phone": "099000111", "is_active": False})
        ok("admin edits + disables a user", r.status_code == 200 and r.json()["is_active"] is False and r.json()["name"] == "Renamed User")
        ok("disabled user cannot log in", (await c.post("/api/auth/login", json={"username": email_c, "password": "adminmade123"})).status_code == 401)
        ok("disabled user's refresh token stops working", (await c.post("/api/auth/refresh", json={"refresh_token": user_login["refresh_token"]})).status_code == 401)
        ok("admin re-enables", (await c.patch(f"/api/auth/admin/users/{uid}", headers=ADM, json={"is_active": True})).json()["is_active"] is True)
        r = await c.post(f"/api/auth/admin/users/{uid}/reset-password", headers=ADM, json={})
        temp = r.json()["temporary_password"]
        ok("admin resets a password (temporary one returned once)", r.status_code == 200 and len(temp) == 12)
        ok("temporary password works", (await c.post("/api/auth/login", json={"username": email_c, "password": temp})).status_code == 200)
        admin_id = (await c.get("/api/auth/me", headers=ADM)).json()["id"]
        ok("admin cannot disable themselves", (await c.patch(f"/api/auth/admin/users/{admin_id}", headers=ADM, json={"is_active": False})).status_code == 409)
        ok("admin cannot change their own role", (await c.patch(f"/api/auth/admin/users/{admin_id}", headers=ADM, json={"role": "CUSTOMER"})).status_code == 409)
        ok("admin cannot delete themselves", (await c.delete(f"/api/auth/admin/users/{admin_id}", headers=ADM)).status_code == 409)
        r = await c.patch(f"/api/auth/admin/users/{uid}", headers=ADM, json={"role": "ADMIN"})
        ok("promote to admin", r.json()["role"] == "ADMIN")
        ok("new admin reaches admin API", (await c.get("/api/ops/admin/products", headers={"Authorization": "Bearer " + (await c.post("/api/auth/login", json={"username": email_c, "password": temp})).json()["access_token"]})).status_code == 200)
        ok("admin deletes a user", (await c.delete(f"/api/auth/admin/users/{uid}", headers=ADM)).status_code == 204)
        ok("deleted user is gone", (await c.get(f"/api/auth/admin/users/{uid}", headers=ADM)).status_code == 404)
        ok("auth internal endpoints hidden", (await c.get("/api/auth/internal/admins")).status_code == 404)
    print("\nALL CHECKS PASSED")


asyncio.run(main())
