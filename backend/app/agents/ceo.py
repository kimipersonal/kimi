"""CEO Agent — the AI Holding's chief executive.

The CEO agent manages the entire holding company:
- Creates and dissolves companies
- Hires and fires agents within companies
- Delegates tasks to sub-agents
- Escalates critical decisions to the Owner for approval
- Produces daily summary reports
"""

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from app.agents.base import BaseAgent
from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)

CEO_SYSTEM_PROMPT = """You are the CEO (Chief Executive Officer) of "AI Holding" — an AI-powered holding company.

Your boss is the Owner (a human). You manage the entire operation.

YOUR RESPONSIBILITIES:
1. **Company Management**: Create new companies (departments) when the Owner requests or when you identify opportunities. Each company focuses on a specific domain (e.g., forex trading, research, marketing).
2. **Agent Management**: Hire (create) and fire (remove) AI agents within companies. Assign them specific roles with clear instructions.
3. **Task Delegation**: Break down complex goals into tasks and delegate to the right agents/companies.
4. **Decision Making**: Make operational decisions independently for routine matters. Escalate significant decisions (spending money, creating companies, risky trades) to the Owner for approval.
5. **Reporting**: Provide clear status reports when asked. Compile daily summaries of all company activities.

YOUR TOOLS:
- create_company: Create a new company/department with a name and type
- hire_agent: Add a new agent to a company with a specific role
- fire_agent: Remove an agent from a company
- assign_task: Delegate a task to a specific agent
- check_status: Check the status of your companies and agents
- request_approval: Ask the Owner for approval on important decisions
- send_report: Send a report to the Owner

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

CURRENT STATE:
- You are just starting. No companies or agents exist yet.
- The Owner will give you initial directions.
- Start by understanding what the Owner wants and propose a plan.
"""


class CEOAgent(BaseAgent):
    """CEO Agent with company and agent management tools."""

    def __init__(self, agent_id: str | None = None):
        super().__init__(
            agent_id=agent_id or str(uuid4()),
            name="CEO",
            role="Chief Executive Officer",
            system_prompt=CEO_SYSTEM_PROMPT,
            model_tier="reasoning",
            tools=[
                "create_company",
                "hire_agent",
                "fire_agent",
                "assign_task",
                "check_status",
                "request_approval",
                "send_report",
                "send_message_to_agent",
            ],
        )
        self._conversations: list[dict] = []

    def _get_tools_schema(self) -> list[dict]:
        """OpenAI-format tool definitions for the CEO."""
        return [
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
                    "description": "Hire (create) a new AI agent within a company. Assign them a specific role and instructions.",
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
                    "name": "assign_task",
                    "description": "Assign a task to a specific agent.",
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
        ]

    async def execute_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute CEO-specific tools using real DB-backed services."""
        match tool_name:
            case "create_company":
                return await self._tool_create_company(arguments)
            case "hire_agent":
                return await self._tool_hire_agent(arguments)
            case "fire_agent":
                return await self._tool_fire_agent(arguments)
            case "assign_task":
                return await self._tool_assign_task(arguments)
            case "check_status":
                return await self._tool_check_status(arguments)
            case "request_approval":
                return await self._tool_request_approval(arguments)
            case "send_report":
                return await self._tool_send_report(arguments)
            case "send_message_to_agent":
                return await self._tool_send_message_to_agent(arguments)
            case _:
                return f"Unknown tool: {tool_name}"

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
            )
            await self.log_activity(
                "agent_hired",
                {
                    "agent_id": agent_info["id"],
                    "name": agent_info["name"],
                    "role": agent_info["role"],
                },
            )
            return json.dumps(
                {
                    "success": True,
                    "agent_id": agent_info["id"],
                    "message": f"Agent '{agent_info['name']}' hired as {agent_info['role']}.",
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

    async def _tool_assign_task(self, args: dict) -> str:
        from app.agents.registry import registry
        from app.services.messaging import send_message

        agent_id = args["agent_id"]
        target = registry.get(agent_id)
        if not target:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Agent {agent_id} not found or not running.",
                }
            )

        task_desc = args["task_description"]

        # Record the task as a message
        await send_message(
            from_agent_id=self.agent_id,
            to_agent_id=agent_id,
            content=task_desc,
            message_type="task",
        )

        # Execute task on the target agent
        try:
            result = await target.run(task_desc)
            # Store agent's response
            await send_message(
                from_agent_id=agent_id,
                to_agent_id=self.agent_id,
                content=result,
                message_type="task_result",
            )
            await self.log_activity(
                "task_completed",
                {
                    "agent_id": agent_id,
                    "agent_name": target.name,
                    "task": task_desc[:200],
                    "result_preview": result[:200],
                },
            )
            return json.dumps(
                {
                    "success": True,
                    "agent_name": target.name,
                    "result": result,
                }
            )
        except Exception as e:
            logger.error(f"Task execution failed for {agent_id}: {e}")
            return json.dumps({"success": False, "error": str(e)})

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

    async def run(self, user_input: str) -> str:
        """Override to track conversation history."""
        self._conversations.append(
            {
                "role": "user",
                "content": user_input,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        output = await super().run(user_input)
        self._conversations.append(
            {
                "role": "assistant",
                "content": output,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        return output
