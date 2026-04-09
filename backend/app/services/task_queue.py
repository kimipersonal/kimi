"""Task Queue — async task execution with status tracking.

Allows the CEO to fire-and-forget tasks to sub-agents, then check results
later using task IDs. Tasks run in background asyncio tasks.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)


class AsyncTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


# Priority ordering for queue sorting (higher = first)
_PRIORITY_ORDER = {TaskPriority.LOW: 0, TaskPriority.NORMAL: 1, TaskPriority.HIGH: 2, TaskPriority.CRITICAL: 3}


@dataclass
class AsyncTaskResult:
    task_id: str
    agent_id: str
    agent_name: str
    description: str
    status: AsyncTaskStatus
    priority: TaskPriority = TaskPriority.NORMAL
    result: str | None = None
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    duration_s: float | None = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "result": self.result,
            "error": self.error,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_s": self.duration_s,
        }


class TaskQueue:
    """Manages async task execution for sub-agents with priority and retry."""

    def __init__(self, max_concurrent: int = 10) -> None:
        self._tasks: dict[str, AsyncTaskResult] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._pending_queue: list[str] = []  # task_ids waiting to run
        self._max_tasks = 500  # keep history
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def submit_task(
        self,
        agent_id: str,
        description: str,
        submitted_by: str = "ceo",
        priority: str = "normal",
        max_retries: int = 0,
    ) -> AsyncTaskResult:
        """Submit a task for async execution. Returns immediately with task_id."""
        from app.agents.registry import registry

        target = registry.get(agent_id)
        if not target:
            raise ValueError(f"Agent {agent_id} not found or not running.")

        try:
            task_priority = TaskPriority(priority)
        except ValueError:
            task_priority = TaskPriority.NORMAL

        task_id = str(uuid4())[:8]
        task_result = AsyncTaskResult(
            task_id=task_id,
            agent_id=agent_id,
            agent_name=target.name,
            description=description,
            status=AsyncTaskStatus.PENDING,
            priority=task_priority,
            max_retries=max(0, min(max_retries, 5)),  # cap at 5
        )
        self._tasks[task_id] = task_result

        # Prune old completed tasks if we exceed max
        if len(self._tasks) > self._max_tasks:
            completed = [
                tid for tid, t in self._tasks.items()
                if t.status in (AsyncTaskStatus.COMPLETED, AsyncTaskStatus.FAILED)
            ]
            for tid in completed[:100]:
                del self._tasks[tid]

        # Launch background task
        bg_task = asyncio.create_task(
            self._run_task(task_id, target, description, submitted_by)
        )
        self._running[task_id] = bg_task

        await event_bus.broadcast("task_submitted", task_result.to_dict(), agent_id=agent_id)
        return task_result

    async def _run_task(self, task_id: str, target, description: str, submitted_by: str) -> None:
        """Execute the task in the background with semaphore and retry."""
        from app.services.messaging import send_message

        try:
            async with self._semaphore:
                task = self._tasks[task_id]
                task.status = AsyncTaskStatus.RUNNING
                task.started_at = datetime.now(timezone.utc).isoformat()
                start = time.monotonic()

                # Persist task to DB
                db_task_id = await self._persist_task_to_db(task, submitted_by)

                await event_bus.broadcast("task_started", task.to_dict(), agent_id=task.agent_id)

                last_error: Exception | None = None
                for attempt in range(task.max_retries + 1):
                    try:
                        if attempt == 0:
                            await send_message(
                                from_agent_id=submitted_by,
                                to_agent_id=task.agent_id,
                                content=description,
                                message_type="task",
                            )

                        result = await target.run(description)
                        elapsed = time.monotonic() - start

                        task.status = AsyncTaskStatus.COMPLETED
                        task.result = result
                        task.retry_count = attempt
                        task.completed_at = datetime.now(timezone.utc).isoformat()
                        task.duration_s = round(elapsed, 2)

                        await send_message(
                            from_agent_id=task.agent_id,
                            to_agent_id=submitted_by,
                            content=result,
                            message_type="task_result",
                        )
                        await self._update_task_in_db(db_task_id, task)
                        await event_bus.broadcast("task_completed", task.to_dict(), agent_id=task.agent_id)
                        logger.info(f"Task {task_id} completed in {elapsed:.1f}s (attempt {attempt + 1}): {task.agent_name}")
                        last_error = None
                        break

                    except Exception as e:
                        last_error = e
                        task.retry_count = attempt + 1
                        if attempt < task.max_retries:
                            wait = min(2 ** attempt * 2, 30)  # exponential backoff, max 30s
                            logger.warning(f"Task {task_id} attempt {attempt + 1} failed, retrying in {wait}s: {e}")
                            await asyncio.sleep(wait)
                        else:
                            break

                if last_error is not None:
                    elapsed = time.monotonic() - start
                    task.status = AsyncTaskStatus.FAILED
                    task.error = str(last_error)
                    task.completed_at = datetime.now(timezone.utc).isoformat()
                    task.duration_s = round(elapsed, 2)

                    await self._update_task_in_db(db_task_id, task)
                    await event_bus.broadcast("task_failed", task.to_dict(), agent_id=task.agent_id)
                    logger.error(f"Task {task_id} failed after {task.retry_count} attempts: {last_error}")

        finally:
            self._running.pop(task_id, None)

    async def _persist_task_to_db(self, task: AsyncTaskResult, submitted_by: str) -> str | None:
        """Save task record to database. Returns None if DB write fails (non-fatal)."""
        try:
            from app.db.database import async_session
            from app.db.models import Task as TaskModel, TaskStatus, Agent as AgentModel
            from uuid import uuid4

            db_task_id = str(uuid4())
            async with async_session() as session:
                # Verify agent exists in DB before inserting (FK constraint)
                agent = await session.get(AgentModel, task.agent_id)
                if not agent:
                    logger.debug(f"Agent {task.agent_id} not in DB, skipping task persistence")
                    return None

                db_task = TaskModel(
                    id=db_task_id,
                    agent_id=task.agent_id,
                    type="async_task",
                    description=task.description,
                    status=TaskStatus.IN_PROGRESS,
                    input_data={"submitted_by": submitted_by, "queue_task_id": task.task_id},
                )
                session.add(db_task)
                await session.commit()
            return db_task_id
        except Exception as e:
            logger.debug(f"Could not persist task to DB: {e}")
            return None

    async def _update_task_in_db(self, db_task_id: str | None, task: AsyncTaskResult) -> None:
        """Update task status and result in database."""
        if not db_task_id:
            return
        try:
            from app.db.database import async_session
            from app.db.models import Task as TaskModel, TaskStatus
            from sqlalchemy import update

            status_map = {
                AsyncTaskStatus.COMPLETED: TaskStatus.COMPLETED,
                AsyncTaskStatus.FAILED: TaskStatus.FAILED,
            }
            async with async_session() as session:
                stmt = (
                    update(TaskModel)
                    .where(TaskModel.id == db_task_id)
                    .values(
                        status=status_map.get(task.status, TaskStatus.IN_PROGRESS),
                        output_data={
                            "result": task.result[:2000] if task.result else None,
                            "error": task.error,
                            "duration_s": task.duration_s,
                        },
                        completed_at=datetime.fromisoformat(task.completed_at) if task.completed_at else None,
                    )
                )
                await session.execute(stmt)
                await session.commit()
        except Exception as e:
            logger.debug(f"Could not update task in DB: {e}")

    def get_task(self, task_id: str) -> AsyncTaskResult | None:
        """Get task status/result by ID."""
        return self._tasks.get(task_id)

    def get_agent_tasks(self, agent_id: str) -> list[dict]:
        """Get all tasks for a specific agent."""
        return [
            t.to_dict() for t in self._tasks.values()
            if t.agent_id == agent_id
        ]

    def get_all_tasks(self, status: AsyncTaskStatus | None = None) -> list[dict]:
        """Get all tasks, optionally filtered by status. Sorted by priority (highest first)."""
        all_tasks = list(self._tasks.values())
        if status:
            all_tasks = [t for t in all_tasks if t.status == status]
        all_tasks.sort(key=lambda t: _PRIORITY_ORDER.get(t.priority, 1), reverse=True)
        return [t.to_dict() for t in all_tasks]

    def get_running_count(self) -> int:
        """Count currently running tasks."""
        return len(self._running)

    async def recover_orphaned_tasks(self) -> int:
        """Mark IN_PROGRESS tasks in DB as FAILED on startup (they were lost on restart)."""
        try:
            from app.db.database import async_session
            from app.db.models import Task as TaskModel, TaskStatus
            from sqlalchemy import update

            async with async_session() as session:
                stmt = (
                    update(TaskModel)
                    .where(TaskModel.status == TaskStatus.IN_PROGRESS)
                    .values(
                        status=TaskStatus.FAILED,
                        output_data={"error": "Lost on service restart", "recovered": True},
                    )
                    .returning(TaskModel.id)
                )
                result = await session.execute(stmt)
                orphaned_ids = result.scalars().all()
                await session.commit()
                count = len(orphaned_ids)
                if count:
                    logger.warning(f"Recovered {count} orphaned task(s) — marked FAILED")
                return count
        except Exception as e:
            logger.error(f"Could not recover orphaned tasks: {e}")
            return 0


# Singleton
task_queue = TaskQueue()
