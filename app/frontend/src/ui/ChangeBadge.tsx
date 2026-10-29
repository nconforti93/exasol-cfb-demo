import { ArrowDownIcon, ArrowUpIcon } from '../charts/icons.tsx'

export interface ChangeBadgeProps {
  direction: 'up' | 'down'
  /** The change as written, such as "+4.5" or "12 places". */
  text: string
}

/** A movement as an arrow icon, the word and the amount, never color alone. */
export function ChangeBadge({ direction, text }: ChangeBadgeProps) {
  return (
    <span className={`move move-${direction}`} role="img" aria-label={`${direction} ${text}`}>
      {direction === 'up' ? <ArrowUpIcon /> : <ArrowDownIcon />}
      <span className="move-word">{direction}</span>
      <span className="num">{text}</span>
    </span>
  )
}
