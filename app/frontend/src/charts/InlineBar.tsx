import { linearScale } from './scales.ts'

const END_RADIUS = 4

interface InlineBarProps {
  /** A fraction of `max`; the bar grows from the left baseline. */
  value: number
  max?: number
  label: string
  width?: number
  height?: number
}

/** A magnitude bar for a table cell: one sequential hue, a flat baseline end and a 4 px rounded data end. */
export function InlineBar({ value, max = 1, label, width = 120, height = 12 }: InlineBarProps) {
  const fraction = Math.min(1, Math.max(0, value / max))
  const fill = fraction === 0 ? 0 : Math.max(1, linearScale([0, 1], [0, width])(fraction))
  const radius = Math.min(END_RADIUS, fill / 2, height / 2)
  return (
    <svg
      className="inline-bar"
      role="img"
      aria-label={label}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
    >
      <rect className="bar-track" x={0} y={0} width={width} height={height} rx={radius} />
      {fill === 0 ? null : (
        <>
          <rect className="bar-fill" data-fraction={fraction} x={0} y={0} width={fill} height={height} rx={radius} />
          <rect className="bar-base" x={0} y={0} width={Math.min(fill, END_RADIUS)} height={height} />
        </>
      )}
    </svg>
  )
}
