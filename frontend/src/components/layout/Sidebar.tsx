'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  LayoutDashboard,
  Bot,
  Building2,
  ScrollText,
  ShieldCheck,
  Settings,
  TrendingUp,
  Monitor,
  Zap,
} from 'lucide-react'

const NAV_ITEMS = [
  { href: '/', label: 'Overview', icon: LayoutDashboard },
  { href: '/office', label: 'Pixel Office', icon: Monitor },
  { href: '/agents', label: 'Agents', icon: Bot },
  { href: '/companies', label: 'Companies', icon: Building2 },
  { href: '/trading', label: 'Trading', icon: TrendingUp },
  { href: '/skills', label: 'Skills', icon: Zap },
  { href: '/approvals', label: 'Approvals', icon: ShieldCheck },
  { href: '/logs', label: 'Logs', icon: ScrollText },
  { href: '/settings', label: 'Settings', icon: Settings },
]

export function Sidebar() {
  const pathname = usePathname()

  return (
    <aside className="w-56 shrink-0 bg-[var(--bg-secondary)] border-r border-[var(--border)] flex flex-col">
      {/* Brand */}
      <div className="px-4 py-5 border-b border-[var(--border)]">
        <Link href="/" className="flex items-center gap-2">
          <span className="text-2xl">🏛️</span>
          <div>
            <h1 className="text-base font-bold leading-tight">AI Holding</h1>
            <p className="text-[10px] text-[var(--text-secondary)] leading-tight">Multi-Agent Dashboard</p>
          </div>
        </Link>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-2 py-3 space-y-0.5">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = href === '/' ? pathname === '/' : pathname.startsWith(href)
          return (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors ${
                active
                  ? 'bg-[var(--accent)]/15 text-[var(--accent)] font-medium'
                  : 'text-[var(--text-secondary)] hover:bg-[var(--bg-card)] hover:text-[var(--text-primary)]'
              }`}
            >
              <Icon size={16} />
              {label}
            </Link>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-[var(--border)] text-[10px] text-[var(--text-secondary)]">
        AI Holding v0.1.0
      </div>
    </aside>
  )
}
