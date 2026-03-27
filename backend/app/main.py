"""AI Holding — Main FastAPI Application."""

import asyncio
import logging
import logging.handlers
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db.database import init_db
from app.agents.ceo import CEOAgent
from app.agents.registry import registry
from app.api import agents, companies, dashboard, skills, trading, websocket, webhooks

# Ensure logs directory exists
_log_dir = Path(__file__).resolve().parents[1] / "logs"
_log_dir.mkdir(exist_ok=True)

# Configure root logger with console + rotating file handler
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s",
)
_file_handler = logging.handlers.RotatingFileHandler(
    _log_dir / "backend.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s")
)
logging.getLogger().addHandler(_file_handler)
logger = logging.getLogger(__name__)
settings = get_settings()

# Ensure GOOGLE_APPLICATION_CREDENTIALS is set as absolute path for LiteLLM/Vertex
if settings.google_application_credentials:
    creds_path = Path(settings.google_application_credentials)
    if not creds_path.is_absolute():
        creds_path = Path(__file__).resolve().parents[2] / creds_path
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(creds_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    logger.info("=== AI Holding Starting ===")

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Discover and load plugins
    from app.plugins.registry import plugin_registry

    enabled = settings.enabled_plugins.split(",") if settings.enabled_plugins else None
    await plugin_registry.discover_and_load(enabled)
    await plugin_registry.initialize_all()
    status = plugin_registry.get_status()
    logger.info(
        f"Plugins loaded: {len(status['providers'])} providers, "
        f"{len(status['channels'])} channels, {len(status['tools'])} tools"
    )

    # Discover and load skills
    from app.skills.registry import skill_registry

    await skill_registry.discover_and_load()
    await skill_registry.initialize_all()
    skills_status = skill_registry.get_status()
    logger.info(
        f"Skills loaded: {skills_status['total']} total, "
        f"{skills_status['enabled']} enabled"
    )

    # Query existing state from DB for CEO's system prompt
    from app.db.database import async_session
    from app.db.models import Agent as AgentModel, AgentStatus, ModelTier
    from app.db.models import Company as CompanyModel

    state_summary = ""
    async with async_session() as session:
        from sqlalchemy import select, func

        company_count = (
            await session.execute(select(func.count()).select_from(CompanyModel))
        ).scalar() or 0
        agent_count = (
            await session.execute(
                select(func.count())
                .select_from(AgentModel)
                .where(AgentModel.id != "ceo")
            )
        ).scalar() or 0

        if company_count > 0 or agent_count > 0:
            # Build summary of existing companies and agents
            companies = (await session.execute(select(CompanyModel))).scalars().all()
            agents = (
                await session.execute(
                    select(AgentModel).where(AgentModel.id != "ceo")
                )
            ).scalars().all()

            lines = [
                f"- The holding has {company_count} active company(ies) and {agent_count} deployed agent(s).",
                "- You MUST call check_status to get full details before answering about companies or agents.",
                "- Existing companies:",
            ]
            for c in companies:
                company_agents = [a for a in agents if a.company_id == c.id]
                agent_names = ", ".join(a.name for a in company_agents) if company_agents else "no agents"
                lines.append(f"  * {c.name} (type: {c.type}) — agents: {agent_names}")

            state_summary = "\n".join(lines)

    # Create and register the CEO agent
    ceo = CEOAgent(agent_id="ceo", state_summary=state_summary)
    registry.register(ceo)
    await ceo.start()

    # Ensure CEO exists in DB
    async with async_session() as session:
        existing = await session.get(AgentModel, "ceo")
        if not existing:
            db_ceo = AgentModel(
                id="ceo",
                name="CEO",
                role="Chief Executive Officer",
                status=AgentStatus.IDLE,
                model_tier=ModelTier.REASONING,
                system_prompt=ceo.system_prompt,
                tools=ceo.tools,
            )
            session.add(db_ceo)
            await session.commit()
            logger.info("CEO saved to database")

    logger.info("CEO Agent registered and started")

    # Restore sub-agents from DB
    from app.services.company_manager import restore_agents_from_db

    restored = await restore_agents_from_db()
    if restored:
        logger.info(f"Restored {restored} agent(s) from database")

    # Recover orphaned tasks from previous runs
    from app.services.task_queue import task_queue

    orphaned = await task_queue.recover_orphaned_tasks()
    if orphaned:
        logger.info(f"Recovered {orphaned} orphaned task(s)")

    # Start Telegram bot
    from app.services.telegram_bot import telegram_bot

    await telegram_bot.start()

    # Start trading service
    from app.services.trading.trading_service import trading_service

    await trading_service.startup()

    # Start scheduler (recurring agent tasks)
    from app.services.scheduler import scheduler

    await scheduler.load_from_redis()

    # Start health monitor
    from app.services.health_monitor import health_monitor

    await health_monitor.start()

    # Start agent watchdog (detects stuck agents)
    from app.services.agent_watchdog import agent_watchdog

    await agent_watchdog.start()

    # Load tool analytics from Redis
    from app.services.tool_analytics import tool_analytics

    tool_analytics.load_from_redis()

    # Load Phase 2 services from Redis
    from app.services.voting_service import voting_service
    from app.services.delegation_service import delegation_service
    from app.services.company_kpi_service import company_kpi_service

    await voting_service.load_from_redis()
    await delegation_service.load_from_redis()
    await company_kpi_service.load_from_redis()
    logger.info("Phase 2 services loaded (voting, delegation, KPIs)")

    # Load Phase 3 services
    from app.services.auto_trade_executor import auto_trade_executor
    from app.services.portfolio_risk_manager import portfolio_risk_manager
    from app.services.market_alerts import market_alert_service

    await auto_trade_executor.load_from_redis()
    await portfolio_risk_manager.load_from_redis()
    await market_alert_service.load_from_redis()
    await market_alert_service.start(interval_seconds=60)
    logger.info("Phase 3 services loaded (auto-trade, risk manager, market alerts)")

    # Load Phase 4 services
    from app.services.prompt_optimizer import prompt_optimizer
    from app.services.tool_usage_optimizer import tool_usage_optimizer
    from app.services.error_pattern_detector import error_pattern_detector
    from app.services.ab_testing_service import ab_testing_service

    await prompt_optimizer.load_from_redis()
    await tool_usage_optimizer.load_from_redis()
    await error_pattern_detector.load_from_redis()
    await ab_testing_service.load_from_redis()
    logger.info("Phase 4 services loaded (prompt optimizer, tool optimizer, error detector, A/B testing)")

    # Load Phase 5 services
    from app.services.tiered_approval import tiered_approval_service
    from app.services.accountability_report import accountability_report_service
    from app.services.rollback_service import rollback_service
    from app.services.multi_owner import multi_owner_service

    await tiered_approval_service.load_from_redis()
    await accountability_report_service.load_from_redis()
    await rollback_service.load_from_redis()
    await multi_owner_service.load_from_redis()
    logger.info("Phase 5 services loaded (tiered approval, accountability, rollback, multi-owner)")

    # Schedule recurring background tasks
    async def _approval_expiry_loop():
        """Expire stale approvals every hour."""
        from app.services.approval_service import expire_old_approvals
        while True:
            try:
                await asyncio.sleep(3600)  # every hour
                count = await expire_old_approvals()
                if count:
                    logger.info(f"Expired {count} stale approval(s)")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Approval expiry task error: {e}")

    async def _daily_cost_reset_loop():
        """Reset daily cost counters and budget enforcement at midnight UTC."""
        from datetime import datetime, timezone, timedelta
        while True:
            try:
                now = datetime.now(timezone.utc)
                tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                seconds_until_midnight = (tomorrow - now).total_seconds()
                await asyncio.sleep(seconds_until_midnight)
                cost_tracker.reset_daily()
                # Also reset budget enforcer daily state
                from app.services.budget_enforcer import budget_enforcer
                budget_enforcer.reset_daily()
                # Also reset auto-trade daily counters
                from app.services.auto_trade_executor import auto_trade_executor as _ate
                _ate.reset_daily()
                # Phase 4: daily error scan and prompt snapshots
                try:
                    await error_pattern_detector.scan_audit_log()
                    await error_pattern_detector.save_to_redis()
                    await prompt_optimizer.capture_all_snapshots()
                except Exception as _p4e:
                    logger.debug(f"Phase 4 daily tasks: {_p4e}")
                logger.info("Daily cost counters and budget enforcement reset")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Daily cost reset error: {e}")

    async def _ceo_event_reactor():
        """Background loop: CEO reacts to critical system events."""
        from app.services.event_bus import event_bus

        subscriber = event_bus.subscribe("dashboard")
        try:
            while True:
                event = await subscriber.get()
                event_type = event.get("event", "")
                data = event.get("data", {})

                # React to important events
                if event_type not in (
                    "agent_stuck", "task_failed", "agent_report", "task_completed"
                ):
                    continue

                # Don't react to CEO's own events to avoid loops
                if data.get("agent_id") == "ceo":
                    continue

                try:
                    if event_type == "agent_stuck":
                        prompt = (
                            f"[SYSTEM ALERT] Agent {data.get('agent_id')} is stuck in "
                            f"{data.get('status')} state for {data.get('stuck_seconds')}s. "
                            f"Use check_agent_health, restart the agent if needed, and report the issue."
                        )
                    elif event_type == "task_failed":
                        prompt = (
                            f"[SYSTEM ALERT] Task {data.get('task_id', 'unknown')} failed "
                            f"for agent {data.get('agent_id', 'unknown')}. "
                            f"Error: {str(data.get('error', ''))[:300]}. "
                            f"Check what happened and take corrective action."
                        )
                    elif event_type == "agent_report":
                        priority = data.get("priority", "medium")
                        # Only auto-react to high/critical priority reports
                        if priority not in ("high", "critical"):
                            continue
                        prompt = (
                            f"[AGENT REPORT — {priority.upper()}] "
                            f"Agent {data.get('agent_name', data.get('agent_id', 'unknown'))} "
                            f"reports: \"{data.get('title', 'No title')}\" — "
                            f"{str(data.get('content', ''))[:500]}. "
                            f"Review and take action if needed."
                        )
                    elif event_type == "task_completed":
                        prompt = (
                            f"[TASK COMPLETED] Task {data.get('task_id', 'unknown')} "
                            f"completed by agent {data.get('agent_name', data.get('agent_id', 'unknown'))}. "
                            f"Result preview: {str(data.get('result', ''))[:300]}. "
                            f"Determine if any follow-up action is needed."
                        )
                    else:
                        continue

                    logger.info(f"CEO reacting to {event_type}: {prompt[:100]}...")
                    await asyncio.wait_for(ceo.run(prompt), timeout=120)
                except asyncio.TimeoutError:
                    logger.warning(f"CEO reaction to {event_type} timed out")
                except Exception as e:
                    logger.error(f"CEO reaction to {event_type} failed: {e}")
        except asyncio.CancelledError:
            pass

    async def _ceo_proactive_loop():
        """CEO proactively checks operations every 2 hours."""
        await asyncio.sleep(120)  # let startup settle
        while True:
            try:
                prompt = (
                    "[SCHEDULED REVIEW] Perform your periodic operations review:\n"
                    "1) Use check_agent_health — restart any stuck or errored agents.\n"
                    "2) Use get_costs — review daily spending. If >80% of budget, "
                    "consider pausing non-essential agents and report to Owner.\n"
                    "3) Use check_status — verify all companies are healthy.\n"
                    "4) If any issues found, take corrective action.\n"
                    "5) Keep responses brief — this is a routine check."
                )
                logger.info("CEO proactive review starting...")
                await asyncio.wait_for(ceo.run(prompt), timeout=180)
            except asyncio.TimeoutError:
                logger.warning("CEO proactive review timed out")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"CEO proactive loop error: {e}")
            await asyncio.sleep(7200)  # every 2 hours

    async def _ceo_startup_review():
        """One-time: CEO reviews all companies/agents after startup (with delay)."""
        await asyncio.sleep(30)  # Let everything settle first
        if company_count > 0 or agent_count > 0:
            try:
                prompt = (
                    "[SYSTEM STARTUP] The system has just started. You must:\n"
                    "1) Use check_status to review all companies and agents.\n"
                    "2) Use check_agent_health to find stuck or errored agents. Restart them.\n"
                    "3) Use list_schedules to check your recurring tasks.\n"
                    "4) If no recurring health check schedule exists, create one:\n"
                    "   schedule_task for yourself: 'check_agent_health and restart stuck agents' hourly.\n"
                    "5) Send a brief startup report to the Owner summarizing the current state."
                )
                logger.info("CEO performing startup review...")
                await asyncio.wait_for(ceo.run(prompt), timeout=180)
            except asyncio.TimeoutError:
                logger.warning("CEO startup review timed out")
            except Exception as e:
                logger.error(f"CEO startup review failed: {e}")

    # --- CEO Self-Scheduling (auto-create essential recurring tasks) ---
    async def _ceo_bootstrap_schedules():
        """Bootstrap CEO's auto-schedules after startup settles."""
        await asyncio.sleep(15)  # let scheduler load from Redis first
        try:
            from app.services.ceo_self_scheduler import bootstrap_ceo_schedules
            result = await bootstrap_ceo_schedules()
            if result.get("created"):
                logger.info(f"CEO self-scheduler created {len(result['created'])} schedule(s)")
            elif result.get("skipped"):
                logger.info("CEO self-scheduler: schedules already exist")
        except Exception as e:
            logger.error(f"CEO self-scheduler failed: {e}")

    # --- Daily Intelligence Report loop ---
    async def _daily_report_loop():
        """Run the daily report at configured UTC hour."""
        try:
            from app.services.ceo_self_scheduler import setup_daily_report_schedule
            await setup_daily_report_schedule()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Daily report loop failed: {e}")

    _bg_tasks = [
        asyncio.create_task(_approval_expiry_loop(), name="approval_expiry"),
        asyncio.create_task(_daily_cost_reset_loop(), name="daily_cost_reset"),
        asyncio.create_task(_ceo_event_reactor(), name="ceo_event_reactor"),
        asyncio.create_task(_ceo_proactive_loop(), name="ceo_proactive_loop"),
        asyncio.create_task(_ceo_startup_review(), name="ceo_startup_review"),
        asyncio.create_task(_ceo_bootstrap_schedules(), name="ceo_bootstrap_schedules"),
        asyncio.create_task(_daily_report_loop(), name="daily_report_loop"),
    ]

    # Wire budget alerts → Telegram
    from app.services.cost_tracker import cost_tracker

    # Load persisted cost data from Redis
    await cost_tracker._load_from_redis()

    async def _budget_alert(current: float, budget: float):
        try:
            if telegram_bot._running and telegram_bot._app and settings.telegram_owner_chat_id:
                from telegram.constants import ParseMode
                await telegram_bot._app.bot.send_message(
                    chat_id=int(settings.telegram_owner_chat_id),
                    text=f"⚠️ <b>Budget Alert</b>\n\n"
                         f"Daily spend: ${current:.4f} / ${budget:.2f}\n"
                         f"Usage: {current / budget * 100:.0f}%\n\n"
                         f"Consider pausing non-essential agents.",
                    parse_mode=ParseMode.HTML,
                )
        except Exception as e:
            logger.warning(f"Failed to send budget alert: {e}")

    cost_tracker.on_budget_alert(_budget_alert)

    # Wire budget enforcer → Telegram + CEO notifications
    from app.services.budget_enforcer import budget_enforcer

    await budget_enforcer._ensure_loaded()

    async def _budget_enforced(entity_id: str, reason: str):
        """Notify owner and CEO when an agent/company is paused for budget."""
        try:
            if telegram_bot._running and telegram_bot._app and settings.telegram_owner_chat_id:
                from telegram.constants import ParseMode
                await telegram_bot._app.bot.send_message(
                    chat_id=int(settings.telegram_owner_chat_id),
                    text=f"🚫 <b>Budget Enforced</b>\n\n{reason}\n\n"
                         f"Agent/company has been auto-paused.",
                    parse_mode=ParseMode.HTML,
                )
        except Exception as e:
            logger.warning(f"Failed to send budget enforcement alert: {e}")
        # Also notify CEO so it can take action
        try:
            prompt = (
                f"[BUDGET ENFORCEMENT] {reason}\n"
                f"The affected agent/company has been auto-paused. "
                f"Review the situation with check_budget_status and decide "
                f"if any reallocation or action is needed."
            )
            await asyncio.wait_for(ceo.run(prompt), timeout=120)
        except Exception as e:
            logger.warning(f"CEO budget reaction failed: {e}")

    budget_enforcer.on_budget_exceeded(_budget_enforced)

    # Hook event bus → Telegram notifications
    from app.services.event_bus import event_bus

    _original_broadcast = event_bus.broadcast

    async def _broadcast_with_telegram(
        event: str, data: dict, agent_id: str | None = None
    ):
        await _original_broadcast(event, data, agent_id)
        await telegram_bot.notify(event, data, agent_id)

    event_bus.broadcast = _broadcast_with_telegram  # type: ignore[assignment]

    # Start nginx reverse proxy + Cloudflare tunnel
    from app.services.cloudflare_tunnel import cloudflare_tunnel

    nginx_conf = Path(__file__).resolve().parents[2] / "nginx" / "ai-holding.conf"
    if nginx_conf.exists():
        import subprocess
        # Symlink our config and restart nginx
        target = Path("/etc/nginx/sites-enabled/ai-holding.conf")
        if not target.exists():
            subprocess.run(
                ["sudo", "ln", "-sf", str(nginx_conf), str(target)],
                check=False,
            )
            subprocess.run(
                ["sudo", "rm", "-f", "/etc/nginx/sites-enabled/default"],
                check=False,
            )
            subprocess.run(["sudo", "nginx", "-t"], check=False)
            subprocess.run(["sudo", "systemctl", "restart", "nginx"], check=False)
            logger.info("Nginx reverse proxy configured on port 8080")

    tunnel_url = await cloudflare_tunnel.start(port=8080)
    if tunnel_url:
        logger.info(f"Dashboard available at: {tunnel_url}")
        # Notify owner via Telegram
        try:
            _settings = get_settings()
            if telegram_bot._running and telegram_bot._app and _settings.telegram_owner_chat_id:
                from telegram.constants import ParseMode
                await telegram_bot._app.bot.send_message(
                    chat_id=int(_settings.telegram_owner_chat_id),
                    text=f"🌐 <b>AI Holding Online</b>\n\n"
                         f"Dashboard: {tunnel_url}\n"
                         f"Agents: {registry.count} active\n\n"
                         f"Use /web to get this link anytime.",
                    parse_mode=ParseMode.HTML,
                )
        except Exception as e:
            logger.warning(f"Failed to send tunnel URL to Telegram: {e}")

    yield

    # Shutdown
    logger.info("=== AI Holding Shutting Down ===")
    # Cancel background tasks
    for t in _bg_tasks:
        t.cancel()
    await market_alert_service.stop()
    await agent_watchdog.stop()
    await health_monitor.stop()
    await scheduler.stop_all()
    await cloudflare_tunnel.stop()
    await telegram_bot.stop()
    await trading_service.shutdown()
    await registry.stop_all()
    await plugin_registry.shutdown_all()
    await skill_registry.shutdown_all()

    # Shutdown browser service if it was used
    try:
        from app.services.browser_service import browser_service
        await browser_service.shutdown()
    except Exception:
        pass


app = FastAPI(
    title="AI Holding",
    description="AI-powered holding company with multi-agent management",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow dashboard frontend (local + tunnel)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost(:\d+)?|127\.0\.0\.1(:\d+)?|.*\.trycloudflare\.com|.*\.devtunnels\.ms)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(agents.router)
app.include_router(companies.router)
app.include_router(dashboard.router)
app.include_router(skills.router)
app.include_router(trading.router)
app.include_router(websocket.router)
app.include_router(webhooks.router)


@app.get("/")
async def root():
    return {
        "name": "AI Holding",
        "version": "0.1.0",
        "status": "running",
        "ceo_status": registry.get("ceo").status.value
        if registry.get("ceo")
        else "not_initialized",
        "total_agents": registry.count,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
