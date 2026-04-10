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
            {filtered.map((e, i) => {
              const d = (e.data || {}) as Record<string, any>
              return (
              <div key={i} className="text-xs font-mono py-1 hover:bg-[var(--bg-secondary)] px-2 rounded">
                <div className="flex gap-2">
                  <span className="text-[var(--text-secondary)] shrink-0 w-20">
                    {e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : ''}
                  </span>
                  <span className={`shrink-0 w-40 ${eventColor(e.event)}`}>{e.event}</span>
                  <span className="shrink-0 text-[var(--text-secondary)]" style={{ width: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {d.name || (e.agent_id ? e.agent_id.slice(0, 8) : '-')}
                  </span>
                  <span className="flex-1 truncate text-[var(--text-primary)]">
                    {e.event === 'tool_call' && d.tool && (
                      <><span className="text-yellow-400 font-semibold">{String(d.tool)}</span>{d.args ? <span className="text-[var(--text-secondary)]"> ({formatArgs(d.args)})</span> : null}</>
                    )}
                    {e.event === 'tool_result' && d.tool && (
                      <><span className={d.success ? 'text-green-400' : 'text-red-400'}>{d.success ? '✓' : '✗'} {String(d.tool)}</span>{d.duration_ms != null && <span className="text-[var(--text-secondary)] ml-1">{String(d.duration_ms)}ms</span>}{d.preview ? <span className="text-[var(--text-secondary)] ml-1">{'\u2192'} {String(d.preview).slice(0, 200)}</span> : d.error ? <span className="text-red-400 ml-1">ERROR: {String(d.error).slice(0, 200)}</span> : null}</>
                    )}
                    {e.event === 'thinking_content' && d.content && (
                      <span className="italic text-purple-300">{String(d.content).slice(0, 300)}</span>
                    )}
                    {e.event === 'thinking' && d.input && (
                      <span className="text-[var(--text-secondary)]">Input: {String(d.input).slice(0, 200)}</span>
                    )}
                    {e.event === 'agent_state_change' && d.old_status && (
                      <><span className="font-semibold">{d.name ? String(d.name) + ': ' : ''}</span><span className="text-[var(--text-secondary)]">{String(d.old_status)}</span> <span className="text-[var(--accent)]">{'\u2192'}</span> <span className="text-[var(--text-primary)]">{String(d.new_status)}</span></>
                    )}
                    {e.event === 'trade_signal' && (
                      <><span className="font-semibold">{String(d.symbol)} {String(d.direction).toUpperCase()}</span> <span className="text-[var(--text-secondary)]">conf={typeof d.confidence === 'number' ? Math.round(d.confidence * 100) : '?'}%</span>{d.entry_price ? <span className="text-[var(--text-secondary)]"> @ {String(d.entry_price)}</span> : null}</>
                    )}
                    {e.event === 'trade_executed' && (
                      <><span className="text-green-400 font-semibold">EXECUTED</span> <span>{String(d.symbol)} {String(d.side)}</span> <span className="text-[var(--text-secondary)]">size={String(d.size)} @ {String(d.filled_price || d.entry_price)}</span></>
                    )}
                    {e.event === 'trade_closed' && (
                      <><span className="font-semibold">CLOSED</span> <span className="text-[var(--text-secondary)]">exit={String(d.exit_price)}</span>{d.pnl != null && <span className={Number(d.pnl) >= 0 ? 'text-green-400 ml-1' : 'text-red-400 ml-1'}>P&L: {Number(d.pnl) >= 0 ? '+' : ''}{Number(d.pnl).toFixed(2)}</span>}</>
                    )}
                    {e.event === 'auto_trade_executed' && (
                      <><span className="text-yellow-400 font-semibold">AUTO-EXEC</span> <span>{String(d.symbol)} {String(d.direction)}</span> <span className="text-[var(--text-secondary)]">conf={typeof d.confidence === 'number' ? Math.round(d.confidence * 100) : '?'}%</span></>
                    )}
                    {e.event === 'log' && (
                      <>
                        {d.name ? <span className="font-semibold">{String(d.name)}: </span> : null}
                        {d.action ? String(d.action) : ''}
                        {d.tool ? ` tool:${String(d.tool)}` : ''}
                        {d.output ? <span className="text-[var(--text-secondary)]"> {'\u2192'} {String(d.output).slice(0, 200)}</span> : null}
                        {d.error ? <span className="text-red-400"> ERROR: {String(d.error)}</span> : null}
                        {d.description ? String(d.description) : ''}
                      </>
                    )}
                    {!['tool_call', 'tool_result', 'thinking_content', 'thinking', 'agent_state_change', 'trade_signal', 'trade_executed', 'trade_closed', 'auto_trade_executed', 'log'].includes(e.event) && (
                      <>
                        {d.name ? <span className="font-semibold">{String(d.name)}: </span> : null}
                        {d.action ? String(d.action) : ''}
                        {d.description ? String(d.description) : ''}
                        {d.output ? ` ${'\u2192'} ${String(d.output).slice(0, 120)}` : ''}
                      </>
                    )}
                  </span>
                </div>
              </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function formatArgs(args: unknown): string {
  if (!args) return ''
  try {
    const s = typeof args === 'string' ? args : JSON.stringify(args)
    return s.length > 120 ? s.slice(0, 120) + '…' : s
  } catch { return '' }
}
