"""Agent Watchdog — Detects stuck agents and alerts the CEO / Owner.

Runs as a background task during the application lifespan.
Checks all registered agents periodically and flags those stuck in
THINKING or ACTING state for too long.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.db.models import AgentStatus

logger = logging.getLogger(__name__)

# How often the watchdog checks agents (seconds)
CHECK_INTERVAL = 60
# Max time an agent can be in THINKING before being considered stuck (seconds)
THINKING_TIMEOUT = 300  # 5 minutes
# Max time an agent can be in ACTING before being considered stuck (seconds)
ACTING_TIMEOUT = 300  # 5 minutes


@dataclass
class AgentTimestamp:
    """Tracks when an agent entered its current state."""
    agent_id: str
    status: AgentStatus
    entered_at: float = field(default_factory=time.time)
    alerted: bool = False


class AgentWatchdog:
    """Monitors agent states and detects stuck agents."""

    def __init__(self):
        self._state_times: dict[str, AgentTimestamp] = {}
        self._task: asyncio.Task | None = None
        self._stuck_history: list[dict] = []  # recent stuck events

    def update_state(self, agent_id: str, new_status: AgentStatus):
        """Called when an agent changes state. Resets the timer."""
        existing = self._state_times.get(agent_id)
        if existing and existing.status == new_status:
            return  # Same state, don't reset timer
        self._state_times[agent_id] = AgentTimestamp(
            agent_id=agent_id,
            status=new_status,
        )

    def get_stuck_agents(self) -> list[dict]:
        """Return list of currently stuck agents."""
        now = time.time()
        stuck = []
        for agent_id, ts in self._state_times.items():
            if ts.status == AgentStatus.THINKING:
                elapsed = now - ts.entered_at
                if elapsed > THINKING_TIMEOUT:
                    stuck.append({
                        "agent_id": agent_id,
                        "status": ts.status.value,
                        "stuck_seconds": round(elapsed),
                        "alerted": ts.alerted,
                    })
            elif ts.status == AgentStatus.ACTING:
                elapsed = now - ts.entered_at
                if elapsed > ACTING_TIMEOUT:
                    stuck.append({
                        "agent_id": agent_id,
                        "status": ts.status.value,
                        "stuck_seconds": round(elapsed),
                        "alerted": ts.alerted,
                    })
        return stuck

    def get_stuck_history(self) -> list[dict]:
        """Return recent stuck events."""
        return list(self._stuck_history[-50:])

    async def _check_loop(self):
        """Background loop that checks for stuck agents."""
        while True:
            try:
                await asyncio.sleep(CHECK_INTERVAL)
                stuck = self.get_stuck_agents()
                for agent_info in stuck:
                    if agent_info["alerted"]:
                        continue
                    agent_id = agent_info["agent_id"]
                    ts = self._state_times[agent_id]
                    ts.alerted = True

                    logger.warning(
                        f"STUCK AGENT DETECTED: {agent_id} has been in "
                        f"{agent_info['status']} for {agent_info['stuck_seconds']}s"
                    )

                    # Record in history
                    self._stuck_history.append({
                        "agent_id": agent_id,
                        "status": agent_info["status"],
                        "stuck_seconds": agent_info["stuck_seconds"],
                        "detected_at": time.time(),
                    })

                    # Broadcast alert to dashboard
                    try:
                        from app.services.event_bus import event_bus
                        await event_bus.broadcast(
                            "agent_stuck",
                            {
                                "agent_id": agent_id,
                                "status": agent_info["status"],
                                "stuck_seconds": agent_info["stuck_seconds"],
                                "message": (
                                    f"Agent {agent_id} appears stuck in "
                                    f"{agent_info['status']} state for "
                                    f"{agent_info['stuck_seconds']}s"
                                ),
                            },
                            agent_id=agent_id,
                        )
                    except Exception as e:
                        logger.debug(f"Failed to broadcast stuck alert: {e}")

                    # Try to force-recover the stuck agent
                    try:
                        from app.agents.registry import registry
                        agent = registry.get(agent_id)
                        if agent and agent.status in (
                            AgentStatus.THINKING, AgentStatus.ACTING
                        ):
                            await agent._set_status(AgentStatus.ERROR)
                            logger.warning(
                                f"Force-set agent {agent_id} to ERROR state"
                            )
                    except Exception as e:
                        logger.debug(f"Failed to recover stuck agent: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Watchdog error: {e}")

    async def start(self):
        """Start the watchdog background task."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._check_loop())
            logger.info("Agent watchdog started")

    async def stop(self):
        """Stop the watchdog."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("Agent watchdog stopped")


# Singleton
agent_watchdog = AgentWatchdog()
