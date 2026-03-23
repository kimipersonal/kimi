'use client'

import type { Overview } from '@/lib/api'

export function OverviewCards({ data }: { data: Overview }) {
  const cards = [
    { label: 'Companies', value: data.total_companies, icon: '🏢' },
    { label: 'Agents', value: `${data.active_agents}/${data.total_agents}`, icon: '🤖' },
    { label: 'Pending Approvals', value: data.pending_approvals, icon: '⏳', highlight: data.pending_approvals > 0 },
    { label: 'Tasks Today', value: data.tasks_today, icon: '📋' },
    { label: 'Cost Today', value: `$${data.total_cost_today.toFixed(2)}`, icon: '💰' },
  ]

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
      {cards.map((card) => (
        <div
          key={card.label}
          className={`rounded-lg border p-4 ${
            card.highlight
              ? 'border-[var(--warning)] bg-[var(--warning)]/10'
              : 'border-[var(--border)] bg-[var(--bg-card)]'
          }`}
        >
          <div className="flex items-center gap-2 mb-1">
            <span>{card.icon}</span>
            <span className="text-xs text-[var(--text-secondary)]">{card.label}</span>
          </div>
          <p className="text-2xl font-bold">{card.value}</p>
        </div>
      ))}
    </div>
  )
}
