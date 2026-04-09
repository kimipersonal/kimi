'use client'

import { useState, useRef, useEffect } from 'react'
import type { StreamStep, ChatMessage as APIChatMessage } from '@/lib/api'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: Date
}

interface ThinkingStep {
  action: string
  detail: string
  icon: string
}

export function ChatPanel({
  agentId,
  agentName,
  onSend,
  onSendStream,
  loading,
  initialMessages,
}: {
  agentId: string
  agentName: string
  onSend: (message: string) => Promise<string>
  onSendStream?: (message: string, onStep: (step: StreamStep) => void) => Promise<string>
  loading: boolean
  initialMessages?: APIChatMessage[]
}) {
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>(() =>
    (initialMessages ?? [])
      .filter(m => m.role === 'user' || m.role === 'assistant')
      .map(m => ({ role: m.role, content: m.content, timestamp: new Date(m.timestamp) }))
  )
  const [steps, setSteps] = useState<ThinkingStep[]>([])
  const scrollRef = useRef<HTMLDivElement>(null)
  const initializedRef = useRef(false)

  // Seed messages from server history once it arrives (agent data loads async)
  useEffect(() => {
    if (!initializedRef.current && initialMessages && initialMessages.length > 0) {
      initializedRef.current = true
      setMessages(
        initialMessages
          .filter(m => m.role === 'user' || m.role === 'assistant')
          .map(m => ({ role: m.role as 'user' | 'assistant', content: m.content, timestamp: new Date(m.timestamp) }))
      )
    }
  }, [initialMessages])

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages, steps])

  const stepIcon = (action: string) => {
    if (action === 'thinking') return '🧠'
    if (action === 'tool_call') return '🔧'
    if (action === 'completed') return '✅'
    if (action === 'error') return '❌'
    return '📋'
  }

  const formatStep = (step: StreamStep): string => {
    if (step.action === 'tool_call' && step.details) {
      const tool = String((step.details as Record<string, unknown>).tool || '')
      const args = (step.details as Record<string, unknown>).args as Record<string, unknown> | undefined
      if (args) {
        const argsStr = Object.entries(args).map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`).join(', ')
        return `Calling ${tool}(${argsStr.slice(0, 80)}${argsStr.length > 80 ? '…' : ''})`
      }
      return `Calling ${tool}`
    }
    if (step.action === 'thinking') {
      const input = (step.details as Record<string, unknown>)?.input
      if (input) return `Thinking about: "${String(input).slice(0, 60)}…"`
      return 'Thinking...'
    }
    if (step.type === 'status') {
      return `${step.agent_name}: ${step.old_status} → ${step.new_status}`
    }
    return step.action || ''
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!input.trim() || loading) return

    const userMsg = input.trim()
    setInput('')
    setSteps([])
    setMessages(prev => [...prev, { role: 'user', content: userMsg, timestamp: new Date() }])

    try {
      let response: string
      if (onSendStream) {
        response = await onSendStream(userMsg, (step) => {
          const action = step.action || step.type || ''
          setSteps(prev => [
            ...prev.slice(-8),
            { action, detail: formatStep(step), icon: stepIcon(action) },
          ])
        })
      } else {
        response = await onSend(userMsg)
      }
      setSteps([])
      setMessages(prev => [...prev, { role: 'assistant', content: response, timestamp: new Date() }])
    } catch (err) {
      setSteps([])
      setMessages(prev => [
        ...prev,
        { role: 'assistant', content: `Error: ${err instanceof Error ? err.message : 'Unknown error'}`, timestamp: new Date() },
      ])
    }
  }

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] flex flex-col h-full">
      <div className="px-4 py-3 border-b border-[var(--border)] flex items-center gap-2">
        <span className="text-lg">💬</span>
        <h3 className="font-semibold">Chat with {agentName}</h3>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3 max-h-[400px]">
        {messages.length === 0 && (
          <p className="text-sm text-[var(--text-secondary)] text-center py-8">
            Send a message to {agentName} to get started.<br />
            Try: &quot;Create a forex trading company&quot;
          </p>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                msg.role === 'user'
                  ? 'bg-[var(--accent)] text-white'
                  : 'bg-[var(--bg-secondary)] text-[var(--text-primary)]'
              }`}
            >
              <pre className="whitespace-pre-wrap font-sans">{msg.content}</pre>
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-[var(--bg-secondary)] rounded-lg px-3 py-2 text-sm space-y-1 max-w-[85%]">
              {steps.length === 0 ? (
                <span className="animate-pulse">Thinking...</span>
              ) : (
                <>
                  {steps.map((s, i) => (
                    <div key={i} className={`text-xs font-mono flex items-start gap-1 ${i < steps.length - 1 ? 'text-[var(--text-secondary)]' : ''}`}>
                      <span className="shrink-0">{s.icon}</span>
                      <span className="break-all">{s.detail}</span>
                    </div>
                  ))}
                  <span className="animate-pulse text-xs">●●●</span>
                </>
              )}
            </div>
          </div>
        )}
      </div>

      <form onSubmit={handleSubmit} className="p-3 border-t border-[var(--border)] flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={`Message ${agentName}...`}
          className="flex-1 bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--accent)]"
          disabled={loading}
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          className="px-4 py-2 bg-[var(--accent)] text-white rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </div>
  )
}
