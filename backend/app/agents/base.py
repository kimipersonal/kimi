"""Base Agent — LangGraph-based agent with state machine, logging, and event broadcasting."""

import asyncio
import logging
from typing import TypedDict

from langgraph.graph import StateGraph, START, END

from app.db.models import AgentStatus
from app.services.event_bus import event_bus
from app.services import llm_router

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
    ):
        self.agent_id = agent_id
        self.name = name
        self.role = role
        self.system_prompt = system_prompt
        self.model_tier = model_tier
        self.model_id = model_id  # specific model override
        self.tools = tools or []
        self._status = AgentStatus.IDLE
        self._paused = asyncio.Event()
        self._paused.set()  # Not paused initially
        self._stopped = False
        self._graph = self._build_graph()

    @property
    def status(self) -> AgentStatus:
        return self._status

    async def _set_status(self, new_status: AgentStatus):
        """Update status and broadcast to dashboard."""
        old = self._status
        self._status = new_status
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
        """Log an activity and broadcast to dashboard."""
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

    # --- Controls ---

    async def start(self):
        self._stopped = False
        self._paused.set()
        await self._set_status(AgentStatus.IDLE)
        await self.log_activity("agent_started")

    async def stop(self):
        self._stopped = True
        self._paused.set()  # Unblock if paused so it can exit
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

        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(state.get("messages", []))
        if state.get("current_input"):
            messages.append({"role": "user", "content": state["current_input"]})

        try:
            tools_schema = self._get_tools_schema() if self.tools else None
            response = await llm_router.chat(
                messages=messages,
                tier=self.model_tier,
                tools=tools_schema,
                model_override=self.model_id,
                agent_id=self.agent_id,
            )

            new_messages = list(state.get("messages", []))
            if state.get("current_input"):
                new_messages.append({"role": "user", "content": state["current_input"]})
            new_messages.append(
                {"role": "assistant", "content": response["content"] or ""}
            )

            tool_calls = response.get("tool_calls") or []

            return {
                **state,
                "messages": new_messages,
                "output": response["content"] or "",
                "tool_calls": [self._parse_tool_call(tc) for tc in tool_calls]
                if tool_calls
                else [],
                "should_continue": bool(tool_calls),
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

            try:
                result = await self.execute_tool(tool_name, tool_args)
                tool_results.append({"tool": tool_name, "result": result})
            except Exception as e:
                logger.error(f"Tool {tool_name} failed: {e}")
                tool_results.append({"tool": tool_name, "error": str(e)})

        # Add tool results as messages for next think cycle
        new_messages = list(state.get("messages", []))
        for tr in tool_results:
            if "error" in tr:
                new_messages.append(
                    {
                        "role": "user",
                        "content": f"Tool '{tr['tool']}' error: {tr['error']}",
                    }
                )
            else:
                new_messages.append(
                    {
                        "role": "user",
                        "content": f"Tool '{tr['tool']}' result: {tr['result']}",
                    }
                )

        return {
            **state,
            "messages": new_messages,
            "tool_calls": [],
            "should_continue": True,
        }

    # --- Tool execution (override in subclasses) ---

    def _get_tools_schema(self) -> list[dict]:
        """Return OpenAI-format tool schemas. Override in subclass."""
        return []

    async def execute_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool by name. Override in subclass to add tools."""
        return f"Unknown tool: {tool_name}"

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
            return {"name": tc.function.name, "arguments": args}
        return {"name": str(tc), "arguments": {}}

    # --- Main execution ---

    async def run(self, user_input: str) -> str:
        """Run the agent with a user input. Returns the final output."""
        if self._stopped:
            return "Agent is stopped. Start it first."

        initial_state: AgentState = {
            "messages": [],
            "current_input": user_input,
            "output": "",
            "tool_calls": [],
            "should_continue": False,
            "awaiting_approval": False,
            "approval_result": None,
            "error": None,
        }

        try:
            result = await self._graph.ainvoke(
                initial_state,
                config={"recursion_limit": self.MAX_TOOL_ROUNDS * 2 + 5},
            )
            await self._set_status(AgentStatus.IDLE)

            output = result.get("output", "")
            if result.get("error"):
                output = f"Error: {result['error']}"
                await self._set_status(AgentStatus.ERROR)

            await self.log_activity("completed", {"output": output[:500]})
            return output
        except Exception as e:
            logger.error(f"Agent {self.name} run error: {e}")
            await self._set_status(AgentStatus.ERROR)
            await self.log_activity("error", {"error": str(e)}, level="error")
            return f"Agent error: {e}"
