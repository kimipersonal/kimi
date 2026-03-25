"""AI Holding — Main FastAPI Application."""

import asyncio
import logging
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s",
)
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
        """Reset daily cost counters at midnight UTC."""
        from datetime import datetime, timezone, timedelta
        while True:
            try:
                now = datetime.now(timezone.utc)
                tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                seconds_until_midnight = (tomorrow - now).total_seconds()
                await asyncio.sleep(seconds_until_midnight)
                cost_tracker.reset_daily()
                logger.info("Daily cost counters reset")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Daily cost reset error: {e}")

    _bg_tasks = [
        asyncio.create_task(_approval_expiry_loop(), name="approval_expiry"),
        asyncio.create_task(_daily_cost_reset_loop(), name="daily_cost_reset"),
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
