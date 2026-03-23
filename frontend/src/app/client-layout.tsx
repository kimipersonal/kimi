'use client'

import { ReactNode } from 'react'
import { WSProvider, useWS } from '@/hooks/WSContext'
import { Sidebar } from '@/components/layout/Sidebar'
import { Header } from '@/components/layout/Header'

function InnerLayout({ children }: { children: ReactNode }) {
  const { connected } = useWS()
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header connected={connected} />
        <main className="flex-1 overflow-y-auto p-6">
          {children}
        </main>
      </div>
    </div>
  )
}

export function ClientLayout({ children }: { children: ReactNode }) {
  return (
    <WSProvider>
      <InnerLayout>{children}</InnerLayout>
    </WSProvider>
  )
}
