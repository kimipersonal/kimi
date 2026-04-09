"""Agent API endpoints — manage and interact with agents."""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from app.agents.registry import registry
from app.api.auth import verify_api_key
from app.models.schemas import AgentCommand
from app.services.event_bus import event_bus

router = APIRouter(prefix="/api/agents", tags=["agents"], dependencies=[Depends(verify_api_key)])


@router.get("")
async def list_agents():
    """List all registered agents with live status."""
    from app.services.company_manager import list_companies

    agents = registry.get_all()
    companies = await list_companies()

    # Build agent→company lookup
    agent_company: dict[str, str] = {}
    for c in companies:
        for a in c.get("agents", []):
            agent_company[a["id"]] = c["name"]

    result = []
    for a in agents:
        info = {
            "id": a.agent_id,
            "name": a.name,
            "role": a.role,
            "status": a.status.value,
            "model_tier": a.model_tier,
            "company": agent_company.get(a.agent_id),
        }
        from app.agents.ceo import CEOAgent

        if isinstance(a, CEOAgent):
            info["companies_count"] = len(companies)
            info["managed_agents_count"] = registry.count - 1  # Exclude CEO
        result.append(info)
    return result


@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    """Get details of a specific agent with enriched data."""
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    info = {
        "id": agent.agent_id,
        "name": agent.name,
        "role": agent.role,
        "status": agent.status.value,
        "model_tier": agent.model_tier,
        "tools": agent.tools,
    }

    from app.agents.ceo import CEOAgent

    if isinstance(agent, CEOAgent):
        from app.services.company_manager import list_companies
        from app.services.approval_service import list_approvals
        from app.services.messaging import get_messages

        companies = await list_companies()
        pending = await list_approvals(status="pending", agent_id=agent_id)
        messages = await get_messages(agent_id=agent_id, limit=50)

        info["companies"] = companies
        info["pending_approvals"] = pending
        info["conversations"] = agent._conversations[-50:]
        info["messages"] = messages

    return info


@router.post("/{agent_id}/command")
async def send_command(agent_id: str, cmd: AgentCommand):
    """Send a control command to an agent (start, stop, pause, resume, message)."""
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    match cmd.command:
        case "start":
            await agent.start()
            return {"status": "started"}
        case "stop":
            await agent.stop()
            return {"status": "stopped"}
        case "pause":
            await agent.pause()
            return {"status": "paused"}
        case "resume":
            await agent.resume()
            return {"status": "resumed"}
        case "message":
            if not cmd.payload:
                raise HTTPException(
                    status_code=400, detail="Payload required for message command"
                )
            output = await agent.run(cmd.payload)
            return {"status": "completed", "output": output}
        case _:
            raise HTTPException(
                status_code=400, detail=f"Unknown command: {cmd.command}"
            )


@router.post("/{agent_id}/chat")
async def chat_with_agent(agent_id: str, body: dict):
    """Send a message to an agent and get the response."""
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    message = body.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    output = await agent.run(message)
    return {"agent_id": agent_id, "response": output}


@router.post("/{agent_id}/chat/stream")
async def chat_with_agent_stream(agent_id: str, body: dict):
    """SSE streaming chat — sends real-time thinking/tool/status events during agent execution."""
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    message = body.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    async def event_stream():
        # Subscribe to events for this agent
        subscriber = event_bus.subscribe("dashboard")
        run_task = asyncio.create_task(agent.run(message))

        try:
            while not run_task.done():
                try:
                    event = await asyncio.wait_for(subscriber.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    # Send heartbeat to keep connection alive
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                    continue

                ev_data = event.get("data", {})
                ev_agent_id = ev_data.get("agent_id") or event.get("agent_id")
                event_type = event.get("event", "")

                # Only forward events for the requested agent
                if ev_agent_id != agent_id:
                    continue

                if event_type == "log":
                    action = ev_data.get("action", "")
                    step = {
                        "type": "step",
                        "action": action,
                        "agent_name": ev_data.get("name", ""),
                        "details": ev_data.get("details"),
                        "level": ev_data.get("level", "info"),
                    }
                    yield f"data: {json.dumps(step)}\n\n"

                elif event_type == "agent_state_change":
                    step = {
                        "type": "status",
                        "old_status": ev_data.get("old_status"),
                        "new_status": ev_data.get("new_status"),
                        "agent_name": ev_data.get("name", ""),
                    }
                    yield f"data: {json.dumps(step)}\n\n"

            # Get the final result
            output = await run_task
            yield f"data: {json.dumps({'type': 'done', 'response': output})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        finally:
            if not run_task.done():
                run_task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
