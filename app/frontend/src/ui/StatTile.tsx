import type { ReactNode } from 'react'

import type { Move } from '../api/types.ts'
import { MoveBadge } from '../charts/MoveBadge.tsx'

export interface StatTileProps {
  label: string
  /** The big number, already formatted. */
  value: ReactNode
  /** The movement since the last point, as the odds board shows it (icon, word and points). */
  delta?: { move: Move; delta: number | null }
  detail?: ReactNode
}

/** A label, a KPI number and, optionally, how it moved. */
export function StatTile({ label, value, delta, detail }: StatTileProps) {
  return (
    <div className="stat-tile">
      <p className="stat-label">{label}</p>
      <p className="stat-value num">{value}</p>
      {(delta !== undefined || detail !== undefined) && (
        <p className="stat-detail">
          {delta !== undefined && <MoveBadge move={delta.move} delta={delta.delta} />}
          {detail}
        </p>
      )}
    </div>
  )
}
