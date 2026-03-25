'use client'

import { useEffect, useState, useCallback } from 'react'
import { useWS } from '@/hooks/WSContext'
import { api } from '@/lib/api'
import type { Agent, Overview } from '@/lib/api'
import { AgentCard } from '@/components/agents/AgentCard'
import { OverviewCards } from '@/components/dashboard/OverviewCards'
import { ActivityLog } from '@/components/dashboard/ActivityLog'
import { ChatPanel } from '@/components/dashboard/ChatPanel'
import { PixelOffice } from '@/components/pixel-office/PixelOffice'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { ErrorBanner } from '@/components/ui/ErrorBanner'

export default function Dashboard() {
  const { events } = useWS()
  const [agents, setAgents] = useState<Agent[]>([])
  const [overview, setOverview] = useState<Overview>({
    total_companies: 0, total_agents: 0, active_agents: 0,
    pending_approvals: 0, tasks_today: 0, total_cost_today: 0,
  })
  const [chatLoading, setChatLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refreshData = useCallback(async () => {
    setError(null)
    let failed = false
    try { setAgents(await api.getAgents()) } catch { failed = true }
    try { setOverview(await api.getOverview()) } catch { failed = true }
    if (failed) setError('Failed to load some dashboard data')
    setLoading(false)
  }, [])

  useEffect(() => {
    refreshData()
  }, [refreshData])

  useEffect(() => {
    if (events.length > 0) {
      const last = events[events.length - 1]
      if (['agent_state_change', 'company_created', 'agent_hired', 'approval_decided'].includes(last.event)) {
        refreshData()
      }
    }
  }, [events, refreshData])

  const handleAgentCommand = async (agentId: string, command: string) => {
    try {
      await api.sendCommand(agentId, command)
      await refreshData()
    } catch (err) {
      console.error('Command failed:', err)
    }
  }

  const handleChatSend = async (message: string): Promise<string> => {
    setChatLoading(true)
    try {
      const result = await api.chatWithAgent('ceo', message)
      await refreshData()
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
      const response = await api.chatWithAgentStream('ceo', message, onStep)
      await refreshData()
      return response
    } finally {
      setChatLoading(false)
    }
  }

  if (loading) return <LoadingSpinner message="Loading dashboard..." />

  return (
    <div className="space-y-6 max-w-7xl">
      <h2 className="text-xl font-bold">Overview</h2>

      {error && <ErrorBanner message={error} onRetry={refreshData} />}

      <OverviewCards data={overview} />

      {/* Pixel Office Visual Layer */}
      <section>
        <PixelOffice />
      </section>

      {/* Agents grid */}
      <section>
        <h3 className="text-lg font-semibold mb-3">Agents</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {agents.map(agent => (
            <AgentCard key={agent.id} agent={agent} onCommand={handleAgentCommand} />
          ))}
          {agents.length === 0 && (
            <p className="text-sm text-[var(--text-secondary)] col-span-3">
              No agents registered yet. The CEO agent should be starting...
            </p>
          )}
        </div>
      </section>

      {/* Chat + Activity Log side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <ChatPanel
          agentId="ceo"
          agentName="CEO"
          onSend={handleChatSend}
          onSendStream={handleChatSendStream}
          loading={chatLoading}
        />
        <ActivityLog events={events} />
      </div>
    </div>
  )
}
