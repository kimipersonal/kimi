'use client'

import { PixelOffice } from '@/components/pixel-office/PixelOffice'

export default function OfficePage() {
  return (
    <div className="max-w-7xl space-y-4">
      <h2 className="text-xl font-bold">Pixel Office</h2>
      <p className="text-sm text-[var(--text-secondary)]">
        Live view of all agents at work. Click any agent to view details.
      </p>
      <PixelOffice />
    </div>
  )
}
