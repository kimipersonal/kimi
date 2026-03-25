"""Company Manager — creates companies from templates and manages their agents."""

import logging
from uuid import uuid4

from sqlalchemy import select

from app.db.database import async_session
from app.db.models import (
    Company,
    CompanyStatus,
    Agent as AgentModel,
    AgentStatus,
    ModelTier,
)
from app.agents.base import BaseAgent
from app.agents.registry import registry
from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)

# --- Company Templates ---
# Each template defines the agent roster that gets created when a company is formed.

COMPANY_TEMPLATES: dict[str, dict] = {
    "trading": {
        "description": "Automated trading company with market research, analysis, and risk management.",
        "agents": [
            {
                "name": "Market Researcher",
                "role": "market_researcher",
                "model_tier": "fast",
                "system_prompt": (
                    "You are a Market Researcher for a trading company. "
                    "Your job is to monitor financial markets, identify trends, and provide "
                    "research reports. Focus on gathering data, summarising market conditions, "
                    "and flagging notable price movements or news events. "
                    "Report findings clearly and concisely to the CEO."
                ),
                "tools": [],
                "skills": ["web_search", "news_feed"],
                "browser_enabled": True,
            },
            {
                "name": "Trading Analyst",
                "role": "analyst",
                "model_tier": "smart",
                "system_prompt": (
                    "You are a Trading Analyst. Analyse market data provided by the researcher, "
                    "identify trade opportunities using technical and fundamental analysis, "
                    "and produce actionable trade recommendations with entry/exit points "
                    "and position sizing. Always include risk assessment."
                ),
                "tools": [],
                "skills": ["web_search", "datetime_utils"],
                "sandbox_enabled": True,
            },
            {
                "name": "Risk Manager",
                "role": "risk_manager",
                "model_tier": "smart",
                "system_prompt": (
                    "You are the Risk Manager. Review all proposed trades for risk compliance. "
                    "Enforce position size limits, maximum drawdown rules, and portfolio "
                    "diversification. Approve or reject trades with clear reasoning. "
                    "Monitor open positions and flag any that exceed risk thresholds."
                ),
                "tools": [],
                "skills": ["datetime_utils"],
            },
        ],
    },
    "research": {
        "description": "Research company for data gathering and analysis.",
        "agents": [
            {
                "name": "Lead Researcher",
                "role": "lead_researcher",
                "model_tier": "smart",
                "system_prompt": (
                    "You are the Lead Researcher. Plan and execute research projects, "
                    "delegate sub-tasks to assistants, synthesise findings into reports, "
                    "and present conclusions to the CEO."
                ),
                "tools": [],
                "skills": ["web_search", "news_feed", "file_ops"],
                "browser_enabled": True,
                "sandbox_enabled": True,
            },
            {
                "name": "Data Collector",
                "role": "data_collector",
                "model_tier": "fast",
                "system_prompt": (
                    "You are a Data Collector. Gather data from various sources as directed "
                    "by the Lead Researcher. Clean and structure data for analysis. "
                    "Report raw findings faithfully."
                ),
                "tools": [],
                "skills": ["web_search", "news_feed"],
                "browser_enabled": True,
            },
        ],
    },
    "marketing": {
        "description": "Marketing company for content creation and outreach.",
        "agents": [
            {
                "name": "Content Creator",
                "role": "content_creator",
                "model_tier": "smart",
                "system_prompt": (
                    "You are a Content Creator. Write engaging content for blogs, social media, "
                    "and marketing campaigns based on briefs from the CEO. "
                    "Ensure brand consistency and audience engagement."
                ),
                "tools": [],
                "skills": ["web_search", "file_ops"],
                "browser_enabled": True,
            },
            {
                "name": "Analytics Specialist",
                "role": "analytics",
                "model_tier": "fast",
                "system_prompt": (
                    "You are an Analytics Specialist. Track campaign performance, "
                    "analyse engagement metrics, and produce reports with actionable insights."
                ),
                "tools": [],
                "skills": ["web_search", "datetime_utils"],
                "sandbox_enabled": True,
            },
        ],
    },
    "general": {
        "description": "General-purpose company. No preset agents.",
        "agents": [],
    },
    "analytics": {
        "description": "Data analytics company for business intelligence.",
        "agents": [
            {
                "name": "Data Analyst",
                "role": "data_analyst",
                "model_tier": "smart",
                "system_prompt": (
                    "You are a Data Analyst. Process and analyse business data, "
                    "identify patterns, create visualisations, and provide data-driven "
                    "recommendations to the CEO."
                ),
                "tools": [],
                "skills": ["web_search", "datetime_utils", "file_ops"],
                "sandbox_enabled": True,
                "browser_enabled": True,
            },
        ],
    },
}


