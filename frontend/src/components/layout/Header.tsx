'use client'

interface HeaderProps {
  connected: boolean
}

export function Header({ connected }: HeaderProps) {
  return (
    <header className="h-12 shrink-0 border-b border-[var(--border)] bg-[var(--bg-secondary)] flex items-center justify-between px-6">
      <div />
      <div className="flex items-center gap-4">
        <span className={`text-xs flex items-center gap-1.5 ${connected ? 'text-[var(--success)]' : 'text-[var(--danger)]'}`}>
          <span className={`status-dot ${connected ? 'status-idle' : 'status-error'}`} />
          {connected ? 'Live' : 'Disconnected'}
        </span>
      </div>
    </header>
  )
}
