import type { ReactNode } from 'react'

export interface ChipProps {
  children: ReactNode
  /** Makes the chip a toggle button, pressed or not. */
  selected?: boolean
  onClick?: () => void
  /** A count shown after the label. */
  count?: number
}

/** A pill: a toggle button when it has a `selected` state or a click handler, else a static label. */
export function Chip({ children, selected, onClick, count }: ChipProps) {
  const body = (
    <>
      <span>{children}</span>
      {count !== undefined && <span className="chip-count num">{count}</span>}
    </>
  )
  if (selected === undefined && onClick === undefined) return <span className="chip">{body}</span>
  return (
    <button type="button" className="chip chip-button" aria-pressed={selected ?? false} onClick={onClick}>
      {body}
    </button>
  )
}
