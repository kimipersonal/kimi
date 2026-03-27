"""Trade Journal — record and learn from trade outcomes.

Stores trade results with context (entry reasoning, market conditions,
outcome) in the AgentMemory vector table. Enables the CEO to query
"what worked?" and "what didn't?" using semantic search, building
institutional trading knowledge.
"""

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text, select, desc

logger = logging.getLogger(__name__)

JOURNAL_CATEGORY = "trade_journal"
MAX_JOURNAL_ENTRIES = 10_000


class TradeJournal:
    """Record trade outcomes and enable semantic search over trade history."""

    async def record_trade(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_price: float,
        exit_price: float | None = None,
        pnl: float | None = None,
        size: float = 0,
        reasoning: str = "",
        market_conditions: str = "",
        lessons: str = "",
        outcome: str = "open",  # "win", "loss", "breakeven", "open"
        agent_id: str = "ceo",
        company_id: str | None = None,
        signal_confidence: float = 0.0,
        metadata: dict | None = None,
    ) -> dict:
        """Record a trade entry in the journal with vector embedding.

        The entry is stored in AgentMemory with category='trade_journal'
        and rich metadata for filtering.
        """
        # Build the journal content text for embedding
        content_parts = [
            f"Trade {trade_id}: {direction.upper()} {symbol} at {entry_price}",
        ]
        if exit_price is not None:
            content_parts.append(f"Exit: {exit_price}")
        if pnl is not None:
            content_parts.append(f"PnL: {pnl:+.2f}")
        content_parts.append(f"Outcome: {outcome}")
        if reasoning:
            content_parts.append(f"Reasoning: {reasoning}")
        if market_conditions:
            content_parts.append(f"Market: {market_conditions}")
        if lessons:
            content_parts.append(f"Lessons: {lessons}")

        content = ". ".join(content_parts)

        # Generate embedding
        embedding = await self._get_embedding(content)
        if embedding is None:
            # Fall back to storing without embedding (no semantic search)
            logger.warning("Could not generate embedding for journal entry")
            embedding = [0.0] * 768

        # Determine importance based on outcome and PnL
        importance = 0.5
        if outcome == "win" and pnl and pnl > 0:
            importance = min(0.9, 0.5 + (pnl / 1000))  # Scale by profit
        elif outcome == "loss" and pnl and pnl < 0:
            importance = min(0.9, 0.5 + (abs(pnl) / 500))  # Losses are more important to learn from

        meta = metadata or {}
        meta.update({
            "trade_id": trade_id,
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl": pnl,
            "size": size,
            "outcome": outcome,
            "signal_confidence": signal_confidence,
            "company_id": company_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })

        memory_id = str(uuid4())
        try:
            from app.db.database import async_session
            from app.db.models import AgentMemory

            async with async_session() as session:
                memory = AgentMemory(
                    id=memory_id,
                    agent_id=agent_id,
                    content=content,
                    embedding=embedding,
                    importance=importance,
                    category=JOURNAL_CATEGORY,
                    metadata_=meta,
                )
                session.add(memory)
                await session.commit()

            logger.info(f"Journal entry recorded: {trade_id} {symbol} {outcome}")
        except Exception as e:
            logger.error(f"Failed to record journal entry: {e}")
            return {"error": str(e)[:200]}

        return {
            "id": memory_id,
            "trade_id": trade_id,
            "symbol": symbol,
            "outcome": outcome,
            "importance": round(importance, 2),
            "recorded": True,
        }

    async def query_journal(
        self,
        query: str,
        limit: int = 10,
        outcome_filter: str | None = None,
        symbol_filter: str | None = None,
    ) -> list[dict]:
        """Semantic search over the trade journal.

        Args:
            query: Natural language query (e.g., "what forex trades worked best?")
            limit: Max results
            outcome_filter: Filter by outcome ("win", "loss", "breakeven")
            symbol_filter: Filter by symbol
        """
        embedding = await self._get_embedding(query)
        if embedding is None:
            return []

        try:
            from app.db.database import async_session

            async with async_session() as session:
                embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

                filters = "category = 'trade_journal'"
                params: dict = {
                    "embedding": embedding_str,
                    "limit": limit,
                }

                if outcome_filter:
                    filters += " AND metadata->>'outcome' = :outcome"
                    params["outcome"] = outcome_filter
                if symbol_filter:
                    filters += " AND metadata->>'symbol' = :symbol"
                    params["symbol"] = symbol_filter.upper()

                sql = text(f"""
                    SELECT id, agent_id, content, importance, created_at, metadata,
                           1 - (embedding <=> CAST(:embedding AS vector)) AS score
                    FROM agent_memories
                    WHERE {filters}
                      AND 1 - (embedding <=> CAST(:embedding AS vector)) >= 0.2
                    ORDER BY score DESC
                    LIMIT :limit
                """)

                result = await session.execute(sql, params)
                rows = result.fetchall()

            return [
                {
                    "id": row.id,
                    "content": row.content,
                    "importance": row.importance,
                    "score": round(row.score, 4),
                    "metadata": row.metadata or {},
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Journal query failed: {e}")
            return []

    async def get_trade_stats(
        self,
        symbol: str | None = None,
        days: int = 30,
    ) -> dict:
        """Get aggregate statistics from the trade journal."""
        try:
            from app.db.database import async_session

            async with async_session() as session:
                filters = "category = 'trade_journal'"
                params: dict = {"days": days}

                if symbol:
                    filters += " AND metadata->>'symbol' = :symbol"
                    params["symbol"] = symbol.upper()

                sql = text(f"""
                    SELECT
                        metadata->>'outcome' AS outcome,
                        metadata->>'symbol' AS symbol,
                        metadata->>'direction' AS direction,
                        CAST(metadata->>'pnl' AS FLOAT) AS pnl,
                        CAST(metadata->>'signal_confidence' AS FLOAT) AS confidence,
                        created_at
                    FROM agent_memories
                    WHERE {filters}
                      AND created_at >= NOW() - INTERVAL ':days days'
                    ORDER BY created_at DESC
                """.replace(":days days", f"{int(days)} days"))

                result = await session.execute(sql, params)
                rows = result.fetchall()

            if not rows:
                return {"total_trades": 0, "message": "No journal entries found"}

            wins = [r for r in rows if r.outcome == "win"]
            losses = [r for r in rows if r.outcome == "loss"]
            total_pnl = sum(r.pnl or 0 for r in rows)
            avg_win = sum(r.pnl or 0 for r in wins) / len(wins) if wins else 0
            avg_loss = sum(r.pnl or 0 for r in losses) / len(losses) if losses else 0

            # Win rate by confidence bands
            high_conf = [r for r in rows if (r.confidence or 0) >= 0.85]
            high_conf_wins = [r for r in high_conf if r.outcome == "win"]

            return {
                "total_trades": len(rows),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(rows) * 100, 1) if rows else 0,
                "total_pnl": round(total_pnl, 2),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "profit_factor": round(
                    abs(sum(r.pnl or 0 for r in wins)) /
                    abs(sum(r.pnl or 0 for r in losses))
                    if losses and sum(r.pnl or 0 for r in losses) != 0 else 0,
                    2,
                ),
                "high_confidence_win_rate": round(
                    len(high_conf_wins) / len(high_conf) * 100, 1
                ) if high_conf else 0,
                "symbols_traded": list(set(r.symbol for r in rows if r.symbol)),
                "period_days": days,
            }
        except Exception as e:
            logger.error(f"Trade stats query failed: {e}")
            return {"error": str(e)[:200]}

    async def get_recent_entries(self, limit: int = 20) -> list[dict]:
        """Get most recent journal entries (no embedding needed)."""
        try:
            from app.db.database import async_session
            from app.db.models import AgentMemory

            async with async_session() as session:
                result = await session.execute(
                    select(AgentMemory)
                    .where(AgentMemory.category == JOURNAL_CATEGORY)
                    .order_by(desc(AgentMemory.created_at))
                    .limit(limit)
                )
                memories = result.scalars().all()

            return [
                {
                    "id": m.id,
                    "content": m.content,
                    "importance": m.importance,
                    "metadata": m.metadata_ or {},
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in memories
            ]
        except Exception as e:
            logger.error(f"Failed to get recent entries: {e}")
            return []

    # ── Embedding ─────────────────────────────────────────────────

    async def _get_embedding(self, text: str) -> list[float] | None:
        """Generate embedding vector for text."""
        try:
            from app.services.memory_service import memory_service
            return await memory_service.get_embedding(text)
        except Exception as e:
            logger.debug(f"Embedding generation failed: {e}")
            return None


# Global singleton
trade_journal = TradeJournal()
