"""Rate Limiter — per-agent and global request throttling.

Uses a sliding window counter in Redis for distributed rate limiting.
"""

import logging
import time

logger = logging.getLogger(__name__)

# Defaults (can be overridden per-agent)
_GLOBAL_RPM = 60        # global requests per minute
_AGENT_RPM = 20         # per-agent requests per minute
_WINDOW_SECONDS = 60


class RateLimiter:
    """Sliding window rate limiter backed by Redis."""

    def __init__(
        self,
        global_rpm: int = _GLOBAL_RPM,
        agent_rpm: int = _AGENT_RPM,
    ) -> None:
        self.global_rpm = global_rpm
        self.agent_rpm = agent_rpm
        self._fallback_windows: dict[str, list[float]] = {}

    async def check(self, agent_id: str) -> tuple[bool, str]:
        """Check if a request is allowed.

        Returns (allowed, reason).
        """
        try:
            return await self._check_redis(agent_id)
        except Exception:
            return self._check_memory(agent_id)

    async def _check_redis(self, agent_id: str) -> tuple[bool, str]:
        import redis.asyncio as aioredis
        from app.config import get_settings

        now = time.time()
        window_start = now - _WINDOW_SECONDS
        r = aioredis.from_url(get_settings().redis_url)

        pipe = r.pipeline()

        # Global key
        gkey = "ratelimit:global"
        pipe.zremrangebyscore(gkey, 0, window_start)
        pipe.zcard(gkey)
        pipe.zadd(gkey, {f"{now}:{agent_id}": now})
        pipe.expire(gkey, _WINDOW_SECONDS * 2)

        # Agent key
        akey = f"ratelimit:agent:{agent_id}"
        pipe.zremrangebyscore(akey, 0, window_start)
        pipe.zcard(akey)
        pipe.zadd(akey, {str(now): now})
        pipe.expire(akey, _WINDOW_SECONDS * 2)

        results = await pipe.execute()
        await r.aclose()

        global_count = results[1]  # zcard result for global
        agent_count = results[5]   # zcard result for agent

        if global_count >= self.global_rpm:
            return False, f"Global rate limit exceeded ({self.global_rpm}/min)"
        if agent_count >= self.agent_rpm:
            return False, f"Agent rate limit exceeded ({self.agent_rpm}/min)"
        return True, "ok"

    def _check_memory(self, agent_id: str) -> tuple[bool, str]:
        """Fallback: in-memory sliding window when Redis is unavailable."""
        now = time.time()
        cutoff = now - _WINDOW_SECONDS

        # Global
        gwindow = self._fallback_windows.setdefault("__global__", [])
        gwindow[:] = [t for t in gwindow if t > cutoff]
        if len(gwindow) >= self.global_rpm:
            return False, f"Global rate limit exceeded ({self.global_rpm}/min)"

        # Per-agent
        awindow = self._fallback_windows.setdefault(agent_id, [])
        awindow[:] = [t for t in awindow if t > cutoff]
        if len(awindow) >= self.agent_rpm:
            return False, f"Agent rate limit exceeded ({self.agent_rpm}/min)"

        gwindow.append(now)
        awindow.append(now)
        return True, "ok"

    async def get_status(self, agent_id: str | None = None) -> dict:
        """Get current rate limit status."""
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings

            now = time.time()
            window_start = now - _WINDOW_SECONDS
            r = aioredis.from_url(get_settings().redis_url)

            pipe = r.pipeline()
            pipe.zremrangebyscore("ratelimit:global", 0, window_start)
            pipe.zcard("ratelimit:global")
            if agent_id:
                pipe.zremrangebyscore(f"ratelimit:agent:{agent_id}", 0, window_start)
                pipe.zcard(f"ratelimit:agent:{agent_id}")
            results = await pipe.execute()
            await r.aclose()

            status = {
                "global_rpm_used": results[1],
                "global_rpm_limit": self.global_rpm,
            }
            if agent_id:
                status["agent_rpm_used"] = results[3]
                status["agent_rpm_limit"] = self.agent_rpm
            return status
        except Exception:
            return {"global_rpm_limit": self.global_rpm, "agent_rpm_limit": self.agent_rpm}


# Global singleton
rate_limiter = RateLimiter()
