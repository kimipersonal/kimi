'use client'

import { useEffect, useState, useCallback } from 'react'
import { useWS } from '@/hooks/WSContext'
import { api } from '@/lib/api'
import type { Company } from '@/lib/api'
import { Building2 } from 'lucide-react'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { ErrorBanner } from '@/components/ui/ErrorBanner'

const TYPE_ICONS: Record<string, string> = {
  trading: '📈',
  research: '🔬',
  marketing: '📢',
  analytics: '📊',
  general: '🏢',
}

export default function CompaniesPage() {
  const { events } = useWS()
  const [companies, setCompanies] = useState<Company[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setError(null)
    try {
      setCompanies(await api.getCompanies())
    } catch {
      setError('Failed to load companies')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    if (events.length > 0) {
      const last = events[events.length - 1]
      if (['company_created', 'agent_hired', 'agent_fired'].includes(last.event)) {
        refresh()
      }
    }
  }, [events, refresh])

  if (loading) return <LoadingSpinner message="Loading companies..." />

  return (
    <div className="space-y-6 max-w-7xl">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold">Companies</h2>
        <span className="text-sm text-[var(--text-secondary)]">{companies.length} companies</span>
      </div>

      {error && <ErrorBanner message={error} onRetry={refresh} />}

      {!error && companies.length === 0 ? (
        <div className="text-center py-16 text-[var(--text-secondary)]">
          <Building2 size={40} className="mx-auto mb-3 opacity-40" />
          <p className="text-lg mb-2">No companies yet</p>
          <p className="text-sm">Ask the CEO to create a company from the Overview chat.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {companies.map(company => (
            <div key={company.id} className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-5">
              <div className="flex items-center gap-2 mb-3">
                <span className="text-xl">{TYPE_ICONS[company.type] || '🏢'}</span>
                <div>
                  <h3 className="font-semibold">{company.name}</h3>
                  <span className="text-xs text-[var(--text-secondary)] capitalize">{company.type}</span>
                </div>
                <span className={`ml-auto text-xs px-2 py-0.5 rounded ${
                  company.status === 'active'
                    ? 'bg-[var(--success)]/20 text-[var(--success)]'
                    : 'bg-[var(--text-secondary)]/20 text-[var(--text-secondary)]'
                }`}>
                  {company.status}
                </span>
              </div>

              {company.description && (
                <p className="text-sm text-[var(--text-secondary)] mb-3">{company.description}</p>
              )}

              <div className="border-t border-[var(--border)] pt-3">
                <h4 className="text-xs font-semibold text-[var(--text-secondary)] mb-2">
                  Agents ({company.agents?.length || 0})
                </h4>
                {company.agents_detail && company.agents_detail.length > 0 ? (
                  <div className="space-y-1.5">
                    {company.agents_detail.map(agent => (
                      <div key={agent.id} className="flex items-center justify-between text-sm">
                        <span>{agent.name}</span>
                        <span className="text-xs text-[var(--text-secondary)]">{agent.role}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-[var(--text-secondary)]">No agents assigned</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
