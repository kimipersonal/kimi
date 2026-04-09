"""Knowledge Base Builder — CEO-managed shared knowledge base using vector memory.

The CEO creates and maintains a shared knowledge base that all agents can query.
Knowledge entries are stored as vector embeddings in PostgreSQL (pgvector) for
semantic search. Categories include: lessons_learned, best_practices, market_insights,
operational_rules, and custom categories.
"""

import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text, select, func, delete

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 768
MAX_KB_ENTRIES = 10_000
DEFAULT_SEARCH_LIMIT = 10
MIN_SIMILARITY = 0.3

# Standard knowledge categories
KB_CATEGORIES = [
    "lessons_learned",
    "best_practices",
    "market_insights",
    "operational_rules",
    "trading_strategies",
    "error_solutions",
    "agent_guidelines",
]


class KnowledgeBase:
    """CEO-managed shared knowledge base with semantic search.

    Uses the AgentMemory table with a special agent_id='knowledge_base' and
    metadata tagging for categorization. All agents can query it.
    """

    KB_AGENT_ID = "knowledge_base"  # virtual agent_id for KB entries

    async def add_entry(
        self,
        content: str,
        category: str = "general",
        tags: list[str] | None = None,
        importance: float = 0.7,
        source: str = "ceo",
    ) -> dict:
        """Add a knowledge entry to the shared knowledge base.

        Args:
            content: The knowledge text.
            category: One of KB_CATEGORIES or custom.
            tags: Optional list of search tags.
            importance: 0.0-1.0 importance weight.
            source: Who added this (agent_id or "ceo").

        Returns:
            dict with entry_id and status.
        """
        try:
            from app.services.memory_service import get_embedding
        except ImportError:
            get_embedding = None

        entry_id = str(uuid4())
        metadata = {
            "type": "knowledge_base",
            "category": category,
            "tags": tags or [],
            "source": source,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }

        # Generate embedding
        embedding = None
        if get_embedding:
            try:
                embedding = await get_embedding(content)
            except Exception as e:
                logging.debug(f"Could not generate embedding: {e}")

        if embedding is None:
            embedding = [0.0] * EMBEDDING_DIM

        try:
            from app.db.database import async_session
            from app.db.models import AgentMemory

            async with async_session() as session:
                # Check count and prune if needed
                count_result = await session.execute(
                    select(func.count()).where(AgentMemory.agent_id == self.KB_AGENT_ID)
                )
                count = count_result.scalar() or 0

                if count >= MAX_KB_ENTRIES:
                    prune_count = count - MAX_KB_ENTRIES + 10
                    subq = (
                        select(AgentMemory.id)
                        .where(AgentMemory.agent_id == self.KB_AGENT_ID)
                        .order_by(AgentMemory.importance.asc(), AgentMemory.created_at.asc())
                        .limit(prune_count)
                    )
                    await session.execute(
                        delete(AgentMemory).where(AgentMemory.id.in_(subq))
                    )

                memory = AgentMemory(
                    id=entry_id,
                    agent_id=self.KB_AGENT_ID,
                    content=content,
                    embedding=embedding,
                    importance=importance,
                    category=category,
                    metadata_=metadata,
                )
                session.add(memory)
                await session.commit()

            return {"success": True, "entry_id": entry_id, "category": category}

        except Exception as e:
            logger.error(f"Failed to add KB entry: {e}")
            return {"success": False, "error": str(e)}

    async def search(
        self,
        query: str,
        category: str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> list[dict]:
        """Semantic search the knowledge base.

        Args:
            query: Natural language search query.
            category: Optional category filter.
            limit: Max results.

        Returns:
            List of matching entries with relevance scores.
        """
        # Generate query embedding
        try:
            from app.services.memory_service import get_embedding
            query_embedding = await get_embedding(query)
        except Exception:
            return []

        if not query_embedding:
            return []

        try:
            from app.db.database import async_session

            embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

            filters = "agent_id = :kb_id"
            params: dict = {
                "kb_id": self.KB_AGENT_ID,
                "limit": limit,
                "min_score": MIN_SIMILARITY,
                "embedding": embedding_str,
            }

            if category:
                filters += " AND category = :category"
                params["category"] = category

            query_sql = text(f"""
                SELECT id, content, category, importance, created_at, metadata AS meta,
                       1 - (embedding <=> CAST(:embedding AS vector)) AS score
                FROM agent_memories
                WHERE {filters}
                  AND 1 - (embedding <=> CAST(:embedding AS vector)) >= :min_score
                ORDER BY score DESC
                LIMIT :limit
            """)

            async with async_session() as session:
                result = await session.execute(query_sql, params)
                rows = result.fetchall()

            return [
                {
                    "id": row.id,
                    "content": row.content,
                    "category": row.category,
                    "importance": row.importance,
                    "score": round(row.score, 4),
                    "tags": (row.meta or {}).get("tags", []),
                    "source": (row.meta or {}).get("source", "unknown"),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]

        except Exception as e:
            logger.error(f"KB search failed: {e}")
            return []

    async def get_stats(self) -> dict:
        """Get knowledge base statistics."""
        try:
            from app.db.database import async_session
            from app.db.models import AgentMemory

            async with async_session() as session:
                total = await session.execute(
                    select(func.count()).where(AgentMemory.agent_id == self.KB_AGENT_ID)
                )
                total_count = total.scalar() or 0

                # Count by category
                cat_query = await session.execute(
                    text(
                        "SELECT category, COUNT(*) as cnt FROM agent_memories "
                        "WHERE agent_id = :kb_id GROUP BY category ORDER BY cnt DESC"
                    ),
                    {"kb_id": self.KB_AGENT_ID},
                )
                categories = {row[0]: row[1] for row in cat_query.fetchall()}

            return {
                "total_entries": total_count,
                "categories": categories,
                "max_capacity": MAX_KB_ENTRIES,
                "available_categories": KB_CATEGORIES,
            }

        except Exception as e:
            logger.error(f"KB stats failed: {e}")
            return {"total_entries": 0, "categories": {}, "error": str(e)}

    async def delete_entry(self, entry_id: str) -> bool:
        """Delete a knowledge base entry by ID."""
        try:
            from app.db.database import async_session
            from app.db.models import AgentMemory

            async with async_session() as session:
                result = await session.execute(
                    delete(AgentMemory).where(
                        AgentMemory.id == entry_id,
                        AgentMemory.agent_id == self.KB_AGENT_ID,
                    )
                )
                await session.commit()
                return result.rowcount > 0

        except Exception as e:
            logger.error(f"KB delete failed: {e}")
            return False


# Singleton
knowledge_base = KnowledgeBase()