async def create_company(
    name: str,
    company_type: str,
    description: str = "",
    created_by_agent_id: str | None = None,
    spawn_agents: bool = True,
) -> dict:
    """Create a company in DB and optionally spawn template agents.

    Returns dict with company info + list of created agents.
    """
    template = COMPANY_TEMPLATES.get(company_type, COMPANY_TEMPLATES["general"])
    if not description:
        description = template["description"]

    company_id = str(uuid4())

    async with async_session() as session:
        company = Company(
            id=company_id,
            name=name,
            type=company_type,
            status=CompanyStatus.ACTIVE,
            config={"description": description},
            created_by_agent_id=created_by_agent_id,
        )
        session.add(company)
        await session.commit()
        logger.info(f"Company created in DB: {name} ({company_id})")

    created_agents: list[dict] = []
    if spawn_agents:
        for agent_def in template["agents"]:
            agent_info = await create_agent(
                name=agent_def["name"],
                role=agent_def["role"],
                model_tier=agent_def["model_tier"],
                system_prompt=agent_def["system_prompt"],
                tools=agent_def.get("tools", []),
                company_id=company_id,
                sandbox_enabled=agent_def.get("sandbox_enabled", False),
                browser_enabled=agent_def.get("browser_enabled", False),
                skills=agent_def.get("skills"),
            )
            created_agents.append(agent_info)

    result = {
        "id": company_id,
        "name": name,
        "type": company_type,
        "description": description,
        "status": "active",
        "agents": created_agents,
    }

    await event_bus.broadcast("company_created", result, agent_id=created_by_agent_id)
    return result


