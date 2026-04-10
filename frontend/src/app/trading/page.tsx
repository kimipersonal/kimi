'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
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
  // Protection metadata
  initial_take_profit?: number | null
  initial_stop_loss?: number | null
  tp_ratcheted?: boolean
  tp_ratchet_tier?: number | null
  breakeven_active?: boolean
  trailing_active?: boolean
  sl_tightened_by_agent?: boolean
  original_stop_loss?: number | null
  highest_price?: number | null
  lowest_price?: number | null
  signal_id?: string | null
  agent_id?: string | null
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
  // Protection metadata
  initial_take_profit?: number | null
  initial_stop_loss?: number | null
  tp_ratcheted?: boolean
  tp_ratchet_tier?: number | null
  breakeven_active?: boolean
  trailing_active?: boolean
  sl_tightened_by_agent?: boolean
  close_reason?: string | null
  signal_id?: string | null
  agent_id?: string | null
}

interface TradeChainStep {
  step: string
  label: string
  agent_name: string
  agent_id?: string
  timestamp?: string
  details?: Record<string, any>
}

interface TradeChain {
  trade_id: string
  symbol: string
  side: string
  status: string
  opened_at?: string
  closed_at?: string
  steps: TradeChainStep[]
  activity_logs: Array<{
    agent_id: string
    action: string
    details?: Record<string, any>
    level: string
    timestamp: string
  }>
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

interface MarketAnalysis {
  symbol: string
  interval: string
  candles_analyzed: number
  current_price: number
  verdict: string
  score: number
  trend_strength: string
  indicators: {
    ema_9?: number | null
    ema_21?: number | null
    ema_55?: number | null
    sma_200?: number | null
    rsi_14?: number | null
    stoch_rsi_k?: number | null
    stoch_rsi_d?: number | null
    macd?: number | null
    macd_signal?: number | null
    adx?: number | null
    atr_14?: number | null
  }
  ichimoku: string
  volume: {
    current: number
    avg_20?: number | null
    ratio?: number | null
    signal: string
  }
  price_action: {
    patterns: string[]
    candle_body_ratio: number
  }
  support_resistance: {
    resistance: number[]
    support: number[]
  }
  scoring: {
    bull_points: number
    bear_points: number
    total_weight: number
  }
}

type Tab = 'positions' | 'signals' | 'history' | 'settings'

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

  // Live price state — updated every 2 seconds independently
  const [livePrices, setLivePrices] = useState<Record<string, number>>({})
  const livePriceRef = useRef<Record<string, number>>({})

  // Market analysis state — per-symbol, loaded on demand
  const [marketAnalyses, setMarketAnalyses] = useState<Record<string, MarketAnalysis>>({})
  const [expandedProfiles, setExpandedProfiles] = useState<Set<string>>(new Set())
  const [loadingProfiles, setLoadingProfiles] = useState<Set<string>>(new Set())

  // Agent chain state — per-trade, loaded on demand
  const [tradeChains, setTradeChains] = useState<Record<string, TradeChain>>({})
  const [expandedChains, setExpandedChains] = useState<Set<string>>(new Set())
  const [loadingChains, setLoadingChains] = useState<Set<string>>(new Set())

