const API_BASE = process.env.NEXT_PUBLIC_API_URL || ''

async function fetchAPI<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    redirect: 'follow',
    ...options,
  })
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`)
  }
  return res.json()
}

export interface Agent {
  id: string
  name: string
  role: string
  status: string
  model_tier: string
  companies_count?: number
  managed_agents_count?: number
  pending_approvals?: number
}

export interface AgentDetail extends Agent {
  tools: string[]
  companies?: Company[]
  managed_agents?: ManagedAgent[]
  conversations?: ChatMessage[]
}

export interface Company {
  id: string
  name: string
  type: string
  description: string
  status: string
  agents: string[]
  agents_detail?: ManagedAgent[]
}

export interface ManagedAgent {
  id: string
  name: string
  role: string
  instructions: string
  model_tier: string
  company_id: string
  status: string
}

export interface Approval {
  id: string
  agent_id: string
  agent_name: string
  description: string
  category: string
  details: string
  status: string
  requested_at: string
  decided_at: string | null
  decision_reason: string | null
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: string
}

export interface Overview {
  total_companies: number
  total_agents: number
  active_agents: number
  pending_approvals: number
  tasks_today: number
  total_cost_today: number
}

export interface WSEvent {
  event: string
  data: Record<string, unknown>
  agent_id?: string
  timestamp?: string
}

export interface AgentCost {
  agent_id: string
  total_calls: number
  total_input_tokens: number
  total_output_tokens: number
  total_cost_usd: number
  calls_today: number
  cost_today_usd: number
}

export interface CostOverview {
  total_cost_usd: number
  cost_today_usd: number
  daily_budget_usd: number
  budget_used_pct: number
  total_calls: number
  calls_today: number
  agents: AgentCost[]
}

export interface HealthStatus {
  status: string
  uptime_seconds: number
  uptime_human: string
  checks: Record<string, { status: string; error?: string; checked_at?: string }>
}

export interface CircuitStatus {
  name: string
  state: string
  failure_count: number
  success_count: number
  failure_threshold: number
  recovery_timeout_s: number
}

export const api = {
  // Dashboard overview
  getOverview: () => fetchAPI<Overview>('/api/dashboard/overview'),

  // Agents
  getAgents: () => fetchAPI<Agent[]>('/api/agents'),
  getAgent: (id: string) => fetchAPI<AgentDetail>(`/api/agents/${id}`),
  sendCommand: (agentId: string, command: string, payload?: string) =>
    fetchAPI<Record<string, unknown>>(`/api/agents/${agentId}/command`, {
      method: 'POST',
      body: JSON.stringify({ command, payload }),
    }),
  chatWithAgent: (agentId: string, message: string) =>
    fetchAPI<{ agent_id: string; response: string }>(`/api/agents/${agentId}/chat`, {
      method: 'POST',
      body: JSON.stringify({ message }),
    }),

  // Companies
  getCompanies: () => fetchAPI<Company[]>('/api/dashboard/companies'),

  // Approvals
  getApprovals: (status?: string) =>
    fetchAPI<Approval[]>(`/api/dashboard/approvals${status ? `?status=${status}` : ''}`),
  decideApproval: (approvalId: string, approved: boolean, reason?: string) =>
    fetchAPI<Approval>(`/api/dashboard/approvals/${approvalId}/decide`, {
      method: 'POST',
      body: JSON.stringify({ approved, reason }),
    }),

  // Activity logs
  getLogs: (agentId?: string, limit?: number) => {
    const params = new URLSearchParams()
    if (agentId) params.set('agent_id', agentId)
    if (limit) params.set('limit', String(limit))
    const q = params.toString()
    return fetchAPI<WSEvent[]>(`/api/dashboard/logs${q ? `?${q}` : ''}`)
  },

  // Conversations
  getConversations: (agentId: string) =>
    fetchAPI<ChatMessage[]>(`/api/dashboard/conversations/${agentId}`),

  // Settings
  getSettings: () => fetchAPI<Record<string, unknown>>('/api/dashboard/settings'),

  // Cost tracking
  getCosts: () => fetchAPI<CostOverview>('/api/dashboard/costs'),
  getAgentCost: (agentId: string) => fetchAPI<AgentCost>(`/api/dashboard/costs/${agentId}`),

  // Health
  getHealth: () => fetchAPI<HealthStatus>('/api/dashboard/health'),
  getCircuits: () => fetchAPI<{ circuits: CircuitStatus[] }>('/api/dashboard/circuits'),
}