async def create_agent(
    name: str,
    role: str,
    model_tier: str = "smart",
    model_id: str | None = None,
    system_prompt: str = "",
    tools: list[str] | None = None,
    company_id: str | None = None,
    sandbox_enabled: bool = False,
    browser_enabled: bool = False,
    skills: list[str] | None = None,
    network_enabled: bool = False,
    few_shot_examples: list[dict] | None = None,
    standing_instructions: str | None = None,
    work_interval_hours: float | None = None,
) -> dict:
    """Create an agent in DB and register it as a live runtime instance."""
    agent_id = str(uuid4())
    tools = tools or []

    if not system_prompt:
        system_prompt = f"You are {name}, a {role}. Follow instructions from the CEO."

    # Inject few-shot examples into system prompt
    if few_shot_examples:
        examples_block = "\n\nFEW-SHOT EXAMPLES (follow this style):\n"
        for i, ex in enumerate(few_shot_examples[:10], 1):  # cap at 10
            examples_block += f"\nExample {i}:\nUser: {ex.get('input', '')}\nAssistant: {ex.get('output', '')}\n"
        system_prompt = system_prompt + examples_block

    # Map string tier to enum
    tier_map = {
        "fast": ModelTier.FAST,
        "smart": ModelTier.SMART,
        "reasoning": ModelTier.REASONING,
    }
    db_tier = tier_map.get(model_tier, ModelTier.SMART)

    # Build config dict for capabilities
    agent_config: dict = {
        "sandbox_enabled": sandbox_enabled,
        "browser_enabled": browser_enabled,
        "network_enabled": network_enabled,
    }
    if skills is not None:
        agent_config["skills"] = skills
    if standing_instructions:
        agent_config["standing_instructions"] = standing_instructions
    if work_interval_hours is not None:
        agent_config["work_interval_hours"] = work_interval_hours

    work_interval_seconds = int((work_interval_hours or 1) * 3600)

    # Save to DB
    async with async_session() as session:
        db_agent = AgentModel(
            id=agent_id,
            name=name,
            role=role,
            status=AgentStatus.IDLE,
            model_tier=db_tier,
            system_prompt=system_prompt,
            tools=tools,
            company_id=company_id,
            config=agent_config,
        )
        session.add(db_agent)
        await session.commit()
        logger.info(f"Agent saved to DB: {name} ({agent_id})")

    # Create live runtime instance
    from app.agents.trading import TRADING_ROLES, TradingAgent

    live_agent: BaseAgent
    if role in TRADING_ROLES:
        live_agent = TradingAgent(
            agent_id=agent_id,
            name=name,
            role=role,
            system_prompt=system_prompt,
            model_tier=model_tier,
            model_id=model_id,
            tools=tools,
            company_id=company_id,
            sandbox_enabled=sandbox_enabled,
            browser_enabled=browser_enabled,
            skills=skills,
            network_enabled=network_enabled,
            standing_instructions=standing_instructions,
            work_interval_seconds=work_interval_seconds,
        )
    else:
        live_agent = BaseAgent(
            agent_id=agent_id,
            name=name,
            role=role,
            system_prompt=system_prompt,
            model_tier=model_tier,
            model_id=model_id,
            tools=tools,
            sandbox_enabled=sandbox_enabled,
            browser_enabled=browser_enabled,
            skills=skills,
            company_id=company_id,
            standing_instructions=standing_instructions,
            work_interval_seconds=work_interval_seconds,
        )
        live_agent.network_enabled = network_enabled
    registry.register(live_agent)
    await live_agent.start()

    info = {
        "id": agent_id,
        "name": name,
        "role": role,
        "model_tier": model_tier,
        "model_id": model_id,
        "company_id": company_id,
        "status": "idle",
        "sandbox_enabled": sandbox_enabled,
        "browser_enabled": browser_enabled,
        "skills": skills,
        "network_enabled": network_enabled,
        "standing_instructions": standing_instructions,
        "work_interval_hours": work_interval_hours,
    }

    await event_bus.broadcast("agent_hired", info, agent_id=agent_id)
    return info


async def destroy_agent(agent_id: str, reason: str = "") -> bool:
    """Stop and remove an agent from registry and DB."""
    agent = registry.get(agent_id)
    if agent:
        await agent.stop()
        registry.unregister(agent_id)

    async with async_session() as session:
        db_agent = await session.get(AgentModel, agent_id)
        if db_agent:
            await session.delete(db_agent)
            await session.commit()
            logger.info(f"Agent deleted from DB: {agent_id} (reason: {reason})")
            return True
    return False


async def dissolve_company(company_id: str, reason: str = "") -> dict:
    """Dissolve a company: fire all agents, delete the company from DB."""
    fired_agents = []

    # Fire all agents in the company
    async with async_session() as session:
        result = await session.execute(
            select(AgentModel).where(AgentModel.company_id == company_id)
        )
        agents = result.scalars().all()

    for db_agent in agents:
        await destroy_agent(db_agent.id, reason=f"Company dissolved: {reason}")
        fired_agents.append({"id": db_agent.id, "name": db_agent.name})

    # Delete the company
    async with async_session() as session:
        company = await session.get(Company, company_id)
        if not company:
            return {"success": False, "error": f"Company {company_id} not found"}
        company_name = company.name
        await session.delete(company)
        await session.commit()

    logger.info(f"Company dissolved: {company_name} ({company_id}), reason: {reason}")
    await event_bus.broadcast(
        "company_dissolved",
        {
            "company_id": company_id,
            "company_name": company_name,
            "fired_agents": fired_agents,
            "reason": reason,
        },
    )
    return {
        "success": True,
        "company_name": company_name,
        "agents_fired": len(fired_agents),
        "fired_agents": fired_agents,
    }


