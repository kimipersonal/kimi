'use client'

import { createContext, useContext, ReactNode, useMemo } from 'react'
import { useWebSocket } from '@/hooks/useWebSocket'
import type { WSEvent } from '@/lib/api'

function getWsUrl(): string {
  if (typeof window === 'undefined') return 'ws://localhost:8000'
  const host = window.location.host
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  // Cloudflare tunnel or any HTTPS domain: WS goes through same origin (nginx proxies /ws/)
  if (host.endsWith('.trycloudflare.com')) {
    return `${proto}://${host}`
  }
  // devtunnels pattern: {id}-{port}.euw.devtunnels.ms → rewrite to backend port
  const devtunnel = host.match(/^(.+)-(\d+)(\..*\.devtunnels\.ms)$/)
  if (devtunnel) {
    return `${proto}://${devtunnel[1]}-8000${devtunnel[3]}`
  }
  // localhost: connect to backend directly
  if (host.startsWith('localhost') || host.startsWith('127.0.0.1')) {
    return `${proto}://localhost:8000`
  }
  return `${proto}://${host}`
}
const WS_URL = process.env.NEXT_PUBLIC_WS_URL || getWsUrl()

interface WSContextValue {
  events: WSEvent[]
  connected: boolean
  lastEvent: WSEvent | null
  sendCommand: (command: string, payload?: Record<string, unknown>) => void
}

const WSContext = createContext<WSContextValue>({
  events: [],
  connected: false,
  lastEvent: null,
  sendCommand: () => {},
})

export function WSProvider({ children }: { children: ReactNode }) {
  const ws = useWebSocket(`${WS_URL}/ws/dashboard`)
  const lastEvent = useMemo(() => ws.events.length > 0 ? ws.events[ws.events.length - 1] : null, [ws.events])
  const value = useMemo(() => ({ ...ws, lastEvent }), [ws, lastEvent])
  return <WSContext.Provider value={value}>{children}</WSContext.Provider>
}

export function useWS() {
  return useContext(WSContext)
}
