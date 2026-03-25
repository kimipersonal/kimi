'use client'

import { useRef, useEffect } from 'react'

interface LogEntry {
  event: string
  data: Record<string, unknown>
  agent_id?: string
  timestamp?: string
}

export function ActivityLog({ events }: { events: LogEntry[] }) {
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [events])

  const formatTime = (ts?: string) => {
    if (!ts) return ''
    const d = new Date(ts)
    return d.toLocaleTimeString()
  }

  const eventColor = (event: string) => {
    if (event.includes('error')) return 'text-[var(--danger)]'
    if (event.includes('approval')) return 'text-[var(--warning)]'
    if (event.includes('created') || event.includes('hired')) return 'text-[var(--success)]'
    return 'text-[var(--text-secondary)]'
  }

  const actionIcon = (action: string) => {
    if (action === 'thinking') return '🧠'
    if (action === 'tool_call') return '🔧'
    if (action === 'completed') return '✅'
    if (action === 'error') return '❌'
    if (action === 'autonomous_run') return '🔄'
    return '📋'
  }

  const formatDetails = (data: Record<string, unknown>) => {
    const details = data?.details as Record<string, unknown> | undefined
    const action = String(data?.action || '')

    if (action === 'tool_call' && details) {
      const tool = String(details.tool || '')
      const args = details.args as Record<string, unknown> | undefined
      const argsStr = args ? Object.entries(args).map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`).join(', ') : ''
      return `${tool}(${argsStr.slice(0, 120)}${argsStr.length > 120 ? '…' : ''})`
    }
    if (action === 'thinking' && details?.input) {
      return `"${String(details.input).slice(0, 100)}${String(details.input).length > 100 ? '…' : ''}"`
    }
    if (action === 'completed' && details?.output) {
      return `→ ${String(details.output).slice(0, 120)}`
    }
    if (data?.output) return `→ ${String(data.output).slice(0, 100)}`
    if (data?.error) return `ERROR: ${String(data.error)}`
    return ''
  }

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] h-full flex flex-col">
      <div className="px-4 py-3 border-b border-[var(--border)]">
        <h3 className="font-semibold">Activity Log</h3>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-1 max-h-[400px]">
        {events.length === 0 ? (
          <p className="text-sm text-[var(--text-secondary)]">No activity yet. Send a message to the CEO to get started.</p>
        ) : (
          events.map((e, i) => {
            const action = String(e.data?.action || '')
            const icon = e.event === 'log' ? actionIcon(action) : ''
            const detailStr = e.event === 'log' ? formatDetails(e.data) : ''
            return (
              <div key={i} className="text-xs font-mono flex gap-2 items-start">
                <span className="text-[var(--text-secondary)] shrink-0">{formatTime(e.timestamp)}</span>
                <span className={`shrink-0 ${eventColor(e.event)}`}>[{e.event}]</span>
                <span className="min-w-0">
                  {icon && <span className="mr-1">{icon}</span>}
                  {e.data?.name ? <span className="font-semibold">{String(e.data.name)}: </span> : null}
                  {action && <span>{action}</span>}
                  {detailStr && (
                    <span className="text-[var(--text-secondary)] ml-1 break-all">{detailStr}</span>
                  )}
                  {!action && !detailStr && e.data?.error && (
                    <span className="text-[var(--danger)]"> ERROR: {String(e.data.error)}</span>
                  )}
                </span>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
