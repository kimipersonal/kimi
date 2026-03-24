"""Company Memory — shared pgvector knowledge base per company.

Agents within the same company can store and query shared knowledge,
enabling collective learning. Uses the existing AgentMemory table
with company-scoped queries.
"""

import json
import logging
from uuid import uuid4

from sqlalchemy import text, select, delete, func

from app.db.database import async_session
from app.db.models import AgentMemory

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 768
MAX_MEMORIES_PER_COMPANY = 5000
DEFAULT_RECALL_LIMIT = 5
MIN_SIMILARITY_SCORE = 0.3


async def store_company_memory(
    company_id: str,
    agent_id: str,
    content: str,
    embedding: list[float],
    importance: float = 0.5,
    category: str = "shared",
    metadata: dict | None = None,
) -> str:
    """Store a shared memory entry for a company.

    Uses agent_id for attribution but scopes via metadata company_id.
    """
    memory_id = str(uuid4())
    meta = metadata or {}
    meta["company_id"] = company_id
    meta["shared"] = True

    async with async_session() as session:
        # Prune if over limit
        count_result = await session.execute(
            text(
                "SELECT COUNT(*) FROM agent_memories "
                "WHERE metadata->>'company_id' = :company_id AND metadata->>'shared' = 'true'"
            ),
            {"company_id": company_id},
        )
        count = count_result.scalar() or 0

        if count >= MAX_MEMORIES_PER_COMPANY:
            prune_count = count - MAX_MEMORIES_PER_COMPANY + 10
            await session.execute(
                text(
                    "DELETE FROM agent_memories WHERE id IN ("
                    "  SELECT id FROM agent_memories "
                    "  WHERE metadata->>'company_id' = :company_id AND metadata->>'shared' = 'true' "
                    "  ORDER BY importance ASC, created_at ASC LIMIT :prune"
                    ")"
                ),
                {"company_id": company_id, "prune": prune_count},
            )

        memory = AgentMemory(
            id=memory_id,
            agent_id=agent_id,
            content=content,
            embedding=embedding,
            importance=importance,
            category=category,
            metadata_=meta,
        )
        session.add(memory)
        await session.commit()

    logger.debug(f"Stored company memory {memory_id} for company {company_id}")
    return memory_id


async def recall_company_memories(
    company_id: str,
    query_embedding: list[float],
    limit: int = DEFAULT_RECALL_LIMIT,
    min_score: float = MIN_SIMILARITY_SCORE,
    category: str | None = None,
) -> list[dict]:
    """Semantic search across shared company memories."""
    async with async_session() as session:
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        filters = "metadata->>'company_id' = :company_id AND metadata->>'shared' = 'true'"
        params: dict = {
            "company_id": company_id,
            "limit": limit,
            "min_score": min_score,
            "embedding": embedding_str,
        }

        if category:
            filters += " AND category = :category"
            params["category"] = category

        query = text(f"""
            SELECT id, agent_id, content, category, importance, created_at,
                   1 - (embedding <=> CAST(:embedding AS vector)) AS score
            FROM agent_memories
            WHERE {filters}
              AND 1 - (embedding <=> CAST(:embedding AS vector)) >= :min_score
            ORDER BY score DESC
            LIMIT :limit
        """)

        result = await session.execute(query, params)
        rows = result.fetchall()

    return [
        {
            "id": row.id,
            "agent_id": row.agent_id,
            "content": row.content,
            "category": row.category,
            "importance": row.importance,
            "score": round(row.score, 4),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


async def get_company_memory_count(company_id: str) -> int:
    """Count shared memories for a company."""
    async with async_session() as session:
        result = await session.execute(
            text(
                "SELECT COUNT(*) FROM agent_memories "
                "WHERE metadata->>'company_id' = :company_id AND metadata->>'shared' = 'true'"
            ),
            {"company_id": company_id},
        )
        return result.scalar() or 0


async def clear_company_memories(company_id: str) -> int:
    """Delete all shared memories for a company."""
    async with async_session() as session:
        result = await session.execute(
            text(
                "DELETE FROM agent_memories "
                "WHERE metadata->>'company_id' = :company_id AND metadata->>'shared' = 'true'"
            ),
            {"company_id": company_id},
        )
        await session.commit()
        return result.rowcount or 0  # type: ignore[attr-defined]


def format_company_memories_for_prompt(memories: list[dict]) -> str:
    """Format recalled company memories for injection into agent prompts."""
    if not memories:
        return ""
    lines = ["[Shared company knowledge:]"]
    for m in memories:
        lines.append(f"- ({m['category']}, relevance {m['score']:.0%}, by agent {m['agent_id'][:8]}) {m['content']}")
    return "\n".join(lines)
