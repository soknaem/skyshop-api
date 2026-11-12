"""Fire-and-forget events to the notification service. A failure here must never break the real operation."""
import asyncio
import logging

import httpx

from .config import settings

log = logging.getLogger("notify-client")
_tasks: set[asyncio.Task] = set()


async def _post(payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.post(f"{settings.notify_url}/internal/events", json=payload,
                         headers={"X-Internal-Key": settings.internal_key})
    except Exception as e:
        log.debug("notify service not reachable: %s", e)


def fire(**payload) -> None:
    """notify.fire(audience="admins", type="order.paid", title="...", body="...", url="/admin/orders", telegram=True)"""
    try:
        t = asyncio.get_running_loop().create_task(_post(payload))
        _tasks.add(t)
        t.add_done_callback(_tasks.discard)
    except RuntimeError:
        pass
