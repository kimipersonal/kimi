'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import type { WSEvent } from '@/lib/api'

export function useWebSocket(url: string) {
  const [events, setEvents] = useState<WSEvent[]>([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<NodeJS.Timeout>()

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      console.log('WebSocket connected')
    }

    ws.onmessage = (e) => {
      try {
        const event: WSEvent = JSON.parse(e.data)
        setEvents(prev => [...prev.slice(-200), event]) // Keep last 200 events
      } catch (err) {
        console.error('Failed to parse WS message:', err)
      }
    }

    ws.onclose = () => {
      setConnected(false)
      // Reconnect after 3 seconds
      reconnectTimer.current = setTimeout(connect, 3000)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [url])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  const sendCommand = useCallback((command: string, payload?: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ command, ...payload }))
    }
  }, [])

  return { events, connected, sendCommand }
}
