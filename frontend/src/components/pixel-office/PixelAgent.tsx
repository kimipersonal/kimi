'use client'

import { useState, useRef, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import type { Agent } from '@/lib/api'
import { AgentActivityPanel } from './AgentActivityPanel'
import type { ActivityEntry } from './AgentActivityPanel'

/** Map agent role keywords → CSS color class suffix */
function getRoleClass(role: string): string {
  const r = role.toLowerCase()
  if (r.includes('ceo') || r.includes('chief')) return 'ceo'
  if (r.includes('research')) return 'researcher'
  if (r.includes('develop') || r.includes('engineer')) return 'developer'
  if (r.includes('analyst') || r.includes('analy')) return 'analyst'
  if (r.includes('trad')) return 'trader'
  if (r.includes('risk')) return 'risk'
  return 'default'
}

/** Status bubble content */
function getStatusIndicator(status: string): { emoji: string; label: string } | null {
  switch (status) {
    case 'thinking': return { emoji: '💭', label: '' }
    case 'acting': return { emoji: '⚡', label: '' }
    case 'waiting_approval': return { emoji: '⏳', label: '' }
    case 'error': return { emoji: '❗', label: '' }
    default: return null
  }
}

interface PixelAgentProps {
  agent: Agent
  activityEntries?: ActivityEntry[]
}

export function PixelAgent({ agent, activityEntries = [] }: PixelAgentProps) {
  const router = useRouter()
  const roleClass = getRoleClass(agent.role)
  const indicator = getStatusIndicator(agent.status)
  const [hovered, setHovered] = useState(false)
  const hoverTimer = useRef<NodeJS.Timeout | null>(null)
  const closeTimer = useRef<NodeJS.Timeout | null>(null)
  const deskRef = useRef<HTMLDivElement>(null)

  // Determine if popover should go left or right based on position in viewport
  const [popoverSide, setPopoverSide] = useState<'left' | 'right'>('right')
  const [anchorRect, setAnchorRect] = useState<DOMRect | null>(null)

  const cancelClose = useCallback(() => {
    if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null }
  }, [])

  const scheduleClose = useCallback(() => {
    cancelClose()
    closeTimer.current = setTimeout(() => setHovered(false), 200)
  }, [cancelClose])

  const onMouseEnter = useCallback(() => {
    cancelClose()
    hoverTimer.current = setTimeout(() => {
      if (deskRef.current) {
        const rect = deskRef.current.getBoundingClientRect()
        setPopoverSide(rect.left > window.innerWidth / 2 ? 'left' : 'right')
        setAnchorRect(rect)
      }
      setHovered(true)
    }, 300)
  }, [cancelClose])

  const onMouseLeave = useCallback(() => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current)
    scheduleClose()
  }, [scheduleClose])

  return (
    <div
      ref={deskRef}
      className={`pixel-desk pixel-desk--${agent.status}`}
      onClick={() => router.push(`/agents/${agent.id}`)}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      {/* Status bubble */}
      {indicator && (
        <div className={`pixel-bubble ${agent.status === 'thinking' ? 'pixel-bubble--thinking' : ''}`}>
          {indicator.emoji}
        </div>
      )}

      {/* Agent character */}
      <div className={`pixel-agent pixel-agent--${roleClass}`}>
        <div className="pixel-agent__head" />
        <div className="pixel-agent__body" />
      </div>

      {/* Desk */}
      <div className="pixel-desk__surface" />
      <div className="pixel-desk__legs" />

      {/* Name label */}
      <span className="pixel-desk__name">{agent.name}</span>

      {/* Activity hover panel */}
      <AgentActivityPanel
        agentName={agent.name}
        agentRole={agent.role}
        agentStatus={agent.status}
        entries={activityEntries}
        visible={hovered}
        position={popoverSide}
        anchorRect={anchorRect}
        onMouseEnter={cancelClose}
        onMouseLeave={scheduleClose}
      />
    </div>
  )
}
