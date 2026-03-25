"""WebSocket hub — real-time event streaming to dashboard clients.

Features:
- Heartbeat/keepalive (30s ping)
- Event type filtering via subscription commands
- Typing indicators when CEO is processing
"""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)
router = APIRouter()

_HEARTBEAT_INTERVAL = 30  # seconds


@router.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    """Main dashboard WebSocket — streams all agent events."""
    await websocket.accept()
    queue = event_bus.subscribe("dashboard")
    logger.info("Dashboard WebSocket client connected")

    # Optional event type filter set by the client
    event_filter: set[str] = set()

    try:
        # Send recent history so new clients get current state
        for msg in event_bus.history[-50:]:
            await websocket.send_json(msg)

        send_task = asyncio.create_task(_send_events(websocket, queue, event_filter))
        recv_task = asyncio.create_task(
            _receive_commands_with_filter(websocket, event_filter)
        )
        heartbeat_task = asyncio.create_task(_heartbeat(websocket))

        done, pending = await asyncio.wait(
            {send_task, recv_task, heartbeat_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        logger.info("Dashboard WebSocket client disconnected")
    finally:
        event_bus.unsubscribe("dashboard", queue)


async def _send_events(
    websocket: WebSocket, queue: asyncio.Queue, event_filter: set[str]
):
    while True:
        message = await queue.get()
        # Apply event type filter if set
        if event_filter and message.get("event") not in event_filter:
            continue
        await websocket.send_json(message)


async def _receive_commands_with_filter(
    websocket: WebSocket, event_filter: set[str]
):
    while True:
        data = await websocket.receive_json()
        command = data.get("command")
        if command == "subscribe_events":
            # Client can send {"command": "subscribe_events", "events": ["log", "agent_state_change"]}
            events = data.get("events", [])
            event_filter.clear()
            event_filter.update(events)
            await websocket.send_json(
                {"event": "filter_set", "events": list(event_filter)}
            )
        elif command == "unsubscribe_events":
            event_filter.clear()
            await websocket.send_json({"event": "filter_cleared"})
        elif command:
            await event_bus.broadcast("dashboard_command", data)
            logger.info(f"Dashboard command received: {command}")


async def _heartbeat(websocket: WebSocket):
    """Send periodic heartbeat pings to keep connection alive."""
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
        try:
            await websocket.send_json(
                {
                    "event": "heartbeat",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
        except Exception:
            break


@router.websocket("/ws/agent/{agent_id}")
async def agent_ws(websocket: WebSocket, agent_id: str):
    """Per-agent WebSocket — streams only events for a specific agent."""
    await websocket.accept()
    queue = event_bus.subscribe(f"agent_{agent_id}")
    logger.info(f"Agent WebSocket connected for {agent_id}")

    try:
        # Send recent history for this agent
        for msg in event_bus.history:
            if msg.get("agent_id") == agent_id:
                await websocket.send_json(msg)

        send_task = asyncio.create_task(_send_agent_events(websocket, queue, agent_id))
        heartbeat_task = asyncio.create_task(_heartbeat(websocket))

        done, pending = await asyncio.wait(
            {send_task, heartbeat_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        logger.info(f"Agent WebSocket disconnected for {agent_id}")
    finally:
        event_bus.unsubscribe(f"agent_{agent_id}", queue)


async def _send_agent_events(
    websocket: WebSocket, queue: asyncio.Queue, agent_id: str
):
    while True:
        message = await queue.get()
        if message.get("agent_id") == agent_id or message.get("agent_id") is None:
            await websocket.send_json(message)
