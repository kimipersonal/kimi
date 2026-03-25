'use client'

import { useEffect, useState, useCallback } from 'react'
import Link from 'next/link'
import { useWS } from '@/hooks/WSContext'
import { api } from '@/lib/api'
import type { Agent } from '@/lib/api'
import { AgentCard } from '@/components/agents/AgentCard'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { ErrorBanner } from '@/components/ui/ErrorBanner'

export default function AgentsPage() {
  const { events } = useWS()
  const [agents, setAgents] = useState<Agent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setError(null)
    try {
      setAgents(await api.getAgents())
    } catch {
      setError('Failed to load agents')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    if (events.length > 0) {
      const last = events[events.length - 1]
      if (['agent_state_change', 'agent_hired'].includes(last.event)) {
        refresh()
      }
    }
  }, [events, refresh])

  const handleCommand = async (agentId: string, command: string) => {
    await api.sendCommand(agentId, command)
    await refresh()
  }

  if (loading) return <LoadingSpinner message="Loading agents..." />

  return (
    <div className="space-y-6 max-w-7xl">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold">Agents</h2>
        <span className="text-sm text-[var(--text-secondary)]">{agents.length} registered</span>
      </div>

      {error && <ErrorBanner message={error} onRetry={refresh} />}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {agents.map(agent => (
          <Link key={agent.id} href={`/agents/${agent.id}`}>
            <AgentCard agent={agent} onCommand={handleCommand} />
          </Link>
        ))}
      </div>

      {!error && agents.length === 0 && (
        <div className="text-center py-12 text-[var(--text-secondary)]">
          <p className="text-lg mb-2">No agents registered</p>
          <p className="text-sm">The CEO agent starts automatically on backend launch.</p>
        </div>
      )}
    </div>
  )
}
