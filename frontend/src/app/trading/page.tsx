'use client'

import { useEffect, useState, useCallback } from 'react'
import { useWS } from '@/hooks/WSContext'
import { api } from '@/lib/api'
import { ErrorBanner } from '@/components/ui/ErrorBanner'

interface Account {
  platform: string
  balance?: number
  equity?: number
  currency?: string
  is_demo?: boolean
  error?: string
}

interface Position {
  id: string
  trade_id?: string
  symbol: string
  side: string
  size: number
  entry_price: number
  current_price: number
  unrealized_pnl: number
  stop_loss?: number
  take_profit?: number
  platform: string
}

interface Signal {
  id: string
  symbol: string
  direction: string
  confidence: number
  entry_price: number | null
  stop_loss: number | null
  take_profit: number | null
  reasoning: string
  status: string
  agent_id: string
  approved_by: string | null
  created_at: string | null
}

interface TradeHistory {
  id: string
  platform: string
  symbol: string
  side: string
  size: number
  entry_price: number
  exit_price: number | null
  stop_loss?: number | null
  take_profit?: number | null
  pnl: number | null
  status: string
  opened_at: string | null
  closed_at: string | null
}

interface Portfolio {
  accounts: Account[]
  positions: Position[]
  total_balance: number
  total_equity: number
  unrealized_pnl: number
  realized_pnl: number
  open_positions_count: number
  platforms_connected: number
  error?: string
}

type Tab = 'positions' | 'signals' | 'history'

