"""Event bus — pub/sub system for real-time WebSocket events."""

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    """Async pub/sub event bus with history for broadcasting agent events."""

    def __init__(self, history_size: int = 500):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._history: deque[dict] = deque(maxlen=history_size)

    @property
    def history(self) -> list[dict]:
        return list(self._history)

    def subscribe(self, channel: str = "dashboard") -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        if channel not in self._subscribers:
            self._subscribers[channel] = []
        self._subscribers[channel].append(queue)
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue):
        if channel in self._subscribers:
            self._subscribers[channel] = [
                q for q in self._subscribers[channel] if q is not queue
            ]

    async def publish(
        self,
        channel: str,
        event: str,
        data: dict[str, Any],
        agent_id: str | None = None,
    ):
        message = {
            "event": event,
            "data": data,
            "agent_id": agent_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        # Store in history for late-joining clients
        if channel == "dashboard":
            self._history.append(message)

        if channel not in self._subscribers:
            return
        dead_queues = []
        for queue in self._subscribers[channel]:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                dead_queues.append(queue)
                logger.warning(f"Dropping slow subscriber on channel '{channel}'")
        for dq in dead_queues:
            self._subscribers[channel].remove(dq)

    async def broadcast(
        self, event: str, data: dict[str, Any], agent_id: str | None = None
    ):
        await self.publish("dashboard", event, data, agent_id)


# Global singleton
event_bus = EventBus()
