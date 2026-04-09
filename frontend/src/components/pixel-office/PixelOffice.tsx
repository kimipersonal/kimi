'use client'

import { useEffect, useState, useCallback, useMemo, useRef } from 'react'
import { useWS } from '@/hooks/WSContext'
import { api } from '@/lib/api'
import type { Agent, Company } from '@/lib/api'
import { PixelRoom } from './PixelRoom'
import { parseActivityEvent } from './AgentActivityPanel'
import type { ActivityEntry } from './AgentActivityPanel'
import './pixel-office.css'

/** Map company type → icon */
function companyIcon(type: string): string {
  const t = type.toLowerCase()
  if (t.includes('trad')) return '📈'
  if (t.includes('research')) return '🔬'
  if (t.includes('develop') || t.includes('tech')) return '💻'
  if (t.includes('market')) return '📢'
  if (t.includes('financ')) return '🏦'
  return '🏢'
}

interface SpeechBubble {
  agentId: string
  text: string
  id: number
}

export function PixelOffice() {
  const { events } = useWS()
  const [agents, setAgents] = useState<Agent[]>([])
  const [companies, setCompanies] = useState<Company[]>([])
  const [speeches, setSpeeches] = useState<SpeechBubble[]>([])
  const [agentActivities, setAgentActivities] = useState<Record<string, ActivityEntry[]>>({})
  const bubbleIdRef = useRef(0)
  const activityIdRef = useRef(0)

  const fetchData = useCallback(async () => {
    try { setAgents(await api.getAgents()) } catch { /* retry on next event */ }
    try { setCompanies(await api.getCompanies()) } catch { /* retry on next event */ }
  }, [])

  // Initial load
  useEffect(() => { fetchData() }, [fetchData])

  // React to WebSocket events
  useEffect(() => {
    if (events.length === 0) return
    const last = events[events.length - 1]

    // Update agent statuses from state change events
    if (last.event === 'agent_state_change') {
      setAgents(prev => prev.map(a =>
        a.id === last.agent_id
          ? { ...a, status: String(last.data?.new_status ?? a.status) }
          : a
      ))
    }

    // Refresh on structural changes
    if (['agent_hired', 'company_created', 'approval_decided'].includes(last.event)) {
      fetchData()
    }

    // Show speech bubble on agent actions
    if (last.event === 'log' && last.agent_id && last.data?.action) {
      const text = String(last.data.action).slice(0, 30)
      const id = ++bubbleIdRef.current
      setSpeeches(prev => [...prev.slice(-5), { agentId: last.agent_id!, text, id }])
      // Remove after animation
      setTimeout(() => {
        setSpeeches(prev => prev.filter(s => s.id !== id))
      }, 3000)

      // Track activity entries per agent
      const entry = parseActivityEvent(
        { data: last.data as Record<string, unknown>, timestamp: last.timestamp },
        ++activityIdRef.current
      )
      if (entry && last.agent_id) {
        setAgentActivities(prev => {
          const existing = prev[last.agent_id!] || []
          return { ...prev, [last.agent_id!]: [...existing.slice(-29), entry] }
        })
      }
    }
  }, [events, fetchData])

  // Organize agents: CEO separate, rest grouped by company
  const { ceoAgent, companyRooms, unassigned } = useMemo(() => {
    const ceo = agents.find(a => a.role.toLowerCase().includes('ceo'))
    const rest = agents.filter(a => a !== ceo)

    // Build agent→company map from company agent lists
    const agentCompanyMap = new Map<string, string>()
    for (const c of companies) {
      for (const agentRef of (c.agents || [])) {
        // agents might be ids or objects
        const id = typeof agentRef === 'string' ? agentRef : (agentRef as { id: string }).id
        agentCompanyMap.set(id, c.id)
      }
    }

    const rooms = companies.map(c => ({
      company: c,
      agents: rest.filter(a => agentCompanyMap.get(a.id) === c.id),
    }))

    const assignedIds = new Set(rooms.flatMap(r => r.agents.map(a => a.id)))
    const unassignedAgents = rest.filter(a => !assignedIds.has(a.id))

    return { ceoAgent: ceo, companyRooms: rooms, unassigned: unassignedAgents }
  }, [agents, companies])

  // Count active (non-idle, non-stopped) agents
  const activeCount = agents.filter(a =>
    !['idle', 'stopped', 'paused'].includes(a.status)
  ).length

  return (
    <div className="pixel-office">
      {/* Header bar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-[var(--border)]">
        <div className="flex items-center gap-2">
          <span className="text-sm">🏛️</span>
          <span className="text-xs font-semibold text-[var(--text-secondary)] uppercase tracking-wider">
            AI Holding — Pixel Office
          </span>
        </div>
        <div className="flex items-center gap-3 text-[10px] text-[var(--text-secondary)]">
          <span>{agents.length} agents</span>
          <span>•</span>
          <span>{companies.length} companies</span>
          {activeCount > 0 && (
            <>
              <span>•</span>
              <span className="text-[var(--accent)]">{activeCount} active</span>
            </>
          )}
        </div>
      </div>

      {/* Office floor */}
      <div className="p-4 space-y-3">
        {/* CEO Office - top center */}
        {ceoAgent && (
          <PixelRoom
            label="CEO Office"
            isCeo
            agents={[ceoAgent]}
            icon="👑"
            agentActivities={agentActivities}
          />
        )}

        {/* Company rooms grid */}
        {companyRooms.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {companyRooms.map(({ company, agents: companyAgents }) => (
              <PixelRoom
                key={company.id}
                label={company.name}
                agents={companyAgents}
                icon={companyIcon(company.type)}
                agentActivities={agentActivities}
              />
            ))}
          </div>
        )}

        {/* Unassigned lobby */}
        {unassigned.length > 0 && (
          <PixelRoom
            label="Lobby"
            agents={unassigned}
            icon="🚪"
            agentActivities={agentActivities}
          />
        )}
      </div>
    </div>
  )
}
