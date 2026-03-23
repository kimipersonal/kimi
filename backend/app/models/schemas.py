"""Pydantic schemas for API request/response models."""

from datetime import datetime
from pydantic import BaseModel


# --- Agent schemas ---


class AgentBase(BaseModel):
    model_config = {"protected_namespaces": ()}

    name: str
    role: str
    model_tier: str = "smart"
    system_prompt: str
    tools: list[str] = []
    company_id: str | None = None


class AgentCreate(AgentBase):
    pass


class AgentResponse(AgentBase):
    id: str
    status: str
    current_task: str | None = None
    last_action_at: datetime | None = None
    token_usage: int = 0
    created_at: datetime

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class AgentCommand(BaseModel):
    command: str  # start, stop, pause, resume, message
    payload: str | None = None


# --- Company schemas ---


class CompanyBase(BaseModel):
    name: str
    type: str
    config: dict = {}


class CompanyCreate(CompanyBase):
    pass


class CompanyResponse(CompanyBase):
    id: str
    status: str
    created_by_agent_id: str | None = None
    created_at: datetime
    agents: list[AgentResponse] = []

    model_config = {"from_attributes": True}


# --- Task schemas ---


class TaskResponse(BaseModel):
    id: str
    agent_id: str
    company_id: str | None = None
    type: str
    description: str
    input_data: dict | None = None
    output_data: dict | None = None
    status: str
    parent_task_id: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


# --- Approval schemas ---


class ApprovalResponse(BaseModel):
    id: str
    task_id: str | None = None
    agent_id: str
    description: str
    category: str
    details: dict | None = None
    status: str
    decision_reason: str | None = None
    requested_at: datetime
    decided_at: datetime | None = None

    model_config = {"from_attributes": True}


class ApprovalDecision(BaseModel):
    approved: bool
    reason: str | None = None


# --- Activity Log schemas ---


class ActivityLogResponse(BaseModel):
    id: str
    agent_id: str
    action: str
    details: dict | None = None
    level: str
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Message schemas ---


class MessageCreate(BaseModel):
    to_agent_id: str | None = None
    content: str
    message_type: str = "chat"


class MessageResponse(BaseModel):
    id: str
    from_agent_id: str | None = None
    to_agent_id: str | None = None
    content: str
    message_type: str
    created_at: datetime

    model_config = {"from_attributes": True}


# --- WebSocket event schemas ---


class WSEvent(BaseModel):
    event: str  # agent_state_change, task_update, approval_request, log, message
    data: dict
    agent_id: str | None = None
    timestamp: datetime | None = None


# --- Dashboard overview ---


class DashboardOverview(BaseModel):
    total_companies: int = 0
    total_agents: int = 0
    active_agents: int = 0
    pending_approvals: int = 0
    tasks_today: int = 0
    total_cost_today: float = 0.0
