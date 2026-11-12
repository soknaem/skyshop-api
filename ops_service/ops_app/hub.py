"""In-process pub/sub: the payment watcher publishes, WebSocket handlers listen."""
import asyncio
from collections import defaultdict


class Hub:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, key: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[key].add(q)
        return q

    def unsubscribe(self, key: str, q: asyncio.Queue) -> None:
        self._subs[key].discard(q)
        if not self._subs[key]:
            self._subs.pop(key, None)

    def publish(self, key: str, message: dict) -> None:
        for q in list(self._subs.get(key, ())):
            q.put_nowait(message)


payment_hub = Hub()


# live updates for ONE logged-in customer (key = user id), e.g. "your order is ready for pickup"
user_hub = Hub()
