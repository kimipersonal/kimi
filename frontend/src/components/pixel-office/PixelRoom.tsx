'use client'

import type { Agent } from '@/lib/api'
import { PixelAgent } from './PixelAgent'
import type { ActivityEntry } from './AgentActivityPanel'

interface PixelRoomProps {
  label: string
  isCeo?: boolean
  agents: Agent[]
  icon?: string
  agentActivities?: Record<string, ActivityEntry[]>
}

export function PixelRoom({ label, isCeo, agents, icon, agentActivities = {} }: PixelRoomProps) {
  return (
    <div className={`pixel-room ${isCeo ? 'pixel-room--ceo' : ''}`}>
      <div className="pixel-room__label">
        {icon && <span style={{ marginRight: 2 }}>{icon}</span>}
        {label}
      </div>
      <div className="flex flex-wrap items-end gap-6 pt-7 px-2 pb-2 justify-center">
        {agents.map(agent => (
          <PixelAgent
            key={agent.id}
            agent={agent}
            activityEntries={agentActivities[agent.id] || []}
          />
        ))}
        {agents.length === 0 && (
          <div className="text-[9px] text-[var(--text-secondary)] italic py-4">
            Empty room
          </div>
        )}
      </div>
    </div>
  )
}
