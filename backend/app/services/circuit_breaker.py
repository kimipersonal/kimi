"""Circuit Breaker — Protects LLM calls with failure detection and auto-recovery.

States:
  CLOSED  = normal operation (calls pass through)
  OPEN    = too many failures → block calls for a cooldown period
  HALF_OPEN = after cooldown, allow one test call to check recovery
"""

import asyncio
import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-model circuit breaker with configurable thresholds."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max: int = 1,
    ):
        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max = half_open_max

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._half_open_calls = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            if time.monotonic() - self._last_failure_time >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                logger.info(f"Circuit '{self.name}' → HALF_OPEN (testing recovery)")
        return self._state

    async def call(self, func, *args, **kwargs):
        """Execute a function through the circuit breaker."""
        async with self._lock:
            current_state = self.state

            if current_state == CircuitState.OPEN:
                raise CircuitOpenError(
                    f"Circuit '{self.name}' is OPEN. "
                    f"Retry after {self._recovery_timeout}s cooldown."
                )

            if (
                current_state == CircuitState.HALF_OPEN
                and self._half_open_calls >= self._half_open_max
            ):
                raise CircuitOpenError(
                    f"Circuit '{self.name}' HALF_OPEN limit reached."
                )

            if current_state == CircuitState.HALF_OPEN:
                self._half_open_calls += 1

        # Execute outside the lock
        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception:
            await self._on_failure()
            raise

    async def _on_success(self):
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                logger.info(f"Circuit '{self.name}' → CLOSED (recovered)")
                self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count += 1

    async def _on_failure(self):
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                logger.warning(f"Circuit '{self.name}' → OPEN (half-open test failed)")
                self._state = CircuitState.OPEN
            elif self._failure_count >= self._failure_threshold:
                logger.warning(
                    f"Circuit '{self.name}' → OPEN "
                    f"({self._failure_count} failures in a row)"
                )
                self._state = CircuitState.OPEN

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "failure_threshold": self._failure_threshold,
            "recovery_timeout_s": self._recovery_timeout,
        }


class CircuitOpenError(Exception):
    """Raised when a circuit breaker is open."""
    pass


class CircuitBreakerRegistry:
    """Manages circuit breakers for different models/services."""

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}

    def get_or_create(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
            )
        return self._breakers[name]

    def get_all_status(self) -> list[dict]:
        return [cb.get_status() for cb in self._breakers.values()]


# Global singleton
circuit_registry = CircuitBreakerRegistry()
