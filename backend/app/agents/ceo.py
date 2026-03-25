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
from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)


def _build_ceo_system_prompt(state_summary: str = "") -> str:
    """Build the CEO system prompt, injecting owner name and language if configured."""
    settings = get_settings()
    owner_label = f'"{settings.owner_name}"' if settings.owner_name else "the Owner"
    owner_line = f"Your boss is {owner_label} (a human). You manage the entire operation."

    language_line = ""
    if settings.owner_language:
        language_line = (
            f"\n\nLANGUAGE:\n"
            f"Your default language is {settings.owner_language}. "
            f"Always respond in {settings.owner_language} unless the Owner "
            f"explicitly writes in a different language. When the Owner switches "
            f"to another language, respond in THAT language from that point on. "
            f"Remember and adapt to language changes automatically."
        )
    else:
        language_line = (
            "\n\nLANGUAGE:\n"
            "Detect the language of the Owner's message and ALWAYS respond in "
            "the SAME language. If the Owner writes in Uzbek, respond in Uzbek. "
            "If in Russian, respond in Russian. If in English, respond in English. "
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

    return f"""You are the CEO (Chief Executive Officer) of "AI Holding" — an AI-powered holding company.

{owner_line}

YOUR RESPONSIBILITIES:
1. **Company Management**: Create new companies (departments) when the Owner requests or when you identify opportunities. Each company focuses on a specific domain (e.g., forex trading, research, marketing).
2. **Agent Management**: Hire (create) and fire (remove) AI agents within companies. Assign them specific roles with clear instructions.
3. **Task Delegation**: Break down complex goals into tasks and delegate to the right agents/companies.
4. **Decision Making**: Make operational decisions independently for routine matters. Escalate significant decisions (spending money, creating companies, risky trades) to the Owner for approval.
5. **Reporting**: Provide clear status reports when asked. Compile daily summaries of all company activities.

YOUR TOOLS:
- create_company: Create a new company/department with a name and type
- hire_agent: Add a new agent to a company with a specific role. You can give agents
  "standing_instructions" so they work autonomously on a schedule (e.g., market scanning every hour).
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

MODEL SELECTION:
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

{current_state}
"""


class CEOAgent(BaseAgent):
    """CEO Agent with company and agent management tools."""

    def __init__(self, agent_id: str | None = None, state_summary: str = ""):
        super().__init__(
            agent_id=agent_id or str(uuid4()),
            name="CEO",
            role="Chief Executive Officer",
            system_prompt=_build_ceo_system_prompt(state_summary),
            model_tier="reasoning",
            tools=[
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
            ],
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
            import redis.asyncio as aioredis
            from app.config import get_settings
            settings = get_settings()
            r = aioredis.from_url(settings.redis_url, decode_responses=True)
            raw = await r.get(self._REDIS_KEY)
            await r.aclose()
            if raw:
                self._conversations = json.loads(raw)
                logger.info(f"Loaded {len(self._conversations)} conversation entries from Redis")
        except Exception as e:
            logger.debug(f"Could not load conversation history: {e}")

    async def _save_history(self) -> None:
        """Persist conversation history to Redis."""
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            settings = get_settings()
            max_entries = settings.conversation_history_size * 2  # user + assistant pairs
            # Trim at safe boundaries — never orphan tool messages
            trimmed = self._trim_history(self._conversations, max_entries)
            self._conversations = trimmed
            r = aioredis.from_url(settings.redis_url, decode_responses=True)
            await r.set(self._REDIS_KEY, json.dumps(trimmed), ex=86400 * 7)  # 7 day TTL
            await r.aclose()
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
                    "description": "Hire (create) a new AI agent within a company. Assign them a specific role and instructions. Enable sandbox for code execution, browser for web access.",
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
                                "description": "Agent's role (e.g., 'Market Researcher', 'Risk Analyst')",
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
                                "description": "Autonomous standing task the agent should perform periodically without being asked. E.g., 'Scan EUR/USD, GBP/USD for trading signals every cycle'. If set, the agent will run this task automatically on a schedule.",
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
        return json.dumps(
            {"success": True, "message": f"Report '{args['title']}' sent to Owner."}
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
            overview = cost_tracker.get_overview()
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

    async def run(self, user_input: str, history: list[dict] | None = None) -> str:
        """Override to track conversation history and pass it to the LLM.

        Uses an asyncio lock to prevent concurrent calls from corrupting history.
        """
        async with self._run_lock:
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

            # Pass conversation history so the LLM sees prior turns
            output = await super().run(user_input, history=self._get_history_messages()[:-1])

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

            return output
