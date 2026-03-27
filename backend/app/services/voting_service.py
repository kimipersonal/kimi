"""Multi-Agent Voting — structured consensus mechanism for distributed decisions.

Allows agents (typically CEO) to initiate a vote among N agents on a question,
collect responses asynchronously, and compute consensus.  Each vote has a deadline;
agents that don't respond in time are marked as abstained.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)

_REDIS_KEY = "voting:sessions"


class VoteChoice(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    ABSTAIN = "abstain"


class VoteStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    EXPIRED = "expired"


@dataclass
class AgentVote:
    agent_id: str
    agent_name: str
    choice: VoteChoice | None = None
    reasoning: str = ""
    voted_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "choice": self.choice.value if self.choice else None,
            "reasoning": self.reasoning,
            "voted_at": self.voted_at,
        }


@dataclass
class VoteSession:
    vote_id: str
    question: str
    initiated_by: str
    participants: list[AgentVote] = field(default_factory=list)
    status: VoteStatus = VoteStatus.OPEN
    deadline_seconds: int = 120
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    closed_at: str | None = None
    result: dict | None = None

    def to_dict(self) -> dict:
        return {
            "vote_id": self.vote_id,
            "question": self.question,
            "initiated_by": self.initiated_by,
            "status": self.status.value,
            "deadline_seconds": self.deadline_seconds,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
            "participants": [p.to_dict() for p in self.participants],
            "result": self.result,
        }


class VotingService:
    """Manages multi-agent voting sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, VoteSession] = {}
        self._pending_tasks: dict[str, asyncio.Task] = {}

    async def initiate_vote(
        self,
        question: str,
        agent_ids: list[str],
        initiated_by: str = "ceo",
        deadline_seconds: int = 120,
    ) -> VoteSession:
        """Start a new voting session.

        Sends the question to each participating agent asynchronously.
        Returns the VoteSession immediately (results arrive later).
        """
        from app.agents.registry import registry

        vote_id = f"vote-{str(uuid4())[:8]}"
        participants = []

        for aid in agent_ids:
            agent = registry.get(aid)
            if agent:
                participants.append(AgentVote(agent_id=aid, agent_name=agent.name))
            else:
                logger.warning(f"Vote {vote_id}: agent {aid} not found, skipping")

        if not participants:
            raise ValueError("No valid agents found for voting")

        session = VoteSession(
            vote_id=vote_id,
            question=question,
            initiated_by=initiated_by,
            participants=participants,
            deadline_seconds=min(max(deadline_seconds, 30), 600),
        )
        self._sessions[vote_id] = session

        await event_bus.broadcast(
            "vote_initiated",
            {"vote_id": vote_id, "question": question, "participant_count": len(participants)},
        )

        # Launch async collection task
        task = asyncio.create_task(self._collect_votes(session))
        self._pending_tasks[vote_id] = task

        logger.info(f"Vote {vote_id} initiated: '{question}' with {len(participants)} participants")
        return session

    async def _collect_votes(self, session: VoteSession) -> None:
        """Ask each agent for their vote and wait up to the deadline."""
        from app.agents.registry import registry
        from app.services.messaging import send_message

        prompt = (
            f"[VOTE REQUEST — ID: {session.vote_id}]\n"
            f"Question: {session.question}\n\n"
            f"You must respond with EXACTLY one of: APPROVE, REJECT, or ABSTAIN\n"
            f"Then on a new line, give a brief reason (1-2 sentences).\n"
            f"Example:\nAPPROVE\nThis aligns with our risk parameters."
        )

        async def _ask_agent(participant: AgentVote) -> None:
            agent = registry.get(participant.agent_id)
            if not agent:
                participant.choice = VoteChoice.ABSTAIN
                participant.reasoning = "Agent not available"
                return
            try:
                # Send vote request as message
                await send_message(
                    from_agent_id=session.initiated_by,
                    to_agent_id=participant.agent_id,
                    content=prompt,
                    message_type="vote_request",
                    metadata={"vote_id": session.vote_id},
                )

                response = await asyncio.wait_for(
                    agent.run(prompt),
                    timeout=session.deadline_seconds,
                )

                # Parse response
                choice, reasoning = self._parse_vote_response(response)
                participant.choice = choice
                participant.reasoning = reasoning
                participant.voted_at = datetime.now(timezone.utc).isoformat()

                await send_message(
                    from_agent_id=participant.agent_id,
                    to_agent_id=session.initiated_by,
                    content=f"Vote: {choice.value} — {reasoning}",
                    message_type="vote_response",
                    metadata={"vote_id": session.vote_id, "choice": choice.value},
                )

            except asyncio.TimeoutError:
                participant.choice = VoteChoice.ABSTAIN
                participant.reasoning = "Timed out"
                logger.warning(f"Vote {session.vote_id}: {participant.agent_name} timed out")
            except Exception as e:
                participant.choice = VoteChoice.ABSTAIN
                participant.reasoning = f"Error: {str(e)[:100]}"
                logger.error(f"Vote {session.vote_id}: {participant.agent_name} error: {e}")

        # Run all agents concurrently
        await asyncio.gather(*[_ask_agent(p) for p in session.participants])

        # Tally results
        session.result = self._tally_votes(session)
        session.status = VoteStatus.CLOSED
        session.closed_at = datetime.now(timezone.utc).isoformat()

        await event_bus.broadcast("vote_completed", session.to_dict())
        self._pending_tasks.pop(session.vote_id, None)

        logger.info(
            f"Vote {session.vote_id} closed: "
            f"{session.result.get('decision', 'unknown')} "
            f"({session.result.get('approve', 0)}A/"
            f"{session.result.get('reject', 0)}R/"
            f"{session.result.get('abstain', 0)}X)"
        )

    @staticmethod
    def _parse_vote_response(response: str) -> tuple["VoteChoice", str]:
        """Parse an agent's vote response into choice + reasoning."""
        lines = response.strip().split("\n", 1)
        first_line = lines[0].strip().upper()
        reasoning = lines[1].strip() if len(lines) > 1 else ""

        if "APPROVE" in first_line:
            return VoteChoice.APPROVE, reasoning
        elif "REJECT" in first_line:
            return VoteChoice.REJECT, reasoning
        else:
            return VoteChoice.ABSTAIN, reasoning or "Unclear response"

    @staticmethod
    def _tally_votes(session: "VoteSession") -> dict:
        """Count votes and determine consensus."""
        approve = sum(1 for p in session.participants if p.choice == VoteChoice.APPROVE)
        reject = sum(1 for p in session.participants if p.choice == VoteChoice.REJECT)
        abstain = sum(1 for p in session.participants if p.choice == VoteChoice.ABSTAIN)
        total = len(session.participants)

        # Simple majority among non-abstaining voters
        active_votes = approve + reject
        if active_votes == 0:
            decision = "no_quorum"
            consensus = False
        elif approve > reject:
            decision = "approved"
            consensus = approve >= (total / 2)  # true consensus = majority of ALL
        elif reject > approve:
            decision = "rejected"
            consensus = reject >= (total / 2)
        else:
            decision = "tied"
            consensus = False

        return {
            "approve": approve,
            "reject": reject,
            "abstain": abstain,
            "total": total,
            "decision": decision,
            "consensus": consensus,
        }

    def get_vote(self, vote_id: str) -> VoteSession | None:
        """Get a vote session by ID."""
        return self._sessions.get(vote_id)

    def get_all_votes(self, status: VoteStatus | None = None) -> list[dict]:
        """Get all vote sessions, optionally filtered by status."""
        sessions = list(self._sessions.values())
        if status:
            sessions = [s for s in sessions if s.status == status]
        return [s.to_dict() for s in sessions]

    async def persist_to_redis(self) -> None:
        """Persist vote history to Redis."""
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings

            data = {vid: s.to_dict() for vid, s in self._sessions.items()}
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            await r.set(_REDIS_KEY, json.dumps(data), ex=86400 * 7)
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not persist voting data: {e}")

    async def load_from_redis(self) -> None:
        """Load completed vote history from Redis (does not restore open sessions)."""
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings

            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            raw = await r.get(_REDIS_KEY)
            await r.aclose()
            if not raw:
                return
            data = json.loads(raw)
            for vid, sdict in data.items():
                if vid not in self._sessions:
                    # Reconstruct as closed sessions (history only)
                    session = VoteSession(
                        vote_id=sdict["vote_id"],
                        question=sdict["question"],
                        initiated_by=sdict["initiated_by"],
                        status=VoteStatus(sdict["status"]),
                        deadline_seconds=sdict.get("deadline_seconds", 120),
                        created_at=sdict.get("created_at", ""),
                        closed_at=sdict.get("closed_at"),
                        result=sdict.get("result"),
                        participants=[
                            AgentVote(
                                agent_id=p["agent_id"],
                                agent_name=p["agent_name"],
                                choice=VoteChoice(p["choice"]) if p.get("choice") else None,
                                reasoning=p.get("reasoning", ""),
                                voted_at=p.get("voted_at"),
                            )
                            for p in sdict.get("participants", [])
                        ],
                    )
                    self._sessions[vid] = session
            logger.info(f"Loaded {len(data)} vote sessions from Redis")
        except Exception as e:
            logger.debug(f"Could not load voting data: {e}")


# Global singleton
voting_service = VotingService()
