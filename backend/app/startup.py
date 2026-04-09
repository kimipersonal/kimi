"""Startup and shutdown helpers — extracted from main.py lifespan.

Keeps the lifespan function lean while grouping related initialization steps.
"""

import asyncio
import logging
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


async def load_plugins():
    """Discover and load plugins."""
    from app.plugins.registry import plugin_registry

    enabled = settings.enabled_plugins.split(",") if settings.enabled_plugins else None
    await plugin_registry.discover_and_load(enabled)
    await plugin_registry.initialize_all()
    status = plugin_registry.get_status()
    logger.info(
        f"Plugins loaded: {len(status['providers'])} providers, "
        f"{len(status['channels'])} channels, {len(status['tools'])} tools"
    )


async def load_skills():
    """Discover and load skills."""
    from app.skills.registry import skill_registry

    await skill_registry.discover_and_load()
    await skill_registry.initialize_all()
    skills_status = skill_registry.get_status()
    logger.info(
        f"Skills loaded: {skills_status['total']} total, "
        f"{skills_status['enabled']} enabled"
    )


async def build_state_summary() -> tuple[str, int, int]:
    """Query DB for existing companies/agents and build a summary for the CEO.

    Returns (summary_text, company_count, agent_count).
    """
    from app.db.database import async_session
    from app.db.models import Agent as AgentModel
    from app.db.models import Company as CompanyModel
    from sqlalchemy import select, func

    async with async_session() as session:
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

        if company_count == 0 and agent_count == 0:
            return "", company_count, agent_count

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

    return "\n".join(lines), company_count, agent_count


async def register_ceo(state_summary: str):
    """Create and register the CEO agent, ensure it exists in DB."""
    from app.agents.ceo import CEOAgent
    from app.agents.registry import registry
    from app.db.database import async_session
    from app.db.models import Agent as AgentModel, AgentStatus, ModelTier

    ceo = CEOAgent(agent_id="ceo", state_summary=state_summary)
    registry.register(ceo)
    await ceo.start()

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
    return ceo


async def restore_runtime():
    """Restore sub-agents and orphaned tasks from a previous run."""
    from app.services.company_manager import restore_agents_from_db
    from app.services.task_queue import task_queue

    restored = await restore_agents_from_db()
    if restored:
        logger.info(f"Restored {restored} agent(s) from database")

    orphaned = await task_queue.recover_orphaned_tasks()
    if orphaned:
        logger.info(f"Recovered {orphaned} orphaned task(s)")


async def start_services():
    """Start Telegram bot, trading service, scheduler, health monitor, watchdog."""
    from app.services.telegram_bot import telegram_bot
    from app.services.trading.trading_service import trading_service
    from app.services.scheduler import scheduler
    from app.services.health_monitor import health_monitor
    from app.services.agent_watchdog import agent_watchdog
    from app.services.tool_analytics import tool_analytics

    await telegram_bot.start()
    await trading_service.startup()
    await scheduler.load_from_redis()
    await health_monitor.start()
    await agent_watchdog.start()
    await tool_analytics.load_from_redis()


