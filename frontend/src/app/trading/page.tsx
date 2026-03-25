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
  symbol: string
  side: string
  size: number
  entry_price: number
  current_price: number
  unrealized_pnl: number
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

export default function TradingPage() {
  const { lastEvent } = useWS()
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null)
  const [signals, setSignals] = useState<Signal[]>([])
  const [history, setHistory] = useState<TradeHistory[]>([])
  const [loading, setLoading] = useState(true)
  const [errors, setErrors] = useState<string[]>([])

  const load = useCallback(async () => {
    const errs: string[] = []
    try { setPortfolio(await api.getPortfolio()) } catch { errs.push('portfolio') }
    try { setSignals(await api.getTradeSignals(20)) } catch { errs.push('signals') }
    try { setHistory(await api.getTradeHistory(20)) } catch { errs.push('history') }
    setErrors(errs)
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (lastEvent?.event === 'trade_signal' || lastEvent?.event === 'trade_executed') {
      load()
    }
  }, [lastEvent, load])

  const decideSignal = async (signalId: string, approved: boolean, reason?: string) => {
    await api.decideSignal(signalId, approved, reason)
    load()
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[var(--accent)]" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Trading</h1>
          <p className="text-sm text-[var(--text-secondary)]">
            {portfolio?.platforms_connected ?? 0} platform(s) connected
          </p>
        </div>
        <button
          onClick={load}
          className="px-3 py-1.5 text-sm bg-[var(--bg-card)] border border-[var(--border)] rounded-md hover:bg-[var(--accent)]/10"
        >
          Refresh
        </button>
      </div>

      {errors.length > 0 && (
        <ErrorBanner
          message={`Failed to load: ${errors.join(', ')}`}
          onRetry={load}
        />
      )}

      {portfolio?.error ? (
        <div className="p-4 bg-yellow-500/10 border border-yellow-500/30 rounded-lg text-sm">
          {portfolio.error}
        </div>
      ) : (
        <>
          {/* Account Cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              label="Total Balance"
              value={`$${(portfolio?.total_balance ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
            />
            <StatCard
              label="Total Equity"
              value={`$${(portfolio?.total_equity ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
            />
            <StatCard
              label="Unrealized P&L"
              value={`$${(portfolio?.unrealized_pnl ?? 0).toFixed(2)}`}
              color={pnlColor(portfolio?.unrealized_pnl ?? 0)}
            />
            <StatCard
              label="Realized P&L"
              value={`$${(portfolio?.realized_pnl ?? 0).toFixed(2)}`}
              color={pnlColor(portfolio?.realized_pnl ?? 0)}
            />
          </div>

          {/* Accounts */}
          {portfolio?.accounts && portfolio.accounts.length > 0 && (
            <Section title="Connected Accounts">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {portfolio.accounts.map((a, i) => (
                  <div key={i} className="p-3 bg-[var(--bg-card)] rounded-lg border border-[var(--border)]">
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-medium text-sm">{a.platform}</span>
                      {a.is_demo && <span className="text-[10px] px-1.5 py-0.5 bg-yellow-500/20 text-yellow-400 rounded">DEMO</span>}
                    </div>
                    {a.error ? (
                      <p className="text-xs text-red-400">{a.error}</p>
                    ) : (
                      <p className="text-xs text-[var(--text-secondary)]">
                        Balance: ${a.balance?.toLocaleString()} {a.currency} &middot; Equity: ${a.equity?.toLocaleString()}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </Section>
          )}

          {/* Open Positions */}
          <Section title={`Open Positions (${portfolio?.open_positions_count ?? 0})`}>
            {portfolio?.positions && portfolio.positions.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-[var(--text-secondary)] border-b border-[var(--border)]">
                      <th className="pb-2 font-medium">Symbol</th>
                      <th className="pb-2 font-medium">Side</th>
                      <th className="pb-2 font-medium">Size</th>
                      <th className="pb-2 font-medium">Entry</th>
                      <th className="pb-2 font-medium">Current</th>
                      <th className="pb-2 font-medium">P&L</th>
                      <th className="pb-2 font-medium">Platform</th>
                    </tr>
                  </thead>
                  <tbody>
                    {portfolio.positions.map((p, i) => (
                      <tr key={i} className="border-b border-[var(--border)]/50">
                        <td className="py-2 font-mono">{p.symbol}</td>
                        <td className={`py-2 ${p.side === 'buy' ? 'text-green-400' : 'text-red-400'}`}>
                          {p.side.toUpperCase()}
                        </td>
                        <td className="py-2">{p.size}</td>
                        <td className="py-2 font-mono">{p.entry_price}</td>
                        <td className="py-2 font-mono">{p.current_price}</td>
                        <td className={`py-2 font-mono ${pnlColor(p.unrealized_pnl)}`}>
                          {p.unrealized_pnl >= 0 ? '+' : ''}{p.unrealized_pnl.toFixed(2)}
                        </td>
                        <td className="py-2 text-[var(--text-secondary)]">{p.platform}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-[var(--text-secondary)]">No open positions</p>
            )}
          </Section>
        </>
      )}

      {/* Trade Signals */}
      <Section title={`Trade Signals (${signals.length})`}>
        {signals.length > 0 ? (
          <div className="space-y-3">
            {signals.map(s => (
              <div key={s.id} className="p-3 bg-[var(--bg-card)] rounded-lg border border-[var(--border)]">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-medium">{s.symbol}</span>
                    <span className={`text-xs px-1.5 py-0.5 rounded ${s.direction === 'buy' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
                      {s.direction.toUpperCase()}
                    </span>
                    <span className="text-xs text-[var(--text-secondary)]">
                      {(s.confidence * 100).toFixed(0)}% confidence
                    </span>
                  </div>
                  <StatusBadge status={s.status} />
                </div>
                <p className="text-xs text-[var(--text-secondary)] mb-2 line-clamp-2">{s.reasoning}</p>
                <div className="flex items-center justify-between text-xs">
                  <div className="flex gap-3 text-[var(--text-secondary)]">
                    {s.entry_price && <span>Entry: {s.entry_price}</span>}
                    {s.stop_loss && <span>SL: {s.stop_loss}</span>}
                    {s.take_profit && <span>TP: {s.take_profit}</span>}
                  </div>
                  {s.status === 'pending' && (
                    <div className="flex gap-1.5">
                      <button
                        onClick={() => decideSignal(s.id, true)}
                        className="px-2 py-1 bg-green-600 text-white rounded text-xs hover:bg-green-500"
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => decideSignal(s.id, false, 'Manual rejection')}
                        className="px-2 py-1 bg-red-600 text-white rounded text-xs hover:bg-red-500"
                      >
                        Reject
                      </button>
                    </div>
                  )}
                </div>
                {s.created_at && (
                  <p className="text-[10px] text-[var(--text-secondary)] mt-1">
                    {new Date(s.created_at).toLocaleString()}
                  </p>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-[var(--text-secondary)]">No trade signals yet</p>
        )}
      </Section>

      {/* Trade History */}
      <Section title={`Trade History (${history.length})`}>
        {history.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[var(--text-secondary)] border-b border-[var(--border)]">
                  <th className="pb-2 font-medium">Time</th>
                  <th className="pb-2 font-medium">Symbol</th>
                  <th className="pb-2 font-medium">Side</th>
                  <th className="pb-2 font-medium">Size</th>
                  <th className="pb-2 font-medium">Entry</th>
                  <th className="pb-2 font-medium">Exit</th>
                  <th className="pb-2 font-medium">P&L</th>
                  <th className="pb-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {history.map(t => (
                  <tr key={t.id} className="border-b border-[var(--border)]/50">
                    <td className="py-2 text-xs text-[var(--text-secondary)]">
                      {t.opened_at ? new Date(t.opened_at).toLocaleString() : '-'}
                    </td>
                    <td className="py-2 font-mono">{t.symbol}</td>
                    <td className={`py-2 ${t.side === 'buy' ? 'text-green-400' : 'text-red-400'}`}>
                      {t.side.toUpperCase()}
                    </td>
                    <td className="py-2">{t.size}</td>
                    <td className="py-2 font-mono">{t.entry_price}</td>
                    <td className="py-2 font-mono">{t.exit_price ?? '-'}</td>
                    <td className={`py-2 font-mono ${pnlColor(t.pnl ?? 0)}`}>
                      {t.pnl != null ? `${t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}` : '-'}
                    </td>
                    <td className="py-2">
                      <StatusBadge status={t.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-[var(--text-secondary)]">No trades yet</p>
        )}
      </Section>
    </div>
  )
}

function StatCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="p-4 bg-[var(--bg-card)] rounded-lg border border-[var(--border)]">
      <p className="text-xs text-[var(--text-secondary)] mb-1">{label}</p>
      <p className={`text-lg font-bold font-mono ${color || ''}`}>{value}</p>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-[var(--bg-secondary)] rounded-lg border border-[var(--border)] p-4">
      <h2 className="text-sm font-semibold mb-3">{title}</h2>
      {children}
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    pending: 'bg-yellow-500/20 text-yellow-400',
    approved: 'bg-green-500/20 text-green-400',
    rejected: 'bg-red-500/20 text-red-400',
    executed: 'bg-blue-500/20 text-blue-400',
    open: 'bg-green-500/20 text-green-400',
    closed: 'bg-[var(--text-secondary)]/20 text-[var(--text-secondary)]',
    filled: 'bg-green-500/20 text-green-400',
  }
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded ${styles[status] || 'bg-[var(--bg-card)] text-[var(--text-secondary)]'}`}>
      {status.toUpperCase()}
    </span>
  )
}

function pnlColor(value: number): string {
  if (value > 0) return 'text-green-400'
  if (value < 0) return 'text-red-400'
  return ''
}
