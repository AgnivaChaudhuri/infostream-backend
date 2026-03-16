"""In-memory event broker used by SSE subscribers.

The broker intentionally stays process-local for the prototype. It can be
replaced later with a durable/pub-sub transport without changing route
contracts.
"""

from __future__ import annotations

import asyncio
from typing import Any


class EventBroker:
    """Simple pub-sub broker for backend lifecycle notifications."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Register a subscriber queue and return it to the caller."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a subscriber queue."""
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish(self, event: dict[str, Any]) -> None:
        """Publish one event to all subscribers with drop-oldest backpressure."""
        async with self._lock:
            subscribers = list(self._subscribers)

        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # If queue is still full after drop-oldest, skip this subscriber.
                continue
