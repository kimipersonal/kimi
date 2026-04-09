'use client'

import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

/** Single activity entry in the feed */
export interface ActivityEntry {
  id: number
  timestamp: string
  action: string        // "thinking", "tool_call", "tool_result", "thinking_content", "completed", "error"
  icon: string
  label: string
  detail?: string
  status?: 'running' | 'success' | 'error'
}

interface Props {
  agentName: string
  agentRole: string
  agentStatus: string
  entries: ActivityEntry[]
  visible: boolean
  position?: 'left' | 'right'
  anchorRect?: DOMRect | null
  onMouseEnter?: () => void
  onMouseLeave?: () => void
}

/** Icon mapping for tool names */
function toolIcon(tool: string): string {
  const t = tool.toLowerCase()
  if (t.includes('technical_analysis') || t.includes('advanced_analysis')) return '📊'
  if (t.includes('market_prices') || t.includes('get_prices')) return '💹'
  if (t.includes('create_signal')) return '📡'
  if (t.includes('trade_history') || t.includes('review')) return '📚'
  if (t.includes('approve')) return '✅'
  if (t.includes('reject')) return '🚫'
  if (t.includes('portfolio') || t.includes('account')) return '💼'
  if (t.includes('close_trade')) return '🔒'
  if (t.includes('position') || t.includes('calculate')) return '🧮'
  if (t.includes('message') || t.includes('report')) return '💬'
  if (t.includes('candles')) return '🕯️'
  if (t.includes('search') || t.includes('web')) return '🔍'
  if (t.includes('memory') || t.includes('recall')) return '🧠'
  if (t.includes('delegate')) return '📋'
  if (t.includes('workspace') || t.includes('file')) return '📄'
  if (t.includes('sandbox') || t.includes('code')) return '💻'
  if (t.includes('signal')) return '📡'
  if (t.includes('open_trades') || t.includes('sl_tp')) return '📈'
  return '🔧'
}

/** Pretty name for tool calls */
function toolLabel(tool: string): string {
  const labels: Record<string, string> = {
    'technical_analysis': 'Technical Analysis',
    'advanced_analysis': 'Advanced Analysis (TradingView)',
    'multi_technical_analysis': 'Multi-Symbol TA',
    'get_market_prices': 'Getting Prices',
    'create_signal': 'Creating Signal',
    'review_trade_history': 'Reviewing Trade History',
    'get_portfolio': 'Checking Portfolio',
    'get_account_summary': 'Account Summary',
    'approve_signal': 'Approving Signal',
    'reject_signal': 'Rejecting Signal',
    'close_trade': 'Closing Trade',
    'get_open_trades': 'Checking Open Trades',
    'check_sl_tp': 'Checking SL/TP Levels',
    'calculate_position_size': 'Calculating Position Size',
    'message_agent': 'Messaging Agent',
    'report_to_ceo': 'Reporting to CEO',
    'delegate_task': 'Delegating Task',
    'get_candles': 'Getting Candles',
    'memory_store': 'Storing Memory',
    'memory_query': 'Querying Memory',
  }
  return labels[tool] || tool.replace(/_/g, ' ')
}