async def load_phase_services():
    """Load phase 2-5 services from Redis."""
    from app.services.voting_service import voting_service
    from app.services.delegation_service import delegation_service
    from app.services.company_kpi_service import company_kpi_service

    await voting_service.load_from_redis()
    await delegation_service.load_from_redis()
    await company_kpi_service.load_from_redis()
    logger.info("Phase 2 services loaded (voting, delegation, KPIs)")

    from app.services.auto_trade_executor import auto_trade_executor
    from app.services.portfolio_risk_manager import portfolio_risk_manager
    from app.services.market_alerts import market_alert_service
    from app.db.database import redis_pool

    await auto_trade_executor.load_from_redis()
    # Initialize auto-trade executor mode if never configured
    from app.services.auto_trade_executor import AutoTradeMode
    if auto_trade_executor.config.mode == AutoTradeMode.CONSERVATIVE:
        # Check if this is a fresh state (never saved to Redis)
        raw = await redis_pool.get(auto_trade_executor._REDIS_KEY)
        if not raw:
            await auto_trade_executor.set_mode("moderate")
            logger.info("Auto-trade executor initialized: mode=moderate")
    await portfolio_risk_manager.load_from_redis()
    await market_alert_service.load_from_redis()
    await market_alert_service.start(interval_seconds=60)
    logger.info("Phase 3 services loaded (auto-trade, risk manager, market alerts)")

    from app.services.prompt_optimizer import prompt_optimizer
    from app.services.tool_usage_optimizer import tool_usage_optimizer
    from app.services.error_pattern_detector import error_pattern_detector
    from app.services.ab_testing_service import ab_testing_service

    await prompt_optimizer.load_from_redis()
    await tool_usage_optimizer.load_from_redis()
    await error_pattern_detector.load_from_redis()
    await ab_testing_service.load_from_redis()
    logger.info("Phase 4 services loaded (prompt optimizer, tool optimizer, error detector, A/B testing)")

    from app.services.tiered_approval import tiered_approval_service
    from app.services.accountability_report import accountability_report_service
    from app.services.rollback_service import rollback_service
    from app.services.multi_owner import multi_owner_service

    await tiered_approval_service.load_from_redis()
    await accountability_report_service.load_from_redis()
    await rollback_service.load_from_redis()
    await multi_owner_service.load_from_redis()
    logger.info("Phase 5 services loaded (tiered approval, accountability, rollback, multi-owner)")


async def setup_tunnel():
    """Configure nginx and start Cloudflare tunnel."""
    from app.services.cloudflare_tunnel import cloudflare_tunnel
    import subprocess

    nginx_conf = Path(__file__).resolve().parents[2] / "nginx" / "ai-holding.conf"
    if nginx_conf.exists():
        target = Path("/etc/nginx/sites-enabled/ai-holding.conf")
        if not target.exists():
            subprocess.run(["sudo", "ln", "-sf", str(nginx_conf), str(target)], check=False)
            subprocess.run(["sudo", "rm", "-f", "/etc/nginx/sites-enabled/default"], check=False)
            subprocess.run(["sudo", "nginx", "-t"], check=False)
            subprocess.run(["sudo", "systemctl", "restart", "nginx"], check=False)
            logger.info("Nginx reverse proxy configured on port 8080")

    tunnel_url = await cloudflare_tunnel.start(port=8080)
    if tunnel_url:
        logger.info(f"Dashboard available at: {tunnel_url}")
        _notify_tunnel(tunnel_url)
    return tunnel_url


def _notify_tunnel(tunnel_url: str):
    """Best-effort Telegram notification of the tunnel URL."""
    async def _send():
        try:
            from app.services.telegram_bot import telegram_bot
            if telegram_bot._running and telegram_bot._app and settings.telegram_owner_chat_id:
                from telegram.constants import ParseMode
                from app.agents.registry import registry
                await telegram_bot._app.bot.send_message(
                    chat_id=int(settings.telegram_owner_chat_id),
                    text=f"🌐 <b>AI Holding Online</b>\n\n"
                         f"Dashboard: {tunnel_url}\n"
                         f"Agents: {registry.count} active\n\n"
                         f"Use /web to get this link anytime.",
                    parse_mode=ParseMode.HTML,
                )
        except Exception as e:
            logger.warning(f"Failed to send tunnel URL to Telegram: {e}")
    asyncio.create_task(_send())


async def shutdown_all():
    """Graceful shutdown of all services."""
    from app.services.market_alerts import market_alert_service
    from app.services.agent_watchdog import agent_watchdog
    from app.services.health_monitor import health_monitor
    from app.services.scheduler import scheduler
    from app.services.cloudflare_tunnel import cloudflare_tunnel
    from app.services.telegram_bot import telegram_bot
    from app.services.trading.trading_service import trading_service
    from app.agents.registry import registry
    from app.plugins.registry import plugin_registry
    from app.skills.registry import skill_registry

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

    try:
        from app.services.browser_service import browser_service
        await browser_service.shutdown()
    except Exception:
        pass

    from app.db.database import redis_pool
    await redis_pool.aclose()
    logger.info("Redis pool closed")
