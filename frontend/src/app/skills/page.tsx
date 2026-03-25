'use client'

import { useEffect, useState, useCallback } from 'react'
import { api } from '@/lib/api'
import type { SkillInfo } from '@/lib/api'
import { Zap, Search, Tag, Wrench, CheckCircle, XCircle } from 'lucide-react'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { ErrorBanner } from '@/components/ui/ErrorBanner'

const CATEGORY_COLORS: Record<string, string> = {
  research: 'bg-blue-500/15 text-blue-400',
  data: 'bg-green-500/15 text-green-400',
  communication: 'bg-purple-500/15 text-purple-400',
  development: 'bg-orange-500/15 text-orange-400',
  finance: 'bg-yellow-500/15 text-yellow-400',
  productivity: 'bg-cyan-500/15 text-cyan-400',
  utility: 'bg-gray-500/15 text-gray-400',
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [categories, setCategories] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setError(null)
    try {
      const [skillsRes, catsRes] = await Promise.all([
        api.getSkills(filter || undefined),
        api.getSkillCategories(),
      ])
      setSkills(skillsRes.skills)
      setCategories(catsRes.categories)
    } catch {
      setError('Failed to load skills')
    }
    setLoading(false)
  }, [filter])

  useEffect(() => { refresh() }, [refresh])

  const filtered = skills.filter(s => {
    if (!search) return true
    const q = search.toLowerCase()
    return (
      s.display_name.toLowerCase().includes(q) ||
      s.description.toLowerCase().includes(q) ||
      s.metadata.tags.some(t => t.toLowerCase().includes(q)) ||
      s.tools.some(t => t.toLowerCase().includes(q))
    )
  })

  if (loading) return <LoadingSpinner />
  if (error) return <ErrorBanner message={error} onRetry={refresh} />

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Zap size={24} className="text-[var(--accent)]" />
          <div>
            <h1 className="text-xl font-bold">Skills Marketplace</h1>
            <p className="text-sm text-[var(--text-secondary)]">
              {skills.length} skills available &middot; {skills.filter(s => s.enabled).length} enabled
            </p>
          </div>
        </div>
      </div>

      {/* Search + Category Filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-secondary)]" />
          <input
            type="text"
            placeholder="Search skills, tools, tags..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full pl-9 pr-3 py-2 bg-[var(--bg-card)] border border-[var(--border)] rounded-lg text-sm focus:outline-none focus:border-[var(--accent)]"
          />
        </div>
        <div className="flex gap-2 flex-wrap">
          <button
            onClick={() => setFilter(null)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              !filter ? 'bg-[var(--accent)]/20 text-[var(--accent)]' : 'bg-[var(--bg-card)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
          >
            All ({Object.values(categories).reduce((a, b) => a + b, 0)})
          </button>
          {Object.entries(categories).map(([cat, count]) => (
            <button
              key={cat}
              onClick={() => setFilter(filter === cat ? null : cat)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors capitalize ${
                filter === cat ? 'bg-[var(--accent)]/20 text-[var(--accent)]' : 'bg-[var(--bg-card)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              {cat} ({count})
            </button>
          ))}
        </div>
      </div>

      {/* Skills Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {filtered.map(skill => (
          <div
            key={skill.name}
            className="bg-[var(--bg-card)] border border-[var(--border)] rounded-xl p-4 hover:border-[var(--accent)]/30 transition-colors cursor-pointer"
            onClick={() => setExpanded(expanded === skill.name ? null : skill.name)}
          >
            {/* Skill Header */}
            <div className="flex items-start gap-3">
              <span className="text-2xl">{skill.metadata.icon}</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <h3 className="font-semibold text-sm truncate">{skill.display_name}</h3>
                  {skill.enabled ? (
                    <CheckCircle size={14} className="text-green-400 shrink-0" />
                  ) : (
                    <XCircle size={14} className="text-red-400 shrink-0" />
                  )}
                </div>
                <p className="text-xs text-[var(--text-secondary)] mt-0.5 line-clamp-2">
                  {skill.description}
                </p>
              </div>
            </div>

            {/* Meta row */}
            <div className="flex items-center gap-2 mt-3 flex-wrap">
              <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium capitalize ${CATEGORY_COLORS[skill.category] || CATEGORY_COLORS.utility}`}>
                {skill.category}
              </span>
              <span className="text-[10px] text-[var(--text-secondary)]">v{skill.version}</span>
              <span className="text-[10px] text-[var(--text-secondary)] flex items-center gap-1">
                <Wrench size={10} /> {skill.tool_count} tool{skill.tool_count !== 1 ? 's' : ''}
              </span>
            </div>

            {/* Tags */}
            {skill.metadata.tags.length > 0 && (
              <div className="flex gap-1 mt-2 flex-wrap">
                {skill.metadata.tags.map(tag => (
                  <span key={tag} className="text-[10px] text-[var(--text-secondary)] bg-[var(--bg-secondary)] px-1.5 py-0.5 rounded flex items-center gap-0.5">
                    <Tag size={8} />{tag}
                  </span>
                ))}
              </div>
            )}

            {/* Expanded: Tool list */}
            {expanded === skill.name && (
              <div className="mt-3 pt-3 border-t border-[var(--border)]">
                <p className="text-[10px] font-medium text-[var(--text-secondary)] uppercase mb-2">Available Tools</p>
                <div className="space-y-1.5">
                  {skill.tools.map(tool => (
                    <div key={tool} className="flex items-center gap-2 text-xs">
                      <code className="text-[var(--accent)] bg-[var(--bg-secondary)] px-1.5 py-0.5 rounded font-mono text-[11px]">
                        {tool}
                      </code>
                    </div>
                  ))}
                </div>
                {skill.metadata.requires_config.length > 0 && (
                  <div className="mt-2">
                    <p className="text-[10px] font-medium text-[var(--text-secondary)] uppercase mb-1">Required Config</p>
                    {skill.metadata.requires_config.map(cfg => (
                      <code key={cfg} className="text-[10px] text-yellow-400 bg-yellow-500/10 px-1.5 py-0.5 rounded mr-1">
                        {cfg}
                      </code>
                    ))}
                    {!skill.configured && (
                      <p className="text-[10px] text-red-400 mt-1">⚠ Missing configuration</p>
                    )}
                  </div>
                )}
                <p className="text-[10px] text-[var(--text-secondary)] mt-2">
                  by {skill.metadata.author}
                </p>
              </div>
            )}
          </div>
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-12 text-[var(--text-secondary)]">
          <Zap size={40} className="mx-auto mb-3 opacity-30" />
          <p>No skills found{search ? ` matching "${search}"` : ''}</p>
        </div>
      )}
    </div>
  )
}
