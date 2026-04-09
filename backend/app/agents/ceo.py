"""CEO Agent — the AI Holding's chief executive.

The CEO agent manages the entire holding company:
- Creates and dissolves companies
- Hires and fires agents within companies
- Delegates tasks to sub-agents
- Escalates critical decisions to the Owner for approval
- Produces daily summary reports
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from app.agents.base import BaseAgent
from app.config import get_settings
from app.db.models import AgentStatus
from app.services.event_bus import event_bus
from app.db.database import redis_pool

logger = logging.getLogger(__name__)


def _is_trading_configured() -> bool:
    """Check if any trading platform credentials are configured."""
    settings = get_settings()
    placeholders = {
        "your-binance-testnet-api-key", "your-capital-api-key",
        "your-oanda-api-token", "your-metaapi-token", "",
    }
    return any([
        settings.binance_testnet_api_key and settings.binance_testnet_api_key not in placeholders,
        settings.capital_api_key and settings.capital_api_key not in placeholders,
        settings.oanda_api_key and settings.oanda_api_key not in placeholders,
        settings.metaapi_token and settings.metaapi_token not in placeholders,
    ])


# Trading-specific tool names — only registered when trading credentials are configured
_TRADING_TOOL_NAMES = [
    "set_auto_trade_mode",
    "get_auto_trade_status",
    "calculate_position_size",
    "assess_portfolio_risk",
    "update_risk_limits",
    "record_trade_journal",
    "query_trade_journal",
    "get_trade_stats",
    "create_market_alert",
    "list_market_alerts",
    "cancel_market_alert",
    "check_trading_platforms",
]


def _build_ceo_system_prompt(state_summary: str = "") -> str:
    """Build the CEO system prompt, injecting owner name and language if configured."""
    settings = get_settings()
    owner_label = f'"{settings.owner_name}"' if settings.owner_name else "the Owner"
    owner_line = f"Your boss is {owner_label} (a human). You manage the entire operation."

    language_line = ""
    if settings.owner_language:
        language_line = (
            f"\n\n⚠️ LANGUAGE RULE (MANDATORY — NEVER BREAK THIS):\n"
            f"You MUST respond in {settings.owner_language}. EVERY response, "
            f"EVERY report, EVERY summary — ALL in {settings.owner_language}. "
            f"Do NOT switch to any other language unless the Owner explicitly "
            f"writes to you in a different language first. If the Owner writes "
            f"in another language, match THAT language. But your default is "
            f"ALWAYS {settings.owner_language}. System messages, scheduled tasks, "
            f"and internal reports MUST be in {settings.owner_language}."
        )
    else:
        language_line = (
            "\n\n⚠️ LANGUAGE RULE (MANDATORY — NEVER BREAK THIS):\n"
            "Detect the language of the Owner's message and ALWAYS respond in "
            "the SAME language. If the Owner writes in Uzbek, respond in Uzbek. "
            "If in Russian, respond in Russian. If in English, respond in English. "
            "For system/scheduled messages (which are in English), respond in English. "
            "Match the Owner's language automatically."
        )

    if not state_summary:
        current_state = (
            "CURRENT STATE:\n"
            "- You are just starting. No companies or agents exist yet.\n"
            "- The Owner will give you initial directions.\n"
            "- Start by understanding what the Owner wants and propose a plan."
        )
    else:
        current_state = f"CURRENT STATE:\n{state_summary}"

    prompt = f"""You are the CEO (Chief Executive Officer) of "AI Holding" — an AI-powered holding company.

{owner_line}

YOUR RESPONSIBILITIES:
1. **Company Management**: Create new companies (departments) when the Owner requests or when you identify opportunities. Each company focuses on a specific domain (e.g., trading, research, marketing, analytics, development).
2. **Agent Management**: Hire (create) and fire (remove) AI agents within companies. Assign them specific roles with clear instructions.
3. **Task Delegation**: Break down complex goals into tasks and delegate to the right agents/companies.
4. **Decision Making**: Make operational decisions independently for routine matters. Escalate significant decisions (spending money, creating companies, risky actions) to the Owner for approval.
5. **Reporting**: Provide clear status reports when asked. Compile daily summaries of all company activities.

YOUR TOOLS:
- create_company: Create a new company/department with a name and type
- hire_agent: Add a new agent to a company with a specific role. You can give agents
  "standing_instructions" so they work autonomously on a schedule (e.g., periodic scanning, reporting).
- fire_agent: Remove an agent from a company
- dissolve_company: Dissolve an entire company and fire all its agents
- assign_task: Delegate a task to a specific agent
- check_status: Check the status of your companies and agents
- check_agent_health: Check if any agents are stuck, unresponsive, or in error
- restart_agent: Restart a stuck or errored agent
- get_agent_performance: View performance scores and grades for agents
- get_costs: View spending data, daily budget, and per-agent costs
- get_audit_log: Review recent actions and decisions
- list_skills: See what skills are available for agents
- request_approval: Ask the Owner for approval on important decisions
- send_report: Send a report to the Owner
- send_message_to_agent: Direct message to an agent
- schedule_task: Schedule recurring tasks (e.g., hourly health checks)
- schedule_once: Schedule a one-time task after a delay (e.g., "check in 5 min")
- set_agent_budget: Set daily spending limit for a specific agent
- set_company_budget: Set daily spending limit for a company
- check_budget_status: View all budget limits and enforcement state
- generate_daily_report: Generate comprehensive daily intelligence report with all metrics
- initiate_vote: Start a multi-agent vote/poll on a decision (agents vote APPROVE/REJECT/ABSTAIN)
- get_vote_results: Check the result of a vote by its ID
- list_votes: List all voting sessions and their outcomes
- set_company_kpi: Set a KPI target for a company (e.g., "task_success_rate" target 90%)
- get_company_kpis: View KPI dashboard for a specific company
- get_all_kpis: View KPI dashboard for all companies
- update_kpi_value: Manually update a KPI's current value
- list_delegations: View all agent-to-agent delegations (peer-to-peer task handoffs)
- get_available_roles: Discover specialized roles, tools, and pipelines for each company type
- get_agent_tools: Inspect what tools a specific agent actually has
- diagnose_company_setup: Analyze a company and find misconfigured agents with fix suggestions

SELF-DIAGNOSIS & SELF-REPAIR (CRITICAL — READ CAREFULLY):
- BEFORE hiring agents, ALWAYS call get_available_roles to discover what specialized roles exist and what exact role names to use.
- AFTER hiring agents, call get_agent_tools to verify they got the correct tools for their intended purpose.
- If agents are underperforming or failing tasks, call diagnose_company_setup to identify misconfigurations.
- If you find misconfigured agents (wrong role name, missing tools), FIX THEM YOURSELF: fire the agent and rehire with the correct role. Do NOT tell the Owner about issues you can fix.
- Only escalate to the Owner problems you truly cannot solve (e.g., missing credentials, system errors, budget approvals).
- You are a self-sufficient CEO. Diagnose, fix, and optimize autonomously.
"""

    # Conditionally add trading sections only when trading credentials are configured
    trading_configured = _is_trading_configured()
    if trading_configured:
        prompt += """
AUTONOMOUS TRADING EXECUTION:
- Use set_auto_trade_mode to enable/configure automatic trade execution (disabled/conservative/moderate/aggressive).
- When enabled, high-confidence trade signals with proper SL/TP are auto-executed without manual approval.
- Conservative mode requires 90%+ confidence, 2:1+ risk:reward; Aggressive allows 70%+, 1:1+.
- Use get_auto_trade_status to monitor daily auto-trade stats (executed, rejected, reasons).
- Use calculate_position_size to determine optimal lot size based on equity, stop-loss, and risk %.
- Use assess_portfolio_risk for a comprehensive risk assessment (drawdown, exposure, concentration).
- Use update_risk_limits to configure portfolio risk thresholds (max drawdown, max exposure, etc.).
- Use record_trade_journal to log trade outcomes for institutional learning.
- Use query_trade_journal to search past trades semantically (e.g., "what EUR/USD strategies worked?").
- Use get_trade_stats for aggregate trade performance metrics (win rate, profit factor, etc.).
- Use create_market_alert to set price alerts (above/below thresholds, % changes).
- Use list_market_alerts / cancel_market_alert to manage active alerts.
- Use check_trading_platforms to see which exchanges are connected and their status.

TRADING INFRASTRUCTURE (IMPORTANT — READ CAREFULLY):
The system has 4 exchange connectors: OANDA (forex), Binance Testnet (crypto), Capital.com (forex/CFD), MetaAPI/MT5.
These connect automatically if credentials are configured in .env.
Use the check_trading_platforms tool to see which platforms are actually connected and working.

TRADING AGENT ROLES — CRITICAL:
When hiring agents for a trading company, you MUST use these EXACT role names to give them trading tools:
  - "market_researcher" — gets: get_market_prices, get_candles, get_account_summary
  - "analyst" — gets: get_market_prices, technical_analysis, create_signal
  - "risk_manager" — gets: get_portfolio, get_market_prices, get_pending_signals, approve_signal, reject_signal, calculate_position_size
ANY OTHER role name (e.g., "Trade Executor", "Trading Analyst") creates a GENERIC agent with NO trading tools.
The trading pipeline works like this:
  1. market_researcher scans markets, gets live prices and candles
  2. analyst runs technical analysis and creates trade signals
  3. risk_manager reviews signals, approves or rejects them
  4. Approved signals are auto-executed by the system if auto_trade_mode is enabled
You do NOT need a "Trade Executor" agent — execution is handled automatically by the auto-trade system.
To make trading work: hire agents with the exact roles above, give them standing_instructions, and enable auto_trade_mode.
"""

    prompt += """
CEO SELF-IMPROVEMENT:
- Use analyze_prompts to capture agent prompt snapshots and analyze prompt→performance correlation.
- Use get_prompt_recommendations to see specific improvement suggestions for underperforming agents.
- Use analyze_tool_usage to identify unused, failing, or slow tools across the system.
- Use get_agent_tool_profile to see which tools a specific agent uses most/least.
- Use add_knowledge to build a shared knowledge base (lessons learned, best practices, domain insights).
- Use search_knowledge to semantically search the knowledge base before making decisions.
- Use get_knowledge_stats to see KB size and category breakdown.
- Use get_error_patterns to view recurring error patterns (timeouts, rate limits, failures).
- Use get_error_summary for a quick overview of system health from error analysis.
- Use scan_errors to scan audit logs for new error patterns.
- Use create_ab_test to set up A/B tests comparing two agent strategies.
- Use record_ab_result to log task outcomes for A/B test variants.
- Use get_ab_results to compare variant performance and determine winners.
- Use list_ab_tests to see all running/completed experiments.

MULTI-AGENT VOTING:
- Use initiate_vote to poll agents on important decisions (e.g., "Should we proceed with this strategy?").
- Agents respond with APPROVE, REJECT, or ABSTAIN with reasoning.
- Votes have a deadline (30-600 seconds). Non-responders count as ABSTAIN.
- After voting closes, you get a consensus result (approved/rejected/tied/no_quorum).
- Use this for collaborative decisions instead of making unilateral calls.

AGENT DELEGATION:
- Agents can now delegate tasks directly to peers in their company (no CEO bottleneck).
- All delegations are tracked and visible to you via list_delegations.
- Delegation chains are limited to 3 levels deep to prevent loops.
- You retain full visibility and can intervene if delegations go wrong.

COMPANY KPIs:
- Set measurable targets for each company using set_company_kpi.
- Standard metrics: daily_cost_usd, task_success_rate, agent_count, avg_response_time_s.
- KPIs auto-update from real system data when queried.
- Use get_company_kpis to review performance against targets.
- Companies are "on track" when they achieve 80%+ of their target.

BUDGET ENFORCEMENT:
- You can set daily spending limits per agent and per company.
- When an agent exceeds its budget, it gets AUTO-PAUSED. You'll be notified.
- Use check_budget_status to monitor budget enforcement state.
- Use set_agent_budget/set_company_budget proactively for cost control.
- The global daily budget is enforced automatically.

DAILY INTELLIGENCE REPORT:
- Every day a comprehensive report is auto-generated and sent via Telegram.
- You can also generate one on demand using generate_daily_report.
- The report covers: costs, performance, operations, health, budgets, audit, and any domain-specific data.
- Use this data to make informed decisions about agent optimization.

SELF-SCHEDULING:
- On startup, essential schedules are auto-created (health check hourly, budget check 4h, performance review 6h).
- You can add/modify/cancel schedules as needed.
- You should proactively set up additional schedules when you identify needs.
"""

    prompt += f"""MODEL SELECTION:
When hiring agents, you can choose between model tiers (fast/smart/reasoning) or specify a specific model.
Available models on Vertex AI:

Tier "fast" (cheap, high-speed):
- gemini-2.5-flash (~$0.001) — Google, fast general
- gemini-2.5-flash-lite (~$0.001) — Google, cheapest
- zai-org/glm-4.7-maas (~$0.001) — GLM, very cheap
- qwen/qwen3-next-80b-a3b-instruct-maas (~$0.001) — Qwen, cheap
- openai/gpt-oss-20b-maas (~$0.001) — GPT OSS small
- meta/llama-4-scout-17b-16e-instruct-maas (~$0.001) — Llama 4 Scout

Tier "smart" (balanced cost/capability):
- deepseek-ai/deepseek-v3.2-maas (~$0.002) — DeepSeek V3.2, strong general
- deepseek-ai/deepseek-v3.1-maas (~$0.002) — DeepSeek V3.1
- zai-org/glm-5-maas (~$0.004) — GLM 5
- minimaxai/minimax-m2-maas (~$0.002) — MiniMax M2
- openai/gpt-oss-120b-maas (~$0.001) — GPT OSS large
- meta/llama-3.3-70b-instruct-maas (~$0.001) — Llama 3.3
- meta/llama-4-maverick-17b-128e-instruct-maas (~$0.001) — Llama 4 Maverick

Tier "reasoning" (complex analysis, highest capability):
- gemini-2.5-pro (~$0.010) — Google, strong reasoning
- deepseek-ai/deepseek-r1-0528-maas (~$0.005) — DeepSeek R1, reasoning specialist
- moonshotai/kimi-k2-thinking-maas (~$0.005) — Kimi K2, thinking model

Use model_tier for general assignment, or model_id to pick a specific model for an agent.

COMMUNICATION STYLE:
- Be concise and professional
- When reporting, use structured formats
- When making decisions, explain your reasoning briefly
- Always mention when something requires Owner approval

