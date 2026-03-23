'use client'

import type { Agent } from '@/lib/api'

const STATUS_CONFIG: Record<string, { label: string; dotClass: string }> = {
  idle: { label: '🟢 Active', dotClass: 'status-idle' },
  thinking: { label: '🔵 Thinking', dotClass: 'status-thinking' },
  acting: { label: '🟡 Acting', dotClass: 'status-acting' },
  waiting_approval: { label: '⏳ Waiting', dotClass: 'status-waiting_approval' },
  paused: { label: '⏸️ Paused', dotClass: 'status-paused' },
  stopped: { label: '🔴 Stopped', dotClass: 'status-stopped' },
  error: { label: '❌ Error', dotClass: 'status-error' },
}

const TIER_LABELS: Record<string, string> = {
  fast: '⚡ Fast',
  smart: '🧠 Smart',
  reasoning: '🎯 Reasoning',
}

export function AgentCard({
  agent,
  onCommand,
}: {
  agent: Agent
  onCommand: (agentId: string, command: string) => void
}) {
  const config = STATUS_CONFIG[agent.status] || { label: agent.status, dotClass: '' }

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4 hover:border-[var(--accent)] transition-colors">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className={`status-dot ${config.dotClass}`} />
          <h3 className="font-semibold text-base">{agent.name}</h3>
        </div>
        <span className="text-[10px] px-2 py-0.5 rounded bg-[var(--bg-secondary)] text-[var(--text-secondary)]">
          {TIER_LABELS[agent.model_tier] || agent.model_tier}
        </span>
      </div>

      {/* Role + Status */}
      <p className="text-sm text-[var(--text-secondary)]">{agent.role}</p>
      <p className="text-xs mt-1 mb-2">{config.label}</p>

      {/* CEO stats */}
      {agent.companies_count !== undefined && (
        <div className="flex gap-3 text-xs text-[var(--text-secondary)] mb-3 border-t border-[var(--border)] pt-2 mt-2">
          <span>🏢 {agent.companies_count} companies</span>
          <span>🤖 {agent.managed_agents_count || 0} agents</span>
          {(agent.pending_approvals || 0) > 0 && (
            <span className="text-[var(--warning)]">⏳ {agent.pending_approvals} pending</span>
          )}
        </div>
      )}

      {/* Controls */}
      <div className="flex gap-2" onClick={e => e.preventDefault()}>
        {agent.status === 'stopped' || agent.status === 'error' ? (
          <button
            onClick={() => onCommand(agent.id, 'start')}
            className="text-xs px-3 py-1 rounded bg-[var(--success)] text-white hover:opacity-80"
          >
            Start
          </button>
        ) : (
          <>
            <button
              onClick={() => onCommand(agent.id, agent.status === 'paused' ? 'resume' : 'pause')}
              className="text-xs px-3 py-1 rounded bg-[var(--warning)] text-black hover:opacity-80"
            >
              {agent.status === 'paused' ? 'Resume' : 'Pause'}
            </button>
            <button
              onClick={() => onCommand(agent.id, 'stop')}
              className="text-xs px-3 py-1 rounded bg-[var(--danger)] text-white hover:opacity-80"
            >
              Stop
            </button>
          </>
        )}
      </div>
    </div>
  )
}
