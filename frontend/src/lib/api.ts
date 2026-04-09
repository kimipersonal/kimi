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

export interface StreamStep {
  type: 'step' | 'status' | 'error'
  action?: string
  agent_name?: string
  details?: Record<string, unknown>
  level?: string
  old_status?: string
  new_status?: string
  error?: string
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

export interface ModelInfo {
  id: string
  name: string
  cost: string
  type: 'native' | 'model_garden'
  tier_hint: string
}

export interface ModelsResponse {
  models: ModelInfo[]
  current_tiers: { fast: string; smart: string; reasoning: string }
}

export interface SettingsUpdateRequest {
  llm_fast?: string
  llm_smart?: string
  llm_reasoning?: string
}

export interface SettingsUpdateResponse {
  updated: Record<string, string>
  current_tiers: { fast: string; smart: string; reasoning: string }
}

export interface ScheduledTask {
  task_id: string
  agent_id: string
  description: string
  interval_seconds: number
  created_by: string
  enabled: boolean
  last_run: string | null
  run_count: number
}

export interface SkillInfo {
  name: string
  display_name: string
  description: string
  version: string
  category: string
  enabled: boolean
  configured: boolean
  tools: string[]
  tool_count: number
  metadata: {
    author: string
    tags: string[]
    requires_config: string[]
    icon: string
  }
}

export interface SkillsResponse {
  skills: SkillInfo[]
  total: number
}

export interface SkillsStatus {
  total: number
  enabled: number
  categories: string[]
  skills: SkillInfo[]
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

  chatWithAgentStream: async (
    agentId: string,
    message: string,
    onStep: (step: StreamStep) => void,
  ): Promise<string> => {
    const res = await fetch(`${API_BASE}/api/agents/${agentId}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    })
    if (!res.ok) throw new Error(`API error: ${res.status}`)
    const reader = res.body?.getReader()
    if (!reader) throw new Error('No response body')

    const decoder = new TextDecoder()
    let finalResponse = ''
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const data = JSON.parse(line.slice(6))
          if (data.type === 'done') {
            finalResponse = data.response
          } else if (data.type !== 'heartbeat') {
            onStep(data as StreamStep)
          }
        } catch { /* skip malformed */ }
      }
    }
    return finalResponse
  },

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
  updateSettings: (data: SettingsUpdateRequest) =>
    fetchAPI<SettingsUpdateResponse>('/api/dashboard/settings', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  // Models
  getModels: () => fetchAPI<ModelsResponse>('/api/dashboard/models'),

  // Cost tracking
  getCosts: () => fetchAPI<CostOverview>('/api/dashboard/costs'),
  getAgentCost: (agentId: string) => fetchAPI<AgentCost>(`/api/dashboard/costs/${agentId}`),

  // Scheduled tasks
  getSchedules: () => fetchAPI<{ schedules: ScheduledTask[] }>('/api/dashboard/schedules'),
  pauseSchedule: (taskId: string) =>
    fetchAPI<{ status: string }>(`/api/dashboard/schedules/${taskId}/pause`, { method: 'POST' }),
  resumeSchedule: (taskId: string) =>
    fetchAPI<{ status: string }>(`/api/dashboard/schedules/${taskId}/resume`, { method: 'POST' }),
  deleteSchedule: (taskId: string) =>
    fetchAPI<{ status: string }>(`/api/dashboard/schedules/${taskId}`, { method: 'DELETE' }),
  updateSchedule: (taskId: string, data: { description?: string; interval_seconds?: number }) =>
    fetchAPI<ScheduledTask>(`/api/dashboard/schedules/${taskId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  // Health
  getHealth: () => fetchAPI<HealthStatus>('/api/dashboard/health'),
  getCircuits: () => fetchAPI<{ circuits: CircuitStatus[] }>('/api/dashboard/circuits'),

  // Skills marketplace
  getSkills: (category?: string) =>
    fetchAPI<SkillsResponse>(`/api/skills${category ? `?category=${category}` : ''}`),
  getSkillsStatus: () => fetchAPI<SkillsStatus>('/api/skills/status'),
  getSkillCategories: () => fetchAPI<{ categories: Record<string, number> }>('/api/skills/categories'),
  getSkill: (name: string) => fetchAPI<SkillInfo>(`/api/skills/${name}`),

  // Trading
  getPortfolio: () => fetchAPI<any>('/api/trading/portfolio'),
  getTradeSignals: (limit = 20) => fetchAPI<any[]>(`/api/trading/signals?limit=${limit}`),
  getTradeHistory: (limit = 20) => fetchAPI<any[]>(`/api/trading/history?limit=${limit}`),
  decideSignal: (signalId: string, approved: boolean, reason?: string) =>
    fetchAPI<Record<string, unknown>>(`/api/trading/signals/${signalId}/decide`, {
      method: 'POST',
      body: JSON.stringify({ approved, reason }),
    }),
  closeTrade: (tradeId: string) =>
    fetchAPI<Record<string, unknown>>(`/api/trading/close/${tradeId}`, {
      method: 'POST',
    }),
}