export default function TradingPage() {
  const { lastEvent } = useWS()
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null)
  const [signals, setSignals] = useState<Signal[]>([])
  const [history, setHistory] = useState<TradeHistory[]>([])
  const [loading, setLoading] = useState(true)
  const [errors, setErrors] = useState<string[]>([])
  const [activeTab, setActiveTab] = useState<Tab>('positions')
  const [signalFilter, setSignalFilter] = useState<string>('all')
  const [closingId, setClosingId] = useState<string | null>(null)

  const load = useCallback(async () => {
    const errs: string[] = []
    try { setPortfolio(await api.getPortfolio()) } catch { errs.push('portfolio') }
    try { setSignals(await api.getTradeSignals(50)) } catch { errs.push('signals') }
    try { setHistory(await api.getTradeHistory(50)) } catch { errs.push('history') }
    setErrors(errs)
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    const iv = setInterval(load, 30000)
    return () => clearInterval(iv)
  }, [load])

  useEffect(() => {
    if (
      lastEvent?.event === 'trade_signal' ||
      lastEvent?.event === 'trade_executed' ||
      lastEvent?.event === 'trade_closed'
    ) {
      load()
    }
  }, [lastEvent, load])

  const decideSignal = async (signalId: string, approved: boolean, reason?: string) => {
    await api.decideSignal(signalId, approved, reason)
    load()
  }

  const closeTrade = async (tradeId: string) => {
    if (!confirm('Close this position? This will sell at market price.')) return
    setClosingId(tradeId)
    try {
      await api.closeTrade(tradeId)
      load()
    } catch (e: any) {
      alert(`Failed to close: ${e.message || e}`)
    } finally {
      setClosingId(null)
    }
  }

  const filteredSignals = signalFilter === 'all'
    ? signals
    : signals.filter(s => s.status === signalFilter)

  const pendingCount = signals.filter(s => s.status === 'pending').length
  const closedTrades = history.filter(h => h.closed_at || h.status === 'closed')
  const openTrades = history.filter(h => !h.closed_at && h.status !== 'closed')
  const totalPnl = closedTrades.reduce((sum, t) => sum + (t.pnl ?? 0), 0)
  const positions = portfolio?.positions ?? []

  const totalInvested = positions.reduce((sum, p) => sum + (p.entry_price || 0) * p.size, 0)
  const totalUnrealized = positions.reduce((sum, p) => sum + (p.unrealized_pnl || 0), 0)

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[var(--accent)]" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Trading</h1>
          <p className="text-sm text-[var(--text-secondary)]">
            {portfolio?.platforms_connected ?? 0} platform(s) connected
          </p>
        </div>
        <button onClick={load}
          className="px-3 py-1.5 text-sm bg-[var(--bg-card)] border border-[var(--border)] rounded-md hover:bg-[var(--accent)]/10">
          Refresh
        </button>
      </div>

      {errors.length > 0 && <ErrorBanner message={`Failed to load: ${errors.join(', ')}`} onRetry={load} />}

      {portfolio?.error ? (
        <div className="p-4 bg-yellow-500/10 border border-yellow-500/30 rounded-lg text-sm">{portfolio.error}</div>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <StatCard label="Free Balance" value={fmtMoney(portfolio?.total_balance ?? 0)} />
            <StatCard label="In Positions" value={fmtMoney(totalInvested)} sub={`${positions.length} open`} />
            <StatCard label="Unrealized P&L" value={fmtMoney(totalUnrealized, true)} color={pnlColor(totalUnrealized)} />
            <StatCard label="Realized P&L" value={fmtMoney(totalPnl, true)} color={pnlColor(totalPnl)} sub={`${closedTrades.length} closed`} />
            <StatCard
              label="Total Equity"
              value={fmtMoney((portfolio?.total_balance ?? 0) + totalInvested + totalUnrealized)}
              sub={portfolio?.accounts?.[0]?.is_demo ? 'DEMO' : 'LIVE'}
              color={portfolio?.accounts?.[0]?.is_demo ? 'text-yellow-400' : undefined}
            />
          </div>

          {portfolio?.accounts && portfolio.accounts.length > 0 && (
            <div className="flex items-center gap-3 text-xs text-[var(--text-secondary)] px-1">
              {portfolio.accounts.map((a, i) => (
                <span key={i} className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-green-400 inline-block" />
                  {a.platform}
                  {a.is_demo && <span className="px-1 py-0.5 bg-yellow-500/20 text-yellow-400 rounded text-[9px]">DEMO</span>}
                </span>
              ))}
            </div>
          )}
        </>
      )}

      <div className="flex items-center gap-1 border-b border-[var(--border)]">
        <TabButton active={activeTab === 'positions'} onClick={() => setActiveTab('positions')} label={`Positions (${positions.length})`} />
        <TabButton active={activeTab === 'signals'} onClick={() => setActiveTab('signals')} label={`Signals (${signals.length})`} badge={pendingCount > 0 ? pendingCount : undefined} />
        <TabButton active={activeTab === 'history'} onClick={() => setActiveTab('history')} label={`Trade History (${history.length})`} />
      </div>

      {activeTab === 'positions' && (
        <div>
          {positions.length > 0 ? (
            <div className="space-y-3">
              {positions.map((p, i) => {
                const value = (p.entry_price || p.current_price || 0) * p.size
                const pnlPct = p.entry_price
                  ? ((p.current_price - p.entry_price) / p.entry_price) * 100 * (p.side === 'sell' ? -1 : 1)
                  : 0
                return (
                  <div key={i} className="p-4 bg-[var(--bg-card)] rounded-lg border border-[var(--border)]">
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-3">
                        <span className="text-base font-bold font-mono">{p.symbol}</span>
                        <span className={`text-xs font-semibold px-2 py-0.5 rounded ${p.side === 'buy' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
                          {p.side.toUpperCase()}
                        </span>
                      </div>
                      <div className="text-right">
                        <p className={`text-base font-bold font-mono ${pnlColor(p.unrealized_pnl)}`}>
                          {fmtMoney(p.unrealized_pnl, true)}
                        </p>
                        <p className={`text-xs ${pnlColor(pnlPct)}`}>{pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%</p>
                      </div>
                    </div>

                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                      <PriceCell label="Entry" value={p.entry_price} />
                      <PriceCell label="Current" value={p.current_price} highlight />
                      <PriceCell label="Stop Loss" value={p.stop_loss} color="text-red-400" />
                      <PriceCell label="Take Profit" value={p.take_profit} color="text-green-400" />
                    </div>

                    <div className="flex items-center justify-between mt-3 text-xs text-[var(--text-secondary)]">
                      <div className="flex gap-4">
                        <span>Size: <span className="text-[var(--text-primary)] font-mono">{p.size}</span></span>
                        <span>Value: <span className="text-[var(--text-primary)] font-mono">{fmtMoney(value)}</span></span>
                      </div>
                      <div className="flex items-center gap-3">
                        <span>{p.platform}</span>
                        <button
                          onClick={() => closeTrade(p.trade_id || p.id)}
                          disabled={closingId === (p.trade_id || p.id)}
                          className="px-3 py-1 bg-red-600/80 text-white rounded text-xs font-medium hover:bg-red-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          {closingId === (p.trade_id || p.id) ? 'Closing...' : 'Close'}
                        </button>
                      </div>
                    </div>

                    {p.stop_loss && p.take_profit && p.current_price > 0 && (
                      <div className="mt-3">
                        <SLTPBar side={p.side} entry={p.entry_price} current={p.current_price} sl={p.stop_loss} tp={p.take_profit} />
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          ) : (
            <p className="text-sm text-[var(--text-secondary)] py-4">No open positions</p>
          )}
        </div>
      )}

      {activeTab === 'signals' && (
        <div>
          <div className="flex gap-2 mb-4 flex-wrap">
            {(['all', 'pending', 'approved', 'rejected', 'executed', 'expired'] as const).map(f => {
              const count = f === 'all' ? signals.length : signals.filter(s => s.status === f).length
              return (
                <button key={f} onClick={() => setSignalFilter(f)}
                  className={`px-2.5 py-1 text-xs rounded-md border transition-colors ${signalFilter === f ? 'bg-[var(--accent)] text-white border-[var(--accent)]' : 'bg-[var(--bg-card)] border-[var(--border)] text-[var(--text-secondary)] hover:bg-[var(--accent)]/10'}`}>
                  {f.charAt(0).toUpperCase() + f.slice(1)}
                  {count > 0 && <span className={`ml-1 text-[10px] ${signalFilter === f ? 'opacity-80' : ''}`}>{count}</span>}
                </button>
              )
            })}
          </div>

          {filteredSignals.length > 0 ? (
            <div className="space-y-3">
              {filteredSignals.map(s => {
                const rr = s.entry_price && s.stop_loss && s.take_profit
                  ? Math.abs(s.take_profit - s.entry_price) / Math.abs(s.entry_price - s.stop_loss)
                  : null
                return (
                  <div key={s.id} className="p-4 bg-[var(--bg-card)] rounded-lg border border-[var(--border)]">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-bold">{s.symbol}</span>
                        <span className={`text-xs font-semibold px-2 py-0.5 rounded ${s.direction === 'buy' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
                          {s.direction.toUpperCase()}
                        </span>
                        <ConfidenceBadge value={s.confidence} />
                        {rr !== null && (
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${rr >= 2 ? 'bg-green-500/20 text-green-400' : rr >= 1.5 ? 'bg-yellow-500/20 text-yellow-400' : 'bg-red-500/20 text-red-400'}`}>
                            R:R {rr.toFixed(1)}
                          </span>
                        )}
                      </div>
                      <StatusBadge status={s.status} />
                    </div>
                    <p className="text-xs text-[var(--text-secondary)] mb-3 line-clamp-2">{s.reasoning}</p>
                    <div className="grid grid-cols-3 gap-2 mb-3">
                      <PriceCell label="Entry" value={s.entry_price} small />
                      <PriceCell label="Stop Loss" value={s.stop_loss} color="text-red-400" small />
                      <PriceCell label="Take Profit" value={s.take_profit} color="text-green-400" small />
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] text-[var(--text-secondary)]">{s.created_at ? timeAgo(s.created_at) : ''}</span>
                      {s.status === 'pending' && (
                        <div className="flex gap-2">
                          <button onClick={() => decideSignal(s.id, true)}
                            className="px-3 py-1.5 bg-green-600 text-white rounded text-xs font-medium hover:bg-green-500 transition-colors">Approve</button>
                          <button onClick={() => decideSignal(s.id, false, 'Manual rejection')}
                            className="px-3 py-1.5 bg-red-600/80 text-white rounded text-xs font-medium hover:bg-red-500 transition-colors">Reject</button>
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <p className="text-sm text-[var(--text-secondary)] py-4">
              {signalFilter === 'all' ? 'No trade signals yet' : `No ${signalFilter} signals`}
            </p>
          )}
        </div>
      )}

      {activeTab === 'history' && (
        <div>
          {history.length > 0 && (
            <div className="grid grid-cols-3 gap-3 mb-4">
              <div className="p-3 bg-[var(--bg-card)] rounded-lg border border-[var(--border)] text-center">
                <p className="text-xs text-[var(--text-secondary)]">Open</p>
                <p className="text-xl font-bold text-blue-400">{openTrades.length}</p>
              </div>
              <div className="p-3 bg-[var(--bg-card)] rounded-lg border border-[var(--border)] text-center">
                <p className="text-xs text-[var(--text-secondary)]">Closed</p>
                <p className="text-xl font-bold">{closedTrades.length}</p>
              </div>
              <div className="p-3 bg-[var(--bg-card)] rounded-lg border border-[var(--border)] text-center">
                <p className="text-xs text-[var(--text-secondary)]">Total P&L</p>
                <p className={`text-xl font-bold font-mono ${pnlColor(totalPnl)}`}>{fmtMoney(totalPnl, true)}</p>
              </div>
            </div>
          )}
          {history.length > 0 ? (
            <div className="space-y-2">
              {history.map(t => {
                const value = t.entry_price * t.size
                return (
                  <div key={t.id} className="p-3 bg-[var(--bg-card)] rounded-lg border border-[var(--border)]">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-medium">{t.symbol}</span>
                        <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${t.side === 'buy' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
                          {t.side.toUpperCase()}
                        </span>
                        <span className="text-xs text-[var(--text-secondary)]">{t.size} @ {fmtPrice(t.entry_price)}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        {t.pnl != null && t.status === 'closed' && (
                          <span className={`font-mono text-sm font-bold ${pnlColor(t.pnl)}`}>{fmtMoney(t.pnl, true)}</span>
                        )}
                        <StatusBadge status={t.status} />
                      </div>
                    </div>
                    <div className="flex items-center justify-between text-[10px] text-[var(--text-secondary)]">
                      <div className="flex gap-3">
                        <span>Value: {fmtMoney(value)}</span>
                        {t.exit_price && <span>Exit: {fmtPrice(t.exit_price)}</span>}
                        {t.stop_loss && <span>SL: {fmtPrice(t.stop_loss)}</span>}
                        {t.take_profit && <span>TP: {fmtPrice(t.take_profit)}</span>}
                      </div>
                      <span>{t.opened_at ? timeAgo(t.opened_at) : ''}{t.closed_at ? ` → closed ${timeAgo(t.closed_at)}` : ''}</span>
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <p className="text-sm text-[var(--text-secondary)] py-4">No trades yet</p>
          )}
        </div>
      )}
    </div>
  )
}

function fmtMoney(v: number, showSign = false): string {
  const sign = showSign && v > 0 ? '+' : ''
  return `${sign}$${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function fmtPrice(v: number | null | undefined): string {
  if (v == null || v === 0) return '-'
  if (v >= 1000) return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (v >= 1) return v.toFixed(4)
  return v.toFixed(6)
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

function pnlColor(value: number): string {
  if (value > 0) return 'text-green-400'
  if (value < 0) return 'text-red-400'
  return 'text-[var(--text-secondary)]'
}

function TabButton({ active, onClick, label, badge }: { active: boolean; onClick: () => void; label: string; badge?: number }) {
  return (
    <button onClick={onClick}
      className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${active ? 'border-[var(--accent)] text-[var(--accent)]' : 'border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}>
      {label}
      {badge != null && badge > 0 && <span className="ml-1.5 px-1.5 py-0.5 text-[10px] bg-yellow-500/20 text-yellow-400 rounded-full">{badge}</span>}
    </button>
  )
}

function StatCard({ label, value, color, sub }: { label: string; value: string; color?: string; sub?: string }) {
  return (
    <div className="p-3 bg-[var(--bg-card)] rounded-lg border border-[var(--border)]">
      <p className="text-[10px] text-[var(--text-secondary)] mb-0.5 uppercase tracking-wide">{label}</p>
      <p className={`text-lg font-bold font-mono ${color || ''}`}>{value}</p>
      {sub && <p className="text-[10px] text-[var(--text-secondary)] mt-0.5">{sub}</p>}
    </div>
  )
}

function PriceCell({ label, value, color, highlight, small }: { label: string; value?: number | null; color?: string; highlight?: boolean; small?: boolean }) {
  return (
    <div className={small ? '' : 'bg-[var(--bg-secondary)] rounded px-2 py-1.5'}>
      <p className="text-[10px] text-[var(--text-secondary)]">{label}</p>
      <p className={`font-mono ${small ? 'text-xs' : 'text-sm'} ${color || ''} ${highlight ? 'font-bold' : ''}`}>{fmtPrice(value)}</p>
    </div>
  )
}

function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const c = pct >= 80 ? 'bg-green-500/20 text-green-400' : pct >= 70 ? 'bg-yellow-500/20 text-yellow-400' : 'bg-red-500/20 text-red-400'
  return <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${c}`}>{pct}%</span>
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    pending: 'bg-yellow-500/20 text-yellow-400',
    approved: 'bg-green-500/20 text-green-400',
    rejected: 'bg-red-500/20 text-red-400',
    executed: 'bg-blue-500/20 text-blue-400',
    expired: 'bg-gray-500/20 text-gray-400',
    open: 'bg-blue-500/20 text-blue-400',
    closed: 'bg-[var(--text-secondary)]/20 text-[var(--text-secondary)]',
  }
  return <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${styles[status] || 'bg-[var(--bg-card)] text-[var(--text-secondary)]'}`}>{status.toUpperCase()}</span>
}

function SLTPBar({ side, entry, current, sl, tp }: { side: string; entry: number; current: number; sl: number; tp: number }) {
  const range = tp - sl
  if (range === 0) return null
  const entryPct = Math.max(0, Math.min(100, ((entry - sl) / range) * 100))
  const currentPct = Math.max(0, Math.min(100, ((current - sl) / range) * 100))
  return (
    <div className="relative">
      <div className="flex justify-between text-[9px] text-[var(--text-secondary)] mb-1">
        <span className="text-red-400">SL {fmtPrice(sl)}</span>
        <span className="text-green-400">TP {fmtPrice(tp)}</span>
      </div>
      <div className="h-2 bg-[var(--bg-secondary)] rounded-full relative overflow-hidden">
        <div className="absolute inset-0 rounded-full" style={{ background: 'linear-gradient(to right, #ef4444, #eab308, #22c55e)', opacity: 0.3 }} />
        <div className="absolute top-0 h-full w-0.5 bg-white/60" style={{ left: `${entryPct}%` }} title={`Entry: ${fmtPrice(entry)}`} />
        <div className="absolute -top-0.5 w-3 h-3 rounded-full border-2 border-white bg-[var(--accent)]" style={{ left: `${currentPct}%`, transform: 'translateX(-50%)' }} title={`Current: ${fmtPrice(current)}`} />
      </div>
    </div>
  )
}