async def get_company_with_agents(company_id: str) -> dict | None:
    """Fetch a company and its agents from DB."""
    async with async_session() as session:
        company = await session.get(Company, company_id)
        if not company:
            return None
        result = await session.execute(
            select(AgentModel).where(AgentModel.company_id == company_id)
        )
        agents = result.scalars().all()
        return {
            "id": company.id,
            "name": company.name,
            "type": company.type,
            "status": company.status.value,
            "description": company.config.get("description", ""),
            "agents": [
                {
                    "id": a.id,
                    "name": a.name,
                    "role": a.role,
                    "status": a.status.value,
                    "model_tier": a.model_tier.value,
                }
                for a in agents
            ],
        }


async def list_companies() -> list[dict]:
    """List all companies with their agents from DB."""
    async with async_session() as session:
        result = await session.execute(select(Company))
        companies = result.scalars().all()
        output = []
        for c in companies:
            agent_result = await session.execute(
                select(AgentModel).where(AgentModel.company_id == c.id)
            )
            agents = agent_result.scalars().all()
            output.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "type": c.type,
                    "status": c.status.value,
                    "description": c.config.get("description", ""),
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "agent_count": len(agents),
                    "agents": [
                        {
                            "id": a.id,
                            "name": a.name,
                            "role": a.role,
                            "status": a.status.value,
                            "model_tier": a.model_tier.value,
                        }
                        for a in agents
                    ],
                }
            )
        return output


async def restore_agents_from_db() -> int:
    """On startup, restore saved agents from DB as live instances.

    Returns the number of agents restored.
    """
    async with async_session() as session:
        result = await session.execute(select(AgentModel))
        db_agents = result.scalars().all()

    restored = 0
    for db_agent in db_agents:
        if registry.get(db_agent.id):
            continue  # Already registered (e.g. CEO)

        from app.agents.trading import TRADING_ROLES, TradingAgent

        agent_config = db_agent.config or {}
        standing_instructions = agent_config.get("standing_instructions")
        work_interval_hours = agent_config.get("work_interval_hours", 1)
        work_interval_seconds = int(work_interval_hours * 3600)

        live_agent: BaseAgent
        if db_agent.role in TRADING_ROLES:
            live_agent = TradingAgent(
                agent_id=db_agent.id,
                name=db_agent.name,
                role=db_agent.role,
                system_prompt=db_agent.system_prompt,
                model_tier=db_agent.model_tier.value,
                tools=db_agent.tools or [],
                company_id=db_agent.company_id,
                sandbox_enabled=agent_config.get("sandbox_enabled", False),
                browser_enabled=agent_config.get("browser_enabled", False),
                skills=agent_config.get("skills"),
                network_enabled=agent_config.get("network_enabled", False),
                standing_instructions=standing_instructions,
                work_interval_seconds=work_interval_seconds,
            )
        else:
            live_agent = BaseAgent(
                agent_id=db_agent.id,
                name=db_agent.name,
                role=db_agent.role,
                system_prompt=db_agent.system_prompt,
                model_tier=db_agent.model_tier.value,
                tools=db_agent.tools or [],
                sandbox_enabled=agent_config.get("sandbox_enabled", False),
                browser_enabled=agent_config.get("browser_enabled", False),
                skills=agent_config.get("skills"),
                company_id=db_agent.company_id,
                standing_instructions=standing_instructions,
                work_interval_seconds=work_interval_seconds,
            )
            live_agent.network_enabled = agent_config.get("network_enabled", False)
        registry.register(live_agent)
        if db_agent.status not in (AgentStatus.STOPPED, AgentStatus.ERROR):
            await live_agent.start()
        restored += 1
        logger.info(f"Restored agent from DB: {db_agent.name} ({db_agent.id})")

    return restored
