
import { Card, Note, type CardSpan } from '../ui/index.ts'

const GHOST_BARS = [34, 52, 44, 66, 58, 80]

export interface EmptyPanelProps {
  title: string
  eyebrow?: string
  span?: CardSpan
  /** Why the panel is empty, in words. */
  reason: string | null
  fallback: string
}

/** The designed empty state of an outlook panel: a faint ghost of its chart, then why it is empty. Never an error. */
export function EmptyPanel({ title, eyebrow, span, reason, fallback }: EmptyPanelProps) {
  return (
    <Card title={title} eyebrow={eyebrow} span={span} className="empty-panel">
      <svg className="empty-ghost" viewBox="0 0 120 48" aria-hidden="true" focusable="false">
        {GHOST_BARS.map((height, index) => (
          <rect key={index} x={4 + index * 19} y={46 - (height * 40) / 100} width={12} height={(height * 40) / 100} rx={3} />
        ))}
      </svg>
      <Note>{reason ?? fallback}</Note>
    </Card>
  )
}
