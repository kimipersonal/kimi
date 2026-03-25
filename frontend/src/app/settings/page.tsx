'use client'

import { useEffect, useState, useCallback } from 'react'
import { api } from '@/lib/api'
import type { CostOverview, HealthStatus, CircuitStatus, ModelsResponse } from '@/lib/api'
import { Settings as SettingsIcon, Check, Loader2 } from 'lucide-react'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { ErrorBanner } from '@/components/ui/ErrorBanner'

export default function SettingsPage() {
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null)
  const [costs, setCosts] = useState<CostOverview | null>(null)
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [circuits, setCircuits] = useState<CircuitStatus[]>([])
  const [modelsData, setModelsData] = useState<ModelsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Model tier editing state
  const [tierFast, setTierFast] = useState('')
  const [tierSmart, setTierSmart] = useState('')
  const [tierReasoning, setTierReasoning] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const refresh = useCallback(async () => {
    setError(null)
    let failed = false
    try {
      const [s, c, h, cb, m] = await Promise.all([
        api.getSettings(),
        api.getCosts(),
        api.getHealth(),
        api.getCircuits(),
        api.getModels(),
      ])
      setSettings(s)
      setCosts(c)
      setHealth(h)
      setCircuits(cb.circuits)
      setModelsData(m)
      setTierFast(m.current_tiers.fast)
      setTierSmart(m.current_tiers.smart)
      setTierReasoning(m.current_tiers.reasoning)
    } catch {
      failed = true
    }
    if (failed) setError('Failed to load settings data')
    setLoading(false)
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const handleSaveTiers = async () => {
    if (!modelsData) return
    const changes: Record<string, string> = {}
    if (tierFast !== modelsData.current_tiers.fast) changes.llm_fast = tierFast
    if (tierSmart !== modelsData.current_tiers.smart) changes.llm_smart = tierSmart
    if (tierReasoning !== modelsData.current_tiers.reasoning) changes.llm_reasoning = tierReasoning
    if (Object.keys(changes).length === 0) {
      setSaveMsg({ type: 'success', text: 'No changes to save' })
      setTimeout(() => setSaveMsg(null), 2000)
      return
    }
    setSaving(true)
    setSaveMsg(null)
    try {
      const result = await api.updateSettings(changes)
      setModelsData(prev => prev ? { ...prev, current_tiers: result.current_tiers } : prev)
      setSaveMsg({ type: 'success', text: `Updated ${Object.keys(result.updated).join(', ')} tier(s)` })
    } catch (err) {
      setSaveMsg({ type: 'error', text: err instanceof Error ? err.message : 'Failed to save' })
    } finally {
      setSaving(false)
      setTimeout(() => setSaveMsg(null), 4000)
    }
  }

  const hasChanges = modelsData && (
    tierFast !== modelsData.current_tiers.fast ||
    tierSmart !== modelsData.current_tiers.smart ||
    tierReasoning !== modelsData.current_tiers.reasoning
  )

  if (loading) return <LoadingSpinner message="Loading settings..." />

  const budgetPct = costs?.budget_used_pct ?? 0
  const budgetColor = budgetPct > 80 ? 'var(--danger)' : budgetPct > 50 ? 'var(--warning)' : 'var(--success)'

  const configSections = [
    {
      title: 'Vertex AI',
      items: [
        { label: 'Project', value: settings?.vertex_project },
        { label: 'Default Location', value: settings?.vertex_location },
      ],
    },
    {
      title: 'Integrations',
      items: [
        { label: 'Telegram Bot', value: settings?.telegram_bot_token ? '✅ Configured' : '❌ Not configured' },
      ],
    },
    {
      title: 'Limits',
      items: [
        { label: 'Auto-Approval Threshold', value: `$${settings?.auto_approval_threshold}` },
      ],
    },
  ]

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-center gap-2">
        <SettingsIcon size={20} className="text-[var(--text-secondary)]" />
        <h2 className="text-xl font-bold">Settings & Monitoring</h2>
      </div>

      {error && <ErrorBanner message={error} onRetry={refresh} />}

      {/* Health Status */}
      {health && (
        <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)]">
          <h3 className="text-sm font-semibold px-4 py-3 border-b border-[var(--border)] flex items-center gap-2">
            🏥 System Health
            <span className={`text-xs px-2 py-0.5 rounded ${health.status === 'healthy' ? 'bg-[var(--success)]/20 text-[var(--success)]' : 'bg-[var(--warning)]/20 text-[var(--warning)]'}`}>
              {health.status}
            </span>
          </h3>
          <div className="px-4 py-3 flex items-center justify-between text-sm">
            <span className="text-[var(--text-secondary)]">Uptime</span>
            <span className="font-mono">{health.uptime_human}</span>
          </div>
          <div className="divide-y divide-[var(--border)]">
            {Object.entries(health.checks).map(([name, check]) => (
              <div key={name} className="px-4 py-2 flex items-center justify-between text-sm">
                <span className="text-[var(--text-secondary)]">{name}</span>
                <span>{check.status === 'healthy' ? '✅' : '❌'} {check.status}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Cost Tracking */}
      {costs && (
        <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)]">
          <h3 className="text-sm font-semibold px-4 py-3 border-b border-[var(--border)]">
            💰 Cost Tracking
          </h3>
          <div className="p-4 space-y-3">
            {/* Budget bar */}
            <div>
              <div className="flex justify-between text-xs mb-1">
                <span className="text-[var(--text-secondary)]">Daily Budget</span>
                <span className="font-mono">${costs.cost_today_usd.toFixed(4)} / ${costs.daily_budget_usd.toFixed(2)}</span>
              </div>
              <div className="h-2 bg-[var(--bg-secondary)] rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all"
                  style={{ width: `${Math.min(budgetPct, 100)}%`, backgroundColor: budgetColor }}
                />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3 text-center">
              <div>
                <div className="text-lg font-bold">{costs.calls_today}</div>
                <div className="text-[10px] text-[var(--text-secondary)]">Calls Today</div>
              </div>
              <div>
                <div className="text-lg font-bold">${costs.cost_today_usd.toFixed(4)}</div>
                <div className="text-[10px] text-[var(--text-secondary)]">Cost Today</div>
              </div>
              <div>
                <div className="text-lg font-bold">${costs.total_cost_usd.toFixed(4)}</div>
                <div className="text-[10px] text-[var(--text-secondary)]">Total Cost</div>
              </div>
            </div>
            {/* Per-agent breakdown */}
            {costs.agents.length > 0 && (
              <div className="border-t border-[var(--border)] pt-3">
                <h4 className="text-xs font-semibold text-[var(--text-secondary)] mb-2">Per Agent</h4>
                <div className="space-y-1">
                  {costs.agents.map(a => (
                    <div key={a.agent_id} className="flex items-center justify-between text-xs">
                      <span className="font-mono">{a.agent_id}</span>
                      <span>{a.total_calls} calls · ${a.total_cost_usd.toFixed(4)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Circuit Breakers */}
      {circuits.length > 0 && (
        <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)]">
          <h3 className="text-sm font-semibold px-4 py-3 border-b border-[var(--border)]">
            ⚡ Circuit Breakers
          </h3>
          <div className="divide-y divide-[var(--border)]">
            {circuits.map(cb => (
              <div key={cb.name} className="px-4 py-2 flex items-center justify-between text-sm">
                <span className="font-mono text-xs">{cb.name}</span>
                <span className={`text-xs px-2 py-0.5 rounded ${
                  cb.state === 'closed' ? 'bg-[var(--success)]/20 text-[var(--success)]' :
                  cb.state === 'open' ? 'bg-[var(--danger)]/20 text-[var(--danger)]' :
                  'bg-[var(--warning)]/20 text-[var(--warning)]'
                }`}>
                  {cb.state} ({cb.failure_count}/{cb.failure_threshold})
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* LLM Model Tiers — Interactive */}
      {modelsData && (
        <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)]">
          <h3 className="text-sm font-semibold px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
            🤖 LLM Model Tiers
            {hasChanges && (
              <button
                onClick={handleSaveTiers}
                disabled={saving}
                className="flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-[var(--accent)] text-white hover:opacity-80 disabled:opacity-50"
              >
                {saving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                {saving ? 'Saving...' : 'Save Changes'}
              </button>
            )}
          </h3>
          <div className="p-4 space-y-4">
            {saveMsg && (
              <div className={`text-xs px-3 py-2 rounded ${saveMsg.type === 'success' ? 'bg-[var(--success)]/20 text-[var(--success)]' : 'bg-[var(--danger)]/20 text-[var(--danger)]'}`}>
                {saveMsg.text}
              </div>
            )}
            {[
              { label: '⚡ Fast (Cheap, Bulk)', value: tierFast, setter: setTierFast },
              { label: '🧠 Smart (Balanced)', value: tierSmart, setter: setTierSmart },
              { label: '💎 Reasoning (Complex)', value: tierReasoning, setter: setTierReasoning },
            ].map(tier => (
              <div key={tier.label}>
                <label className="text-xs text-[var(--text-secondary)] mb-1 block">{tier.label}</label>
                <select
                  value={tier.value}
                  onChange={e => tier.setter(e.target.value)}
                  className="w-full text-sm px-3 py-2 rounded bg-[var(--bg-secondary)] border border-[var(--border)] text-[var(--text-primary)]"
                >
                  {modelsData.models.map(m => (
                    <option key={m.id} value={m.id}>
                      {m.name} — {m.cost} ({m.type === 'native' ? 'Gemini' : 'Model Garden'})
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Config sections */}
      {configSections.map(section => (
        <div key={section.title} className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)]">
          <h3 className="text-sm font-semibold px-4 py-3 border-b border-[var(--border)]">{section.title}</h3>
          <div className="divide-y divide-[var(--border)]">
            {section.items.map(item => (
              <div key={item.label} className="px-4 py-2.5 flex items-center justify-between">
                <span className="text-sm text-[var(--text-secondary)]">{item.label}</span>
                <span className="text-sm font-mono">{String(item.value ?? '—')}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
