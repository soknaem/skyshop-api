"""Telegram login WITHOUT the website widget (the widget needs a public domain, this works on localhost).

Flow: website asks /auth/telegram/start -> gets a one-time token and a link  t.me/<bot>?start=<token>.
The customer opens the link and presses START. This service long-polls the Telegram Bot API, sees
"/start <token>", remembers who sent it, and the website (polling /auth/telegram/status) gets logged in.
"""
import asyncio
import logging
import secrets
import time

import httpx

from .config import settings

log = logging.getLogger("auth.telegram")
TTL = 300  # seconds a login link stays valid
PENDING: dict[str, dict] = {}


def start_login() -> tuple[str, str]:
    now = time.time()
    for k in [k for k, v in PENDING.items() if now - v["created"] > TTL]:
        PENDING.pop(k, None)
    token = secrets.token_urlsafe(16)
    PENDING[token] = {"created": now, "user": None}
    return token, f"https://t.me/{settings.telegram_bot_username}?start={token}"


def status(token: str) -> tuple[str, dict | None]:
    item = PENDING.get(token)
    if not item or time.time() - item["created"] > TTL:
        PENDING.pop(token, None)
        return "expired", None
    if item["user"]:
        return "confirmed", PENDING.pop(token)["user"]
    return "pending", None


async def _send(client: httpx.AsyncClient, chat_id: int, text: str) -> None:
    try:
        await client.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                          json={"chat_id": chat_id, "text": text})
    except Exception:
        pass


async def handle_update(update: dict, client: httpx.AsyncClient | None = None) -> None:
    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    sender = msg.get("from") or {}
    if not sender.get("id"):
        return
    chat_id = (msg.get("chat") or {}).get("id", sender["id"])
    if text.startswith("/id"):
        # lets the shop owner find the chat id to put in TELEGRAM_ALERT_CHAT_IDS
        if client:
            await _send(client, chat_id, f"Your Telegram chat id is: {chat_id}")
        return
    if not text.startswith("/start"):
        return
    parts = text.split(maxsplit=1)
    token = parts[1].strip() if len(parts) > 1 else ""
    if not token:
        if client:
            await _send(client, sender["id"], "Hi! Use the Telegram button on the website to sign in. Send /id to get your chat id for shop alerts.")
        return
    item = PENDING.get(token)
    if not item or time.time() - item["created"] > TTL:
        if client:
            await _send(client, sender["id"], "This login link has expired. Go back to the website and try again.")
        return
    name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x) or sender.get("username", "")
    item["user"] = {"provider_id": str(sender["id"]), "name": name, "email": "", "avatar": ""}
    if client:
        await _send(client, sender["id"], "You are logged in. Return to the website. You can close this chat.")


async def poller() -> None:
    """Long-polls Telegram. Needs internet access to api.telegram.org."""
    base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
    offset = 0
    async with httpx.AsyncClient(timeout=40) as client:
        while True:
            try:
                r = await client.get(f"{base}/getUpdates", params={"timeout": 25, "offset": offset,
                                                                    "allowed_updates": '["message"]'})
                data = r.json()
                if not data.get("ok"):
                    log.warning("Telegram error: %s", data.get("description"))
                    await asyncio.sleep(10)
                    continue
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1
                    await handle_update(upd, client)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Telegram polling problem: %s", e)
                await asyncio.sleep(5)
