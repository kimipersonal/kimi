'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import { useWS } from '@/hooks/WSContext'
import { api } from '@/lib/api'
import type { Agent, WSEvent } from '@/lib/api'

export default function LogsPage() {
  const { events } = useWS()
  const [agents, setAgents] = useState<Agent[]>([])
  const [filterAgent, setFilterAgent] = useState<string>('all')
  const [filterEvent, setFilterEvent] = useState<string>('all')
  const [autoScroll, setAutoScroll] = useState(true)
  const scrollRef = useRef<HTMLDivElement>(null)

  const refresh = useCallback(async () => {
    try {
      setAgents(await api.getAgents())
    } catch (err) {
      console.error('Failed to fetch agents:', err)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [events, autoScroll])

  // Filter events
  let filtered = events
  if (filterAgent !== 'all') {
    filtered = filtered.filter(e => e.agent_id === filterAgent)
  }
  if (filterEvent !== 'all') {
    filtered = filtered.filter(e => e.event === filterEvent)
  }

  // Collect unique event types
  const eventTypes = Array.from(new Set(events.map(e => e.event))).sort()

  const eventColor = (event: string) => {
    if (event.includes('error')) return 'text-[var(--danger)]'
    if (event.includes('approval')) return 'text-[var(--warning)]'
    if (event.includes('created') || event.includes('hired')) return 'text-[var(--success)]'
    if (event.includes('state_change')) return 'text-[var(--accent)]'
    return 'text-[var(--text-secondary)]'
  }

  return (
    <div className="space-y-4 max-w-7xl h-full flex flex-col">
      <div className="flex items-center justify-between shrink-0">
        <h2 className="text-xl font-bold">Activity Logs</h2>
        <span className="text-sm text-[var(--text-secondary)]">{filtered.length} events</span>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-4 shrink-0">
        <div className="flex items-center gap-2">
          <label className="text-xs text-[var(--text-secondary)]">Agent:</label>
          <select
            value={filterAgent}
            onChange={e => setFilterAgent(e.target.value)}
            className="text-xs px-2 py-1 rounded bg-[var(--bg-card)] border border-[var(--border)] text-[var(--text-primary)]"
          >
            <option value="all">All Agents</option>
            {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-[var(--text-secondary)]">Event:</label>
          <select
            value={filterEvent}
            onChange={e => setFilterEvent(e.target.value)}
            className="text-xs px-2 py-1 rounded bg-[var(--bg-card)] border border-[var(--border)] text-[var(--text-primary)]"
          >
            <option value="all">All Events</option>
            {eventTypes.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <label className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)] ml-auto cursor-pointer">
          <input type="checkbox" checked={autoScroll} onChange={e => setAutoScroll(e.target.checked)} className="rounded" />
          Auto-scroll
        </label>
      </div>

      {/* Log stream */}
      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
        {filtered.length === 0 ? (
          <p className="text-sm text-[var(--text-secondary)] text-center py-8">
            No events yet. Activity will appear here in real-time.
          </p>
        ) : (
          <div className="space-y-1">
            {filtered.map((e, i) => (
              <div key={i} className="text-xs font-mono flex gap-2 py-0.5 hover:bg-[var(--bg-secondary)] px-2 rounded">
                <span className="text-[var(--text-secondary)] shrink-0 w-20">
                  {e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : ''}
                </span>
                <span className={`shrink-0 w-40 ${eventColor(e.event)}`}>{e.event}</span>
                <span className="shrink-0 w-16 text-[var(--text-secondary)]">{e.agent_id || '-'}</span>
                <span className="truncate text-[var(--text-primary)]">
                  {e.data?.name ? <span className="font-semibold">{String(e.data.name)}: </span> : null}
                  {e.data?.action ? String(e.data.action) : ''}
                  {e.data?.tool ? `tool:${String(e.data.tool)}` : ''}
                  {e.data?.output ? ` → ${String(e.data.output).slice(0, 120)}` : ''}
                  {e.data?.error ? ` ERROR: ${String(e.data.error)}` : ''}
                  {e.data?.old_status ? `${String(e.data.old_status)} → ${String(e.data.new_status)}` : ''}
                  {e.data?.description ? String(e.data.description) : ''}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
