import { AlertTriangle } from 'lucide-react'

export function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex items-center gap-3 p-4 rounded-lg border border-[var(--danger)]/40 bg-[var(--danger)]/10">
      <AlertTriangle size={20} className="text-[var(--danger)] shrink-0" />
      <p className="text-sm text-[var(--danger)] flex-1">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="text-xs px-3 py-1.5 rounded bg-[var(--danger)] text-white hover:opacity-80 shrink-0"
        >
          Retry
        </button>
      )}
    </div>
  )
}
