'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { useWS } from '@/hooks/WSContext'
import { api } from '@/lib/api'
import type { AgentDetail, WSEvent } from '@/lib/api'
import { ChatPanel } from '@/components/dashboard/ChatPanel'
import { ArrowLeft } from 'lucide-react'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { ErrorBanner } from '@/components/ui/ErrorBanner'

const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  idle: { label: '🟢 Active', color: 'var(--success)' },
  thinking: { label: '🔵 Thinking', color: 'var(--accent)' },
  acting: { label: '🟡 Acting', color: 'var(--warning)' },
  waiting_approval: { label: '⏳ Waiting', color: 'var(--warning)' },
  paused: { label: '⏸️ Paused', color: 'var(--text-secondary)' },
  stopped: { label: '🔴 Stopped', color: 'var(--danger)' },
  error: { label: '❌ Error', color: 'var(--danger)' },
}

export default function AgentDetailPage() {
  const params = useParams()
  const router = useRouter()
  const agentId = params.id as string
  const { events } = useWS()
  const [agent, setAgent] = useState<AgentDetail | null>(null)
  const [chatLoading, setChatLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setError(null)
    try {
      setAgent(await api.getAgent(agentId))
    } catch {
      setError('Failed to load agent details')
    }
  }, [agentId])

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    if (events.length > 0) {
      const last = events[events.length - 1]
      if (last.agent_id === agentId && ['agent_state_change', 'log'].includes(last.event)) {
        refresh()
      }
    }
  }, [events, refresh, agentId])

  const handleCommand = async (command: string) => {
    await api.sendCommand(agentId, command)
    await refresh()
  }

  const handleChatSend = async (message: string): Promise<string> => {
    setChatLoading(true)
    try {
      const result = await api.chatWithAgent(agentId, message)
      await refresh()
      return result.response
    } finally {
      setChatLoading(false)
    }
  }

  const handleChatSendStream = async (
    message: string,
    onStep: (step: import('@/lib/api').StreamStep) => void,
  ): Promise<string> => {
    setChatLoading(true)
    try {
      const result = await api.chatWithAgentStream(agentId, message, onStep)
      await refresh()
      return result
    } finally {
      setChatLoading(false)
    }
  }

  if (error) {
    return (
      <div className="space-y-4 max-w-6xl">
        <button onClick={() => router.back()} className="flex items-center gap-1 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
          <ArrowLeft size={16} /> Back
        </button>
        <ErrorBanner message={error} onRetry={refresh} />
      </div>
    )
  }

  if (!agent) {
    return <LoadingSpinner message="Loading agent..." />
  }

  const statusInfo = STATUS_LABELS[agent.status] || { label: agent.status, color: 'var(--text-secondary)' }

  // Filter events for this agent
  const agentEvents = events.filter(e => e.agent_id === agentId)

  return (
    <div className="space-y-6 max-w-6xl">
      {/* Back + Header */}
      <div className="flex items-center gap-4">
        <button onClick={() => router.back()} className="p-1 hover:bg-[var(--bg-card)] rounded">
          <ArrowLeft size={18} />
        </button>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <span className={`status-dot status-${agent.status}`} />
            <h2 className="text-xl font-bold">{agent.name}</h2>
            <span className="text-xs px-2 py-0.5 rounded bg-[var(--bg-card)] text-[var(--text-secondary)]">
              {agent.model_tier}
            </span>
          </div>
          <p className="text-sm text-[var(--text-secondary)] mt-0.5">{agent.role}</p>
        </div>
        <span className="text-sm" style={{ color: statusInfo.color }}>{statusInfo.label}</span>
      </div>

      {/* Controls */}
      <div className="flex gap-2">
        {agent.status === 'stopped' || agent.status === 'error' ? (
          <button onClick={() => handleCommand('start')} className="text-sm px-4 py-1.5 rounded bg-[var(--success)] text-white hover:opacity-80">
            Start
          </button>
        ) : (
          <>
            <button onClick={() => handleCommand(agent.status === 'paused' ? 'resume' : 'pause')} className="text-sm px-4 py-1.5 rounded bg-[var(--warning)] text-black hover:opacity-80">
              {agent.status === 'paused' ? 'Resume' : 'Pause'}
            </button>
            <button onClick={() => handleCommand('stop')} className="text-sm px-4 py-1.5 rounded bg-[var(--danger)] text-white hover:opacity-80">
              Stop
            </button>
          </>
        )}
      </div>

      {/* Info Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Tools */}
        <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
          <h4 className="text-xs font-semibold text-[var(--text-secondary)] mb-2 uppercase">Tools</h4>
          <div className="flex flex-wrap gap-1.5">
            {agent.tools.map(t => (
              <span key={t} className="text-xs px-2 py-0.5 rounded bg-[var(--bg-secondary)] text-[var(--text-secondary)]">{t}</span>
            ))}
          </div>
        </div>

        {/* Companies managed */}
        {agent.companies && (
          <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
            <h4 className="text-xs font-semibold text-[var(--text-secondary)] mb-2 uppercase">Companies</h4>
            {agent.companies.length === 0 ? (
              <p className="text-xs text-[var(--text-secondary)]">No companies yet</p>
            ) : (
              <div className="space-y-1">
                {agent.companies.map(c => (
                  <div key={c.id} className="text-sm flex items-center justify-between">
                    <span>{c.name}</span>
                    <span className="text-xs text-[var(--text-secondary)]">{c.type}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Managed agents */}
        {agent.managed_agents && (
          <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
            <h4 className="text-xs font-semibold text-[var(--text-secondary)] mb-2 uppercase">Managed Agents</h4>
            {agent.managed_agents.length === 0 ? (
              <p className="text-xs text-[var(--text-secondary)]">No sub-agents yet</p>
            ) : (
              <div className="space-y-1">
                {agent.managed_agents.map(a => (
                  <div key={a.id} className="text-sm flex items-center justify-between">
                    <span>{a.name}</span>
                    <span className="text-xs text-[var(--text-secondary)]">{a.role}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Chat + Activity for this agent */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <ChatPanel
          agentId={agentId}
          agentName={agent.name}
          onSend={handleChatSend}
          onSendStream={handleChatSendStream}
          loading={chatLoading}
          initialMessages={agent.conversations}
        />
        {/* Agent-specific activity log */}
        <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] flex flex-col">
          <div className="px-4 py-3 border-b border-[var(--border)]">
            <h3 className="font-semibold text-sm">Activity Log</h3>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-2 max-h-[400px]">
            {agentEvents.length === 0 ? (
              <p className="text-xs text-[var(--text-secondary)]">No activity for this agent yet.</p>
            ) : (
              agentEvents.map((e, i) => (
                <div key={i} className="text-xs font-mono flex gap-2">
                  <span className="text-[var(--text-secondary)] shrink-0">
                    {e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : ''}
                  </span>
                  <span className="text-[var(--accent)] shrink-0">[{e.event}]</span>
                  <span className="truncate">
                    {e.data?.action ? String(e.data.action) : ''}
                    {e.data?.output ? ` → ${String(e.data.output).slice(0, 100)}` : ''}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
