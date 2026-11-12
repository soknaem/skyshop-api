"""Tests which events become Telegram messages and who receives them. Telegram itself is replaced by a recorder.

    python scripts/test_telegram_alerts.py
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notify_service"))
os.environ.update({"TELEGRAM_BOT_TOKEN": "123:dummy", "TELEGRAM_ALERT_CHAT_IDS": "111, 222", "TELEGRAM_ALERTS": "true"})

import httpx  # noqa: E402
from notify_app import main as m, telegram  # noqa: E402

KEY = {"X-Internal-Key": os.environ.get("INTERNAL_KEY", "change-me-internal-key")}
sent: list[tuple[str, str]] = []


def ok(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        sys.exit(1)


async def main():
    async def fake_send(chat_id, text):
        sent.append((chat_id, text))
        return True, ""
    telegram.send = fake_send
    contacts = {7: {"telegram_chat_id": "777", "name": "Tele User"}, 8: {"telegram_chat_id": None, "name": "Email User"}}

    async def fake_contact(uid):
        return contacts.get(uid)
    m.contact = fake_contact

    async with m.app.router.lifespan_context(m.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://n", headers=KEY) as c:
            async def settle():
                await asyncio.sleep(0.2)

            r = await c.post("/internal/events", json={"audience": "none", "type": "security.admin_login", "title": "Admin <b>signed</b> in",
                                                        "body": "Sok & friends", "telegram": True, "store": False})
            await settle()
            ok("admin alert goes to every configured chat id", sorted(x[0] for x in sent) == ["111", "222"] and r.json()["stored"] == 0)
            ok("message is HTML-escaped (no injection)", "&lt;b&gt;signed&lt;/b&gt;" in sent[0][1] and "Sok &amp; friends" in sent[0][1] and sent[0][1].startswith("🔐"))
            sent.clear()

            await c.post("/internal/events", json={"audience": "none", "type": "order.paid", "title": "New paid order", "telegram": False, "store": False})
            await settle()
            ok("telegram=false sends nothing", sent == [])

            await c.post("/internal/events", json={"audience": "user", "user_id": 7, "type": "order.paid", "title": "Payment received", "body": "ORD1", "url": "/order/ORD1", "telegram": True, "store": False})
            await settle()
            ok("customer who signed in with Telegram gets a DM", [x[0] for x in sent] == ["777"] and "/order/ORD1" in sent[0][1])
            sent.clear()

            await c.post("/internal/events", json={"audience": "user", "user_id": 8, "type": "order.paid", "title": "Payment received", "telegram": True, "store": False})
            await settle()
            ok("customer without Telegram gets no DM", sent == [])

            await c.put("/preferences", json={"telegram_enabled": False}, headers={**KEY, "X-User-Id": "7"})
            await c.post("/internal/events", json={"audience": "user", "user_id": 7, "type": "order.status", "title": "Ready", "telegram": True, "store": False})
            await settle()
            ok("customer can switch Telegram messages off", sent == [])

            telegram.settings.telegram_alerts = False
            await c.post("/internal/events", json={"audience": "none", "type": "stock.low", "title": "Low stock", "telegram": True, "store": False})
            await settle()
            ok("TELEGRAM_ALERTS=false silences owner alerts", sent == [])
    print("\nTELEGRAM ROUTING OK")


asyncio.run(main())
