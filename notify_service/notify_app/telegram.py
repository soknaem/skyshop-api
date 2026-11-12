"""Sends messages with the Telegram Bot API. Never raises: a failed alert must not break the real operation."""
import html
import logging

import httpx

from .config import settings

log = logging.getLogger("notify.telegram")

ICONS = {"order": "🛒", "security": "🔐", "user": "👤", "stock": "📦", "chat": "💬", "admin": "🛠", "system": "⚙️", "test": "✅"}


def format_message(type_: str, title: str, body: str, url: str) -> str:
    icon = ICONS.get(type_.split(".")[0], "🔔")
    lines = [f"{icon} <b>{html.escape(title)}</b>"]
    if body:
        lines.append(html.escape(body))
    if url:
        lines.append(f'<a href="{html.escape(settings.site_url + url)}">Open</a>')
    return "\n".join(lines)


async def send(chat_id: str, text: str) -> tuple[bool, str]:
    if not settings.telegram_bot_token:
        return False, "TELEGRAM_BOT_TOKEN is not set"
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                             json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
        data = r.json()
        if data.get("ok"):
            return True, ""
        return False, str(data.get("description", "Telegram refused the message"))
    except Exception as e:
        log.warning("telegram send failed: %s", e)
        return False, "Could not reach Telegram"


async def send_to_admins(text: str) -> list[dict]:
    """Returns one result per configured chat id."""
    if not settings.telegram_alerts:
        return []
    results = []
    for cid in settings.alert_chat_ids:
        ok, err = await send(cid, text)
        results.append({"chat_id": cid, "ok": ok, "error": err})
    return results
