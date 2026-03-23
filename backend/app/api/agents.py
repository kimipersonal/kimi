"""Agent API endpoints — manage and interact with agents."""

from fastapi import APIRouter, HTTPException
from app.agents.registry import registry
from app.models.schemas import AgentCommand

router = APIRouter(prefix="/api/agents", tags=["agents"])


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
