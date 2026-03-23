'use client'

import { useEffect, useState, useCallback } from 'react'
import { api } from '@/lib/api'
import type { CostOverview, HealthStatus, CircuitStatus } from '@/lib/api'
import { Settings as SettingsIcon } from 'lucide-react'

export default function SettingsPage() {
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null)
  const [costs, setCosts] = useState<CostOverview | null>(null)
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [circuits, setCircuits] = useState<CircuitStatus[]>([])

  const refresh = useCallback(async () => {
    try {
      const [s, c, h, cb] = await Promise.all([
        api.getSettings(),
        api.getCosts(),
        api.getHealth(),
        api.getCircuits(),
      ])
      setSettings(s)
      setCosts(c)
      setHealth(h)
      setCircuits(cb.circuits)
    } catch (err) {
      console.error('Failed to fetch settings:', err)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  if (!settings) {
    return <div className="text-[var(--text-secondary)]">Loading settings...</div>
  }

  const sections = [
    {
      title: 'LLM Models',
      items: [
        { label: 'Fast (Cheap, Bulk)', value: settings.llm_model_fast },
        { label: 'Smart (Balanced)', value: settings.llm_model_smart },
        { label: 'Reasoning (Complex)', value: settings.llm_model_reasoning },
      ],
    },
    {
      title: 'Vertex AI',
      items: [
        { label: 'Project', value: settings.vertex_project },
        { label: 'Default Location', value: settings.vertex_location },
      ],
    },
    {
      title: 'Integrations',
      items: [
        { label: 'Telegram Bot', value: settings.telegram_bot_token ? '✅ Configured' : '❌ Not configured' },
      ],
    },
    {
      title: 'Limits',
      items: [
        { label: 'Auto-Approval Threshold', value: `$${settings.auto_approval_threshold}` },
      ],
    },
  ]

  const budgetPct = costs?.budget_used_pct ?? 0
  const budgetColor = budgetPct > 80 ? 'var(--danger)' : budgetPct > 50 ? 'var(--warning)' : 'var(--success)'

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-center gap-2">
        <SettingsIcon size={20} className="text-[var(--text-secondary)]" />
        <h2 className="text-xl font-bold">Settings & Monitoring</h2>
      </div>

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

      {/* Config sections */}
      <p className="text-sm text-[var(--text-secondary)]">
        System configuration via <code className="text-xs px-1.5 py-0.5 rounded bg-[var(--bg-card)]">.env</code> file.
      </p>

      {sections.map(section => (
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