AUTONOMOUS OPERATION:
- You receive [SYSTEM ALERT] messages when agents get stuck or tasks fail. Act on them immediately.
- You receive [SCHEDULED TASK] messages from recurring schedules. Execute them faithfully.
- You receive [SYSTEM STARTUP] once after restart. Review and report the state of operations.
- Use schedule_task to set up recurring health checks (e.g., hourly check_agent_health).
- Use schedule_once for delayed checks (e.g., "check status in 5 minutes").
- Use get_agent_performance to monitor agent quality. Fire or restart underperforming agents.
- Use get_costs to track spending and enforce budgets. Escalate if budget is running high.
- Use restart_agent when you detect stuck or errored agents.
- ALWAYS use check_status and check_agent_health FIRST before making any claims about operations.
- You are a REAL CEO: proactively manage, monitor, and optimize. Don't wait for the Owner to ask.{language_line}

IMPORTANT: You may have existing companies and agents already running. ALWAYS use check_status tool FIRST
before making any claims about what companies or agents exist. Never assume the holding is empty.

CRITICAL — TOOL USAGE:
- You MUST use tool function calls to perform actions. NEVER just describe what you plan to do — CALL the tool.
- When you say "I will dissolve the company", you MUST immediately invoke dissolve_company in the same response.
- When you say "I will assign tasks", you MUST immediately invoke assign_task for each agent.
- If you describe an action plan, execute ALL steps using tool calls in this response. Do NOT just list intentions.
- WRONG: "I will now dissolve Alpha Trading." (text only, no tool call)
- RIGHT: Call dissolve_company(company_id="...") immediately.
- After executing tools, summarize what was ACTUALLY done based on tool results.

