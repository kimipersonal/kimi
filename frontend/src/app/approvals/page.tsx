'use client'

import { useEffect, useState, useCallback } from 'react'
import { useWS } from '@/hooks/WSContext'
import { api } from '@/lib/api'
import type { Approval } from '@/lib/api'
import { ShieldCheck } from 'lucide-react'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { ErrorBanner } from '@/components/ui/ErrorBanner'

const CATEGORY_ICONS: Record<string, string> = {
  company_creation: '🏢',
  trade: '📈',
  hiring: '👤',
  spending: '💰',
  general: '📋',
}

export default function ApprovalsPage() {
  const { events } = useWS()
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [filter, setFilter] = useState<string>('all')
  const [rejectReasons, setRejectReasons] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setError(null)
    setLoading(true)
    try {
      const status = filter === 'all' ? undefined : filter
      setApprovals(await api.getApprovals(status))
    } catch {
      setError('Failed to load approvals')
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    if (events.length > 0) {
      const last = events[events.length - 1]
      if (['approval_request', 'approval_decided'].includes(last.event)) {
        refresh()
      }
    }
  }, [events, refresh])

  const handleDecide = async (approvalId: string, approved: boolean) => {
    try {
      const reason = approved ? 'Approved by Owner' : (rejectReasons[approvalId] || 'Rejected by Owner')
      await api.decideApproval(approvalId, approved, reason)
      await refresh()
    } catch (err) {
      console.error('Decision failed:', err)
    }
  }

  const pending = approvals.filter(a => a.status === 'pending')
  const decided = approvals.filter(a => a.status !== 'pending')

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold">Approvals</h2>
        <div className="flex gap-2">
          {['all', 'pending', 'approved', 'rejected'].map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`text-xs px-3 py-1 rounded capitalize ${
                filter === f
                  ? 'bg-[var(--accent)] text-white'
                  : 'bg-[var(--bg-card)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {loading && <LoadingSpinner message="Loading approvals..." />}
      {error && <ErrorBanner message={error} onRetry={refresh} />}

      {/* Pending approvals */}
      {!loading && pending.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold text-[var(--warning)] mb-3">⏳ Pending ({pending.length})</h3>
          <div className="space-y-3">
            {pending.map(approval => (
              <div key={approval.id} className="rounded-lg border-2 border-[var(--warning)]/40 bg-[var(--bg-card)] p-4">
                <div className="flex items-start gap-3">
                  <span className="text-xl">{CATEGORY_ICONS[approval.category] || '📋'}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-semibold text-sm">{approval.description}</span>
                      <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--bg-secondary)] text-[var(--text-secondary)] capitalize">
                        {approval.category}
                      </span>
                    </div>
                    {approval.details && (
                      <p className="text-xs text-[var(--text-secondary)] mb-2">{approval.details}</p>
                    )}
                    <p className="text-xs text-[var(--text-secondary)]">
                      From: {approval.agent_name} • {new Date(approval.requested_at).toLocaleString()}
                    </p>

                    {/* Reject reason input */}
                    <div className="mt-3 flex items-center gap-2">
                      <button
                        onClick={() => handleDecide(approval.id, true)}
                        className="text-xs px-4 py-1.5 rounded bg-[var(--success)] text-white hover:opacity-80 font-medium"
                      >
                        ✓ Approve
                      </button>
                      <input
                        type="text"
                        placeholder="Rejection reason (optional)"
                        value={rejectReasons[approval.id] || ''}
                        onChange={e => setRejectReasons(prev => ({ ...prev, [approval.id]: e.target.value }))}
                        className="flex-1 text-xs px-3 py-1.5 rounded bg-[var(--bg-secondary)] border border-[var(--border)] text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]"
                      />
                      <button
                        onClick={() => handleDecide(approval.id, false)}
                        className="text-xs px-4 py-1.5 rounded bg-[var(--danger)] text-white hover:opacity-80 font-medium"
                      >
                        ✗ Reject
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Decided approvals */}
      {!loading && decided.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold text-[var(--text-secondary)] mb-3">History ({decided.length})</h3>
          <div className="space-y-2">
            {decided.map(approval => (
              <div key={approval.id} className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
                <div className="flex items-start gap-3">
                  <span className="text-lg">{CATEGORY_ICONS[approval.category] || '📋'}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-sm">{approval.description}</span>
                      <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${
                        approval.status === 'approved'
                          ? 'bg-[var(--success)]/20 text-[var(--success)]'
                          : 'bg-[var(--danger)]/20 text-[var(--danger)]'
                      }`}>
                        {approval.status}
                      </span>
                    </div>
                    <p className="text-xs text-[var(--text-secondary)]">
                      {approval.decision_reason && `Reason: ${approval.decision_reason} • `}
                      {approval.decided_at && new Date(approval.decided_at).toLocaleString()}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {!loading && !error && approvals.length === 0 && (
        <div className="text-center py-16 text-[var(--text-secondary)]">
          <ShieldCheck size={40} className="mx-auto mb-3 opacity-40" />
          <p className="text-lg mb-2">No approvals</p>
          <p className="text-sm">When agents request approval, they&apos;ll appear here.</p>
        </div>
      )}
    </div>
  )
}