  // Settings state
  const [tradingSettings, setTradingSettings] = useState<any>(null)
  const [settingsLoading, setSettingsLoading] = useState(false)
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [settingsDirty, setSettingsDirty] = useState(false)
  const [settingsMsg, setSettingsMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  // Full data load (portfolio, signals, history) — every 30s + on events
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

  // ── Fast price polling (2s) — only when on positions tab ──────
  useEffect(() => {
    if (activeTab !== 'positions') return
    let active = true
    const poll = async () => {
      try {
        const prices = await api.getLivePrices()
        if (active) {
          livePriceRef.current = prices
          setLivePrices(prices)
        }
      } catch { /* silent — full load handles errors */ }
    }
    poll() // immediate first fetch
    const iv = setInterval(poll, 2000) // 2-second updates
    return () => { active = false; clearInterval(iv) }
  }, [activeTab])

  // ── Market Analysis toggle ─────────────────────────────────────
  const toggleProfile = async (symbol: string) => {
    const key = symbol
    if (expandedProfiles.has(key)) {
      setExpandedProfiles(prev => { const n = new Set(prev); n.delete(key); return n })
      return
    }
    // Load if not cached
    if (!marketAnalyses[key]) {
      setLoadingProfiles(prev => new Set(prev).add(key))
      try {
        const analysis = await api.getAdvancedAnalysis(symbol, '4h')
        setMarketAnalyses(prev => ({ ...prev, [key]: analysis }))
      } catch {
        // Store minimal error object so the panel can show an error state
        setMarketAnalyses(prev => ({ ...prev, [key]: { symbol, interval: '4h', candles_analyzed: 0, current_price: 0, verdict: 'ERROR', score: 0, trend_strength: 'UNKNOWN', indicators: {}, ichimoku: 'N/A', volume: { current: 0, signal: 'N/A' }, price_action: { patterns: [], candle_body_ratio: 0 }, support_resistance: { resistance: [], support: [] }, scoring: { bull_points: 0, bear_points: 0, total_weight: 0 } } as MarketAnalysis }))
      } finally {
        setLoadingProfiles(prev => { const n = new Set(prev); n.delete(key); return n })
      }
    }
    setExpandedProfiles(prev => new Set(prev).add(key))
  }

  const collapseAllProfiles = () => setExpandedProfiles(new Set())

  // ── Agent Chain toggle ─────────────────────────────────────────
  const toggleChain = async (tradeId: string) => {
    if (expandedChains.has(tradeId)) {
      setExpandedChains(prev => { const n = new Set(prev); n.delete(tradeId); return n })
      return
    }
    if (!tradeChains[tradeId]) {
      setLoadingChains(prev => new Set(prev).add(tradeId))
      try {
        const chain = await api.getTradeChain(tradeId)
        setTradeChains(prev => ({ ...prev, [tradeId]: chain }))
      } catch {
        setTradeChains(prev => ({ ...prev, [tradeId]: { trade_id: tradeId, symbol: '', side: '', status: '', steps: [], activity_logs: [] } }))
      } finally {
        setLoadingChains(prev => { const n = new Set(prev); n.delete(tradeId); return n })
      }
    }
    setExpandedChains(prev => new Set(prev).add(tradeId))
  }

  // ── Settings load/save ─────────────────────────────────────────
  const loadSettings = useCallback(async () => {
    setSettingsLoading(true)
    try {
      const data = await api.getTradingSettings()
      setTradingSettings(data)
      setSettingsDirty(false)
    } catch { /* silent */ }
    setSettingsLoading(false)
  }, [])

  useEffect(() => {
    if (activeTab === 'settings' && !tradingSettings) loadSettings()
  }, [activeTab, tradingSettings, loadSettings])

  const updateSettingsField = (section: 'auto_trade' | 'risk_caps', key: string, value: any) => {
    setTradingSettings((prev: any) => ({
      ...prev,
      [section]: { ...prev[section], [key]: value },
    }))
    setSettingsDirty(true)
    setSettingsMsg(null)
  }

  const saveAutoTrade = async () => {
    if (!tradingSettings) return
    setSettingsSaving(true)
    setSettingsMsg(null)
    try {
      await api.updateAutoTradeConfig(tradingSettings.auto_trade)
      setSettingsMsg({ type: 'ok', text: 'Auto-trade config saved' })
      setSettingsDirty(false)
    } catch (e: any) {
      setSettingsMsg({ type: 'err', text: e.message || 'Failed to save' })
    }
    setSettingsSaving(false)
  }

  const saveRiskCaps = async () => {
    if (!tradingSettings) return
    setSettingsSaving(true)
    setSettingsMsg(null)
    try {
      await api.updateRiskCaps(tradingSettings.risk_caps)
      setSettingsMsg({ type: 'ok', text: 'Risk caps saved' })
      setSettingsDirty(false)
    } catch (e: any) {
      setSettingsMsg({ type: 'err', text: e.message || 'Failed to save' })
    }
    setSettingsSaving(false)
  }

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
        <div className="flex items-center gap-3">
          {activeTab === 'positions' && (
            <span className="flex items-center gap-1.5 text-[10px] text-green-400">
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
              Live • 2s
            </span>
          )}
          <button onClick={load}
            className="px-3 py-1.5 text-sm bg-[var(--bg-card)] border border-[var(--border)] rounded-md hover:bg-[var(--accent)]/10">
            Refresh
          </button>
        </div>
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
        <TabButton active={activeTab === 'settings'} onClick={() => setActiveTab('settings')} label="⚙️ Settings" />
      </div>

      {activeTab === 'positions' && (
        <div>
          {positions.length > 0 && expandedProfiles.size > 0 && (
            <div className="flex justify-end mb-2">
              <button onClick={collapseAllProfiles}
                className="px-2 py-1 text-[10px] text-[var(--text-secondary)] bg-[var(--bg-card)] border border-[var(--border)] rounded hover:bg-[var(--accent)]/10 transition-colors">
                Collapse All Indicators
              </button>
            </div>
          )}
          {positions.length > 0 ? (
            <div className="space-y-3">
              {positions.map((p, i) => {
                // Use live price if available, fall back to portfolio price
                const currentPrice = livePrices[p.symbol] || p.current_price
                const value = (p.entry_price || currentPrice || 0) * p.size
                const unrealizedPnl = p.entry_price
                  ? (p.side === 'buy'
                    ? (currentPrice - p.entry_price) * p.size
                    : (p.entry_price - currentPrice) * p.size)
                  : p.unrealized_pnl
                const pnlPct = p.entry_price
                  ? ((currentPrice - p.entry_price) / p.entry_price) * 100 * (p.side === 'sell' ? -1 : 1)
                  : 0
                const isExpanded = expandedProfiles.has(p.symbol)
                const isLoadingProfile = loadingProfiles.has(p.symbol)
                const analysis = marketAnalyses[p.symbol]

                // TP progress calculation
                let tpProgressPct = 0
                if (p.entry_price && p.take_profit) {
                  const tpDist = Math.abs(p.take_profit - p.entry_price)
                  const currMove = p.side === 'buy'
                    ? Math.max(0, currentPrice - p.entry_price)
                    : Math.max(0, p.entry_price - currentPrice)
                  tpProgressPct = tpDist > 0 ? (currMove / tpDist) * 100 : 0
                }

                return (
                  <div key={i} className="p-4 bg-[var(--bg-card)] rounded-lg border border-[var(--border)]">
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-3">
                        <span className="text-base font-bold font-mono">{p.symbol}</span>
                        <span className={`text-xs font-semibold px-2 py-0.5 rounded ${p.side === 'buy' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
                          {p.side.toUpperCase()}
                        </span>
                        {p.take_profit && (
                          <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                            tpProgressPct >= 60 ? 'bg-green-500/20 text-green-400' :
                            tpProgressPct >= 30 ? 'bg-yellow-500/20 text-yellow-400' :
                            'bg-[var(--bg-secondary)] text-[var(--text-secondary)]'
                          }`}>
                            TP {tpProgressPct.toFixed(0)}%
                          </span>
                        )}
                        {p.tp_ratcheted && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-400 border border-purple-500/30 font-medium">
                            🔒 Ratcheted
                          </span>
                        )}
                        {p.breakeven_active && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400 border border-blue-500/30 font-medium">
                            🛡️ Breakeven
                          </span>
                        )}
                        {p.trailing_active && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 font-medium">
                            📈 Trailing
                          </span>
                        )}
                        {p.sl_tightened_by_agent && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-orange-500/20 text-orange-400 border border-orange-500/30 font-medium" title={p.original_stop_loss ? `Original SL: ${p.original_stop_loss}` : undefined}>
                            🔧 SL Tightened
                          </span>
                        )}
                      </div>
                      <div className="text-right">
                        <p className={`text-base font-bold font-mono ${pnlColor(unrealizedPnl)}`}>
                          {fmtMoney(unrealizedPnl, true)}
                        </p>
                        <p className={`text-xs ${pnlColor(pnlPct)}`}>{pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%</p>
                      </div>
                    </div>

                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                      <PriceCell label="Entry" value={p.entry_price} />
                      <PriceCell label="Current" value={currentPrice} highlight />
                      <PriceCell label="Stop Loss" value={p.stop_loss} color="text-red-400" />
                      {p.tp_ratcheted && p.initial_take_profit ? (
                        <div className="bg-[var(--bg-secondary)] rounded px-2 py-1.5">
                          <p className="text-[10px] text-[var(--text-secondary)]">Take Profit <span className="text-purple-400">🔒</span></p>
                          <p className="font-mono text-sm text-green-400 font-bold">{fmtPrice(p.take_profit)}</p>
                          <p className="text-[9px] text-[var(--text-secondary)] line-through">{fmtPrice(p.initial_take_profit)}</p>
                        </div>
                      ) : (
                        <PriceCell label="Take Profit" value={p.take_profit} color="text-green-400" />
                      )}
                    </div>

                    {/* Ratchet detail strip */}
                    {p.tp_ratcheted && p.initial_take_profit && p.take_profit && (
                      <div className="mt-2 px-2 py-1.5 bg-purple-500/5 border border-purple-500/20 rounded flex items-center justify-between text-[10px]">
                        <div className="flex items-center gap-3">
                          <span className="text-purple-400 font-medium">TP Protection Active</span>
                          {p.tp_ratchet_tier != null && (
                            <span className="text-[var(--text-secondary)]">Tier: <span className="text-purple-300 font-mono">{(p.tp_ratchet_tier * 100).toFixed(0)}%</span></span>
                          )}
                          <span className="text-[var(--text-secondary)]">
                            Original: <span className="font-mono">{fmtPrice(p.initial_take_profit)}</span>
                            <span className="mx-1">→</span>
                            Protected: <span className="text-green-400 font-mono font-bold">{fmtPrice(p.take_profit)}</span>
                          </span>
                        </div>
                        {(p.highest_price || p.lowest_price) && (
                          <span className="text-[var(--text-secondary)]">
                            {p.side === 'buy' && p.highest_price ? `Peak: ${fmtPrice(p.highest_price)}` : ''}
                            {p.side === 'sell' && p.lowest_price ? `Low: ${fmtPrice(p.lowest_price)}` : ''}
                          </span>
                        )}
                      </div>
                    )}

                    <div className="flex items-center justify-between mt-3 text-xs text-[var(--text-secondary)]">
                      <div className="flex gap-4">
                        <span>Size: <span className="text-[var(--text-primary)] font-mono">{p.size}</span></span>
                        <span>Value: <span className="text-[var(--text-primary)] font-mono">{fmtMoney(value)}</span></span>
                      </div>
                      <div className="flex items-center gap-3">
                        <span>{p.platform}</span>
                        <button
                          onClick={() => toggleProfile(p.symbol)}
                          className={`px-2.5 py-1 rounded text-[10px] font-medium transition-colors ${
                            isExpanded
                              ? 'bg-[var(--accent)]/20 text-[var(--accent)] border border-[var(--accent)]/30'
                              : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:bg-[var(--accent)]/10 border border-transparent'
                          }`}
                        >
                          {isLoadingProfile ? '...' : isExpanded ? '▲ Hide History' : '▼ History'}
                        </button>
                        {(p.trade_id || p.id) && (
                          <button
                            onClick={() => toggleChain(p.trade_id || p.id)}
                            className={`px-2.5 py-1 rounded text-[10px] font-medium transition-colors ${
                              expandedChains.has(p.trade_id || p.id)
                                ? 'bg-orange-500/20 text-orange-400 border border-orange-500/30'
                                : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:bg-orange-500/10 border border-transparent'
                            }`}
                          >
                            {loadingChains.has(p.trade_id || p.id) ? '...' : expandedChains.has(p.trade_id || p.id) ? '▲ Hide Chain' : '⚡ Agent Chain'}
                          </button>
                        )}
                        <button
                          onClick={() => closeTrade(p.trade_id || p.id)}
                          disabled={closingId === (p.trade_id || p.id)}
                          className="px-3 py-1 bg-red-600/80 text-white rounded text-xs font-medium hover:bg-red-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          {closingId === (p.trade_id || p.id) ? 'Closing...' : 'Close'}
                        </button>
                      </div>
                    </div>

                    {p.stop_loss && p.take_profit && currentPrice > 0 && (
                      <div className="mt-3">
                        <SLTPBar side={p.side} entry={p.entry_price} current={currentPrice} sl={p.stop_loss} tp={p.take_profit} />
                      </div>
                    )}

                    {/* Collapsible Agent Chain */}
                    {expandedChains.has(p.trade_id || p.id) && tradeChains[p.trade_id || p.id] && (
                      <AgentChainPanel chain={tradeChains[p.trade_id || p.id]} />
                    )}

                    {/* Collapsible Real Market Analysis */}
                    {isExpanded && analysis && (
                      <div className="mt-3 p-3 bg-[var(--bg-secondary)] rounded-lg border border-[var(--border)] animate-in slide-in-from-top-2 duration-200">
                        {analysis.candles_analyzed === 0 ? (
                          <p className="text-xs text-[var(--text-secondary)]">Failed to load market data for {p.symbol}.</p>
                        ) : (
                          <>
                            {/* Header: Verdict + Score */}
                            <div className="flex items-center justify-between mb-3">
                              <div className="flex items-center gap-2">
                                <span className="text-xs font-semibold text-[var(--text-primary)]">
                                  📊 Market Analysis — {p.symbol}
                                </span>
                                <VerdictBadge verdict={analysis.verdict} />
                              </div>
                              <div className="flex items-center gap-2 text-[10px] text-[var(--text-secondary)]">
                                <span>{analysis.interval} · {analysis.candles_analyzed} candles</span>
                                <span className={`font-mono font-bold ${analysis.score > 0 ? 'text-green-400' : analysis.score < 0 ? 'text-red-400' : 'text-[var(--text-secondary)]'}`}>
                                  {analysis.score > 0 ? '+' : ''}{analysis.score}
                                </span>
                              </div>
                            </div>

                            {/* Score bar */}
                            <div className="mb-3">
                              <div className="flex justify-between text-[9px] text-[var(--text-secondary)] mb-1">
                                <span>STRONG SELL</span>
                                <span>NEUTRAL</span>
                                <span>STRONG BUY</span>
                              </div>
                              <div className="h-2.5 bg-[var(--bg-card)] rounded-full relative overflow-hidden">
                                <div className="absolute inset-0 rounded-full" style={{ background: 'linear-gradient(to right, #ef4444, #eab308, #22c55e)', opacity: 0.2 }} />
                                {/* Center line */}
                                <div className="absolute top-0 h-full w-px bg-[var(--text-secondary)]/30" style={{ left: '50%' }} />
                                {/* Score marker */}
                                <div className="absolute -top-0.5 w-3 h-3.5 rounded-full border-2 border-white"
                                  style={{
                                    left: `${Math.max(2, Math.min(98, (analysis.score + 100) / 2))}%`,
                                    transform: 'translateX(-50%)',
                                    backgroundColor: analysis.score > 20 ? '#22c55e' : analysis.score > 0 ? '#86efac' : analysis.score < -20 ? '#ef4444' : analysis.score < 0 ? '#fca5a5' : '#eab308'
                                  }} />
                              </div>
                            </div>

                            {/* Key Indicators Grid */}
                            <div className="grid grid-cols-3 md:grid-cols-6 gap-2 mb-3">
                              <IndicatorCell label="RSI (14)" value={analysis.indicators.rsi_14}
                                color={analysis.indicators.rsi_14 != null ? (analysis.indicators.rsi_14 > 70 ? 'text-red-400' : analysis.indicators.rsi_14 < 30 ? 'text-green-400' : 'text-[var(--text-primary)]') : undefined}
                                sub={analysis.indicators.rsi_14 != null ? (analysis.indicators.rsi_14 > 70 ? 'Overbought' : analysis.indicators.rsi_14 < 30 ? 'Oversold' : 'Neutral') : undefined} />
                              <IndicatorCell label="MACD" value={analysis.indicators.macd}
                                color={analysis.indicators.macd != null && analysis.indicators.macd_signal != null ? (analysis.indicators.macd > analysis.indicators.macd_signal ? 'text-green-400' : 'text-red-400') : undefined}
                                sub={analysis.indicators.macd != null && analysis.indicators.macd_signal != null ? (analysis.indicators.macd > analysis.indicators.macd_signal ? 'Bullish' : 'Bearish') : undefined} />
                              <IndicatorCell label="ADX" value={analysis.indicators.adx}
                                sub={analysis.trend_strength} />
                              <IndicatorCell label="Stoch RSI K" value={analysis.indicators.stoch_rsi_k}
                                color={analysis.indicators.stoch_rsi_k != null ? (analysis.indicators.stoch_rsi_k > 80 ? 'text-red-400' : analysis.indicators.stoch_rsi_k < 20 ? 'text-green-400' : 'text-[var(--text-primary)]') : undefined} />
                              <IndicatorCell label="ATR (14)" value={analysis.indicators.atr_14} />
                              <IndicatorCell label="Vol Ratio" value={analysis.volume.ratio}
                                color={analysis.volume.signal === 'HIGH' ? 'text-yellow-400' : analysis.volume.signal === 'LOW' ? 'text-red-400' : 'text-[var(--text-primary)]'}
                                sub={analysis.volume.signal} />
                            </div>

                            {/* EMAs */}
                            <div className="grid grid-cols-4 gap-2 mb-3">
                              <ProfileStat label="EMA 9" value={analysis.indicators.ema_9 != null ? fmtPrice(analysis.indicators.ema_9) : '—'}
                                color={analysis.indicators.ema_9 != null && currentPrice > analysis.indicators.ema_9 ? 'text-green-400' : 'text-red-400'} />
                              <ProfileStat label="EMA 21" value={analysis.indicators.ema_21 != null ? fmtPrice(analysis.indicators.ema_21) : '—'}
                                color={analysis.indicators.ema_21 != null && currentPrice > analysis.indicators.ema_21 ? 'text-green-400' : 'text-red-400'} />
                              <ProfileStat label="EMA 55" value={analysis.indicators.ema_55 != null ? fmtPrice(analysis.indicators.ema_55) : '—'}
                                color={analysis.indicators.ema_55 != null && currentPrice > analysis.indicators.ema_55 ? 'text-green-400' : 'text-red-400'} />
                              <ProfileStat label="SMA 200" value={analysis.indicators.sma_200 != null ? fmtPrice(analysis.indicators.sma_200) : '—'}
                                color={analysis.indicators.sma_200 != null && currentPrice > analysis.indicators.sma_200 ? 'text-green-400' : 'text-red-400'} />
                            </div>

                            {/* Ichimoku + Patterns row */}
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mb-3">
                              {/* Ichimoku Cloud */}
                              <div className="p-2 bg-[var(--bg-card)] rounded">
                                <p className="text-[9px] text-[var(--text-secondary)] mb-1">Ichimoku Cloud</p>
                                <p className={`text-xs font-semibold ${
                                  analysis.ichimoku.includes('BULLISH') ? 'text-green-400'
                                  : analysis.ichimoku.includes('BEARISH') ? 'text-red-400'
                                  : 'text-yellow-400'
                                }`}>
                                  {analysis.ichimoku}
                                </p>
                              </div>
                              {/* Candlestick Patterns */}
                              <div className="p-2 bg-[var(--bg-card)] rounded">
                                <p className="text-[9px] text-[var(--text-secondary)] mb-1">Candlestick Patterns</p>
                                <div className="flex flex-wrap gap-1">
                                  {analysis.price_action.patterns.map((pat, pi) => (
                                    <span key={pi} className={`text-[10px] px-1.5 py-0.5 rounded ${
                                      pat.includes('bullish') ? 'bg-green-500/15 text-green-400'
                                      : pat.includes('bearish') ? 'bg-red-500/15 text-red-400'
                                      : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)]'
                                    }`}>
                                      {pat}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            </div>

                            {/* Support & Resistance */}
                            <div className="grid grid-cols-2 gap-2">
                              <div className="p-2 bg-[var(--bg-card)] rounded">
                                <p className="text-[9px] text-[var(--text-secondary)] mb-1">Support Levels</p>
                                <div className="flex flex-wrap gap-1">
                                  {analysis.support_resistance.support.map((lvl, si) => (
                                    <span key={si} className="text-[10px] font-mono px-1.5 py-0.5 bg-green-500/10 text-green-400 rounded">
                                      {fmtPrice(lvl)}
                                    </span>
                                  ))}
                                </div>
                              </div>
                              <div className="p-2 bg-[var(--bg-card)] rounded">
                                <p className="text-[9px] text-[var(--text-secondary)] mb-1">Resistance Levels</p>
                                <div className="flex flex-wrap gap-1">
                                  {analysis.support_resistance.resistance.map((lvl, ri) => (
                                    <span key={ri} className="text-[10px] font-mono px-1.5 py-0.5 bg-red-500/10 text-red-400 rounded">
                                      {fmtPrice(lvl)}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            </div>

                            {/* Scoring breakdown */}
                            <div className="mt-2 flex items-center gap-3 text-[10px] text-[var(--text-secondary)]">
                              <span>Bull: <span className="text-green-400 font-mono">{analysis.scoring.bull_points}</span></span>
                              <span>Bear: <span className="text-red-400 font-mono">{analysis.scoring.bear_points}</span></span>
                              <span>Trend: <span className="text-[var(--text-primary)]">{analysis.trend_strength}</span></span>
                            </div>
                          </>
                        )}
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
                        {t.tp_ratcheted && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-400 border border-purple-500/30 font-medium">🔒 Ratcheted</span>
                        )}
                        {t.breakeven_active && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400 border border-blue-500/30 font-medium">🛡️ BE</span>
                        )}
                        {t.trailing_active && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 font-medium">📈 Trail</span>
                        )}
                        {t.sl_tightened_by_agent && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-orange-500/20 text-orange-400 border border-orange-500/30 font-medium">🔧 SL Tight</span>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        {t.close_reason && (
                          <CloseReasonBadge reason={t.close_reason} />
                        )}
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
                        {t.tp_ratcheted && t.initial_take_profit ? (
                          <span>TP: <span className="line-through opacity-60">{fmtPrice(t.initial_take_profit)}</span> → <span className="text-green-400">{fmtPrice(t.take_profit)}</span></span>
                        ) : (
                          t.take_profit && <span>TP: {fmtPrice(t.take_profit)}</span>
                        )}
                      </div>
                      <span>{t.opened_at ? timeAgo(t.opened_at) : ''}{t.closed_at ? ` → closed ${timeAgo(t.closed_at)}` : ''}</span>
                    </div>
                    {/* Agent chain toggle */}
                    <div className="flex justify-end mt-1.5">
                      <button
                        onClick={() => toggleChain(t.id)}
                        className={`px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${
                          expandedChains.has(t.id)
                            ? 'bg-orange-500/20 text-orange-400 border border-orange-500/30'
                            : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:bg-orange-500/10 border border-transparent'
                        }`}
                      >
                        {loadingChains.has(t.id) ? '...' : expandedChains.has(t.id) ? '▲ Hide Chain' : '⚡ Agent Chain'}
                      </button>
                    </div>
                    {expandedChains.has(t.id) && tradeChains[t.id] && (
                      <AgentChainPanel chain={tradeChains[t.id]} />
                    )}
                  </div>
                )
              })}
            </div>
          ) : (
            <p className="text-sm text-[var(--text-secondary)] py-4">No trades yet</p>
          )}
        </div>
      )}

