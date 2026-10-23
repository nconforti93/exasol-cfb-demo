import { formatDeltaPoints } from '../format/format.ts'
import type { Move } from '../api/types.ts'
import { ArrowDownIcon, ArrowUpIcon } from './icons.tsx'

interface MoveBadgeProps {
  move: Move
  /** The change in the probability, as a fraction (0.023 reads "+2.3"). */
  delta: number | null
}

/** Odds movement: an arrow icon, the word and the signed change in percentage points, never color alone. */
export function MoveBadge({ move, delta }: MoveBadgeProps) {
  if (move === 'SAME') return null
  if (move === 'NEW') return <span className="badge-new">new</span>
  const word = move === 'UP' ? 'up' : 'down'
  const change = delta === null ? '' : formatDeltaPoints(delta)
  const name = change === '' ? word : `${word} ${change} percentage points`
  return (
    <span className={`move move-${word}`} role="img" aria-label={name}>
      {move === 'UP' ? <ArrowUpIcon /> : <ArrowDownIcon />}
      <span className="move-word">{word}</span>
      {change === '' ? null : <span className="num">{change}</span>}
    </span>
  )
}
