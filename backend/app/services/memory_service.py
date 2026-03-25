"""Memory Service — Persistent vector memory for agents using pgvector.

Stores agent memories as text + embeddings in PostgreSQL with pgvector.
Supports semantic search via cosine similarity for auto-recall during agent execution.
"""

import logging
from uuid import uuid4

from sqlalchemy import text, select, delete, func

from app.db.database import async_session
from app.db.models import AgentMemory

logger = logging.getLogger(__name__)

# Embedding dimension (Vertex AI text-embedding-005 uses 768)
EMBEDDING_DIM = 768
MAX_MEMORIES_PER_AGENT = 1000
DEFAULT_RECALL_LIMIT = 5
MIN_SIMILARITY_SCORE = 0.3


async def init_pgvector():
    """Ensure pgvector extension is enabled (call once at startup)."""
    async with async_session() as session:
        await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await session.commit()
    logger.info("pgvector extension enabled")


async def store_memory(
    agent_id: str,
    content: str,
    embedding: list[float],
    importance: float = 0.5,
    category: str = "general",
    metadata: dict | None = None,
) -> str:
    """Store a memory entry with its embedding vector.

    Returns the memory ID.
    """

    memory_id = str(uuid4())

    async with async_session() as session:
        # Check count and prune if over limit
        count_result = await session.execute(
            select(func.count()).where(AgentMemory.agent_id == agent_id)
        )
        count = count_result.scalar() or 0

        if count >= MAX_MEMORIES_PER_AGENT:
            # Delete lowest importance memories to make room
            prune_count = count - MAX_MEMORIES_PER_AGENT + 1
            subq = (
                select(AgentMemory.id)
                .where(AgentMemory.agent_id == agent_id)
                .order_by(AgentMemory.importance.asc(), AgentMemory.created_at.asc())
                .limit(prune_count)
            )
            await session.execute(
                delete(AgentMemory).where(AgentMemory.id.in_(subq))
            )
            logger.info(f"Pruned {prune_count} low-importance memories for agent {agent_id}")

        memory = AgentMemory(
            id=memory_id,
            agent_id=agent_id,
            content=content,
            embedding=embedding,
            importance=importance,
            category=category,
            metadata_=metadata or {},
        )
        session.add(memory)
        await session.commit()

    logger.debug(f"Stored memory {memory_id} for agent {agent_id} (category={category})")
    return memory_id


async def recall_memories(
    agent_id: str,
    query_embedding: list[float],
    limit: int = DEFAULT_RECALL_LIMIT,
    min_score: float = MIN_SIMILARITY_SCORE,
    category: str | None = None,
) -> list[dict]:
    """Search memories by semantic similarity using cosine distance.

    Returns list of dicts with id, content, score, category, created_at.
    """
    async with async_session() as session:
        # pgvector cosine distance: 1 - (a <=> b) gives similarity score
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        filters = "agent_id = :agent_id"
        params: dict = {"agent_id": agent_id, "limit": limit, "min_score": min_score}

        if category:
            filters += " AND category = :category"
            params["category"] = category

        query = text(f"""
            SELECT id, content, category, importance, created_at,
                   1 - (embedding <=> CAST(:embedding AS vector)) AS score
            FROM agent_memories
            WHERE {filters}
              AND 1 - (embedding <=> CAST(:embedding AS vector)) >= :min_score
            ORDER BY score DESC
            LIMIT :limit
        """)
        params["embedding"] = embedding_str

        result = await session.execute(query, params)
        rows = result.fetchall()

    return [
        {
            "id": row.id,
            "content": row.content,
            "category": row.category,
            "importance": row.importance,
            "score": round(row.score, 4),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


async def delete_memory(memory_id: str) -> bool:
    """Delete a specific memory by ID."""
    async with async_session() as session:
        result = await session.execute(
            delete(AgentMemory).where(AgentMemory.id == memory_id)
        )
        await session.commit()
        return result.rowcount > 0


async def get_agent_memory_count(agent_id: str) -> int:
    """Get the number of memories stored for an agent."""
    async with async_session() as session:
        result = await session.execute(
            select(func.count()).where(AgentMemory.agent_id == agent_id)
        )
        return result.scalar() or 0


async def clear_agent_memories(agent_id: str) -> int:
    """Delete all memories for an agent. Returns count deleted."""
    async with async_session() as session:
        result = await session.execute(
            delete(AgentMemory).where(AgentMemory.agent_id == agent_id)
        )
        await session.commit()
        return result.rowcount


def format_memories_for_prompt(memories: list[dict]) -> str:
    """Format recalled memories as a text block to inject into the system prompt."""
    if not memories:
        return ""
    lines = ["[Relevant memories from past interactions:]"]
    for m in memories:
        lines.append(f"- ({m['category']}, relevance {m['score']:.0%}) {m['content']}")
    return "\n".join(lines)
