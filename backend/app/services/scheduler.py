"""Scheduler — recurring tasks for agents managed via Redis."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)


class ScheduledTask:
    """A recurring task definition."""

    def __init__(
        self,
        task_id: str,
        agent_id: str,
        description: str,
        interval_seconds: int,
        created_by: str = "ceo",
    ):
        self.task_id = task_id
        self.agent_id = agent_id
        self.description = description
        self.interval_seconds = interval_seconds
        self.created_by = created_by
        self.enabled = True
        self.last_run: datetime | None = None
        self.run_count = 0
        self._handle: asyncio.Task | None = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "description": self.description,
            "interval_seconds": self.interval_seconds,
            "created_by": self.created_by,
            "enabled": self.enabled,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "run_count": self.run_count,
        }


class Scheduler:
    """Manages recurring tasks for agents."""

    _REDIS_KEY = "scheduler:tasks"

    def __init__(self):
        self._tasks: dict[str, ScheduledTask] = {}
        self._lock = asyncio.Lock()

    async def load_from_redis(self) -> None:
        """Load scheduled tasks from Redis on startup."""
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            raw = await r.get(self._REDIS_KEY)
            await r.aclose()
            if raw:
                tasks_data = json.loads(raw)
                for td in tasks_data:
                    task = ScheduledTask(
                        task_id=td["task_id"],
                        agent_id=td["agent_id"],
                        description=td["description"],
                        interval_seconds=td["interval_seconds"],
                        created_by=td.get("created_by", "ceo"),
                    )
                    task.enabled = td.get("enabled", True)
                    task.run_count = td.get("run_count", 0)
                    self._tasks[task.task_id] = task
                    if task.enabled:
                        self._start_task(task)
                logger.info(f"Loaded {len(tasks_data)} scheduled task(s) from Redis")
        except Exception as e:
            logger.debug(f"Could not load scheduled tasks: {e}")

    async def _save_to_redis(self) -> None:
        """Persist scheduled tasks to Redis."""
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            data = [t.to_dict() for t in self._tasks.values()]
            await r.set(self._REDIS_KEY, json.dumps(data))
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not save scheduled tasks: {e}")

    async def add_task(
        self,
        agent_id: str,
        description: str,
        interval_seconds: int,
        created_by: str = "ceo",
    ) -> dict:
        """Schedule a new recurring task."""
        task_id = str(uuid4())[:8]
        task = ScheduledTask(
            task_id=task_id,
            agent_id=agent_id,
            description=description,
            interval_seconds=interval_seconds,
            created_by=created_by,
        )
        async with self._lock:
            self._tasks[task_id] = task
            self._start_task(task)
            await self._save_to_redis()
        logger.info(
            f"Scheduled task {task_id}: '{description}' for {agent_id} "
            f"every {interval_seconds}s"
        )
        return task.to_dict()

    async def remove_task(self, task_id: str) -> bool:
        """Cancel and remove a scheduled task."""
        async with self._lock:
            task = self._tasks.pop(task_id, None)
            if not task:
                return False
            if task._handle and not task._handle.done():
                task._handle.cancel()
            await self._save_to_redis()
        logger.info(f"Removed scheduled task {task_id}")
        return True

    async def pause_task(self, task_id: str) -> bool:
        """Pause a scheduled task."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.enabled = False
        if task._handle and not task._handle.done():
            task._handle.cancel()
            task._handle = None
        await self._save_to_redis()
        return True

    async def resume_task(self, task_id: str) -> bool:
        """Resume a paused task."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.enabled = True
        self._start_task(task)
        await self._save_to_redis()
        return True

    async def add_one_shot_task(
        self,
        agent_id: str,
        description: str,
        delay_seconds: int,
        created_by: str = "ceo",
    ) -> dict:
        """Schedule a one-time task that runs after a delay, then auto-removes."""
        task_id = f"once_{str(uuid4())[:8]}"
        task = ScheduledTask(
            task_id=task_id,
            agent_id=agent_id,
            description=description,
            interval_seconds=delay_seconds,
            created_by=created_by,
        )
        async with self._lock:
            self._tasks[task_id] = task
            # Start a one-shot loop that executes once then removes itself
            task._handle = asyncio.create_task(
                self._run_one_shot(task), name=f"oneshot_{task_id}"
            )
        logger.info(
            f"One-shot task {task_id}: '{description}' for {agent_id} "
            f"in {delay_seconds}s"
        )
        return task.to_dict()

    async def _run_one_shot(self, task: ScheduledTask) -> None:
        """Execute a task once after a delay, then auto-remove."""
        try:
            await asyncio.sleep(task.interval_seconds)
            if not task.enabled:
                return
            await self._execute_task(task)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"One-shot task {task.task_id} error: {e}")
        finally:
            # Auto-remove after execution
            async with self._lock:
                self._tasks.pop(task.task_id, None)
                await self._save_to_redis()
            logger.info(f"One-shot task {task.task_id} completed and removed")

    def list_tasks(self) -> list[dict]:
        """List all scheduled tasks."""
        return [t.to_dict() for t in self._tasks.values()]

    def _start_task(self, task: ScheduledTask) -> None:
        """Start the background loop for a task."""
        if task._handle and not task._handle.done():
            return
        task._handle = asyncio.create_task(
            self._run_loop(task), name=f"scheduled_{task.task_id}"
        )

    async def _run_loop(self, task: ScheduledTask) -> None:
        """Background loop that runs a task at its interval."""
        await asyncio.sleep(5)  # initial delay to let system settle
        while task.enabled:
            try:
                await asyncio.sleep(task.interval_seconds)
                if not task.enabled:
                    break
                await self._execute_task(task)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduled task {task.task_id} error: {e}")
                await asyncio.sleep(60)  # back off on error

    async def _execute_task(self, task: ScheduledTask) -> None:
        """Execute a scheduled task on the target agent."""
        from app.agents.registry import registry

        agent = registry.get(task.agent_id)
        if not agent:
            logger.warning(f"Scheduled task {task.task_id}: agent {task.agent_id} not found")
            return

        try:
            result = await asyncio.wait_for(
                agent.run(f"[SCHEDULED TASK] {task.description}"),
                timeout=120,
            )
            task.last_run = datetime.now(timezone.utc)
            task.run_count += 1
            await self._save_to_redis()

            from app.services.event_bus import event_bus
            await event_bus.broadcast(
                "scheduled_task_completed",
                {
                    "task_id": task.task_id,
                    "agent_id": task.agent_id,
                    "description": task.description,
                    "result_preview": result[:200] if result else "",
                },
                agent_id=task.agent_id,
            )
            logger.info(f"Scheduled task {task.task_id} completed (run #{task.run_count})")
        except asyncio.TimeoutError:
            logger.warning(f"Scheduled task {task.task_id} timed out")
        except Exception as e:
            logger.error(f"Scheduled task {task.task_id} failed: {e}")

    async def stop_all(self) -> None:
        """Cancel all running task loops."""
        for task in self._tasks.values():
            if task._handle and not task._handle.done():
                task._handle.cancel()


# Global singleton
scheduler = Scheduler()