      {activeTab === 'settings' && (
        <div className="space-y-6">
          {settingsLoading ? (
            <div className="flex items-center justify-center py-8">
              <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-[var(--accent)]" />
            </div>
          ) : tradingSettings ? (
            <>
              {settingsMsg && (
                <div className={`px-3 py-2 rounded text-sm ${
                  settingsMsg.type === 'ok' ? 'bg-green-500/10 border border-green-500/30 text-green-400' : 'bg-red-500/10 border border-red-500/30 text-red-400'
                }`}>{settingsMsg.text}</div>
              )}

              {/* Account Info (read-only) */}
              {tradingSettings.accounts?.length > 0 && (
                <div className="p-4 bg-[var(--bg-card)] rounded-lg border border-[var(--border)]">
                  <h3 className="text-sm font-semibold mb-3">Connected Accounts</h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {tradingSettings.accounts.map((a: any, i: number) => (
                      <div key={i} className="flex items-center justify-between px-3 py-2 bg-[var(--bg-primary)] rounded border border-[var(--border)]">
                        <div className="flex items-center gap-2">
                          <span className="w-2 h-2 rounded-full bg-green-400" />
                          <span className="text-sm font-medium">{a.platform}</span>
                          {a.is_demo && <span className="px-1.5 py-0.5 bg-yellow-500/20 text-yellow-400 rounded text-[9px] font-bold">DEMO</span>}
                        </div>
                        <div className="text-right">
                          <p className="text-sm font-mono">{fmtMoney(a.balance ?? 0)}</p>
                          <p className="text-[10px] text-[var(--text-secondary)]">{a.currency ?? 'USDT'}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                  <p className="text-[10px] text-[var(--text-secondary)] mt-2">Balance is read-only — it reflects your Binance Testnet account balance.</p>
                </div>
              )}

              {/* Auto-Trade Configuration */}
              <div className="p-4 bg-[var(--bg-card)] rounded-lg border border-[var(--border)]">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-sm font-semibold">Auto-Trade Configuration</h3>
                  <button onClick={saveAutoTrade} disabled={settingsSaving}
                    className="px-3 py-1.5 text-xs bg-[var(--accent)] text-white rounded hover:opacity-90 disabled:opacity-50">
                    {settingsSaving ? 'Saving...' : 'Save Auto-Trade'}
                  </button>
                </div>

                {/* Mode selector */}
                <div className="mb-4">
                  <label className="block text-xs text-[var(--text-secondary)] mb-1">Trading Mode</label>
                  <div className="grid grid-cols-4 gap-2">
                    {['disabled', 'conservative', 'moderate', 'aggressive'].map(mode => (
                      <button key={mode} onClick={() => updateSettingsField('auto_trade', 'mode', mode)}
                        className={`px-3 py-2 rounded text-xs font-medium border transition-colors ${
                          tradingSettings.auto_trade.mode === mode
                            ? mode === 'disabled' ? 'bg-gray-500/20 border-gray-500 text-gray-300'
                              : mode === 'conservative' ? 'bg-blue-500/20 border-blue-500 text-blue-400'
                              : mode === 'moderate' ? 'bg-yellow-500/20 border-yellow-500 text-yellow-400'
                              : 'bg-red-500/20 border-red-500 text-red-400'
                            : 'border-[var(--border)] text-[var(--text-secondary)] hover:bg-[var(--accent)]/10'
                        }`}>
                        {mode.charAt(0).toUpperCase() + mode.slice(1)}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Parameter grid */}
                <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                  <SettingsInput label="Min Confidence" value={tradingSettings.auto_trade.min_confidence}
                    onChange={(v: number) => updateSettingsField('auto_trade', 'min_confidence', v)} suffix="(0-1)" step={0.05} min={0} max={1} />
                  <SettingsInput label="Risk per Trade %" value={tradingSettings.auto_trade.max_risk_per_trade_pct}
                    onChange={(v: number) => updateSettingsField('auto_trade', 'max_risk_per_trade_pct', v)} suffix="%" step={0.1} min={0.1} max={10} />
                  <SettingsInput label="Max Daily Trades" value={tradingSettings.auto_trade.max_daily_trades}
                    onChange={(v: number) => updateSettingsField('auto_trade', 'max_daily_trades', Math.round(v))} step={1} min={1} max={50} />
                  <SettingsInput label="Max Daily Loss %" value={tradingSettings.auto_trade.max_daily_loss_pct}
                    onChange={(v: number) => updateSettingsField('auto_trade', 'max_daily_loss_pct', v)} suffix="%" step={0.5} min={0.5} max={20} />
                  <SettingsInput label="Min Risk:Reward" value={tradingSettings.auto_trade.min_risk_reward_ratio}
                    onChange={(v: number) => updateSettingsField('auto_trade', 'min_risk_reward_ratio', v)} suffix=":1" step={0.1} min={0.5} max={5} />
                  <SettingsInput label="Max Open Positions" value={tradingSettings.auto_trade.max_open_positions}
                    onChange={(v: number) => updateSettingsField('auto_trade', 'max_open_positions', Math.round(v))} step={1} min={1} max={20} />
                  <SettingsInput label="Max per Symbol" value={tradingSettings.auto_trade.max_position_per_symbol}
                    onChange={(v: number) => updateSettingsField('auto_trade', 'max_position_per_symbol', Math.round(v))} step={1} min={1} max={5} />
                </div>

                {/* Toggles */}
                <div className="flex items-center gap-6 mt-4">
                  <SettingsToggle label="Require Stop Loss" checked={tradingSettings.auto_trade.require_stop_loss}
                    onChange={(v: boolean) => updateSettingsField('auto_trade', 'require_stop_loss', v)} />
                  <SettingsToggle label="Require Take Profit" checked={tradingSettings.auto_trade.require_take_profit}
                    onChange={(v: boolean) => updateSettingsField('auto_trade', 'require_take_profit', v)} />
                </div>

                {/* Daily Stats */}
                {tradingSettings.auto_trade_daily_stats && (
                  <div className="mt-4 pt-3 border-t border-[var(--border)]">
                    <p className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider mb-2">Today&apos;s Auto-Trade Stats</p>
                    <div className="flex gap-6 text-xs">
                      <span>Executed: <span className="font-mono text-green-400">{tradingSettings.auto_trade_daily_stats.trades_executed}</span></span>
                      <span>Rejected: <span className="font-mono text-red-400">{tradingSettings.auto_trade_daily_stats.trades_rejected}</span></span>
                      <span>P&amp;L: <span className={`font-mono font-bold ${pnlColor(tradingSettings.auto_trade_daily_stats.total_pnl)}`}>{fmtMoney(tradingSettings.auto_trade_daily_stats.total_pnl, true)}</span></span>
                    </div>
                  </div>
                )}
              </div>

              {/* Risk Caps */}
              <div className="p-4 bg-[var(--bg-card)] rounded-lg border border-[var(--border)]">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-sm font-semibold">Risk Caps</h3>
                  <button onClick={saveRiskCaps} disabled={settingsSaving}
                    className="px-3 py-1.5 text-xs bg-[var(--accent)] text-white rounded hover:opacity-90 disabled:opacity-50">
                    {settingsSaving ? 'Saving...' : 'Save Risk Caps'}
                  </button>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                  <SettingsInput label="Max Stop Loss %" value={tradingSettings.risk_caps.max_sl_pct}
                    onChange={(v: number) => updateSettingsField('risk_caps', 'max_sl_pct', v)} suffix="%" step={0.5} min={0.5} max={20} />
                  <SettingsInput label="Max Take Profit %" value={tradingSettings.risk_caps.max_tp_pct}
                    onChange={(v: number) => updateSettingsField('risk_caps', 'max_tp_pct', v)} suffix="%" step={0.5} min={1} max={50} />
                  <SettingsInput label="Max Position Size %" value={tradingSettings.risk_caps.max_position_pct}
                    onChange={(v: number) => updateSettingsField('risk_caps', 'max_position_pct', v)} suffix="%" step={0.5} min={1} max={50} />
                  <SettingsInput label="Breakeven Trigger %" value={tradingSettings.risk_caps.breakeven_activate_pct}
                    onChange={(v: number) => updateSettingsField('risk_caps', 'breakeven_activate_pct', v)} suffix="%" step={0.1} min={0.1} max={5} />
                  <SettingsInput label="Trailing Stop Trigger %" value={tradingSettings.risk_caps.trailing_stop_activate_pct}
                    onChange={(v: number) => updateSettingsField('risk_caps', 'trailing_stop_activate_pct', v)} suffix="%" step={0.1} min={0.2} max={10} />
                  <SettingsInput label="Trailing Stop Distance %" value={tradingSettings.risk_caps.trailing_stop_distance_pct}
                    onChange={(v: number) => updateSettingsField('risk_caps', 'trailing_stop_distance_pct', v)} suffix="%" step={0.1} min={0.1} max={5} />
                </div>
              </div>
            </>
          ) : (
            <p className="text-sm text-[var(--text-secondary)] py-4">Failed to load settings</p>
          )}
        </div>
      )}
    </div>
  )
}

/* ── Settings helper components ──────────────────────────────────── */

function SettingsInput({ label, value, onChange, suffix, step, min, max }: {
  label: string; value: number; onChange: (v: number) => void;
  suffix?: string; step?: number; min?: number; max?: number;
}) {
  return (
    <div>
      <label className="block text-[10px] text-[var(--text-secondary)] mb-1">{label} {suffix && <span className="opacity-60">{suffix}</span>}</label>
      <input type="number" value={value} step={step} min={min} max={max}
        onChange={e => onChange(parseFloat(e.target.value) || 0)}
        className="w-full px-2 py-1.5 text-sm font-mono bg-[var(--bg-primary)] border border-[var(--border)] rounded focus:border-[var(--accent)] focus:outline-none" />
    </div>
  )
}

function SettingsToggle({ label, checked, onChange }: {
  label: string; checked: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 cursor-pointer">
      <div onClick={() => onChange(!checked)}
        className={`w-8 h-4 rounded-full transition-colors relative cursor-pointer ${
          checked ? 'bg-[var(--accent)]' : 'bg-[var(--border)]'
        }`}>
        <div className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-all ${
          checked ? 'left-4' : 'left-0.5'
        }`} />
      </div>
      <span className="text-xs text-[var(--text-secondary)]">{label}</span>
    </label>
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

function ProfileStat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="p-2 bg-[var(--bg-card)] rounded text-center">
      <p className="text-[9px] text-[var(--text-secondary)] mb-0.5">{label}</p>
      <p className={`text-sm font-bold font-mono ${color || 'text-[var(--text-primary)]'}`}>{value}</p>
    </div>
  )
}

function VerdictBadge({ verdict }: { verdict: string }) {
  const styles: Record<string, string> = {
    'STRONG BUY': 'bg-green-500/20 text-green-400 border-green-500/30',
    'BUY': 'bg-green-500/15 text-green-400 border-green-500/20',
    'NEUTRAL': 'bg-yellow-500/15 text-yellow-400 border-yellow-500/20',
    'SELL': 'bg-red-500/15 text-red-400 border-red-500/20',
    'STRONG SELL': 'bg-red-500/20 text-red-400 border-red-500/30',
  }
  return (
    <span className={`text-[10px] font-bold px-2 py-0.5 rounded border ${styles[verdict] || 'bg-[var(--bg-card)] text-[var(--text-secondary)] border-[var(--border)]'}`}>
      {verdict}
    </span>
  )
}

function IndicatorCell({ label, value, color, sub }: { label: string; value?: number | null; color?: string; sub?: string }) {
  return (
    <div className="p-2 bg-[var(--bg-card)] rounded text-center">
      <p className="text-[9px] text-[var(--text-secondary)] mb-0.5">{label}</p>
      <p className={`text-xs font-bold font-mono ${color || 'text-[var(--text-primary)]'}`}>
        {value != null ? (Math.abs(value) < 1 ? value.toFixed(5) : value.toFixed(2)) : '—'}
      </p>
      {sub && <p className="text-[8px] text-[var(--text-secondary)] mt-0.5">{sub}</p>}
    </div>
  )
}

function CloseReasonBadge({ reason }: { reason: string }) {
  const styles: Record<string, string> = {
    'take_profit': 'bg-green-500/20 text-green-400 border-green-500/30',
    'tp_ratchet': 'bg-purple-500/20 text-purple-400 border-purple-500/30',
    'stop_loss': 'bg-red-500/20 text-red-400 border-red-500/30',
    'breakeven': 'bg-blue-500/20 text-blue-400 border-blue-500/30',
    'trailing_stop': 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30',
    'manual': 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
    'signal_expired': 'bg-gray-500/20 text-gray-400 border-gray-500/30',
  }
  const labels: Record<string, string> = {
    'take_profit': '🎯 TP Hit',
    'tp_ratchet': '🔒 Ratchet TP',
    'stop_loss': '🛑 SL Hit',
    'breakeven': '🛡️ Breakeven',
    'trailing_stop': '📈 Trailing SL',
    'manual': '✋ Manual',
    'signal_expired': '⏰ Expired',
  }
  const style = styles[reason] || 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] border-[var(--border)]'
  const label = labels[reason] || reason.replace(/_/g, ' ')
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${style}`}>
      {label}
    </span>
  )
}

function AgentChainPanel({ chain }: { chain: TradeChain }) {
  const [showLogs, setShowLogs] = useState(false)

  const stepIcons: Record<string, string> = {
    signal_created: '🔍',
    signal_decided: '✅',
    trade_executed: '⚡',
    tp_ratcheted: '🔒',
    breakeven_activated: '🛡️',
    trailing_activated: '📈',
    trade_closed: '🏁',
  }

  const stepColors: Record<string, string> = {
    signal_created: 'border-blue-500 bg-blue-500',
    signal_decided: 'border-green-500 bg-green-500',
    trade_executed: 'border-yellow-500 bg-yellow-500',
    tp_ratcheted: 'border-purple-500 bg-purple-500',
    breakeven_activated: 'border-blue-400 bg-blue-400',
    trailing_activated: 'border-cyan-500 bg-cyan-500',
    trade_closed: 'border-gray-400 bg-gray-400',
  }

  if (chain.steps.length === 0) {
    return (
      <div className="mt-3 p-3 bg-[var(--bg-secondary)] rounded-lg border border-[var(--border)]">
        <p className="text-xs text-[var(--text-secondary)]">No agent chain data available for this trade.</p>
      </div>
    )
  }

  return (
    <div className="mt-3 p-3 bg-[var(--bg-secondary)] rounded-lg border border-orange-500/20 animate-in slide-in-from-top-2 duration-200">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-semibold text-orange-400">⚡ Agent Action Chain</span>
        {chain.activity_logs.length > 0 && (
          <button
            onClick={() => setShowLogs(!showLogs)}
            className="text-[10px] px-2 py-0.5 rounded bg-[var(--bg-card)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] border border-[var(--border)] transition-colors"
          >
            {showLogs ? '▲ Hide Logs' : `▼ Activity Logs (${chain.activity_logs.length})`}
          </button>
        )}
      </div>

      {/* Step timeline */}
      <div className="relative">
        {chain.steps.map((step, idx) => {
          const dotColor = stepColors[step.step] || 'border-gray-500 bg-gray-500'
          const isLast = idx === chain.steps.length - 1
          return (
            <div key={idx} className="flex gap-3 mb-0">
              {/* Timeline line + dot */}
              <div className="flex flex-col items-center w-5 shrink-0">
                <div className={`w-3 h-3 rounded-full border-2 ${dotColor} shrink-0 z-10`} />
                {!isLast && <div className="w-0.5 flex-1 bg-[var(--border)] min-h-[20px]" />}
              </div>
              {/* Content */}
              <div className={`flex-1 ${isLast ? 'pb-0' : 'pb-3'}`}>
                <div className="flex items-center gap-2">
                  <span className="text-[10px]">{stepIcons[step.step] || '•'}</span>
                  <span className="text-xs font-semibold text-[var(--text-primary)]">{step.label}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--bg-card)] text-orange-400 font-medium">
                    {step.agent_name}
                  </span>
                  {step.timestamp && (
                    <span className="text-[9px] text-[var(--text-secondary)]">
                      {new Date(step.timestamp).toLocaleTimeString()}
                    </span>
                  )}
                </div>
                {/* Step details */}
                {step.details && (
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-[var(--text-secondary)]">
                    {step.step === 'signal_created' && step.details.confidence != null && (
                      <>
                        <span>Conf: <span className="text-[var(--text-primary)] font-mono">{Math.round(step.details.confidence * 100)}%</span></span>
                        <span>{step.details.direction?.toUpperCase()}</span>
                        {step.details.entry_price && <span>Entry: <span className="font-mono">{fmtPrice(step.details.entry_price)}</span></span>}
                        {step.details.stop_loss && <span>SL: <span className="font-mono text-red-400">{fmtPrice(step.details.stop_loss)}</span></span>}
                        {step.details.take_profit && <span>TP: <span className="font-mono text-green-400">{fmtPrice(step.details.take_profit)}</span></span>}
                      </>
                    )}
                    {step.step === 'signal_created' && step.details.reasoning && (
                      <p className="w-full mt-1 text-[10px] text-[var(--text-secondary)] line-clamp-2 italic">
                        &quot;{step.details.reasoning}&quot;
                      </p>
                    )}
                    {step.step === 'signal_decided' && (
                      <span>By: <span className="text-[var(--text-primary)]">{step.details.approved_by || 'Auto'}</span></span>
                    )}
                    {step.step === 'trade_executed' && (
                      <>
                        <span>Platform: <span className="text-[var(--text-primary)]">{step.details.platform}</span></span>
                        <span>Size: <span className="font-mono">{step.details.size}</span></span>
                        <span>@ <span className="font-mono">{fmtPrice(step.details.entry_price)}</span></span>
                      </>
                    )}
                    {step.step === 'tp_ratcheted' && (
                      <>
                        <span>Original: <span className="font-mono line-through">{fmtPrice(step.details.original_tp)}</span></span>
                        <span>→ Protected: <span className="font-mono text-green-400">{fmtPrice(step.details.ratcheted_tp)}</span></span>
                        {step.details.tier != null && <span>Tier: <span className="font-mono text-purple-400">{(step.details.tier * 100).toFixed(0)}%</span></span>}
                      </>
                    )}
                    {step.step === 'trade_closed' && (
                      <>
                        {step.details.exit_price && <span>Exit: <span className="font-mono">{fmtPrice(step.details.exit_price)}</span></span>}
                        {step.details.pnl != null && <span>P&L: <span className={`font-mono font-bold ${pnlColor(step.details.pnl)}`}>{fmtMoney(step.details.pnl, true)}</span></span>}
                        {step.details.close_reason && <span>Reason: <span className="text-[var(--text-primary)]">{step.details.close_reason}</span></span>}
                      </>
                    )}
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* Detailed activity logs */}
      {showLogs && chain.activity_logs.length > 0 && (
        <div className="mt-3 pt-3 border-t border-[var(--border)]">
          <p className="text-[10px] font-semibold text-[var(--text-secondary)] mb-2 uppercase tracking-wider">Detailed Agent Logs</p>
          <div className="space-y-1 max-h-60 overflow-y-auto">
            {chain.activity_logs.map((log, idx) => (
              <div key={idx} className="text-[10px] font-mono flex gap-2 py-0.5 px-1.5 rounded hover:bg-[var(--bg-card)]">
                <span className="text-[var(--text-secondary)] shrink-0 w-16">
                  {log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : ''}
                </span>
                <span className={`shrink-0 w-24 ${
                  log.action === 'tool_call' ? 'text-yellow-400' :
                  log.action === 'tool_result' ? 'text-cyan-400' :
                  log.action === 'thinking_content' ? 'text-purple-400' :
                  'text-[var(--text-secondary)]'
                }`}>
                  {log.action}
                </span>
                <span className="truncate text-[var(--text-primary)]">
                  {log.action === 'tool_call' && log.details?.tool && (
                    <><span className="text-yellow-400">{String(log.details.tool)}</span>{log.details.args ? ` (${JSON.stringify(log.details.args).slice(0, 100)})` : ''}</>
                  )}
                  {log.action === 'tool_result' && log.details?.tool && (
                    <><span className={log.details.success ? 'text-green-400' : 'text-red-400'}>{log.details.success ? '✓' : '✗'} {String(log.details.tool)}</span> <span className="text-[var(--text-secondary)]">{log.details.duration_ms}ms</span> {log.details.preview ? `→ ${String(log.details.preview).slice(0, 120)}` : log.details.error ? `ERROR: ${String(log.details.error).slice(0, 120)}` : ''}</>
                  )}
                  {log.action === 'thinking_content' && log.details?.content && (
                    <span className="italic text-purple-300">{String(log.details.content).slice(0, 200)}</span>
                  )}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
