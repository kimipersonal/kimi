'use client'

import { useRouter } from 'next/navigation'
import type { Agent } from '@/lib/api'

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

export function PixelAgent({ agent }: { agent: Agent }) {
  const router = useRouter()
  const roleClass = getRoleClass(agent.role)
  const indicator = getStatusIndicator(agent.status)

  return (
    <div
      className={`pixel-desk pixel-desk--${agent.status}`}
      onClick={() => router.push(`/agents/${agent.id}`)}
      title={`${agent.name} — ${agent.role} (${agent.status})`}
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
    </div>
  )
}
