"""Base Agent — LangGraph-based agent with state machine, logging, and event broadcasting."""

import asyncio
import logging
from typing import TypedDict

from langgraph.graph import StateGraph, START, END

from app.db.models import AgentStatus
from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    """State carried through the agent's LangGraph execution."""

    messages: list[dict]
    current_input: str
    output: str
    tool_calls: list[dict]
    should_continue: bool
    awaiting_approval: bool
    approval_result: dict | None
    error: str | None


class BaseAgent:
    """Base class for all AI Holding agents.

    Provides:
    - LangGraph state machine (idle → thinking → acting → complete)
    - LLM integration via the router
    - Activity logging to DB + event broadcasting to dashboard
    - Start/stop/pause controls callable from the Owner dashboard
    """

    def __init__(
        self,
        agent_id: str,
        name: str,
        role: str,
        system_prompt: str,
        model_tier: str = "smart",
        model_id: str | None = None,
        tools: list | None = None,
        sandbox_enabled: bool = False,
        browser_enabled: bool = False,
        skills: list[str] | None = None,
        company_id: str | None = None,
        standing_instructions: str | None = None,
        work_interval_seconds: int = 3600,
    ):
        self.agent_id = agent_id
        self.name = name
        self.role = role
        self.system_prompt = system_prompt
        self.model_tier = model_tier
        self.model_id = model_id  # specific model override
        self.tools = tools or []
        self.sandbox_enabled = sandbox_enabled
        self.browser_enabled = browser_enabled
        self.skills = skills  # list of skill names (None = all enabled)
        self.company_id = company_id
        self.standing_instructions = standing_instructions
        self.work_interval_seconds = work_interval_seconds
        self.network_enabled = False  # set externally
        self._status = AgentStatus.IDLE
        self._paused = asyncio.Event()
        self._paused.set()  # Not paused initially
        self._stopped = False
        self._autonomous_task: asyncio.Task | None = None
        self._graph = self._build_graph()

    @property
    def status(self) -> AgentStatus:
        return self._status

    async def _set_status(self, new_status: AgentStatus):
        """Update status and broadcast to dashboard."""
        old = self._status
        self._status = new_status
        # Update watchdog tracking
        try:
            from app.services.agent_watchdog import agent_watchdog
            agent_watchdog.update_state(self.agent_id, new_status)
        except Exception:
            pass
        await event_bus.broadcast(
            "agent_state_change",
            {
                "agent_id": self.agent_id,
                "name": self.name,
                "old_status": old.value,
                "new_status": new_status.value,
            },
            agent_id=self.agent_id,
        )

    async def log_activity(
        self, action: str, details: dict | None = None, level: str = "info"
    ):
        """Log an activity, persist to DB, and broadcast to dashboard."""
        await event_bus.broadcast(
            "log",
            {
                "agent_id": self.agent_id,
                "name": self.name,
                "action": action,
                "details": details,
                "level": level,
            },
            agent_id=self.agent_id,
        )
        # Persist to PostgreSQL (fire-and-forget, non-blocking)
        try:
            from app.db.database import async_session
            from app.db.models import ActivityLog

            async with async_session() as session:
                log_entry = ActivityLog(
                    agent_id=self.agent_id,
                    action=action[:100],
                    details=details,
                    level=level,
                )
                session.add(log_entry)
                await session.commit()
        except Exception as e:
            logger.debug(f"Could not persist activity log: {e}")

    # --- Controls ---

    async def start(self):
        self._stopped = False
        self._paused.set()
        await self._set_status(AgentStatus.IDLE)
        await self.log_activity("agent_started")
        # Start autonomous work loop if standing instructions exist
        if self.standing_instructions and self._autonomous_task is None:
            self._autonomous_task = asyncio.create_task(
                self._autonomous_loop(),
                name=f"autonomous_{self.agent_id}",
            )

    async def stop(self):
        self._stopped = True
        self._paused.set()  # Unblock if paused so it can exit
        # Cancel autonomous loop
        if self._autonomous_task and not self._autonomous_task.done():
            self._autonomous_task.cancel()
            try:
                await self._autonomous_task
            except asyncio.CancelledError:
                pass
            self._autonomous_task = None
        await self._set_status(AgentStatus.STOPPED)
        await self.log_activity("agent_stopped")

    async def pause(self):
        self._paused.clear()
        await self._set_status(AgentStatus.PAUSED)
        await self.log_activity("agent_paused")

    async def resume(self):
        self._paused.set()
        await self._set_status(AgentStatus.IDLE)
        await self.log_activity("agent_resumed")

    # --- Autonomous Work Loop ---

    async def _autonomous_loop(self):
        """Run standing instructions periodically if configured."""
        await asyncio.sleep(60)  # initial settle time
        while not self._stopped:
            try:
                await self._paused.wait()  # respect pause
                if self._stopped:
                    break
                prompt = (
                    f"[STANDING INSTRUCTIONS] Execute your standing task:\n"
                    f"{self.standing_instructions}\n\n"
                    f"Complete the work, then use report_to_ceo to report "
                    f"any important findings or results."
                )
                await self.log_activity(
                    "autonomous_run",
                    {"instructions": self.standing_instructions[:200]},
                )
                await asyncio.wait_for(
                    self.run(prompt), timeout=300  # 5 min timeout
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"Autonomous task timed out for agent {self.name}"
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"Autonomous loop error for {self.name}: {e}"
                )
            # Wait for next cycle
            try:
                await asyncio.sleep(self.work_interval_seconds)
            except asyncio.CancelledError:
                break

    # --- LangGraph ---

    def _build_graph(self):
        """Build the agent's LangGraph state machine."""
        graph = StateGraph(AgentState)

        graph.add_node("think", self._think_node)
        graph.add_node("act", self._act_node)

        graph.add_edge(START, "think")
        graph.add_conditional_edges(
            "think", self._should_act, {"act": "act", "end": END}
        )
        graph.add_conditional_edges(
            "act", self._should_continue, {"think": "think", "end": END}
        )

        return graph.compile()

    MAX_TOOL_ROUNDS = 10

    def _should_act(self, state: AgentState) -> str:
        """Decide whether to proceed to the act node or end."""
        if state.get("error") or not state.get("tool_calls"):
            return "end"
        return "act"

    def _should_continue(self, state: AgentState) -> str:
        """After acting, decide if we need another think cycle."""
        if state.get("should_continue") and not state.get("error"):
            return "think"
        return "end"

    async def _think_node(self, state: AgentState) -> AgentState:
        """LLM inference: process input and decide what to do."""
        await self._paused.wait()
        if self._stopped:
            return {**state, "error": "Agent stopped", "should_continue": False}

        await self._set_status(AgentStatus.THINKING)
        await self.log_activity(
            "thinking", {"input": state.get("current_input", "")[:200]}
        )

        # Build system prompt with memory context
        system_content = self.system_prompt
        if state.get("current_input"):
            try:
                from app.services import llm_router as _lr
                from app.services.memory_service import recall_memories, format_memories_for_prompt

                query_embedding = await _lr.get_embedding(
                    state["current_input"][:500]
                )
                memories = await recall_memories(
                    self.agent_id, query_embedding, limit=5
                )
                mem_block = format_memories_for_prompt(memories)
                if mem_block:
                    system_content = f"{self.system_prompt}\n\n{mem_block}"
            except Exception as e:
                logger.debug(f"Memory recall skipped: {e}")

        messages = [{"role": "system", "content": system_content}]
        messages.extend(state.get("messages", []))
        if state.get("current_input"):
            messages.append({"role": "user", "content": state["current_input"]})

        # Context window compaction
        try:
            from app.services.context_engine import compact_messages

            messages = await compact_messages(
                messages, tier=self.model_tier
            )
        except Exception as e:
            logger.debug(f"Context compaction skipped: {e}")

        try:
            import json

            tools_schema = self._get_tools_schema() if (self.tools or self.sandbox_enabled or self.browser_enabled or self.skills is not None) else None
            if tools_schema is not None and len(tools_schema) == 0:
                tools_schema = None

            # Use enhanced failover service
            from app.services.model_failover import failover_service

            response = await failover_service.chat_with_failover(
                messages=messages,
                tier=self.model_tier,
                tools=tools_schema,
                model_override=self.model_id,
                agent_id=self.agent_id,
            )

            new_messages = list(state.get("messages", []))
            if state.get("current_input"):
                new_messages.append({"role": "user", "content": state["current_input"]})

            tool_calls = response.get("tool_calls") or []
            parsed_calls = [self._parse_tool_call(tc) for tc in tool_calls] if tool_calls else []

            # Build assistant message — attach tool_calls so the LLM
            # sees a proper function-calling conversation on the next turn
            assistant_msg: dict = {"role": "assistant", "content": response["content"] or ""}
            if parsed_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": pc["id"],
                        "type": "function",
                        "function": {"name": pc["name"], "arguments": json.dumps(pc["arguments"])},
                    }
                    for pc in parsed_calls
                ]
            new_messages.append(assistant_msg)

            return {
                **state,
                "messages": new_messages,
                "output": response["content"] or "",
                "tool_calls": parsed_calls,
                "should_continue": bool(parsed_calls),
                "current_input": "",
                "error": None,
            }
        except Exception as e:
            logger.error(f"Agent {self.name} think error: {e}")
            await self.log_activity("error", {"error": str(e)}, level="error")
            return {**state, "error": str(e), "should_continue": False}

    MAX_TOOL_CALLS_PER_BATCH = 3

    async def _act_node(self, state: AgentState) -> AgentState:
        """Execute tool calls produced by the think node."""
        await self._paused.wait()
        if self._stopped:
            return {**state, "error": "Agent stopped", "should_continue": False}

        await self._set_status(AgentStatus.ACTING)

        # Deduplicate identical tool calls (same name + same args)
        raw_calls = state.get("tool_calls", [])
        seen = set()
        unique_calls = []
        for tc in raw_calls:
            import json as _json

            key = (tc["name"], _json.dumps(tc["arguments"], sort_keys=True))
            if key not in seen:
                seen.add(key)
                unique_calls.append(tc)

        if len(unique_calls) < len(raw_calls):
            logger.warning(
                f"Agent {self.name}: deduplicated {len(raw_calls)} → {len(unique_calls)} tool calls"
            )

        # Cap tool calls per batch
        calls_to_run = unique_calls[: self.MAX_TOOL_CALLS_PER_BATCH]
        if len(unique_calls) > self.MAX_TOOL_CALLS_PER_BATCH:
            logger.warning(
                f"Agent {self.name}: capped tool calls from {len(unique_calls)} to {self.MAX_TOOL_CALLS_PER_BATCH}"
            )

        tool_results = []
        for tc in calls_to_run:
            tool_name = tc["name"]
            tool_args = tc["arguments"]

            await self.log_activity("tool_call", {"tool": tool_name, "args": tool_args})

            import time as _time
            _t0 = _time.perf_counter()
            try:
                result = await self.execute_tool(tool_name, tool_args)
                _dur = (_time.perf_counter() - _t0) * 1000
                tool_results.append({"tool": tool_name, "result": result})
                # Record analytics
                try:
                    from app.services.tool_analytics import tool_analytics
                    tool_analytics.record(tool_name, self.agent_id, self.name, True, _dur)
                except Exception:
                    pass
            except Exception as e:
                _dur = (_time.perf_counter() - _t0) * 1000
                logger.error(f"Tool {tool_name} failed: {e}")
                tool_results.append({"tool": tool_name, "error": str(e)})
                try:
                    from app.services.tool_analytics import tool_analytics
                    tool_analytics.record(tool_name, self.agent_id, self.name, False, _dur, str(e))
                except Exception:
                    pass

        # Add tool results as proper tool-role messages for next think cycle
        new_messages = list(state.get("messages", []))
        for i, tr in enumerate(tool_results):
            # Get the corresponding tool call ID
            tc_id = calls_to_run[i]["id"] if i < len(calls_to_run) else f"call_{i}"
            if "error" in tr:
                new_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": f"Error: {tr['error']}",
                    }
                )
            else:
                new_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": str(tr["result"]),
                    }
                )

        return {
            **state,
            "messages": new_messages,
            "tool_calls": [],
            "should_continue": True,
        }

    # --- Tool execution (override in subclasses) ---

    _BROWSER_TOOLS = {"browse_url", "screenshot_url"}
    _SANDBOX_TOOLS = {"execute_code"}

    def _get_tools_schema(self) -> list[dict]:
        """Return OpenAI-format tool schemas. Override in subclass."""
        schemas: list[dict] = []
        if self.sandbox_enabled:
            from app.tools.agent_tools import SANDBOX_TOOLS_SCHEMA
            schemas.extend(SANDBOX_TOOLS_SCHEMA)
        if self.browser_enabled:
            from app.tools.agent_tools import BROWSER_TOOLS_SCHEMA
            schemas.extend(BROWSER_TOOLS_SCHEMA)
        # Agent-to-agent messaging (available to all agents)
        schemas.append({
            "type": "function",
            "function": {
                "name": "message_agent",
                "description": "Send a message to another agent and get their response. Use this to collaborate with other agents.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string", "description": "ID of the agent to message"},
                        "message": {"type": "string", "description": "The message to send"},
                    },
                    "required": ["agent_id", "message"],
                },
            },
        })
        # Proactive reporting to CEO
        schemas.append({
            "type": "function",
            "function": {
                "name": "report_to_ceo",
                "description": "Send a proactive report or finding to the CEO. Use this when you discover something important, complete a significant task, or encounter a problem that needs attention. The CEO will be notified.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Brief report title"},
                        "content": {"type": "string", "description": "Report content with your findings, results, or concerns"},
                        "priority": {
                            "type": "string",
                            "enum": ["low", "medium", "high", "critical"],
                            "description": "Priority level (default: medium)",
                        },
                    },
                    "required": ["title", "content"],
                },
            },
        })
        # Workspace tools (available if agent belongs to a company)
        if self.company_id:
            schemas.extend([
                {
                    "type": "function",
                    "function": {
                        "name": "workspace_write",
                        "description": "Write a file to the shared company workspace. Other agents in the same company can read it.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "filename": {"type": "string", "description": "Name of the file to write"},
                                "content": {"type": "string", "description": "File content"},
                            },
                            "required": ["filename", "content"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "workspace_read",
                        "description": "Read a file from the shared company workspace.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "filename": {"type": "string", "description": "Name of the file to read"},
                            },
                            "required": ["filename"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "workspace_list",
                        "description": "List all files in the shared company workspace.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "memory_store",
                        "description": "Store a piece of knowledge in the shared company memory. Other agents in the same company can search and retrieve it later. Use for insights, findings, decisions, or any reusable knowledge.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "The knowledge or insight to store"},
                                "category": {
                                    "type": "string",
                                    "enum": ["insight", "finding", "decision", "data", "error"],
                                    "description": "Category of memory (default: insight)",
                                },
                                "importance": {
                                    "type": "number",
                                    "description": "Importance score 0.0-1.0 (default: 0.5). Higher = retained longer.",
                                },
                            },
                            "required": ["content"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "memory_query",
                        "description": "Search the shared company memory for relevant knowledge. Returns semantically similar memories stored by any agent in this company.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "What to search for in company memory"},
                                "limit": {"type": "integer", "description": "Max results (default: 5)"},
                            },
                            "required": ["query"],
                        },
                    },
                },
            ])
            # Artifact tools
            schemas.extend([
                {
                    "type": "function",
                    "function": {
                        "name": "create_artifact",
                        "description": "Create a structured deliverable (report, dataset, analysis, etc.) in the company workspace. Artifacts are persisted and tracked with metadata.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Artifact name/title"},
                                "content": {"type": "string", "description": "The artifact content"},
                                "artifact_type": {
                                    "type": "string",
                                    "enum": ["report", "dataset", "analysis", "summary", "code", "chart"],
                                    "description": "Type of artifact (default: report)",
                                },
                                "format": {
                                    "type": "string",
                                    "enum": ["markdown", "json", "csv", "html", "text"],
                                    "description": "Content format (default: markdown)",
                                },
                                "description": {"type": "string", "description": "Brief description of the artifact"},
                            },
                            "required": ["name", "content"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "list_artifacts",
                        "description": "List all artifacts in the company workspace, optionally filtered by type.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "artifact_type": {
                                    "type": "string",
                                    "enum": ["report", "dataset", "analysis", "summary", "code", "chart"],
                                    "description": "Filter by artifact type (optional)",
                                },
                            },
                        },
                    },
                },
            ])
        # Add skill tools
        from app.skills.registry import skill_registry
        schemas.extend(skill_registry.get_tool_schemas(self.skills))
        return schemas

    async def execute_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool by name. Override in subclass to add tools."""
        if tool_name in self._BROWSER_TOOLS and self.browser_enabled:
            from app.tools.agent_tools import execute_browser_tool
            return await execute_browser_tool(tool_name, arguments)
        if tool_name in self._SANDBOX_TOOLS and self.sandbox_enabled:
            from app.tools.agent_tools import execute_sandbox_tool
            return await execute_sandbox_tool(tool_name, arguments, network_enabled=getattr(self, 'network_enabled', False))
        if tool_name == "message_agent":
            return await self._tool_message_agent(arguments)
        if tool_name == "report_to_ceo":
            return await self._tool_report_to_ceo(arguments)
        if tool_name in ("workspace_write", "workspace_read", "workspace_list"):
            return await self._tool_workspace(tool_name, arguments)
        if tool_name in ("memory_store", "memory_query"):
            return await self._tool_company_memory(tool_name, arguments)
        if tool_name in ("create_artifact", "list_artifacts"):
            return await self._tool_artifact(tool_name, arguments)
        # Try skill registry
        from app.skills.registry import skill_registry
        result = await skill_registry.execute_tool(tool_name, arguments)
        if result is not None:
            return result
        return f"Unknown tool: {tool_name}"

    async def _tool_message_agent(self, args: dict) -> str:
        """Send a message to another agent and return their response."""
        import json as _json
        from app.agents.registry import registry
        from app.services.messaging import send_message

        target_id = args["agent_id"]
        target = registry.get(target_id)
        if not target:
            return _json.dumps({"success": False, "error": f"Agent {target_id} not found."})

        msg_content = args["message"]
        await send_message(self.agent_id, target_id, msg_content, "chat")

        try:
            response = await target.run(msg_content)
            await send_message(target_id, self.agent_id, response, "chat")
            return _json.dumps({"success": True, "agent_name": target.name, "response": response})
        except Exception as e:
            return _json.dumps({"success": False, "error": str(e)})

    async def _tool_report_to_ceo(self, args: dict) -> str:
        """Send a proactive report to the CEO and broadcast to dashboard."""
        import json as _json
        from app.services.messaging import send_message
        from app.services.event_bus import event_bus

        title = args.get("title", "Report")
        content = args.get("content", "")
        priority = args.get("priority", "medium")

        report_content = f"**[{priority.upper()}] {title}**\n\nFrom: {self.name} ({self.role})\n\n{content}"

        # Store as a message to CEO (to_agent_id=None means Owner/CEO)
        await send_message(
            from_agent_id=self.agent_id,
            to_agent_id=None,
            content=report_content,
            message_type="report",
            metadata={"priority": priority, "title": title, "agent_name": self.name},
        )

        # Broadcast to dashboard for real-time notification
        await event_bus.broadcast(
            "agent_report",
            {
                "agent_id": self.agent_id,
                "agent_name": self.name,
                "title": title,
                "content": content[:500],
                "priority": priority,
            },
            agent_id=self.agent_id,
        )

        return _json.dumps({
            "success": True,
            "message": f"Report '{title}' sent to CEO with priority {priority}.",
        })

    async def _tool_workspace(self, tool_name: str, args: dict) -> str:
        """Handle shared company workspace operations."""
        import json as _json
        from app.services.workspace import write_file, read_file, list_files

        if not self.company_id:
            return _json.dumps({"success": False, "error": "Agent has no company workspace."})

        if tool_name == "workspace_write":
            result = write_file(self.company_id, args["filename"], args["content"])
        elif tool_name == "workspace_read":
            result = read_file(self.company_id, args["filename"])
        elif tool_name == "workspace_list":
            result = list_files(self.company_id)
        else:
            result = {"success": False, "error": f"Unknown workspace tool: {tool_name}"}
        return _json.dumps(result)

    async def _tool_company_memory(self, tool_name: str, args: dict) -> str:
        """Handle shared company memory store/query operations."""
        import json as _json

        if not self.company_id:
            return _json.dumps({"success": False, "error": "Agent has no company."})

        if tool_name == "memory_store":
            content = args.get("content", "")
            category = args.get("category", "insight")
            importance = min(max(args.get("importance", 0.5), 0.0), 1.0)

            # Generate embedding
            try:
                from app.services.memory_service import store_memory
                from app.services.llm_router import get_embedding
                embedding = await get_embedding(content)
            except Exception:
                # Fallback: zero vector (still stores, just not searchable)
                embedding = [0.0] * 768

            from app.services.company_memory import store_company_memory
            memory_id = await store_company_memory(
                company_id=self.company_id,
                agent_id=self.agent_id,
                content=content,
                embedding=embedding,
                importance=importance,
                category=category,
            )
            return _json.dumps({"success": True, "memory_id": memory_id})

        elif tool_name == "memory_query":
            query = args.get("query", "")
            limit = min(max(args.get("limit", 5), 1), 20)

            try:
                from app.services.llm_router import get_embedding
                query_embedding = await get_embedding(query)
            except Exception:
                return _json.dumps({"success": False, "error": "Could not generate query embedding."})

            from app.services.company_memory import recall_company_memories
            memories = await recall_company_memories(
                company_id=self.company_id,
                query_embedding=query_embedding,
                limit=limit,
            )
            return _json.dumps({"success": True, "memories": memories, "count": len(memories)})

        return _json.dumps({"success": False, "error": f"Unknown memory tool: {tool_name}"})

    async def _tool_artifact(self, tool_name: str, args: dict) -> str:
        """Handle structured artifact creation and listing."""
        import json as _json

        if not self.company_id:
            return _json.dumps({"success": False, "error": "Agent has no company."})

        if tool_name == "create_artifact":
            from app.services.artifact_service import create_artifact
            artifact = create_artifact(
                company_id=self.company_id,
                agent_id=self.agent_id,
                agent_name=self.name,
                name=args["name"],
                content=args["content"],
                artifact_type=args.get("artifact_type", "report"),
                format=args.get("format", "markdown"),
                description=args.get("description", ""),
            )
            return _json.dumps({"success": True, "artifact": artifact})

        elif tool_name == "list_artifacts":
            from app.services.artifact_service import list_artifacts
            artifacts = list_artifacts(
                company_id=self.company_id,
                artifact_type=args.get("artifact_type"),
                agent_id=None,
            )
            return _json.dumps({"artifacts": artifacts, "count": len(artifacts)})

        return _json.dumps({"success": False, "error": f"Unknown artifact tool: {tool_name}"})

    def _parse_tool_call(self, tc) -> dict:
        """Parse a tool call from LiteLLM response format."""
        import json

        if hasattr(tc, "function"):
            args = tc.function.arguments
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"raw": args}
            return {
                "id": getattr(tc, "id", None) or f"call_{id(tc)}",
                "name": tc.function.name,
                "arguments": args,
            }
        return {"id": f"call_{id(tc)}", "name": str(tc), "arguments": {}}

    # --- Main execution ---

    async def run(self, user_input: str, history: list[dict] | None = None) -> str:
        """Run the agent with a user input. Returns the final output.

        Args:
            user_input: The current user message.
            history: Optional prior conversation messages (role/content dicts)
                     to include before the current input.
        """
        if self._stopped:
            return "Agent is stopped. Start it first."

        # Rate limiting check
        from app.services.rate_limiter import rate_limiter
        allowed, reason = await rate_limiter.check(self.agent_id)
        if not allowed:
            return f"Rate limited: {reason}. Please wait before retrying."

        initial_state: AgentState = {
            "messages": history or [],
            "current_input": user_input,
            "output": "",
            "tool_calls": [],
            "should_continue": False,
            "awaiting_approval": False,
            "approval_result": None,
            "error": None,
        }

        import time as _time
        _start = _time.monotonic()

        try:
            result = await self._graph.ainvoke(
                initial_state,
                config={"recursion_limit": self.MAX_TOOL_ROUNDS * 2 + 5},
            )
            # Store final messages for subclasses that need tool interaction history
            self._last_run_messages = result.get("messages", [])
            await self._set_status(AgentStatus.IDLE)

            output = result.get("output", "")
            _elapsed = _time.monotonic() - _start
            has_error = bool(result.get("error"))
            if has_error:
                output = f"Error: {result['error']}"
                await self._set_status(AgentStatus.ERROR)

            await self.log_activity("completed", {"output": output[:500]})

            # Record performance
            try:
                from app.services.performance_tracker import performance_tracker
                await performance_tracker.record_task(
                    agent_id=self.agent_id,
                    success=not has_error,
                    response_time_s=_elapsed,
                )
            except Exception:
                pass

            # Auto-capture memory of the interaction
            if output and not result.get("error"):
                try:
                    from app.services import llm_router as _lr
                    from app.services.memory_service import store_memory

                    summary = f"Task: {user_input[:200]} → Result: {output[:300]}"
                    embedding = await _lr.get_embedding(summary)
                    await store_memory(
                        agent_id=self.agent_id,
                        content=summary,
                        embedding=embedding,
                        importance=0.5,
                        category="conversation",
                    )
                except Exception as e:
                    logger.debug(f"Memory capture skipped: {e}")

            return output
        except Exception as e:
            logger.error(f"Agent {self.name} run error: {e}")
            await self._set_status(AgentStatus.ERROR)
            await self.log_activity("error", {"error": str(e)}, level="error")
            # Record failure in performance tracker
            try:
                _elapsed = _time.monotonic() - _start
                from app.services.performance_tracker import performance_tracker
                await performance_tracker.record_task(
                    agent_id=self.agent_id, success=False, response_time_s=_elapsed
                )
            except Exception:
                pass
            return f"Agent error: {e}"