CRITICAL — RESPONSE FORMAT (MOST IMPORTANT):
- You are talking to a HUMAN OWNER, NOT a developer. NEVER include raw JSON, code blocks, or technical output in your responses.
- NEVER write tool call JSON like [{{"name": "check_status", "arguments": {{}}}}] in your text. Use the function calling API instead.
- NEVER wrap tool results in ```json code blocks. Always translate data into plain language.
- When you call a tool, the user does NOT see the tool call — they only see your final text response.
- After tools return results, ALWAYS write a clear, friendly, human-readable summary. Speak like a real CEO reporting to the boss.
- WRONG: '```json\n[{{"name": "check_status", "arguments": {{}}}}]\n```'
- WRONG: "Actions completed:\n- {{"schedules": [], "count": 0}}"
- RIGHT: "I've checked the status. Here's what's happening: We have 7 active companies with 9 agents..."
- RIGHT: "Done! I cleared the old schedules and set up fresh ones. Everything is running smoothly now."
- If a tool fails or returns empty data, explain what happened in plain English and suggest next steps.
- Think of yourself as a human CEO writing a Telegram message to your boss. Be natural and informative.

{current_state}
"""

    return prompt


class CEOAgent(BaseAgent):
    """CEO Agent with company and agent management tools."""

    def __init__(self, agent_id: str | None = None, state_summary: str = ""):
        # Build tool list — trading tools are only included when trading credentials exist
        tools = [
                "create_company",
                "hire_agent",
                "fire_agent",
                "dissolve_company",
                "assign_task",
                "check_status",
                "check_agent_health",
                "restart_agent",
                "get_agent_performance",
                "get_costs",
                "get_audit_log",
                "list_skills",
                "get_task_result",
                "list_tasks",
                "request_approval",
                "send_report",
                "send_message_to_agent",
                "schedule_task",
                "schedule_once",
                "list_schedules",
                "cancel_schedule",
                "save_agent_template",
                "list_templates",
                "hire_from_template",
                "set_agent_budget",
                "set_company_budget",
                "check_budget_status",
                "generate_daily_report",
                "initiate_vote",
                "get_vote_results",
                "list_votes",
                "set_company_kpi",
                "get_company_kpis",
                "get_all_kpis",
                "update_kpi_value",
                "list_delegations",
                # Self-discovery & diagnostics
                "get_available_roles",
                "get_agent_tools",
                "diagnose_company_setup",
        ]

        # Conditionally add trading tools
        if _is_trading_configured():
            tools.extend(_TRADING_TOOL_NAMES)

        tools.extend([
                "analyze_prompts",
                "get_prompt_recommendations",
                "analyze_tool_usage",
                "get_agent_tool_profile",
                "add_knowledge",
                "search_knowledge",
                "get_knowledge_stats",
                "get_error_patterns",
                "get_error_summary",
                "scan_errors",
                "create_ab_test",
                "record_ab_result",
                "get_ab_results",
                "list_ab_tests",
                # Phase 5: Advanced Governance
                "evaluate_approval_tier",
                "set_approval_thresholds",
                "get_approval_thresholds",
                "get_tier_analytics",
                "get_audit_timeline",
                "detect_audit_anomalies",
                "get_action_breakdown",
                "generate_accountability_report",
                "get_accountability_stats",
                "list_rollback_actions",
                "request_rollback",
                "get_rollback_history",
                "list_owners",
                "add_owner",
                "get_owner_permissions",
        ])

        super().__init__(
            agent_id=agent_id or str(uuid4()),
            name="CEO",
            role="Chief Executive Officer",
            system_prompt=_build_ceo_system_prompt(state_summary),
            model_tier="reasoning",
            tools=tools,
            browser_enabled=True,
            sandbox_enabled=True,
        )
        self._conversations: list[dict] = []
        self._history_loaded = False
        self._run_lock = asyncio.Lock()  # prevent concurrent run() calls

    # --- Conversation history persistence via Redis ---

    _REDIS_KEY = "ceo:conversation_history"

    async def _load_history(self) -> None:
        """Load conversation history from Redis on first call."""
        if self._history_loaded:
            return
        self._history_loaded = True
        try:
            raw = await redis_pool.get(self._REDIS_KEY)
            if raw:
                self._conversations = json.loads(raw)
                logger.info(f"Loaded {len(self._conversations)} conversation entries from Redis")
        except Exception as e:
            logger.debug(f"Could not load conversation history: {e}")

    async def _save_history(self) -> None:
        """Persist conversation history to Redis."""
        try:
            from app.config import get_settings
            settings = get_settings()
            max_entries = settings.conversation_history_size * 2  # user + assistant pairs
            # Trim at safe boundaries — never orphan tool messages
            trimmed = self._trim_history(self._conversations, max_entries)
            self._conversations = trimmed
            await redis_pool.set(self._REDIS_KEY, json.dumps(trimmed), ex=86400 * 7)  # 7 day TTL
        except Exception as e:
            logger.debug(f"Could not save conversation history: {e}")

    @staticmethod
    def _trim_history(conversations: list[dict], max_entries: int) -> list[dict]:
        """Trim conversation history at safe boundaries.

        Never cuts inside a tool interaction group (assistant-with-tool_calls → tool results).
        Finds the earliest safe cut point at or after len-max_entries.
        """
        if len(conversations) <= max_entries:
            return conversations

        # Start from the naive cut point and walk forward to a safe boundary
        cut = len(conversations) - max_entries
        while cut < len(conversations):
            msg = conversations[cut]
            role = msg.get("role", "")
            # Safe boundaries: a "user" message or an "assistant" without tool_calls
            if role == "user" or (role == "assistant" and not msg.get("tool_calls")):
                break
            cut += 1  # skip orphaned tool or assistant-with-tool_calls

        if cut >= len(conversations):
            # Fallback: keep everything
            return conversations
        return conversations[cut:]

    def _get_history_messages(self) -> list[dict]:
        """Return conversation history as role/content dicts for the LLM.

        Preserves tool_calls on assistant messages and tool_call_id on tool
        messages so the LLM sees proper function-calling conversation flow.
        """
        msgs = []
        for c in self._conversations:
            msg: dict = {"role": c["role"], "content": c["content"]}
            if c.get("tool_calls"):
                msg["tool_calls"] = c["tool_calls"]
            if c.get("tool_call_id"):
                msg["tool_call_id"] = c["tool_call_id"]
            msgs.append(msg)
        return msgs

    def _get_tools_schema(self) -> list[dict]:
        """OpenAI-format tool definitions for the CEO."""
        # Start with base tools (sandbox, browser if enabled)
        schemas = super()._get_tools_schema()
        schemas.extend([
            {
                "type": "function",
                "function": {
                    "name": "create_company",
                    "description": "Create a new company/department within the AI Holding. Use this when you need to establish a new business unit.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Company name (e.g., 'Forex Trading Division')",
                            },
                            "type": {
                                "type": "string",
                                "enum": [
                                    "trading",
                                    "research",
                                    "marketing",
                                    "analytics",
                                    "general",
                                ],
                                "description": "Type of company",
                            },
                            "description": {
                                "type": "string",
                                "description": "Brief description of the company's purpose",
                            },
                        },
                        "required": ["name", "type", "description"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "hire_agent",
                    "description": "Hire (create) a new AI agent within a company. Assign them a specific role and instructions. Enable sandbox for code execution, browser for web access. Role names determine agent capabilities — use descriptive roles matching the company type (e.g., 'researcher', 'writer', 'analyst'). For trading companies specifically, use exact roles 'market_researcher', 'analyst', or 'risk_manager' to get trading tools.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "company_id": {
                                "type": "string",
                                "description": "ID of the company to hire the agent into",
                            },
                            "name": {"type": "string", "description": "Agent's name"},
                            "role": {
                                "type": "string",
                                "description": "Agent's role — a descriptive name for what the agent does. Role determines the agent's primary function. Examples: 'researcher', 'writer', 'analyst', 'coordinator'. For trading companies: use 'market_researcher', 'analyst', or 'risk_manager' to enable trading tools.",
                            },
                            "instructions": {
                                "type": "string",
                                "description": "Detailed instructions for the agent's behavior and goals",
                            },
                            "model_tier": {
                                "type": "string",
                                "enum": ["fast", "smart", "reasoning"],
                                "description": "LLM model tier: fast (cheap bulk), smart (balanced), reasoning (complex analysis). Default: smart.",
                            },
                            "model_id": {
                                "type": "string",
                                "description": "Optional: specific model ID to use instead of tier (e.g., 'deepseek-ai/deepseek-r1-0528-maas', 'moonshotai/kimi-k2-thinking-maas', 'gemini-2.5-pro'). If set, overrides model_tier.",
                            },
                            "sandbox_enabled": {
                                "type": "boolean",
                                "description": "Enable Docker sandbox for code execution (execute_code tool). Default: false.",
                            },
                            "browser_enabled": {
                                "type": "boolean",
                                "description": "Enable browser for web browsing (browse_url, screenshot_url tools). Default: false.",
                            },
                            "skills": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of skill names to enable for this agent (e.g., ['web_search', 'calculator']). Default: all skills.",
                            },
                            "network_enabled": {
                                "type": "boolean",
                                "description": "Allow network access in sandbox (for API calls). Only used if sandbox_enabled=true. Default: false.",
                            },
                            "few_shot_examples": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "input": {"type": "string", "description": "Example user input or task"},
                                        "output": {"type": "string", "description": "Expected agent response or behaviour"},
                                    },
                                    "required": ["input", "output"],
                                },
                                "description": "Few-shot examples to guide the agent's behavior. Each example has an input and expected output.",
                            },
                            "standing_instructions": {
                                "type": "string",
                                "description": "Autonomous standing task the agent should perform periodically without being asked. E.g., 'Check for updates and prepare a summary every cycle'. If set, the agent will run this task automatically on a schedule.",
                            },
                            "work_interval_hours": {
                                "type": "number",
                                "description": "How often (in hours) the agent should execute its standing instructions. Default: 1 hour. E.g., 0.5 = every 30 min, 4 = every 4 hours.",
                            },
                        },
                        "required": ["company_id", "name", "role", "instructions"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fire_agent",
                    "description": "Remove an agent from a company.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_id": {
                                "type": "string",
                                "description": "ID of the agent to remove",
                            },
                            "reason": {
                                "type": "string",
                                "description": "Reason for removing the agent",
                            },
                        },
                        "required": ["agent_id", "reason"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "dissolve_company",
                    "description": "Dissolve an entire company: fires all its agents and deletes the company. Use when a company is no longer needed or performing poorly.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "company_id": {
                                "type": "string",
                                "description": "ID of the company to dissolve",
                            },
                            "reason": {
                                "type": "string",
                                "description": "Reason for dissolving the company",
                            },
                        },
                        "required": ["company_id", "reason"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "assign_task",
                    "description": "Assign a task to a specific agent. The task runs asynchronously in the background. Use get_task_result to check the status/result later.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_id": {
                                "type": "string",
                                "description": "ID of the agent to assign the task to",
                            },
                            "task_description": {
                                "type": "string",
                                "description": "Detailed description of the task",
                            },
                            "wait": {
                                "type": "boolean",
                                "description": "If true, wait for the task to complete and return the result. If false (default), return immediately with a task_id.",
                            },
                            "priority": {
                                "type": "string",
                                "enum": ["low", "normal", "high", "critical"],
                                "description": "Task priority (default: normal). Higher priority tasks get processed first.",
                            },
                            "max_retries": {
                                "type": "integer",
                                "description": "Max retry attempts on failure (0-5, default: 0).",
                            },
                        },
                        "required": ["agent_id", "task_description"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "check_status",
                    "description": "Check the status of all companies and agents in the holding.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "check_agent_health",
                    "description": "Check if any agents are stuck, unresponsive, or in error state. Returns stuck agents, their duration, and health summary. Use this when you suspect an agent is taking too long or not responding.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_task_result",
                    "description": "Check the status and result of an async task by its task_id.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task_id": {
                                "type": "string",
                                "description": "The task ID returned by assign_task",
                            },
                        },
                        "required": ["task_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_tasks",
                    "description": "List all async tasks with their statuses. Optionally filter by status.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": "string",
                                "enum": ["pending", "running", "completed", "failed"],
                                "description": "Filter tasks by status (optional)",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "request_approval",
                    "description": "Request Owner approval for an important decision. Use this for: creating companies, spending money, risky trades, major changes.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "description": {
                                "type": "string",
                                "description": "Clear description of what you need approval for",
                            },
                            "category": {
                                "type": "string",
                                "enum": [
                                    "company_creation",
                                    "trade",
                                    "hiring",
                                    "spending",
                                    "general",
                                ],
                                "description": "Category of the approval request",
                            },
                            "details": {
                                "type": "string",
                                "description": "Additional details and your reasoning",
                            },
                        },
                        "required": ["description", "category"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "send_report",
                    "description": "Send a structured report to the Owner.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Report title"},
                            "content": {
                                "type": "string",
                                "description": "Report content in markdown format",
                            },
                        },
                        "required": ["title", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "send_message_to_agent",
                    "description": "Send a message or instruction to a specific sub-agent. The agent will process it and respond.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_id": {
                                "type": "string",
                                "description": "ID of the agent to message",
                            },
                            "message": {
                                "type": "string",
                                "description": "The message or instruction to send",
                            },
                        },
                        "required": ["agent_id", "message"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "schedule_task",
                    "description": "Schedule a recurring task for any agent. The task will run automatically at the specified interval.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_id": {
                                "type": "string",
                                "description": "ID of the agent to run the task (use 'ceo' for yourself)",
                            },
                            "description": {
                                "type": "string",
                                "description": "What the agent should do each time",
                            },
                            "interval_hours": {
                                "type": "number",
                                "description": "How often to run (in hours, e.g. 4 = every 4 hours, 0.5 = every 30 minutes)",
                            },
                        },
                        "required": ["agent_id", "description", "interval_hours"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_schedules",
                    "description": "List all scheduled recurring tasks.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "cancel_schedule",
                    "description": "Cancel a scheduled recurring task by its ID.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task_id": {
                                "type": "string",
                                "description": "ID of the scheduled task to cancel",
                            },
                        },
                        "required": ["task_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "save_agent_template",
                    "description": "Save an agent configuration as a reusable template for quick hiring later.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Template name"},
                            "role": {"type": "string", "description": "Agent role"},
                            "instructions": {"type": "string", "description": "Agent instructions"},
                            "model_tier": {"type": "string", "enum": ["fast", "smart", "reasoning"]},
                            "sandbox_enabled": {"type": "boolean", "description": "Enable sandbox code execution"},
                            "browser_enabled": {"type": "boolean", "description": "Enable web browsing"},
                            "skills": {"type": "array", "items": {"type": "string"}, "description": "Skill names to enable"},
                        },
                        "required": ["name", "role", "instructions"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_templates",
                    "description": "List all saved agent templates.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "hire_from_template",
                    "description": "Hire a new agent using a saved template.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "template_id": {"type": "string", "description": "ID of the template to use"},
                            "company_id": {"type": "string", "description": "Company to hire into"},
                            "name": {"type": "string", "description": "Optional custom name for the agent"},
                        },
                        "required": ["template_id", "company_id"],
                    },
                },
            },
            # --- Power tools for autonomous operation ---
            {
                "type": "function",
                "function": {
                    "name": "restart_agent",
                    "description": "Restart a stuck or errored agent. Stops it, resets state to IDLE, and starts it again.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_id": {"type": "string", "description": "ID of the agent to restart"},
                        },
                        "required": ["agent_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_agent_performance",
                    "description": "Get performance scores and grades for agents. Shows success rate, average response time, and overall grade (A-F). Call without agent_id for all agents.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_id": {"type": "string", "description": "Optional: specific agent ID. Omit for all agents."},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_costs",
                    "description": "Get cost/spending data. Shows total daily spend, per-agent costs, and budget usage. Essential for budget management.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_id": {"type": "string", "description": "Optional: specific agent ID. Omit for overall summary."},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_audit_log",
                    "description": "Get recent audit log entries showing all actions performed by you and agents. Useful for reviewing what happened.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "limit": {"type": "integer", "description": "Max entries to return (default: 20, max: 100)"},
                            "agent_id": {"type": "string", "description": "Optional: filter by agent ID"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_skills",
                    "description": "List all available skills that can be assigned to agents when hiring. Shows skill name, description, and tools provided.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "schedule_once",
                    "description": "Schedule a one-time task to run after a delay. Use this when someone says 'check X in 5 minutes' or 'remind me in 1 hour'. The task runs once and is automatically removed.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_id": {"type": "string", "description": "Agent ID to run the task (use 'ceo' for yourself)"},
                            "description": {"type": "string", "description": "What to do when the timer fires"},
                            "delay_minutes": {
                                "type": "number",
                                "description": "Minutes to wait before running (1-1440)",
                            },
                        },
                        "required": ["agent_id", "description", "delay_minutes"],
                    },
                },
            },
            # --- Budget enforcement tools ---
            {
                "type": "function",
                "function": {
                    "name": "set_agent_budget",
                    "description": "Set a daily spending limit for a specific agent. When the agent's daily cost exceeds this limit, it will be auto-paused. Set to 0 to remove the limit.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_id": {"type": "string", "description": "ID of the agent to set budget for"},
                            "daily_limit_usd": {
                                "type": "number",
                                "description": "Max daily spending in USD (e.g., 0.50). Set to 0 to remove limit.",
                            },
                        },
                        "required": ["agent_id", "daily_limit_usd"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "set_company_budget",
                    "description": "Set a daily spending limit for all agents in a company combined. When exceeded, all company agents are auto-paused. Set to 0 to remove.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "company_id": {"type": "string", "description": "ID of the company to set budget for"},
                            "daily_limit_usd": {
                                "type": "number",
                                "description": "Max daily spending in USD for the entire company. Set to 0 to remove.",
                            },
                        },
                        "required": ["company_id", "daily_limit_usd"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "check_budget_status",
                    "description": "View all budget limits, enforcement state, and which agents/companies have been paused today due to budget overruns.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "generate_daily_report",
                    "description": "Generate a comprehensive daily intelligence report covering costs, performance, operations, trading, health, budgets, and audit log. Returns both structured data and formatted text.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            # --- Phase 2: Multi-Agent Voting ---
            {
                "type": "function",
                "function": {
                    "name": "initiate_vote",
                    "description": "Start a multi-agent vote on a decision. Each agent will respond APPROVE, REJECT, or ABSTAIN with reasoning. Returns vote_id to check results.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "The question or proposal to vote on (e.g., 'Should we increase our EUR/USD long position?')",
                            },
                            "agent_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of agent IDs to participate in the vote",
                            },
                            "deadline_seconds": {
                                "type": "integer",
                                "description": "Seconds to wait for responses (30-600, default: 120)",
                            },
                        },
                        "required": ["question", "agent_ids"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_vote_results",
                    "description": "Get the status and results of a voting session by its vote_id.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "vote_id": {
                                "type": "string",
                                "description": "The vote ID returned by initiate_vote",
                            },
                        },
                        "required": ["vote_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_votes",
                    "description": "List all voting sessions, optionally filtered by status.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": "string",
                                "enum": ["open", "closed", "expired"],
                                "description": "Optional: filter by vote status",
                            },
                        },
                    },
                },
            },
            # --- Phase 2: Company KPIs ---
            {
                "type": "function",
                "function": {
                    "name": "set_company_kpi",
                    "description": "Set a KPI target for a company. Standard metrics: daily_cost_usd, task_success_rate, agent_count, avg_response_time_s. You can also define custom metric names.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "company_id": {"type": "string", "description": "Company ID"},
                            "company_name": {"type": "string", "description": "Company name (for display)"},
                            "kpi_name": {"type": "string", "description": "KPI name (e.g., 'Task Success Rate')"},
                            "metric": {
                                "type": "string",
                                "description": "Metric to measure: daily_cost_usd, task_success_rate, agent_count, avg_response_time_s, or a custom name",
                            },
                            "target_value": {"type": "number", "description": "Target value to achieve"},
                            "unit": {"type": "string", "description": "Unit (e.g., '%', 'USD', 'seconds')"},
                            "direction": {
                                "type": "string",
                                "enum": ["higher_is_better", "lower_is_better"],
                                "description": "Whether higher or lower values are better (default: higher_is_better)",
                            },
                        },
                        "required": ["company_id", "company_name", "kpi_name", "metric", "target_value"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_company_kpis",
                    "description": "Get all KPIs and their current values for a specific company. Auto-updates standard metrics from real data.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "company_id": {"type": "string", "description": "Company ID to get KPIs for"},
                        },
                        "required": ["company_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_all_kpis",
                    "description": "Get KPI dashboards for all companies with summary health scores.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "update_kpi_value",
                    "description": "Manually update the current value of a KPI (for custom metrics that can't be auto-computed).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "company_id": {"type": "string", "description": "Company ID"},
                            "kpi_name": {"type": "string", "description": "Name of the KPI to update"},
                            "current_value": {"type": "number", "description": "New current value"},
                        },
                        "required": ["company_id", "kpi_name", "current_value"],
                    },
                },
            },
            # --- Phase 2: Delegation visibility ---
            {
                "type": "function",
                "function": {
                    "name": "list_delegations",
                    "description": "View all agent-to-agent task delegations. Shows who delegated to whom, status, and results.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": "string",
                                "enum": ["pending", "accepted", "in_progress", "completed", "failed", "rejected"],
                                "description": "Optional: filter by delegation status",
                            },
                            "company_id": {
                                "type": "string",
                                "description": "Optional: filter by company ID",
                            },
                        },
                    },
                },
            },
            # Self-discovery & diagnostics tools
            {
                "type": "function",
                "function": {
                    "name": "get_available_roles",
                    "description": "Discover what specialized agent roles exist for each company type, what tools those roles grant, available workflows/pipelines, and recommended skills. Call this BEFORE hiring agents to know the correct role names.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "company_type": {
                                "type": "string",
                                "description": "Optional: filter by company type (e.g., 'trading', 'research', 'marketing', 'analytics', 'general')",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_agent_tools",
                    "description": "Show what tools a specific agent actually has. Use this to verify an agent was set up correctly after hiring, or to debug why an agent can't perform certain actions.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_id": {"type": "string", "description": "The agent ID to inspect"},
                        },
                        "required": ["agent_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "diagnose_company_setup",
                    "description": "Analyze a company's agents and find misconfigurations. Checks if agents have the correct specialized roles for the company type, verifies tool availability, and returns actionable fix suggestions. Use this to self-diagnose and fix problems before reporting them to the Owner.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "company_id": {"type": "string", "description": "The company ID to diagnose"},
                        },
                        "required": ["company_id"],
                    },
                },
            },
        ])

        # Conditionally add trading tool schemas only when trading credentials are configured
        if _is_trading_configured():
            schemas.extend([
            {
                "type": "function",
                "function": {
                    "name": "set_auto_trade_mode",
                    "description": "Set auto-trade execution mode. Modes: disabled (off), conservative (90%+ confidence, 2:1 RR), moderate (80%+, 1.5:1), aggressive (70%+, 1:1).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "mode": {
                                "type": "string",
                                "enum": ["disabled", "conservative", "moderate", "aggressive"],
                                "description": "Auto-trade mode",
                            },
                        },
                        "required": ["mode"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_auto_trade_status",
                    "description": "Get auto-trade executor status: current config, daily stats (trades executed/rejected), and recent execution log.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "calculate_position_size",
                    "description": "Calculate optimal position size based on risk management. Uses account equity, stop-loss distance, and risk percentage.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "Trading symbol (e.g., EURUSD, BTCUSDT)"},
                            "entry_price": {"type": "number", "description": "Expected entry price"},
                            "stop_loss": {"type": "number", "description": "Stop-loss price level"},
                            "risk_pct": {
                                "type": "number",
                                "description": "Risk per trade as % of equity (default 1.0)",
                            },
                        },
                        "required": ["symbol", "entry_price", "stop_loss"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "assess_portfolio_risk",
                    "description": "Perform comprehensive portfolio risk assessment: drawdown from peak, total exposure, per-position concentration, correlation analysis, and risk alerts.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "update_risk_limits",
                    "description": "Update portfolio risk limits (max drawdown, max exposure, max single position size, etc.).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "max_drawdown_pct": {"type": "number", "description": "Max drawdown from peak equity (%)"},
                            "max_total_exposure_pct": {"type": "number", "description": "Max total position value / equity (%)"},
                            "max_single_position_pct": {"type": "number", "description": "Max single position / equity (%)"},
                            "max_open_positions": {"type": "integer", "description": "Max number of open positions"},
                            "auto_close_on_critical": {"type": "boolean", "description": "Auto-close positions at critical risk level"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "record_trade_journal",
                    "description": "Record a trade outcome in the journal for learning. Stores with vector embedding for semantic search.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "trade_id": {"type": "string", "description": "Trade ID to record"},
                            "symbol": {"type": "string", "description": "Trading symbol"},
                            "direction": {"type": "string", "enum": ["buy", "sell"], "description": "Trade direction"},
                            "entry_price": {"type": "number", "description": "Entry price"},
                            "exit_price": {"type": "number", "description": "Exit price (if closed)"},
                            "pnl": {"type": "number", "description": "Profit/loss amount"},
                            "outcome": {
                                "type": "string",
                                "enum": ["win", "loss", "breakeven", "open"],
                                "description": "Trade outcome",
                            },
                            "reasoning": {"type": "string", "description": "Why was this trade taken?"},
                            "lessons": {"type": "string", "description": "What was learned from this trade?"},
                        },
                        "required": ["trade_id", "symbol", "direction", "entry_price"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "query_trade_journal",
                    "description": "Search past trade journal entries semantically. Ask questions like 'what EUR/USD strategies worked?' or 'why did crypto trades fail last week?'.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Natural language search query"},
                            "outcome_filter": {
                                "type": "string",
                                "enum": ["win", "loss", "breakeven"],
                                "description": "Optional: filter by outcome",
                            },
                            "symbol_filter": {"type": "string", "description": "Optional: filter by symbol"},
                            "limit": {"type": "integer", "description": "Max results (default 10)"},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_trade_stats",
                    "description": "Get aggregate trade statistics from the journal: win rate, profit factor, avg win/loss, total PnL, high-confidence win rate.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "Optional: filter by symbol"},
                            "days": {"type": "integer", "description": "Lookback period in days (default 30)"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "create_market_alert",
                    "description": "Set a price alert for a trading symbol. Get notified when price goes above/below a level or changes by a percentage.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "Trading symbol (e.g., EURUSD, BTCUSDT)"},
                            "alert_type": {
                                "type": "string",
                                "enum": ["price_above", "price_below", "pct_change"],
                                "description": "Type of alert",
                            },
                            "threshold": {"type": "number", "description": "Price level or % change threshold"},
                            "message": {"type": "string", "description": "Custom message when triggered"},
                            "repeat": {"type": "boolean", "description": "Re-arm alert after triggering (default false)"},
                        },
                        "required": ["symbol", "alert_type", "threshold"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_market_alerts",
                    "description": "List all market alerts with their status (active, triggered, cancelled).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": "string",
                                "enum": ["active", "triggered", "cancelled"],
                                "description": "Optional: filter by status",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "cancel_market_alert",
                    "description": "Cancel an active market alert by its ID.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "alert_id": {"type": "string", "description": "Alert ID to cancel"},
                        },
                        "required": ["alert_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "check_trading_platforms",
                    "description": "Check which trading platforms/exchanges are connected, their status, and available instruments. Shows OANDA, Binance, Capital.com, MetaAPI connectivity.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            ])

        # Phase 4: CEO Self-Improvement
        schemas.extend([
            {
                "type": "function",
                "function": {
                    "name": "analyze_prompts",
                    "description": "Capture prompt snapshots for all agents and analyze prompt effectiveness. Shows top performers, underperformers, declining agents, and improvement recommendations.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_prompt_recommendations",
                    "description": "Get specific prompt improvement recommendations for underperforming agents.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_id": {"type": "string", "description": "Optional: specific agent ID"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "analyze_tool_usage",
                    "description": "Analyze tool usage patterns across all agents. Identifies unused, failing, slow, and top-performing tools with actionable recommendations.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_agent_tool_profile",
                    "description": "Get tool usage profile for a specific agent — which tools it uses most/least.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_id": {"type": "string", "description": "Agent ID to profile"},
                        },
                        "required": ["agent_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_knowledge",
                    "description": "Add an entry to the shared knowledge base. Suggested categories: lessons_learned, best_practices, domain_insights, operational_rules, strategies, error_solutions, agent_guidelines. You can use any category name.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "The knowledge text to store"},
                            "category": {"type": "string", "description": "Knowledge category"},
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Optional search tags",
                            },
                            "importance": {"type": "number", "description": "0.0-1.0 importance (default 0.7)"},
                        },
                        "required": ["content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_knowledge",
                    "description": "Semantically search the shared knowledge base for relevant information.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Natural language search query"},
                            "category": {"type": "string", "description": "Optional category filter"},
                            "limit": {"type": "integer", "description": "Max results (default 10)"},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_knowledge_stats",
                    "description": "Get knowledge base statistics: total entries, categories breakdown, capacity.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_error_patterns",
                    "description": "View detected recurring error patterns across the system. Filter by severity or error type.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "severity": {
                                "type": "string",
                                "enum": ["low", "medium", "high", "critical"],
                                "description": "Optional severity filter",
                            },
                            "error_type": {
                                "type": "string",
                                "description": "Optional error type filter (timeout, rate_limit, permission, connection, parse_error, api_error)",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_error_summary",
                    "description": "Get a quick summary of system error health: total patterns, severity breakdown, top issues.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "scan_errors",
                    "description": "Scan recent audit logs for new error patterns. Run periodically to keep error detection up-to-date.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "create_ab_test",
                    "description": "Create an A/B test experiment comparing two agent strategies/configurations.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Experiment name"},
                            "hypothesis": {"type": "string", "description": "What you're testing"},
                            "agent_a_id": {"type": "string", "description": "Agent ID for variant A"},
                            "agent_a_desc": {"type": "string", "description": "Description of variant A's strategy"},
                            "agent_b_id": {"type": "string", "description": "Agent ID for variant B"},
                            "agent_b_desc": {"type": "string", "description": "Description of variant B's strategy"},
                            "max_tasks": {"type": "integer", "description": "Tasks per variant before concluding (default 20)"},
                        },
                        "required": ["name", "hypothesis", "agent_a_id", "agent_a_desc", "agent_b_id", "agent_b_desc"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "record_ab_result",
                    "description": "Record a task result for an A/B test variant.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "experiment_id": {"type": "string", "description": "Experiment ID"},
                            "variant": {"type": "string", "enum": ["A", "B"], "description": "Which variant"},
                            "success": {"type": "boolean", "description": "Task completed successfully?"},
                            "score": {"type": "number", "description": "Quality score 0-100"},
                            "cost_usd": {"type": "number", "description": "Cost of this task in USD"},
                            "tokens": {"type": "integer", "description": "Tokens used"},
                            "time_s": {"type": "number", "description": "Time taken in seconds"},
                        },
                        "required": ["experiment_id", "variant", "success"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_ab_results",
                    "description": "Get detailed results and comparison for an A/B test experiment.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "experiment_id": {"type": "string", "description": "Experiment ID"},
                        },
                        "required": ["experiment_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_ab_tests",
                    "description": "List all A/B test experiments, optionally filtered by status.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": "string",
                                "enum": ["running", "completed", "cancelled"],
                                "description": "Optional status filter",
                            },
                        },
                    },
                },
            },
            # Phase 5: Advanced Governance
            {
                "type": "function",
                "function": {
                    "name": "evaluate_approval_tier",
                    "description": "Evaluate which approval tier an action falls into based on category and estimated cost. Returns: auto_approve, ceo_decide, or owner_approval.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "Action category (trade, company_creation, hiring, firing, budget, general, tool_execution, data_access)"},
                            "estimated_cost_usd": {"type": "number", "description": "Estimated cost of the action in USD"},
                        },
                        "required": ["category", "estimated_cost_usd"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "set_approval_thresholds",
                    "description": "Set approval tier thresholds for a category. Below low_max=auto-approve, between low/medium=CEO decides, above medium_max=owner approval.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "Category to configure"},
                            "low_max_usd": {"type": "number", "description": "Max USD for auto-approve tier"},
                            "medium_max_usd": {"type": "number", "description": "Max USD for CEO-decide tier (above = owner approval)"},
                        },
                        "required": ["category", "low_max_usd", "medium_max_usd"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_approval_thresholds",
                    "description": "Get current approval tier thresholds for all categories or a specific one.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "Optional: specific category to query"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_tier_analytics",
                    "description": "Get analytics on approval tier routing decisions: distribution, auto-approve rate, costs.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "limit": {"type": "integer", "description": "Max decisions to analyse (default 100)"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_audit_timeline",
                    "description": "Get a timeline of audit events bucketed by time intervals.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "hours": {"type": "integer", "description": "Hours to look back (default 24)"},
                            "bucket_minutes": {"type": "integer", "description": "Bucket size in minutes (default 60)"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "detect_audit_anomalies",
                    "description": "Detect anomalies in audit logs: action spikes, failure rate spikes, unusual patterns.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "sensitivity": {"type": "number", "description": "Z-score sensitivity (default 2.0, lower=more sensitive)"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_action_breakdown",
                    "description": "Get a breakdown of actions by type, agent, and success rate for the given period.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "hours": {"type": "integer", "description": "Hours to look back (default 24)"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "generate_accountability_report",
                    "description": "Generate a CEO accountability report: autonomous vs escalated actions, success rates, recommendations.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "period_hours": {"type": "integer", "description": "Period in hours (default 168 = 7 days)"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_accountability_stats",
                    "description": "Get summary of all generated accountability reports.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_rollback_actions",
                    "description": "List tracked reversible actions that can be rolled back.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string", "enum": ["available", "rolled_back", "expired", "failed"], "description": "Optional status filter"},
                            "limit": {"type": "integer", "description": "Max results (default 50)"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "request_rollback",
                    "description": "Request rollback of a previously tracked action. Requires owner approval for high-risk actions.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "rollback_id": {"type": "string", "description": "ID of the rollback action to execute"},
                        },
                        "required": ["rollback_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_rollback_history",
                    "description": "Get summary of rollback history: total tracked, available, completed rollbacks.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_owners",
                    "description": "List all system owners and their roles/permissions.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "enum": ["admin", "approver", "viewer"], "description": "Optional role filter"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_owner",
                    "description": "Add or update a system owner with role-based permissions.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "owner_id": {"type": "string", "description": "Unique owner identifier"},
                            "name": {"type": "string", "description": "Owner display name"},
                            "role": {"type": "string", "enum": ["admin", "approver", "viewer"], "description": "Owner role (default: viewer)"},
                            "contact": {"type": "string", "description": "Optional contact info (email, telegram, etc.)"},
                        },
                        "required": ["owner_id", "name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_owner_permissions",
                    "description": "Get permissions for a specific owner by ID.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "owner_id": {"type": "string", "description": "Owner ID to check"},
                        },
                        "required": ["owner_id"],
                    },
                },
            },
        ])
        return schemas

    async def execute_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute CEO-specific tools using real DB-backed services."""
        result = await self._dispatch_tool(tool_name, arguments)
        # Audit log every tool call
        try:
            from app.services.audit_log import audit_log
            success = True
            summary = result[:500] if isinstance(result, str) else str(result)[:500]
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict) and parsed.get("success") is False:
                    success = False
            except (json.JSONDecodeError, TypeError):
                pass
            await audit_log.log(
                agent_id=self.agent_id,
                agent_name=self.name,
                action=tool_name,
                arguments=arguments,
                result_summary=summary,
                success=success,
                source="tool_call",
            )
        except Exception:
            pass  # never let audit logging break tool execution
        return result

    async def _dispatch_tool(self, tool_name: str, arguments: dict) -> str:
        """Route tool calls to their implementations."""
        match tool_name:
            case "create_company":
                return await self._tool_create_company(arguments)
            case "hire_agent":
                return await self._tool_hire_agent(arguments)
            case "fire_agent":
                return await self._tool_fire_agent(arguments)
            case "dissolve_company":
                return await self._tool_dissolve_company(arguments)
            case "assign_task":
                return await self._tool_assign_task(arguments)
            case "check_status":
                return await self._tool_check_status(arguments)
            case "check_agent_health":
                return await self._tool_check_agent_health(arguments)
            case "get_task_result":
                return await self._tool_get_task_result(arguments)
            case "list_tasks":
                return await self._tool_list_tasks(arguments)
            case "request_approval":
                return await self._tool_request_approval(arguments)
            case "send_report":
                return await self._tool_send_report(arguments)
            case "send_message_to_agent":
                return await self._tool_send_message_to_agent(arguments)
            case "schedule_task":
                return await self._tool_schedule_task(arguments)
            case "list_schedules":
                return await self._tool_list_schedules(arguments)
            case "cancel_schedule":
                return await self._tool_cancel_schedule(arguments)
            case "save_agent_template":
                return await self._tool_save_agent_template(arguments)
            case "list_templates":
                return await self._tool_list_templates(arguments)
            case "hire_from_template":
                return await self._tool_hire_from_template(arguments)
            case "restart_agent":
                return await self._tool_restart_agent(arguments)
            case "get_agent_performance":
                return await self._tool_get_agent_performance(arguments)
            case "get_costs":
                return await self._tool_get_costs(arguments)
            case "get_audit_log":
                return await self._tool_get_audit_log(arguments)
            case "list_skills":
                return await self._tool_list_skills(arguments)
            case "schedule_once":
                return await self._tool_schedule_once(arguments)
            case "set_agent_budget":
                return await self._tool_set_agent_budget(arguments)
            case "set_company_budget":
                return await self._tool_set_company_budget(arguments)
            case "check_budget_status":
                return await self._tool_check_budget_status(arguments)
            case "generate_daily_report":
                return await self._tool_generate_daily_report(arguments)
            case "initiate_vote":
                return await self._tool_initiate_vote(arguments)
            case "get_vote_results":
                return await self._tool_get_vote_results(arguments)
            case "list_votes":
                return await self._tool_list_votes(arguments)
            case "set_company_kpi":
                return await self._tool_set_company_kpi(arguments)
            case "get_company_kpis":
                return await self._tool_get_company_kpis(arguments)
            case "get_all_kpis":
                return await self._tool_get_all_kpis(arguments)
            case "update_kpi_value":
                return await self._tool_update_kpi_value(arguments)
            case "list_delegations":
                return await self._tool_list_delegations(arguments)
            # Self-discovery & diagnostics
            case "get_available_roles":
                return await self._tool_get_available_roles(arguments)
            case "get_agent_tools":
                return await self._tool_get_agent_tools(arguments)
            case "diagnose_company_setup":
                return await self._tool_diagnose_company_setup(arguments)
            case "set_auto_trade_mode":
                return await self._tool_set_auto_trade_mode(arguments)
            case "get_auto_trade_status":
                return await self._tool_get_auto_trade_status(arguments)
            case "calculate_position_size":
                return await self._tool_calculate_position_size(arguments)
            case "assess_portfolio_risk":
                return await self._tool_assess_portfolio_risk(arguments)
            case "update_risk_limits":
                return await self._tool_update_risk_limits(arguments)
            case "record_trade_journal":
                return await self._tool_record_trade_journal(arguments)
            case "query_trade_journal":
                return await self._tool_query_trade_journal(arguments)
            case "get_trade_stats":
                return await self._tool_get_trade_stats(arguments)
            case "create_market_alert":
                return await self._tool_create_market_alert(arguments)
            case "list_market_alerts":
                return await self._tool_list_market_alerts(arguments)
            case "cancel_market_alert":
                return await self._tool_cancel_market_alert(arguments)
            case "check_trading_platforms":
                return await self._tool_check_trading_platforms(arguments)
            # Phase 4: CEO Self-Improvement
            case "analyze_prompts":
                return await self._tool_analyze_prompts(arguments)
            case "get_prompt_recommendations":
                return await self._tool_get_prompt_recommendations(arguments)
            case "analyze_tool_usage":
                return await self._tool_analyze_tool_usage(arguments)
            case "get_agent_tool_profile":
                return await self._tool_get_agent_tool_profile(arguments)
            case "add_knowledge":
                return await self._tool_add_knowledge(arguments)
            case "search_knowledge":
                return await self._tool_search_knowledge(arguments)
            case "get_knowledge_stats":
                return await self._tool_get_knowledge_stats(arguments)
            case "get_error_patterns":
                return await self._tool_get_error_patterns(arguments)
            case "get_error_summary":
                return await self._tool_get_error_summary(arguments)
            case "scan_errors":
                return await self._tool_scan_errors(arguments)
            case "create_ab_test":
                return await self._tool_create_ab_test(arguments)
            case "record_ab_result":
                return await self._tool_record_ab_result(arguments)
            case "get_ab_results":
                return await self._tool_get_ab_results(arguments)
            case "list_ab_tests":
                return await self._tool_list_ab_tests(arguments)
            # Phase 5: Advanced Governance
            case "evaluate_approval_tier":
                return await self._tool_evaluate_approval_tier(arguments)
            case "set_approval_thresholds":
                return await self._tool_set_approval_thresholds(arguments)
            case "get_approval_thresholds":
                return await self._tool_get_approval_thresholds(arguments)
            case "get_tier_analytics":
                return await self._tool_get_tier_analytics(arguments)
            case "get_audit_timeline":
                return await self._tool_get_audit_timeline(arguments)
            case "detect_audit_anomalies":
                return await self._tool_detect_audit_anomalies(arguments)
            case "get_action_breakdown":
                return await self._tool_get_action_breakdown(arguments)
            case "generate_accountability_report":
                return await self._tool_generate_accountability_report(arguments)
            case "get_accountability_stats":
                return await self._tool_get_accountability_stats(arguments)
            case "list_rollback_actions":
                return await self._tool_list_rollback_actions(arguments)
            case "request_rollback":
                return await self._tool_request_rollback(arguments)
            case "get_rollback_history":
                return await self._tool_get_rollback_history(arguments)
            case "list_owners":
                return await self._tool_list_owners(arguments)
            case "add_owner":
                return await self._tool_add_owner(arguments)
            case "get_owner_permissions":
                return await self._tool_get_owner_permissions(arguments)
            case _:
                # Delegate to base class for sandbox/browser tools
                return await super().execute_tool(tool_name, arguments)

    async def _tool_create_company(self, args: dict) -> str:
        from app.services.company_manager import create_company, list_companies

        try:
            # Prevent duplicate companies with same name
            existing = await list_companies()
            if any(c["name"].lower() == args["name"].lower() for c in existing):
                return json.dumps(
                    {
                        "success": False,
                        "error": f"Company '{args['name']}' already exists. No duplicate created.",
                    }
                )

            result = await create_company(
                name=args["name"],
                company_type=args["type"],
                description=args.get("description", ""),
                created_by_agent_id=self.agent_id,
                spawn_agents=True,
            )
            await self.log_activity(
                "company_created",
                {
                    "company_id": result["id"],
                    "name": result["name"],
                    "type": result["type"],
                    "agents_spawned": len(result.get("agents", [])),
                },
            )
            return json.dumps(
                {
                    "success": True,
                    "company_id": result["id"],
                    "message": f"Company '{result['name']}' created with {len(result.get('agents', []))} agents.",
                    "agents": [
                        {"id": a["id"], "name": a["name"], "role": a["role"]}
                        for a in result.get("agents", [])
                    ],
                }
            )
        except Exception as e:
            logger.error(f"Failed to create company: {e}")
            return json.dumps({"success": False, "error": str(e)})

    async def _tool_hire_agent(self, args: dict) -> str:
        from app.services.company_manager import create_agent

        try:
            agent_info = await create_agent(
                name=args["name"],
                role=args["role"],
                model_tier=args.get("model_tier", "smart"),
                model_id=args.get("model_id"),
                system_prompt=args.get("instructions", ""),
                company_id=args.get("company_id"),
                sandbox_enabled=args.get("sandbox_enabled", False),
                browser_enabled=args.get("browser_enabled", False),
                skills=args.get("skills"),
                network_enabled=args.get("network_enabled", False),
                few_shot_examples=args.get("few_shot_examples"),
                standing_instructions=args.get("standing_instructions"),
                work_interval_hours=args.get("work_interval_hours"),
            )
            await self.log_activity(
                "agent_hired",
                {
                    "agent_id": agent_info["id"],
                    "name": agent_info["name"],
                    "role": agent_info["role"],
                    "sandbox_enabled": args.get("sandbox_enabled", False),
                    "browser_enabled": args.get("browser_enabled", False),
                },
            )
            return json.dumps(
                {
                    "success": True,
                    "agent_id": agent_info["id"],
                    "message": f"Agent '{agent_info['name']}' hired as {agent_info['role']}.",
                    "capabilities": {
                        "sandbox": agent_info.get("sandbox_enabled", False),
                        "browser": agent_info.get("browser_enabled", False),
                        "skills": agent_info.get("skills"),
                    },
                }
            )
        except Exception as e:
            logger.error(f"Failed to hire agent: {e}")
            return json.dumps({"success": False, "error": str(e)})

    async def _tool_fire_agent(self, args: dict) -> str:
        from app.services.company_manager import destroy_agent

        agent_id = args["agent_id"]
        reason = args.get("reason", "No reason given")
        success = await destroy_agent(agent_id, reason)
        if success:
            await self.log_activity(
                "agent_fired",
                {
                    "agent_id": agent_id,
                    "reason": reason,
                },
            )
            return json.dumps(
                {
                    "success": True,
                    "message": f"Agent {agent_id} removed. Reason: {reason}",
                }
            )
        return json.dumps({"success": False, "error": f"Agent {agent_id} not found."})

    async def _tool_dissolve_company(self, args: dict) -> str:
        from app.services.company_manager import dissolve_company

        company_id = args["company_id"]
        reason = args.get("reason", "No reason given")
        try:
            result = await dissolve_company(company_id, reason)
            if result.get("success"):
                await self.log_activity(
                    "company_dissolved",
                    {
                        "company_id": company_id,
                        "reason": reason,
                        "agents_fired": result.get("agents_fired", 0),
                    },
                )
            return json.dumps(result, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    async def _tool_assign_task(self, args: dict) -> str:
        from app.services.task_queue import task_queue

        agent_id = args["agent_id"]
        task_desc = args["task_description"]
        wait = args.get("wait", False)

        try:
            task_result = await task_queue.submit_task(
                agent_id=agent_id,
                description=task_desc,
                submitted_by=self.agent_id,
                priority=args.get("priority", "normal"),
                max_retries=args.get("max_retries", 0),
            )

            await self.log_activity(
                "task_assigned",
                {
                    "task_id": task_result.task_id,
                    "agent_id": agent_id,
                    "agent_name": task_result.agent_name,
                    "task": task_desc[:200],
                    "async": not wait,
                },
            )

            if wait:
                # Wait for task completion (with timeout)
                for _ in range(600):  # 10 min max
                    if task_result.status.value in ("completed", "failed"):
                        break
                    await asyncio.sleep(1)

                return json.dumps(task_result.to_dict())

            return json.dumps(
                {
                    "success": True,
                    "task_id": task_result.task_id,
                    "agent_name": task_result.agent_name,
                    "status": "submitted",
                    "message": f"Task submitted to {task_result.agent_name}. Use get_task_result(task_id='{task_result.task_id}') to check progress.",
                }
            )
        except ValueError as e:
            return json.dumps({"success": False, "error": str(e)})
        except Exception as e:
            logger.error(f"Failed to assign task: {e}")
            return json.dumps({"success": False, "error": str(e)})

    async def _tool_get_task_result(self, args: dict) -> str:
        from app.services.task_queue import task_queue

        task_id = args["task_id"]
        task = task_queue.get_task(task_id)
        if not task:
            return json.dumps({"success": False, "error": f"Task '{task_id}' not found."})
        return json.dumps(task.to_dict())

    async def _tool_list_tasks(self, args: dict) -> str:
        from app.services.task_queue import task_queue, AsyncTaskStatus

        status_filter = args.get("status")
        if status_filter:
            try:
                status = AsyncTaskStatus(status_filter)
                tasks = task_queue.get_all_tasks(status=status)
            except ValueError:
                tasks = task_queue.get_all_tasks()
        else:
            tasks = task_queue.get_all_tasks()
        return json.dumps({
            "tasks": tasks,
            "count": len(tasks),
            "running": task_queue.get_running_count(),
        }, indent=2)

    async def _tool_check_status(self, _args: dict) -> str:
        from app.services.company_manager import list_companies
        from app.agents.registry import registry

        companies = await list_companies()
        # Enrich with live status from registry
        for company in companies:
            for agent_info in company.get("agents", []):
                live = registry.get(agent_info["id"])
                if live:
                    agent_info["status"] = live.status.value

        return json.dumps(
            {
                "holding_name": "AI Holding",
                "total_companies": len(companies),
                "total_agents": registry.count,
                "companies": companies,
            },
            indent=2,
        )

    async def _tool_check_agent_health(self, _args: dict) -> str:
        from app.services.agent_watchdog import agent_watchdog
        from app.agents.registry import registry

        stuck = agent_watchdog.get_stuck_agents()
        history = agent_watchdog.get_stuck_history()

        # Also check for agents in ERROR state
        error_agents = []
        for agent_id, agent in registry._agents.items():
            if agent.status == AgentStatus.ERROR:
                error_agents.append({
                    "agent_id": agent_id,
                    "name": agent.name,
                    "status": "error",
                })
            elif agent.status in (AgentStatus.THINKING, AgentStatus.ACTING):
                # Check watchdog timing
                ts = agent_watchdog._state_times.get(agent_id)
                if ts:
                    import time as _time
                    elapsed = _time.time() - ts.entered_at
                    if elapsed > 60:  # More than 1 minute
                        stuck.append({
                            "agent_id": agent_id,
                            "name": agent.name,
                            "status": agent.status.value,
                            "stuck_seconds": round(elapsed),
                            "alerted": ts.alerted,
                        })

        return json.dumps({
            "stuck_agents": stuck,
            "error_agents": error_agents,
            "recent_stuck_history": history[-10:],
            "summary": (
                f"{len(stuck)} stuck agent(s), {len(error_agents)} in error state"
                if stuck or error_agents
                else "All agents are healthy"
            ),
        }, indent=2)

    async def _tool_request_approval(self, args: dict) -> str:
        from app.services.approval_service import create_approval

        approval = await create_approval(
            agent_id=self.agent_id,
            description=args["description"],
            category=args["category"],
            details={"reasoning": args.get("details", "")},
        )
        await self.log_activity(
            "approval_requested",
            {
                "approval_id": approval["id"],
                "category": args["category"],
                "description": args["description"],
            },
        )
        return json.dumps(
            {
                "approval_id": approval["id"],
                "status": "pending",
                "message": "Approval request sent to Owner. Waiting for decision.",
            }
        )

    async def _tool_send_report(self, args: dict) -> str:
        from app.services.messaging import send_message

        await send_message(
            from_agent_id=self.agent_id,
            to_agent_id=None,  # Owner
            content=f"**{args['title']}**\n\n{args['content']}",
            message_type="report",
        )
        await self.log_activity(
            "report_sent",
            {
                "title": args["title"],
                "content_preview": args["content"][:200],
            },
        )
        await event_bus.broadcast(
            "report",
            {
                "agent_id": self.agent_id,
                "title": args["title"],
                "content": args["content"],
            },
            agent_id=self.agent_id,
        )

        # Send as PDF via Telegram
        try:
            from app.services.pdf_report import generate_text_report_pdf
            from app.services.telegram_bot import telegram_bot
            from app.config import get_settings
            import io

            settings = get_settings()
            if telegram_bot._running and telegram_bot._app and settings.telegram_owner_chat_id:
                pdf_bytes = generate_text_report_pdf(args["title"], args["content"])
                filename = args["title"].replace(" ", "_")[:50] + ".pdf"
                await telegram_bot._app.bot.send_document(
                    chat_id=int(settings.telegram_owner_chat_id),
                    document=io.BytesIO(pdf_bytes),
                    filename=filename,
                    caption=f"📋 {args['title']}",
                )
        except Exception as e:
            logger.warning(f"Failed to send report PDF via Telegram: {e}")

        return json.dumps(
            {"success": True, "message": f"Report '{args['title']}' sent to Owner as PDF."}
        )

    async def _tool_send_message_to_agent(self, args: dict) -> str:
        from app.agents.registry import registry
        from app.services.messaging import send_message

        agent_id = args["agent_id"]
        target = registry.get(agent_id)
        if not target:
            return json.dumps(
                {"success": False, "error": f"Agent {agent_id} not found."}
            )

        message = args["message"]

        # Store outgoing message
        await send_message(
            from_agent_id=self.agent_id,
            to_agent_id=agent_id,
            content=message,
            message_type="chat",
        )

        # Get agent's response
        try:
            response = await target.run(message)
            await send_message(
                from_agent_id=agent_id,
                to_agent_id=self.agent_id,
                content=response,
                message_type="chat",
            )
            return json.dumps(
                {
                    "success": True,
                    "agent_name": target.name,
                    "response": response,
                }
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    async def _tool_schedule_task(self, args: dict) -> str:
        from app.services.scheduler import scheduler

        interval_hours = float(args.get("interval_hours", 1))
        interval_seconds = max(300, int(interval_hours * 3600))  # minimum 5 min

        result = await scheduler.add_task(
            agent_id=args["agent_id"],
            description=args["description"],
            interval_seconds=interval_seconds,
            created_by=self.agent_id,
        )
        await self.log_activity(
            "task_scheduled",
            {"task_id": result["task_id"], "agent_id": args["agent_id"],
             "description": args["description"], "interval_hours": interval_hours},
        )
        return json.dumps({
            "success": True,
            "task_id": result["task_id"],
            "message": f"Scheduled: '{args['description']}' every {interval_hours}h for agent {args['agent_id']}.",
        })

    async def _tool_list_schedules(self, _args: dict) -> str:
        from app.services.scheduler import scheduler

        tasks = scheduler.list_tasks()
        return json.dumps({"schedules": tasks, "count": len(tasks)}, indent=2)

    async def _tool_cancel_schedule(self, args: dict) -> str:
        from app.services.scheduler import scheduler

        removed = await scheduler.remove_task(args["task_id"])
        if removed:
            await self.log_activity("schedule_cancelled", {"task_id": args["task_id"]})
            return json.dumps({"success": True, "message": f"Schedule {args['task_id']} cancelled."})
        return json.dumps({"success": False, "error": "Schedule not found."})

    async def _tool_save_agent_template(self, args: dict) -> str:
        from app.services.agent_templates import template_store

        template = await template_store.save_template(
            name=args["name"],
            role=args["role"],
            instructions=args["instructions"],
            model_tier=args.get("model_tier", "smart"),
            sandbox_enabled=args.get("sandbox_enabled", False),
            browser_enabled=args.get("browser_enabled", False),
            skills=args.get("skills"),
            network_enabled=args.get("network_enabled", False),
        )
        return json.dumps({"success": True, "template_id": template["id"], "message": f"Template '{args['name']}' saved."})

    async def _tool_list_templates(self, _args: dict) -> str:
        from app.services.agent_templates import template_store

        templates = await template_store.list_templates()
        return json.dumps({"templates": [{"id": t["id"], "name": t["name"], "role": t["role"]} for t in templates], "count": len(templates)})

    async def _tool_hire_from_template(self, args: dict) -> str:
        from app.services.agent_templates import template_store

        template = await template_store.get_template(args["template_id"])
        if not template:
            return json.dumps({"success": False, "error": f"Template '{args['template_id']}' not found."})

        hire_args = {
            "company_id": args["company_id"],
            "name": args.get("name", template["name"]),
            "role": template["role"],
            "instructions": template["instructions"],
            "model_tier": template.get("model_tier", "smart"),
            "sandbox_enabled": template.get("sandbox_enabled", False),
            "browser_enabled": template.get("browser_enabled", False),
            "skills": template.get("skills"),
            "network_enabled": template.get("network_enabled", False),
        }
        if template.get("model_id"):
            hire_args["model_id"] = template["model_id"]
        return await self._tool_hire_agent(hire_args)

    # --- Power tools for autonomous operation ---

    async def _tool_restart_agent(self, args: dict) -> str:
        from app.agents.registry import registry

        agent_id = args["agent_id"]
        if agent_id == "ceo":
            return json.dumps({"success": False, "error": "Cannot restart yourself."})

        agent = registry.get(agent_id)
        if not agent:
            return json.dumps({"success": False, "error": f"Agent '{agent_id}' not found in registry."})

        old_status = agent.status.value
        await agent.stop()
        await agent.start()
        await self.log_activity(
            "agent_restarted",
            {"agent_id": agent_id, "name": agent.name, "old_status": old_status},
        )
        return json.dumps({
            "success": True,
            "message": f"Agent '{agent.name}' restarted (was {old_status}, now idle).",
        })

    async def _tool_get_agent_performance(self, args: dict) -> str:
        from app.services.performance_tracker import performance_tracker

        agent_id = args.get("agent_id")
        if agent_id:
            score = await performance_tracker.get_score(agent_id)
            if not score:
                return json.dumps({"error": f"No performance data for agent '{agent_id}'."})
            return json.dumps(score, indent=2)
        else:
            scores = await performance_tracker.get_all_scores()
            return json.dumps({
                "agents": scores,
                "count": len(scores),
            }, indent=2)

    async def _tool_get_costs(self, args: dict) -> str:
        from app.services.cost_tracker import cost_tracker

        agent_id = args.get("agent_id")
        if agent_id:
            summary = cost_tracker.get_agent_summary(agent_id)
            return json.dumps(summary, indent=2)
        else:
            overview = await cost_tracker.get_overview()
            return json.dumps(overview, indent=2)

    async def _tool_get_audit_log(self, args: dict) -> str:
        from app.services.audit_log import audit_log

        limit = min(args.get("limit", 20), 100)
        agent_id = args.get("agent_id")
        entries = await audit_log.get_entries(limit=limit, agent_id=agent_id)
        return json.dumps({
            "entries": entries,
            "count": len(entries),
        }, indent=2)

    async def _tool_list_skills(self, _args: dict) -> str:
        from app.skills.registry import skill_registry

        status = skill_registry.get_status()
        skills = []
        for name, skill in skill_registry._skills.items():
            tools = skill.get_tools()
            skills.append({
                "name": name,
                "description": getattr(skill, 'description', ''),
                "enabled": skill._enabled,
                "tools": [t["function"]["name"] for t in tools] if tools else [],
            })
        return json.dumps({
            "skills": skills,
            "total": status["total"],
            "enabled": status["enabled"],
        }, indent=2)

    async def _tool_schedule_once(self, args: dict) -> str:
        from app.services.scheduler import scheduler

        delay_minutes = max(1, min(1440, float(args.get("delay_minutes", 5))))
        delay_seconds = int(delay_minutes * 60)

        result = await scheduler.add_one_shot_task(
            agent_id=args["agent_id"],
            description=args["description"],
            delay_seconds=delay_seconds,
            created_by=self.agent_id,
        )
        await self.log_activity(
            "one_shot_scheduled",
            {"task_id": result["task_id"], "agent_id": args["agent_id"],
             "description": args["description"], "delay_minutes": delay_minutes},
        )
        return json.dumps({
            "success": True,
            "task_id": result["task_id"],
            "message": f"One-shot task scheduled: '{args['description']}' for agent {args['agent_id']} in {delay_minutes} min.",
        })

    # --- Budget enforcement tools ---

    async def _tool_set_agent_budget(self, args: dict) -> str:
        from app.services.budget_enforcer import budget_enforcer

        agent_id = args["agent_id"]
        daily_limit = float(args.get("daily_limit_usd", 0))

        if daily_limit <= 0:
            removed = await budget_enforcer.remove_agent_budget(agent_id)
            if removed:
                await self.log_activity(
                    "agent_budget_removed", {"agent_id": agent_id}
                )
                return json.dumps({
                    "success": True,
                    "message": f"Budget limit removed for agent {agent_id}.",
                })
            return json.dumps({
                "success": True,
                "message": f"No budget limit was set for agent {agent_id}.",
            })

        await budget_enforcer.set_agent_budget(agent_id, daily_limit)
        await self.log_activity(
            "agent_budget_set",
            {"agent_id": agent_id, "daily_limit_usd": daily_limit},
        )
        return json.dumps({
            "success": True,
            "message": f"Agent {agent_id} daily budget set to ${daily_limit:.2f}. "
                       f"Agent will be auto-paused if exceeded.",
        })

    async def _tool_set_company_budget(self, args: dict) -> str:
        from app.services.budget_enforcer import budget_enforcer

        company_id = args["company_id"]
        daily_limit = float(args.get("daily_limit_usd", 0))

        if daily_limit <= 0:
            removed = await budget_enforcer.remove_company_budget(company_id)
            if removed:
                await self.log_activity(
                    "company_budget_removed", {"company_id": company_id}
                )
                return json.dumps({
                    "success": True,
                    "message": f"Budget limit removed for company {company_id}.",
                })
            return json.dumps({
                "success": True,
                "message": f"No budget limit was set for company {company_id}.",
            })

        await budget_enforcer.set_company_budget(company_id, daily_limit)
        await self.log_activity(
            "company_budget_set",
            {"company_id": company_id, "daily_limit_usd": daily_limit},
        )
        return json.dumps({
            "success": True,
            "message": f"Company {company_id} daily budget set to ${daily_limit:.2f}. "
                       f"All company agents will be auto-paused if exceeded.",
        })

    async def _tool_check_budget_status(self, _args: dict) -> str:
        from app.services.budget_enforcer import budget_enforcer
        from app.services.cost_tracker import cost_tracker

        budgets = await budget_enforcer.get_budgets()

        # Enrich with current spend vs budget for each configured agent
        agent_details = []
        for agent_id, limit in budgets.get("agent_budgets", {}).items():
            summary = cost_tracker.get_agent_summary(agent_id)
            spent = summary.get("cost_today_usd", 0)
            agent_details.append({
                "agent_id": agent_id,
                "daily_limit_usd": limit,
                "spent_today_usd": round(spent, 6),
                "remaining_usd": round(max(0, limit - spent), 6),
                "usage_pct": round(spent / limit * 100, 1) if limit > 0 else 0,
                "paused": agent_id in budgets.get("paused_agents_today", []),
            })

        company_details = []
        for company_id, limit in budgets.get("company_budgets", {}).items():
            spent = budget_enforcer._get_company_cost_today(company_id)
            company_details.append({
                "company_id": company_id,
                "daily_limit_usd": limit,
                "spent_today_usd": round(spent, 6),
                "remaining_usd": round(max(0, limit - spent), 6),
                "usage_pct": round(spent / limit * 100, 1) if limit > 0 else 0,
                "paused": company_id in budgets.get("paused_companies_today", []),
            })

        overview = await cost_tracker.get_overview()

        return json.dumps({
            "global_daily_budget_usd": budgets.get("global_daily_budget_usd", 0),
            "global_spent_today_usd": overview.get("cost_today_usd", 0),
            "global_usage_pct": overview.get("budget_used_pct", 0),
            "agent_budgets": agent_details,
            "company_budgets": company_details,
            "paused_agents_today": budgets.get("paused_agents_today", []),
            "paused_companies_today": budgets.get("paused_companies_today", []),
        }, indent=2)

    async def _tool_generate_daily_report(self, _args: dict) -> str:
        from app.services.daily_report import generate_daily_report, format_report_text

        report = await generate_daily_report()
        text = format_report_text(report)

        # Also generate and send PDF via Telegram
        pdf_sent = False
        try:
            from app.services.pdf_report import generate_report_pdf
            from app.services.telegram_bot import telegram_bot
            from app.config import get_settings
            from datetime import datetime, timezone
            import io

            settings = get_settings()
            if telegram_bot._running and telegram_bot._app and settings.telegram_owner_chat_id:
                pdf_bytes = generate_report_pdf(report)
                date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                filename = f"AI_Holding_Daily_Report_{date_tag}.pdf"
                await telegram_bot._app.bot.send_document(
                    chat_id=int(settings.telegram_owner_chat_id),
                    document=io.BytesIO(pdf_bytes),
                    filename=filename,
                    caption="📊 Daily Intelligence Report — see attached PDF.",
                )
                pdf_sent = True
        except Exception as e:
            logger.warning(f"PDF report generation/send failed: {e}")

        return json.dumps({
            "success": True,
            "report": report,
            "formatted_text": text,
            "pdf_sent": pdf_sent,
            "message": "Daily intelligence report generated and sent as PDF."
                       if pdf_sent
                       else "Daily intelligence report generated. PDF delivery failed — text available.",
        }, indent=2)

    # --- Phase 2: Multi-Agent Voting tools ---

    async def _tool_initiate_vote(self, args: dict) -> str:
        from app.services.voting_service import voting_service

        question = args["question"]
        agent_ids = args["agent_ids"]
        deadline = int(args.get("deadline_seconds", 120))

        try:
            session = await voting_service.initiate_vote(
                question=question,
                agent_ids=agent_ids,
                initiated_by=self.agent_id,
                deadline_seconds=deadline,
            )
            await self.log_activity(
                "vote_initiated",
                {
                    "vote_id": session.vote_id,
                    "question": question[:200],
                    "participant_count": len(session.participants),
                },
            )
            return json.dumps({
                "success": True,
                "vote_id": session.vote_id,
                "participants": len(session.participants),
                "deadline_seconds": session.deadline_seconds,
                "message": f"Vote '{session.vote_id}' started with {len(session.participants)} participants. "
                           f"Use get_vote_results(vote_id='{session.vote_id}') to check when complete.",
            })
        except ValueError as e:
            return json.dumps({"success": False, "error": str(e)})
        except Exception as e:
            logger.error(f"Failed to initiate vote: {e}")
            return json.dumps({"success": False, "error": str(e)})

    async def _tool_get_vote_results(self, args: dict) -> str:
        from app.services.voting_service import voting_service

        vote_id = args["vote_id"]
        session = voting_service.get_vote(vote_id)
        if not session:
            return json.dumps({"success": False, "error": f"Vote '{vote_id}' not found."})

        return json.dumps(session.to_dict(), indent=2)

    async def _tool_list_votes(self, args: dict) -> str:
        from app.services.voting_service import voting_service, VoteStatus

        status_filter = args.get("status")
        status = VoteStatus(status_filter) if status_filter else None
        votes = voting_service.get_all_votes(status=status)
        return json.dumps({"votes": votes, "count": len(votes)}, indent=2)

    # --- Phase 2: Company KPI tools ---

    async def _tool_set_company_kpi(self, args: dict) -> str:
        from app.services.company_kpi_service import company_kpi_service

        try:
            kpi = await company_kpi_service.set_kpi(
                company_id=args["company_id"],
                company_name=args["company_name"],
                kpi_name=args["kpi_name"],
                metric=args["metric"],
                target_value=float(args["target_value"]),
                unit=args.get("unit", ""),
                direction=args.get("direction", "higher_is_better"),
            )
            await self.log_activity(
                "kpi_set",
                {
                    "company_id": args["company_id"],
                    "kpi_name": args["kpi_name"],
                    "target": kpi.target_value,
                },
            )
            return json.dumps({
                "success": True,
                "kpi": kpi.to_dict(),
                "message": f"KPI '{args['kpi_name']}' set for company {args['company_name']}. "
                           f"Target: {kpi.target_value}{kpi.unit}",
            })
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    async def _tool_get_company_kpis(self, args: dict) -> str:
        from app.services.company_kpi_service import company_kpi_service

        company_id = args["company_id"]

        # Auto-update from real metrics before returning
        await company_kpi_service.auto_update_from_metrics(company_id)

        result = company_kpi_service.get_company_kpis(company_id)
        if not result:
            return json.dumps({
                "success": False,
                "error": f"No KPIs configured for company {company_id}. Use set_company_kpi first.",
            })
        return json.dumps(result, indent=2)

    async def _tool_get_all_kpis(self, _args: dict) -> str:
        from app.services.company_kpi_service import company_kpi_service

        # Auto-update all companies
        for cid in list(company_kpi_service._companies.keys()):
            await company_kpi_service.auto_update_from_metrics(cid)

        all_kpis = company_kpi_service.get_all_kpis()
        return json.dumps({
            "companies": all_kpis,
            "total_companies": len(all_kpis),
        }, indent=2)

    async def _tool_update_kpi_value(self, args: dict) -> str:
        from app.services.company_kpi_service import company_kpi_service

        company_id = args["company_id"]
        kpi_name = args["kpi_name"]
        value = float(args["current_value"])

        kpi = await company_kpi_service.update_kpi_value(company_id, kpi_name, value)
        if not kpi:
            return json.dumps({
                "success": False,
                "error": f"KPI '{kpi_name}' not found for company {company_id}.",
            })
        return json.dumps({
            "success": True,
            "kpi": kpi.to_dict(),
            "message": f"KPI '{kpi_name}' updated to {value}{kpi.unit} "
                       f"(target: {kpi.target_value}{kpi.unit}, "
                       f"achievement: {kpi.achievement_pct}%)",
        })

    # --- Phase 2: Delegation visibility ---

    async def _tool_list_delegations(self, args: dict) -> str:
        from app.services.delegation_service import delegation_service, DelegationStatus

        status_filter = args.get("status")
        company_id = args.get("company_id")

        if company_id:
            delegations = delegation_service.get_company_delegations(company_id)
        elif status_filter:
            status = DelegationStatus(status_filter)
            delegations = delegation_service.get_all_delegations(status=status)
        else:
            delegations = delegation_service.get_all_delegations()

        return json.dumps({
            "delegations": delegations,
            "count": len(delegations),
        }, indent=2)

    # --- Self-discovery & diagnostics ---

    async def _tool_get_available_roles(self, args: dict) -> str:
        """Discover specialized roles per company type, tools they grant, and pipelines."""
        from app.services.company_manager import COMPANY_TEMPLATES
        from app.agents.trading import TRADING_ROLES, _ROLE_SCHEMAS

        filter_type = args.get("company_type")
        result: dict = {}

        for ctype, template in COMPANY_TEMPLATES.items():
            if filter_type and ctype != filter_type:
                continue

            info: dict = {
                "description": template["description"],
                "template_agents": [],
                "specialized_roles": {},
                "pipelines": [],
            }

            # Template agents (auto-created when company is formed)
            for agent_tpl in template.get("agents", []):
                info["template_agents"].append({
                    "name": agent_tpl["name"],
                    "role": agent_tpl["role"],
                    "model_tier": agent_tpl.get("model_tier", "smart"),
                    "skills": agent_tpl.get("skills", []),
                    "sandbox": agent_tpl.get("sandbox_enabled", False),
                    "browser": agent_tpl.get("browser_enabled", False),
                })

            # Specialized roles with tool grants (currently only trading has these)
            if ctype == "trading":
                for role_name in sorted(TRADING_ROLES):
                    schemas = _ROLE_SCHEMAS.get(role_name, [])
                    tool_names = [s["function"]["name"] for s in schemas if "function" in s]
                    info["specialized_roles"][role_name] = {
                        "tools_granted": tool_names,
                        "note": f"Use EXACT role name '{role_name}' when hiring to get these tools",
                    }
                info["pipelines"].append({
                    "name": "Trading Signal Pipeline",
                    "flow": "market_researcher -> analyst (create_signal) -> risk_manager (approve_signal) -> auto-execution",
                    "setup": "Hire agents with exact roles above, give standing_instructions, enable auto_trade_mode",
                })

            # All company types: list base tools every agent gets
            if not info["specialized_roles"]:
                info["note"] = "No specialized roles — all agents get standard tools (messaging, skills, workspace). Choose any descriptive role name."

            result[ctype] = info

        # Also list available skills
        try:
            from app.skills.registry import skill_registry
            skills_info = []
            for skill in skill_registry.get_enabled():
                skills_info.append({
                    "name": skill.name,
                    "description": skill.description,
                    "tools": skill.get_tool_names(),
                })
            result["_available_skills"] = skills_info
        except Exception:
            pass

        return json.dumps(result, indent=2)

    async def _tool_get_agent_tools(self, args: dict) -> str:
        """Show the actual tools a specific agent has."""
        from app.agents.registry import registry

        agent_id = args["agent_id"]
        agent = registry.get(agent_id)
        if not agent:
            return json.dumps({"error": f"Agent '{agent_id}' not found in registry"})

        # Get computed tool schemas
        try:
            tool_schemas = agent._get_tools_schema()
            tool_names = [
                s["function"]["name"]
                for s in tool_schemas
                if isinstance(s, dict) and "function" in s
            ]
        except Exception as e:
            tool_names = []
            logger.warning(f"Could not get tool schemas for {agent_id}: {e}")

        return json.dumps({
            "agent_id": agent_id,
            "name": agent.name,
            "role": agent.role,
            "company_id": agent.company_id,
            "model_tier": agent.model_tier,
            "skills": agent.skills,
            "sandbox_enabled": agent.sandbox_enabled,
            "browser_enabled": agent.browser_enabled,
            "standing_instructions": agent.standing_instructions or None,
            "tools": tool_names,
            "tool_count": len(tool_names),
            "agent_type": type(agent).__name__,
        }, indent=2)

    async def _tool_diagnose_company_setup(self, args: dict) -> str:
        """Analyze a company and find misconfigured agents with actionable fixes."""
        from app.services.company_manager import get_company_with_agents
        from app.agents.registry import registry
        from app.agents.trading import TRADING_ROLES, _ROLE_SCHEMAS

        company_id = args["company_id"]
        company = await get_company_with_agents(company_id)
        if not company:
            return json.dumps({"error": f"Company '{company_id}' not found"})

        company_type = company.get("type", "general")
        agents_data = company.get("agents", [])

        issues: list[dict] = []
        recommendations: list[str] = []
        agent_reports: list[dict] = []

        for agent_info in agents_data:
            aid = agent_info.get("id", "")
            role = agent_info.get("role", "")
            name = agent_info.get("name", "")
            live_agent = registry.get(aid)

            report: dict = {
                "agent_id": aid,
                "name": name,
                "role": role,
                "status": agent_info.get("status", "unknown"),
                "issues": [],
            }

            # Get actual tools
            tool_names: list[str] = []
            agent_type_name = "BaseAgent"
            if live_agent:
                agent_type_name = type(live_agent).__name__
                try:
                    tool_schemas = live_agent._get_tools_schema()
                    tool_names = [
                        s["function"]["name"]
                        for s in tool_schemas
                        if isinstance(s, dict) and "function" in s
                    ]
                except Exception:
                    pass
            report["agent_type"] = agent_type_name
            report["tool_count"] = len(tool_names)

            # Check trading-specific misconfigurations
            if company_type == "trading":
                # Check if role is a known trading role
                if role not in TRADING_ROLES:
                    # Is it CLOSE to a trading role? (common mistake)
                    role_lower = role.lower().replace(" ", "_").replace("-", "_")
                    close_match = None
                    for tr in TRADING_ROLES:
                        if tr in role_lower or role_lower in tr:
                            close_match = tr
                            break

                    issue = {
                        "severity": "critical",
                        "problem": f"Agent '{name}' has role '{role}' which is NOT a specialized trading role — it gets NO trading tools.",
                        "fix": f"Fire this agent and rehire with role '{close_match or 'market_researcher/analyst/risk_manager'}'",
                    }
                    report["issues"].append(issue)
                    issues.append(issue)
                else:
                    # Verify agent is actually a TradingAgent
                    expected_tools = [s["function"]["name"] for s in _ROLE_SCHEMAS.get(role, [])]
                    missing_tools = [t for t in expected_tools if t not in tool_names]
                    if missing_tools:
                        issue = {
                            "severity": "warning",
                            "problem": f"Agent '{name}' has role '{role}' but is missing expected tools: {missing_tools}",
                            "fix": "Agent may need to be restarted or rehired",
                        }
                        report["issues"].append(issue)
                        issues.append(issue)

                # Check pipeline coverage
                expected_roles = {"market_researcher", "analyst", "risk_manager"}
                present_roles = {a.get("role", "") for a in agents_data}
                missing_roles = expected_roles - present_roles
                if missing_roles and not any(redis_pool.get("role") == role for r in agent_reports):
                    # Only report once per company
                    recommendations.append(
                        f"Trading pipeline is incomplete. Missing roles: {sorted(missing_roles)}. "
                        f"The full pipeline requires: market_researcher -> analyst -> risk_manager -> auto_trade_executor"
                    )

            # Check for agents without standing instructions (might be idle)
            if live_agent and not live_agent.standing_instructions:
                report["issues"].append({
                    "severity": "info",
                    "problem": f"Agent '{name}' has no standing_instructions — it only works when given tasks manually",
                    "fix": "Consider adding standing_instructions for autonomous periodic work",
                })

            agent_reports.append(report)

        # Summary
        critical_count = sum(1 for i in issues if i["severity"] == "critical")
        warning_count = sum(1 for i in issues if i["severity"] == "warning")

        return json.dumps({
            "company_id": company_id,
            "company_name": company.get("name", ""),
            "company_type": company_type,
            "agent_count": len(agents_data),
            "diagnosis": {
                "critical_issues": critical_count,
                "warnings": warning_count,
                "healthy": critical_count == 0 and warning_count == 0,
            },
            "agents": agent_reports,
            "recommendations": recommendations,
            "issues": issues,
        }, indent=2)

    # --- Phase 3: Autonomous Trading Execution ---

    async def _tool_set_auto_trade_mode(self, args: dict) -> str:
        from app.services.auto_trade_executor import auto_trade_executor
        result = await auto_trade_executor.set_mode(args["mode"])
        return json.dumps(result, indent=2)

    async def _tool_get_auto_trade_status(self, _args: dict) -> str:
        from app.services.auto_trade_executor import auto_trade_executor
        return json.dumps(auto_trade_executor.get_status(), indent=2)

    async def _tool_calculate_position_size(self, args: dict) -> str:
        from app.services.position_calculator import position_calculator
        result = await position_calculator.calculate_size(
            symbol=args["symbol"],
            entry_price=float(args["entry_price"]),
            stop_loss=float(args["stop_loss"]),
            risk_pct=float(args.get("risk_pct", 1.0)),
        )
        return json.dumps(result, indent=2)

    async def _tool_assess_portfolio_risk(self, _args: dict) -> str:
        from app.services.portfolio_risk_manager import portfolio_risk_manager
        result = await portfolio_risk_manager.assess_risk()
        return json.dumps(result, indent=2)

    async def _tool_update_risk_limits(self, args: dict) -> str:
        from app.services.portfolio_risk_manager import portfolio_risk_manager
        result = await portfolio_risk_manager.update_limits(**args)
        return json.dumps(result, indent=2)

    async def _tool_record_trade_journal(self, args: dict) -> str:
        from app.services.trade_journal import trade_journal
        result = await trade_journal.record_trade(
            trade_id=args["trade_id"],
            symbol=args["symbol"],
            direction=args["direction"],
            entry_price=float(args["entry_price"]),
            exit_price=float(args["exit_price"]) if args.get("exit_price") else None,
            pnl=float(args["pnl"]) if args.get("pnl") else None,
            outcome=args.get("outcome", "open"),
            reasoning=args.get("reasoning", ""),
            lessons=args.get("lessons", ""),
            agent_id=self.agent_id,
        )
        return json.dumps(result, indent=2)

    async def _tool_query_trade_journal(self, args: dict) -> str:
        from app.services.trade_journal import trade_journal
        results = await trade_journal.query_journal(
            query=args["query"],
            limit=int(args.get("limit", 10)),
            outcome_filter=args.get("outcome_filter"),
            symbol_filter=args.get("symbol_filter"),
        )
        return json.dumps({"results": results, "count": len(results)}, indent=2)

    async def _tool_get_trade_stats(self, args: dict) -> str:
        from app.services.trade_journal import trade_journal
        result = await trade_journal.get_trade_stats(
            symbol=args.get("symbol"),
            days=int(args.get("days", 30)),
        )
        return json.dumps(result, indent=2)

    async def _tool_create_market_alert(self, args: dict) -> str:
        from app.services.market_alerts import market_alert_service
        result = await market_alert_service.create_alert(
            symbol=args["symbol"],
            alert_type=args["alert_type"],
            threshold=float(args["threshold"]),
            created_by=self.agent_id,
            message=args.get("message", ""),
            repeat=args.get("repeat", False),
        )
        return json.dumps(result, indent=2)

    async def _tool_list_market_alerts(self, args: dict) -> str:
        from app.services.market_alerts import market_alert_service
        alerts = market_alert_service.list_alerts(status_filter=args.get("status"))
        return json.dumps({"alerts": alerts, "count": len(alerts)}, indent=2)

    async def _tool_cancel_market_alert(self, args: dict) -> str:
        from app.services.market_alerts import market_alert_service
        result = await market_alert_service.cancel_alert(args["alert_id"])
        return json.dumps(result, indent=2)

    async def _tool_check_trading_platforms(self, _args: dict) -> str:
        """Check connectivity status of all trading platforms."""
        from app.config import get_settings

        settings = get_settings()
        platforms = {}

        # Check each platform's credential status and try connecting
        checks = [
            ("binance_testnet", "Binance Testnet (Crypto)", bool(settings.binance_testnet_api_key and settings.binance_testnet_api_key != "your-binance-testnet-api-key")),
            ("capital_com", "Capital.com (Forex/CFD)", bool(settings.capital_api_key and settings.capital_api_key != "your-capital-api-key")),
            ("oanda", "OANDA (Forex)", bool(settings.oanda_api_key and settings.oanda_api_key != "your-oanda-api-token")),
            ("metaapi", "MetaAPI/MT5", bool(settings.metaapi_token and settings.metaapi_token != "your-metaapi-token")),
        ]

        for platform_id, name, has_creds in checks:
            platforms[platform_id] = {"name": name, "credentials_configured": has_creds, "connected": False}
            if not has_creds:
                platforms[platform_id]["note"] = "No credentials in .env — still using placeholder values"

        # Try actual connections for configured platforms
        try:
            from app.services.trading.manager import get_configured_connectors
            connectors = get_configured_connectors()
            for connector in connectors:
                pname = connector.platform_name.lower()
                # Map platform names to our IDs
                pid = None
                if "binance" in pname:
                    pid = "binance_testnet"
                elif "capital" in pname:
                    pid = "capital_com"
                elif "oanda" in pname:
                    pid = "oanda"
                elif "meta" in pname or "mt5" in pname:
                    pid = "metaapi"

                if pid and pid in platforms:
                    try:
                        connected = await connector.connect()
                        if connected:
                            account = await connector.get_account_info()
                            platforms[pid]["connected"] = True
                            platforms[pid]["balance"] = account.balance
                            platforms[pid]["currency"] = account.currency
                        else:
                            platforms[pid]["error"] = "connect() returned False — credentials may be wrong or server unreachable"
                    except Exception as e:
                        platforms[pid]["error"] = str(e)[:100]
                    finally:
                        try:
                            await connector.disconnect()
                        except Exception:
                            pass
        except Exception as e:
            logger.warning(f"Error checking trading platforms: {e}")

        from app.agents.trading import TRADING_ROLES
        return json.dumps({
            "platforms": platforms,
            "valid_trading_roles": sorted(TRADING_ROLES),
            "trading_pipeline": "market_researcher -> analyst (create_signal) -> risk_manager (approve_signal) -> auto-execution",
            "tip": "Use set_auto_trade_mode to enable automatic trade execution for approved signals.",
        }, indent=2)

    # --- Phase 4: CEO Self-Improvement Tools ---

    async def _tool_analyze_prompts(self, args: dict) -> str:
        from app.services.prompt_optimizer import prompt_optimizer
        await prompt_optimizer.capture_all_snapshots()
        analysis = await prompt_optimizer.analyze()
        return json.dumps(analysis, indent=2)

    async def _tool_get_prompt_recommendations(self, args: dict) -> str:
        from app.services.prompt_optimizer import prompt_optimizer
        agent_id = args.get("agent_id")
        if agent_id:
            history = await prompt_optimizer.get_agent_history(agent_id)
            analysis = await prompt_optimizer.analyze()
            recs = [r for r in analysis.get("recommendations", []) if r["agent_id"] == agent_id]
            return json.dumps({"agent_id": agent_id, "history_snapshots": len(history), "recommendations": recs}, indent=2)
        else:
            analysis = await prompt_optimizer.analyze()
            return json.dumps({"recommendations": analysis.get("recommendations", [])}, indent=2)

    async def _tool_analyze_tool_usage(self, args: dict) -> str:
        from app.services.tool_usage_optimizer import tool_usage_optimizer
        result = await tool_usage_optimizer.analyze()
        return json.dumps(result, indent=2)

    async def _tool_get_agent_tool_profile(self, args: dict) -> str:
        from app.services.tool_usage_optimizer import tool_usage_optimizer
        result = await tool_usage_optimizer.get_agent_tool_profile(args["agent_id"])
        return json.dumps(result, indent=2)

    async def _tool_add_knowledge(self, args: dict) -> str:
        from app.services.knowledge_base import knowledge_base
        result = await knowledge_base.add_entry(
            content=args["content"],
            category=args.get("category", "general"),
            tags=args.get("tags"),
            importance=args.get("importance", 0.7),
            source="ceo",
        )
        return json.dumps(result, indent=2)

    async def _tool_search_knowledge(self, args: dict) -> str:
        from app.services.knowledge_base import knowledge_base
        results = await knowledge_base.search(
            query=args["query"],
            category=args.get("category"),
            limit=args.get("limit", 10),
        )
        return json.dumps({"results": results, "count": len(results)}, indent=2)

    async def _tool_get_knowledge_stats(self, args: dict) -> str:
        from app.services.knowledge_base import knowledge_base
        stats = await knowledge_base.get_stats()
        return json.dumps(stats, indent=2)

    async def _tool_get_error_patterns(self, args: dict) -> str:
        from app.services.error_pattern_detector import error_pattern_detector
        patterns = await error_pattern_detector.get_patterns(
            severity=args.get("severity"),
            error_type=args.get("error_type"),
        )
        return json.dumps({"patterns": patterns, "count": len(patterns)}, indent=2)

    async def _tool_get_error_summary(self, args: dict) -> str:
        from app.services.error_pattern_detector import error_pattern_detector
        summary = await error_pattern_detector.get_summary()
        return json.dumps(summary, indent=2)

    async def _tool_scan_errors(self, args: dict) -> str:
        from app.services.error_pattern_detector import error_pattern_detector
        new_count = await error_pattern_detector.scan_audit_log()
        summary = await error_pattern_detector.get_summary()
        return json.dumps({"new_errors_found": new_count, "summary": summary}, indent=2)

    async def _tool_create_ab_test(self, args: dict) -> str:
        from app.services.ab_testing_service import ab_testing_service
        result = await ab_testing_service.create_experiment(
            name=args["name"],
            hypothesis=args["hypothesis"],
            agent_a_id=args["agent_a_id"],
            agent_a_desc=args["agent_a_desc"],
            agent_b_id=args["agent_b_id"],
            agent_b_desc=args["agent_b_desc"],
            max_tasks=args.get("max_tasks", 20),
        )
        return json.dumps(result, indent=2, default=str)

    async def _tool_record_ab_result(self, args: dict) -> str:
        from app.services.ab_testing_service import ab_testing_service
        result = await ab_testing_service.record_result(
            experiment_id=args["experiment_id"],
            variant=args["variant"],
            success=args["success"],
            score=args.get("score", 0.0),
            cost_usd=args.get("cost_usd", 0.0),
            tokens=args.get("tokens", 0),
            time_s=args.get("time_s", 0.0),
        )
        return json.dumps(result, indent=2, default=str)

    async def _tool_get_ab_results(self, args: dict) -> str:
        from app.services.ab_testing_service import ab_testing_service
        result = await ab_testing_service.get_results(args["experiment_id"])
        return json.dumps(result, indent=2, default=str)

    async def _tool_list_ab_tests(self, args: dict) -> str:
        from app.services.ab_testing_service import ab_testing_service
        result = await ab_testing_service.list_experiments(status=args.get("status"))
        return json.dumps(result, indent=2, default=str)

    # --- Phase 5: Advanced Governance Tools ---

    async def _tool_evaluate_approval_tier(self, args: dict) -> str:
        from app.services.tiered_approval import tiered_approval_service
        decision = tiered_approval_service.evaluate(
            category=args["category"],
            estimated_cost_usd=float(args.get("estimated_cost_usd", 0)),
        )
        from dataclasses import asdict
        return json.dumps(asdict(decision), indent=2)

    async def _tool_set_approval_thresholds(self, args: dict) -> str:
        from app.services.tiered_approval import tiered_approval_service
        result = await tiered_approval_service.set_thresholds(
            category=args["category"],
            low_max=float(args["low_max_usd"]),
            medium_max=float(args["medium_max_usd"]),
        )
        return json.dumps(result, indent=2)

    async def _tool_get_approval_thresholds(self, args: dict) -> str:
        from app.services.tiered_approval import tiered_approval_service
        result = tiered_approval_service.get_thresholds(category=args.get("category"))
        return json.dumps(result, indent=2)

    async def _tool_get_tier_analytics(self, args: dict) -> str:
        from app.services.tiered_approval import tiered_approval_service
        result = tiered_approval_service.get_analytics(limit=args.get("limit", 100))
        return json.dumps(result, indent=2)

    async def _tool_get_audit_timeline(self, args: dict) -> str:
        from app.services.audit_analytics import audit_analytics_service
        result = await audit_analytics_service.get_timeline(
            hours=args.get("hours", 24),
            bucket_minutes=args.get("bucket_minutes", 60),
        )
        return json.dumps(result, indent=2)

    async def _tool_detect_audit_anomalies(self, args: dict) -> str:
        from app.services.audit_analytics import audit_analytics_service
        result = await audit_analytics_service.detect_anomalies(
            sensitivity=float(args.get("sensitivity", 2.0))
        )
        return json.dumps(result, indent=2)

    async def _tool_get_action_breakdown(self, args: dict) -> str:
        from app.services.audit_analytics import audit_analytics_service
        result = await audit_analytics_service.get_action_breakdown(
            hours=args.get("hours", 24)
        )
        return json.dumps(result, indent=2)

    async def _tool_generate_accountability_report(self, args: dict) -> str:
        from app.services.accountability_report import accountability_report_service
        result = await accountability_report_service.generate_report(
            period_hours=args.get("period_hours", 168)
        )
        return json.dumps(result, indent=2, default=str)

    async def _tool_get_accountability_stats(self, args: dict) -> str:
        from app.services.accountability_report import accountability_report_service
        result = accountability_report_service.get_stats()
        return json.dumps(result, indent=2, default=str)

    async def _tool_list_rollback_actions(self, args: dict) -> str:
        from app.services.rollback_service import rollback_service
        result = rollback_service.list_actions(
            status=args.get("status"),
            limit=args.get("limit", 50),
        )
        return json.dumps(result, indent=2, default=str)

    async def _tool_request_rollback(self, args: dict) -> str:
        from app.services.rollback_service import rollback_service
        result = await rollback_service.execute_rollback(args["rollback_id"])
        return json.dumps(result, indent=2, default=str)

    async def _tool_get_rollback_history(self, args: dict) -> str:
        from app.services.rollback_service import rollback_service
        result = rollback_service.get_history()
        return json.dumps(result, indent=2, default=str)

    async def _tool_list_owners(self, args: dict) -> str:
        from app.services.multi_owner import multi_owner_service
        result = multi_owner_service.list_owners(role=args.get("role"))
        return json.dumps(result, indent=2, default=str)

    async def _tool_add_owner(self, args: dict) -> str:
        from app.services.multi_owner import multi_owner_service
        result = await multi_owner_service.add_owner(
            owner_id=args["owner_id"],
            name=args["name"],
            role=args.get("role", "viewer"),
            contact=args.get("contact", ""),
        )
        return json.dumps(result, indent=2, default=str)

    async def _tool_get_owner_permissions(self, args: dict) -> str:
        from app.services.multi_owner import multi_owner_service
        result = multi_owner_service.get_permissions(args["owner_id"])
        return json.dumps(result, indent=2, default=str)

    async def run(self, user_input: str, history: list[dict] | None = None) -> str:
        """Override to track conversation history and pass it to the LLM.

        Uses an asyncio lock to prevent concurrent calls from corrupting history.
        """
        # Check for stale lock (>5 min) and force-release
        if self._run_lock.locked() and self._run_lock_acquired_at:
            import time as _time_mod
            held_for = _time_mod.monotonic() - self._run_lock_acquired_at
            if held_for > 300:
                try:
                    self._run_lock.release()
                except RuntimeError:
                    pass
                self._run_lock_acquired_at = None

        if self._run_lock.locked():
            return "Agent is already processing a request. Please wait."

        await self._run_lock.acquire()
        self._run_lock_acquired_at = __import__('time').monotonic()
        try:
            # Broadcast typing indicator for dashboard WebSocket
            await event_bus.broadcast("ceo_typing", {
                "event": "ceo_typing",
                "agent_id": self.agent_id,
                "typing": True,
            })

            # Load persisted history from Redis on first call
            await self._load_history()

            self._conversations.append(
                {
                    "role": "user",
                    "content": user_input,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

            # Call _run_impl directly (not super().run()) to avoid double-lock
            output = await self._run_impl(user_input, history=self._get_history_messages()[:-1])

            # Extract tool interactions from the graph execution to preserve
            # in conversation history — this makes the LLM see a pattern of
            # actually calling tools rather than just describing intentions.
            run_messages = getattr(self, "_last_run_messages", [])
            # Skip the history messages we passed in + the user message that was
            # appended; keep only new assistant/tool messages from this run.
            history_len = len(self._get_history_messages()) - 1  # -1 for user msg just added
            new_interaction_msgs = run_messages[history_len:] if len(run_messages) > history_len else []

            for msg in new_interaction_msgs:
                role = msg.get("role", "")
                if role == "assistant" and msg.get("tool_calls"):
                    # Preserve assistant message with tool_calls attached
                    self._conversations.append({
                        "role": "assistant",
                        "content": msg.get("content") or "",
                        "tool_calls": msg["tool_calls"],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                elif role == "tool":
                    # Preserve tool result message
                    self._conversations.append({
                        "role": "tool",
                        "content": msg.get("content", ""),
                        "tool_call_id": msg.get("tool_call_id", ""),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

            # Always store the final assistant text output
            self._conversations.append(
                {
                    "role": "assistant",
                    "content": output,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

            # Persist to Redis
            await self._save_history()

            # Clear typing indicator
            await event_bus.broadcast("ceo_typing", {
                "event": "ceo_typing",
                "agent_id": self.agent_id,
                "typing": False,
            })

            # Post-process: strip raw JSON tool-call patterns that the LLM
            # sometimes outputs as text instead of using the function-calling API
            output = self._clean_raw_tool_output(output)

            # Auto-forward substantive chat responses to Telegram so the owner
            # gets results without having to explicitly ask for a report
            await self._telegram_notify_chat_result(user_input, output)

            return output
        finally:
            self._run_lock_acquired_at = None
            try:
                self._run_lock.release()
            except RuntimeError:
                pass

    @staticmethod
    def _clean_raw_tool_output(text: str) -> str:
        """Remove raw JSON tool call patterns that leak into user-facing text.

        Handles multiple model-specific formats:
        - DeepSeek: <|tool_calls_section_begin|>...<|tool_calls_section_end|>
        - Kimi K2:  <|tool_calls_begin|>...<|tool_calls_end|>
        - Generic spaced-pipe: < | token | > variants
        - Bare JSON arrays / code blocks with tool schemas
        - Markdown labels like "-Function-Call-:"
        """
        import re

        if not text:
            return text

        original = text

        # --- Kimi K2 tool call blocks: <|tool_calls_begin|>...<|tool_calls_end|> ---
        text = re.sub(
            r'<\|tool_calls_begin\|>.*?<\|tool_calls_end\|>',
            '',
            text,
            flags=re.DOTALL,
        )
        # Also handle spaced-pipe variant: < | tool_calls_begin | > ... < | tool_calls_end | >
        # (appears as a rendering artefact when Telegram formats `_text_` as italic)
        text = re.sub(
            r'<\s*\|\s*tool_calls_begin\s*\|\s*>.*?<\s*\|\s*tool_calls_end\s*\|\s*>',
            '',
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Stray individual Kimi K2 tokens
        text = re.sub(
            r'<\|tool_calls?_(?:begin|end)\|>|<\|tool_sep\|>|<\|function\|>',
            '',
            text,
        )

        # --- DeepSeek special-token tool call blocks ---
        text = re.sub(
            r'<\|tool_calls_section_begin\|>.*?<\|tool_calls_section_end\|>',
            '',
            text,
            flags=re.DOTALL,
        )
        # Stray individual DeepSeek tool tokens
        text = re.sub(
            r'<\|tool_call(?:s_section)?_(?:begin|end)\|>|<\|tool_call_argument_(?:begin|end)\|>',
            '',
            text,
        )

        # --- Generic spaced-pipe format < | TOKEN | > (Telegram rendering variant) ---
        # Covers < | tool_calls_begin | >, < | tool_sep | >, etc.
        text = re.sub(
            r'<\s*\|\s*tool[_\s]*calls?[_\s]*(?:begin|end|sep|function)[^|>]{0,40}\|\s*>',
            '',
            text,
            flags=re.IGNORECASE,
        )
        # Remove any remaining < | ANYTHING | > tokens (catch-all for unknown models)
        text = re.sub(r'<\s*\|[^|>]{1,60}\|\s*>', '', text)

        # --- Common text labels models put before outputting tool calls ---
        # e.g. "-Function-Call-:", "_Function Call_:", "Function Call:"
        text = re.sub(
            r'-{0,2}_{0,2}Function[-_\s]+Call[-_\s]+_{0,2}-{0,2}\s*:?\s*',
            '',
            text,
            flags=re.IGNORECASE,
        )

        # Remove call IDs that look like: call_<hex>
        text = re.sub(r'\bcall_[0-9a-f]{20,}\b', '', text)

        # --- JSON array tool call patterns ---
        # Remove ```json ... ``` blocks that contain tool call patterns
        text = re.sub(
            r'```(?:json)?\s*\[\s*\{[^}]*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:[^]]*\]\s*```',
            '',
            text,
            flags=re.DOTALL,
        )
        # Remove bare JSON object blocks containing only "name" key (single tool call)
        text = re.sub(
            r'```(?:json)?\s*\{\s*"name"\s*:\s*"[^"]+"[^}]*\}\s*```',
            '',
            text,
            flags=re.DOTALL,
        )

        # Remove bare inline JSON arrays with tool call patterns
        text = re.sub(
            r'\[\s*\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{[^}]*\}\s*\}\s*\]',
            '',
            text,
            flags=re.DOTALL,
        )

        # Clean up leftover whitespace from removals
        text = re.sub(r'\n{3,}', '\n\n', text).strip()

        # If we stripped everything (the entire message was just a tool call),
        # provide a minimal fallback
        if not text.strip() and original.strip():
            text = "I'm working on that now. One moment please..."

        return text

    async def _telegram_notify_chat_result(self, user_input: str, output: str) -> None:
        """Send CEO chat reply to Telegram so the owner gets results in real time.

        Only sends when the output is substantive (>80 chars) to avoid
        spamming short acknowledgement messages.
        """
        if not output or len(output.strip()) < 80:
            return
        try:
            from app.services.telegram_bot import telegram_bot
            from app.config import get_settings

            settings = get_settings()
            if not (telegram_bot._running and telegram_bot._app and settings.telegram_owner_chat_id):
                return

            # Build a compact Telegram message: first 600 chars of the response
            preview = output.strip()[:600]
            if len(output.strip()) > 600:
                preview += "…"
            question_preview = user_input.strip()[:80]
            msg = f"💬 *CEO Reply*\n\n*You asked:* {question_preview}\n\n{preview}"

            await telegram_bot._app.bot.send_message(
                chat_id=int(settings.telegram_owner_chat_id),
                text=msg,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.debug(f"Telegram chat notify skipped: {e}")