/** Format a WSEvent into an ActivityEntry */
export function parseActivityEvent(
  event: { data: Record<string, unknown>; timestamp?: string },
  idCounter: number
): ActivityEntry | null {
  const action = String(event.data?.action || '')
  const details = (event.data?.details || {}) as Record<string, unknown>
  const ts = event.timestamp || new Date().toISOString()
  const time = ts.slice(11, 19) // HH:MM:SS

  switch (action) {
    case 'thinking':
      return { id: idCounter, timestamp: time, action, icon: '💭', label: 'Thinking...', status: 'running' }

    case 'thinking_content': {
      const content = String(details.content || '').slice(0, 120)
      const hasCalls = details.has_tool_calls
      return {
        id: idCounter, timestamp: time, action,
        icon: hasCalls ? '🧠' : '💬',
        label: hasCalls ? 'Planning next action' : 'Reasoning',
        detail: content,
      }
    }

    case 'tool_call': {
      const tool = String(details.tool || '')
      const args = details.args as Record<string, unknown> | undefined
      let argPreview = ''
      if (args) {
        if (args.symbol) argPreview = String(args.symbol)
        else if (args.symbols) argPreview = String((args.symbols as string[]).slice(0, 3).join(', '))
        else if (args.signal_id) argPreview = `signal ${String(args.signal_id).slice(0, 8)}...`
        else if (args.trade_id) argPreview = `trade ${String(args.trade_id).slice(0, 8)}...`
      }
      return {
        id: idCounter, timestamp: time, action,
        icon: toolIcon(tool),
        label: toolLabel(tool),
        detail: argPreview || undefined,
        status: 'running',
      }
    }

    case 'tool_result': {
      const tool = String(details.tool || '')
      const success = details.success as boolean
      const dur = details.duration_ms as number
      const errMsg = details.error as string | undefined
      const preview = String(details.preview || '').slice(0, 80)
      return {
        id: idCounter, timestamp: time, action,
        icon: success ? '✅' : '❌',
        label: `${toolLabel(tool)} ${dur ? `(${dur}ms)` : ''}`.trim(),
        detail: success ? (preview || 'Done') : (errMsg || 'Failed'),
        status: success ? 'success' : 'error',
      }
    }

    case 'completed': {
      const output = String(details.output || '').slice(0, 120)
      return {
        id: idCounter, timestamp: time, action,
        icon: '🏁',
        label: 'Cycle Complete',
        detail: output || undefined,
        status: 'success',
      }
    }

    case 'error': {
      const err = String(details.error || 'Unknown error').slice(0, 120)
      return {
        id: idCounter, timestamp: time, action,
        icon: '❗',
        label: 'Error',
        detail: err,
        status: 'error',
      }
    }

    case 'agent_started':
      return { id: idCounter, timestamp: time, action, icon: '▶️', label: 'Agent Started', status: 'success' }

    case 'agent_stopped':
      return { id: idCounter, timestamp: time, action, icon: '⏹️', label: 'Agent Stopped' }

    case 'agent_paused':
      return { id: idCounter, timestamp: time, action, icon: '⏸️', label: 'Agent Paused' }

    case 'agent_resumed':
      return { id: idCounter, timestamp: time, action, icon: '▶️', label: 'Agent Resumed' }

    default:
      return null
  }
}

/** Status indicator dot color */
function statusColor(status: string): string {
  switch (status) {
    case 'thinking': return '#4c6ef5'
    case 'acting': return '#fab005'
    case 'idle': return '#40c057'
    case 'error': return '#fa5252'
    case 'waiting_approval': return '#fd7e14'
    case 'paused': return '#868e96'
    case 'stopped': return '#495057'
    default: return '#868e96'
  }
}

export function AgentActivityPanel({ agentName, agentRole, agentStatus, entries, visible, position = 'left', anchorRect, onMouseEnter, onMouseLeave }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom on new entries
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [entries.length])

  if (!visible || !anchorRect) return null

  // Calculate fixed position based on anchor element
  const panelWidth = 280
  const gap = 12
  const style: React.CSSProperties = {
    position: 'fixed',
    top: Math.max(8, anchorRect.top - 8),
    zIndex: 9999,
  }
  if (position === 'right') {
    style.left = anchorRect.right + gap
  } else {
    style.left = anchorRect.left - panelWidth - gap
  }
  // Prevent going off-screen
  if ((style.left as number) < 8) style.left = 8
  if ((style.left as number) + panelWidth > window.innerWidth - 8) {
    style.left = window.innerWidth - panelWidth - 8
  }

  const panel = (
    <div
      className="activity-panel activity-panel--visible"
      style={style}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      {/* Header */}
      <div className="activity-panel__header">
        <div className="activity-panel__agent-info">
          <span
            className="activity-panel__status-dot"
            style={{ background: statusColor(agentStatus) }}
          />
          <span className="activity-panel__name">{agentName}</span>
        </div>
        <span className="activity-panel__role">{agentRole}</span>
      </div>

      {/* Current status badge */}
      <div className={`activity-panel__badge activity-panel__badge--${agentStatus}`}>
        {agentStatus === 'thinking' && '💭 Thinking...'}
        {agentStatus === 'acting' && '⚡ Executing tools...'}
        {agentStatus === 'idle' && '💤 Idle — waiting for next cycle'}
        {agentStatus === 'waiting_approval' && '⏳ Waiting for approval'}
        {agentStatus === 'error' && '❌ Error state'}
        {agentStatus === 'paused' && '⏸️ Paused'}
        {agentStatus === 'stopped' && '⏹️ Stopped'}
      </div>

      {/* Activity feed */}
      <div className="activity-panel__feed" ref={scrollRef}>
        {entries.length === 0 ? (
          <div className="activity-panel__empty">No activity yet</div>
        ) : (
          entries.map(entry => (
            <div key={entry.id} className={`activity-entry activity-entry--${entry.status || 'default'}`}>
              <span className="activity-entry__time">{entry.timestamp}</span>
              <span className="activity-entry__icon">{entry.icon}</span>
              <div className="activity-entry__content">
                <span className="activity-entry__label">{entry.label}</span>
                {entry.detail && (
                  <span className="activity-entry__detail">{entry.detail}</span>
                )}
              </div>
              {entry.status === 'running' && (
                <span className="activity-entry__spinner" />
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )

  return createPortal(panel, document.body)
}
