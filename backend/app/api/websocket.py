"""WebSocket hub — real-time event streaming to dashboard clients."""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    """Main dashboard WebSocket — streams all agent events."""
    await websocket.accept()
    queue = event_bus.subscribe("dashboard")
    logger.info("Dashboard WebSocket client connected")

    try:
        # Send recent history so new clients get current state
        for msg in event_bus.history[-50:]:
            await websocket.send_json(msg)

        send_task = asyncio.create_task(_send_events(websocket, queue))
        recv_task = asyncio.create_task(_receive_commands(websocket))

        done, pending = await asyncio.wait(
            {send_task, recv_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        logger.info("Dashboard WebSocket client disconnected")
    finally:
        event_bus.unsubscribe("dashboard", queue)


async def _send_events(websocket: WebSocket, queue: asyncio.Queue):
    while True:
        message = await queue.get()
        await websocket.send_json(message)


async def _receive_commands(websocket: WebSocket):
    while True:
        data = await websocket.receive_json()
        command = data.get("command")
        if command:
            await event_bus.broadcast("dashboard_command", data)
            logger.info(f"Dashboard command received: {command}")


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

        while True:
            message = await queue.get()
            if message.get("agent_id") == agent_id or message.get("agent_id") is None:
                await websocket.send_json(message)
    except WebSocketDisconnect:
        logger.info(f"Agent WebSocket disconnected for {agent_id}")
    finally:
        event_bus.unsubscribe(f"agent_{agent_id}", queue)
