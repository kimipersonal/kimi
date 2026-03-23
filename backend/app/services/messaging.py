"""Inter-agent messaging — structured message passing stored in DB."""

import logging
from uuid import uuid4
from datetime import datetime, timezone

from sqlalchemy import select, desc

from app.db.database import async_session
from app.db.models import Message
from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)


async def send_message(
    from_agent_id: str | None,
    to_agent_id: str | None,
    content: str,
    message_type: str = "chat",
    metadata: dict | None = None,
) -> dict:
    """Send a message between agents (or from/to owner).

    from_agent_id=None means Owner → agent.
    to_agent_id=None means agent → Owner.
    """
    msg_id = str(uuid4())

    async with async_session() as session:
        msg = Message(
            id=msg_id,
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            content=content,
            message_type=message_type,
            metadata_=metadata,
        )
        session.add(msg)
        await session.commit()

    result = {
        "id": msg_id,
        "from_agent_id": from_agent_id,
        "to_agent_id": to_agent_id,
        "content": content,
        "message_type": message_type,
        "metadata": metadata,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    await event_bus.broadcast("message", result, agent_id=from_agent_id or to_agent_id)
    return result


async def get_messages(
    agent_id: str | None = None,
    message_type: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Get messages, optionally filtered by agent or type."""
    async with async_session() as session:
        query = select(Message).order_by(desc(Message.created_at)).limit(limit)

        if agent_id:
            query = query.where(
                (Message.from_agent_id == agent_id) | (Message.to_agent_id == agent_id)
            )
        if message_type:
            query = query.where(Message.message_type == message_type)

        result = await session.execute(query)
        messages = result.scalars().all()

        return [
            {
                "id": m.id,
                "from_agent_id": m.from_agent_id,
                "to_agent_id": m.to_agent_id,
                "content": m.content,
                "message_type": m.message_type,
                "metadata": m.metadata_,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in reversed(messages)  # Oldest first
        ]


async def get_conversation(
    agent1_id: str, agent2_id: str, limit: int = 50
) -> list[dict]:
    """Get the conversation between two specific agents."""
    async with async_session() as session:
        query = (
            select(Message)
            .where(
                (
                    (Message.from_agent_id == agent1_id)
                    & (Message.to_agent_id == agent2_id)
                )
                | (
                    (Message.from_agent_id == agent2_id)
                    & (Message.to_agent_id == agent1_id)
                )
            )
            .order_by(desc(Message.created_at))
            .limit(limit)
        )
        result = await session.execute(query)
        messages = result.scalars().all()

        return [
            {
                "id": m.id,
                "from_agent_id": m.from_agent_id,
                "to_agent_id": m.to_agent_id,
                "content": m.content,
                "message_type": m.message_type,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in reversed(messages)
        ]
