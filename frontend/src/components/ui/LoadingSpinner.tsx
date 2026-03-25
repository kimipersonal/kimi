export function LoadingSpinner({ message = 'Loading...' }: { message?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-3">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[var(--accent)]" />
      <p className="text-sm text-[var(--text-secondary)]">{message}</p>
    </div>
  )
}
