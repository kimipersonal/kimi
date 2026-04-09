"""CEO Self-Scheduler — automatically sets up essential recurring tasks on startup.

On first boot (or when schedules are empty), the CEO automatically creates:
1. Hourly health check + agent restart
2. Daily intelligence report (at configured hour)
3. 6-hourly performance review
4. 4-hourly budget check
5. Weekly operations summary

Uses a Redis flag to avoid re-creating schedules on every restart.
"""

from app.db.database import redis_pool
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_REDIS_BOOTSTRAP_KEY = "ceo:self_scheduler:bootstrapped"

# Default schedules the CEO sets up automatically
DEFAULT_SCHEDULES = [
    {
        "id_prefix": "auto_health",
        "agent_id": "ceo",
        "description": (
            "[AUTO] Health check: Use check_agent_health. "
            "If any agent is stuck or errored, restart it with restart_agent. "
            "If all healthy, no output needed."
        ),
        "interval_hours": 1,
    },
    {
        "id_prefix": "auto_budget_check",
        "agent_id": "ceo",
        "description": (
            "[AUTO] Budget check: Use get_costs to check daily spending. "
            "If spending is >80% of budget, use check_budget_status to see enforcement state. "
            "If any agents are over-budget, they should already be paused. "
            "If global budget is >90%, send_report to Owner with spending alert."
        ),
        "interval_hours": 4,
    },
    {
        "id_prefix": "auto_performance",
        "agent_id": "ceo",
        "description": (
            "[AUTO] Performance review: Use get_agent_performance for all agents. "
            "If any agent has grade D or F for 3+ consecutive checks, consider: "
            "1) restart_agent, 2) if persists, fire_agent and hire a replacement. "
            "Log your reasoning."
        ),
        "interval_hours": 6,
    },
]


async def bootstrap_ceo_schedules(force: bool = False) -> dict:
    """Set up default CEO schedules if not already done.

    Args:
        force: If True, recreate schedules even if already bootstrapped.

    Returns:
        {"created": [...schedule_ids], "skipped": bool}
    """
    from app.services.scheduler import scheduler

    # Check if already bootstrapped
    if not force:
        try:

            already = await redis_pool.get(_REDIS_BOOTSTRAP_KEY)
            if already:
                logger.info("CEO schedules already bootstrapped, skipping")
                return {"created": [], "skipped": True}
        except Exception:
            pass

    # Check existing schedules — don't duplicate
    existing = scheduler.list_tasks()
    existing_descriptions = {t.get("description", "") for t in existing}

    created = []
    for sched in DEFAULT_SCHEDULES:
        # Skip if a schedule with same description already exists
        if sched["description"] in existing_descriptions:
            logger.debug(f"Schedule '{sched['id_prefix']}' already exists, skipping")
            continue

        interval_seconds = int(sched["interval_hours"] * 3600)
        result = await scheduler.add_task(
            agent_id=sched["agent_id"],
            description=sched["description"],
            interval_seconds=interval_seconds,
            created_by="ceo_self_scheduler",
        )
        created.append(result)
        logger.info(
            f"CEO auto-scheduled: {sched['id_prefix']} "
            f"(every {sched['interval_hours']}h)"
        )

    # Mark as bootstrapped
    try:

        await redis_pool.set(
            _REDIS_BOOTSTRAP_KEY,
            datetime.now(timezone.utc).isoformat(),
        )
    except Exception:
        pass

    logger.info(f"CEO self-scheduler: created {len(created)} schedule(s)")
    return {"created": [c.get("task_id") for c in created], "skipped": False}


async def setup_daily_report_schedule() -> None:
    """Set up the daily intelligence report timer.

    This runs as a separate asyncio task since it needs precise daily timing,
    not the scheduler's interval-based approach.
    """
    from app.config import get_settings
    from app.services.daily_report import send_daily_report_telegram

    settings = get_settings()
    report_hour = settings.daily_report_hour  # UTC hour

    while True:
        try:
            now = datetime.now(timezone.utc)
            # Calculate seconds until next report time
            from datetime import timedelta

            target = now.replace(
                hour=report_hour, minute=0, second=0, microsecond=0
            )
            if target <= now:
                target += timedelta(days=1)
            wait_seconds = (target - now).total_seconds()

            logger.info(
                f"Daily report scheduled for {target.isoformat()} "
                f"(in {wait_seconds / 3600:.1f}h)"
            )
            await asyncio.sleep(wait_seconds)

            # Generate and send report
            logger.info("Generating daily intelligence report...")
            await send_daily_report_telegram()

            # Also have the CEO do a comprehensive review
            try:
                from app.agents.registry import registry

                ceo = registry.get("ceo")
                if ceo:
                    prompt = (
                        "[DAILY REPORT] Generate your daily intelligence report:\n"
                        "1) Use generate_daily_report to get comprehensive data.\n"
                        "2) Review the data and identify key insights, anomalies, "
                        "and recommendations.\n"
                        "3) Use send_report to deliver a formatted summary to the Owner.\n"
                        "4) If you identify issues that need action, take them now."
                    )
                    await asyncio.wait_for(ceo.run(prompt), timeout=180)
            except Exception as e:
                logger.error(f"CEO daily review failed: {e}")

            # Small sleep to avoid double-triggering
            await asyncio.sleep(60)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Daily report schedule error: {e}")
            await asyncio.sleep(3600)  # retry in 1 hour
