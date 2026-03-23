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

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] h-full flex flex-col">
      <div className="px-4 py-3 border-b border-[var(--border)]">
        <h3 className="font-semibold">Activity Log</h3>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-2 max-h-[400px]">
        {events.length === 0 ? (
          <p className="text-sm text-[var(--text-secondary)]">No activity yet. Send a message to the CEO to get started.</p>
        ) : (
          events.map((e, i) => (
            <div key={i} className="text-xs font-mono flex gap-2">
              <span className="text-[var(--text-secondary)] shrink-0">{formatTime(e.timestamp)}</span>
              <span className={`shrink-0 ${eventColor(e.event)}`}>[{e.event}]</span>
              <span className="truncate">
                {e.data?.name ? <span className="font-semibold">{String(e.data.name)}: </span> : null}
                {e.data?.action ? String(e.data.action) : ''}
                {e.data?.output ? ` → ${String(e.data.output).slice(0, 100)}` : ''}
                {e.data?.error ? ` ERROR: ${String(e.data.error)}` : ''}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
