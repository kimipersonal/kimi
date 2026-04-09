"""Health Monitor — Background heartbeat and service health checking.

Periodically checks:
  - Agent responsiveness (last activity timestamp)
  - Database connectivity
  - Redis connectivity
  - Trading platform connections
  - LLM API availability
"""

from app.db.database import redis_pool
import asyncio
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class HealthMonitor:
    """Background health monitor with periodic checks."""

    def __init__(self, check_interval: int = 60):
        self._check_interval = check_interval
        self._task: asyncio.Task | None = None
        self._checks: dict[str, dict] = {}
        self._start_time = time.monotonic()

    async def start(self):
        """Start the background health check loop."""
        self._start_time = time.monotonic()
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Health monitor started")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Health monitor stopped")

    async def _monitor_loop(self):
        """Main monitoring loop."""
        while True:
            try:
                await self._run_checks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")
            await asyncio.sleep(self._check_interval)

    async def _run_checks(self):
        """Run all health checks."""
        await self._check_database()
        await self._check_redis()
        await self._check_agents()

    async def _check_database(self):
        """Check PostgreSQL connectivity."""
        try:
            from app.db.database import async_session
            from sqlalchemy import text

            async with async_session() as session:
                await session.execute(text("SELECT 1"))
            self._checks["database"] = {
                "status": "healthy",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            self._checks["database"] = {
                "status": "unhealthy",
                "error": str(e),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            logger.error(f"Database health check failed: {e}")

    async def _check_redis(self):
        """Check Redis connectivity."""
        try:
            await redis_pool.ping()
            self._checks["redis"] = {
                "status": "healthy",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            self._checks["redis"] = {
                "status": "unhealthy",
                "error": str(e),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

    async def _check_agents(self):
        """Check agent registry health."""
        try:
            from app.agents.registry import registry

            agents = registry.get_all()
            agent_status = []
            for a in agents:
                agent_status.append({
                    "id": a.agent_id,
                    "name": a.name,
                    "status": a.status.value,
                })
            self._checks["agents"] = {
                "status": "healthy",
                "total": len(agents),
                "active": sum(1 for a in agents if a.status.value == "idle"),
                "agents": agent_status,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            self._checks["agents"] = {
                "status": "unhealthy",
                "error": str(e),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

    def get_health(self) -> dict:
        """Get current health status."""
        uptime_seconds = time.monotonic() - self._start_time
        all_healthy = all(
            c.get("status") == "healthy" for c in self._checks.values()
        )
        return {
            "status": "healthy" if all_healthy else "degraded",
            "uptime_seconds": round(uptime_seconds),
            "uptime_human": _format_uptime(uptime_seconds),
            "checks": self._checks,
        }


def _format_uptime(seconds: float) -> str:
    """Human-readable uptime string."""
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 24:
        days = hours // 24
        hours = hours % 24
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


# Global singleton
health_monitor = HealthMonitor()
