import type { CSSProperties } from 'react'

export interface ProbBarProps {
  /** A probability, 0 to 1. */
  value: number
  label: string
}

/** A thin bar for one probability: a track, and the share on top, from the left. */
export function ProbBar({ value, label }: ProbBarProps) {
  const share = Math.min(1, Math.max(0, value))
  return (
    <span
      className="prob-bar"
      role="img"
      aria-label={label}
      style={{ '--prob-share': `${(share * 100).toFixed(1)}%` } as CSSProperties}
    />
  )
}
