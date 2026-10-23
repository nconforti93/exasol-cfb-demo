import type { CSSProperties } from 'react'

import './series.css'
import { linearScale } from './scales.ts'
import { useWidth } from './useWidth.ts'

export interface SeriesPoint {
  x: number
  /** A gap in the line when null. */
  y: number | null
}

export interface Series {
  id: string
  name: string
  points: SeriesPoint[]
  shape: 'circle' | 'diamond'
  /** The series line and marks: the team accent, or a neutral ink. */
  tone: 'accent' | 'neutral'
  /** A direct label at the last mark, such as "#6". */
  endLabel?: string
}

export interface SeriesChartProps {
  label: string
  series: Series[]
  /** The value range, bottom of the axis first unless `invert` flips it. */
  yDomain: readonly [number, number]
  yTicks: number[]
  formatY: (value: number) => string
  xTicks: Array<{ x: number; label: string }>
  /** Draws the smallest values at the top: a rank of 1 is the best. */
  invert?: boolean
  accent?: string
  height?: number
}

const FALLBACK_WIDTH = 480
const MARGIN = { top: 16, right: 76, bottom: 32, left: 52 }
const MARK_RADIUS = 5
const DIAMOND = MARK_RADIUS + 1

function pathOf(points: Array<{ cx: number; cy: number | null }>): string {
  let path = ''
  let open = false
  for (const point of points) {
    if (point.cy === null) {
      open = false
      continue
    }
    path += `${open ? 'L' : 'M'}${point.cx.toFixed(1)} ${point.cy.toFixed(1)}`
    open = true
  }
  return path
}

/** A small line chart drawn at its real pixel width: gridlines, a marked point per x, direct end labels. */
export function SeriesChart({ label, series, yDomain, yTicks, formatY, xTicks, invert = false, accent, height = 220 }: SeriesChartProps) {
  const [ref, width] = useWidth<HTMLDivElement>(FALLBACK_WIDTH)
  const xs = series.flatMap((one) => one.points.map((point) => point.x))
  const xMin = Math.min(...xs)
  const xMax = Math.max(...xs)
  const left = MARGIN.left
  const right = width - MARGIN.right
  const top = MARGIN.top
  const bottom = height - MARGIN.bottom
  const scaleX = xMax === xMin ? () => (left + right) / 2 : linearScale([xMin, xMax], [left, right])
  const scaleY = linearScale(yDomain, invert ? [top, bottom] : [bottom, top])
  const style = accent === undefined ? undefined : ({ '--series-accent': accent } as CSSProperties)

  return (
    <div className="series-chart" ref={ref} style={style}>
      <svg role="img" aria-label={label} width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
        {yTicks.map((tick) => (
          <g key={tick}>
            <line className="series-grid" x1={left} x2={right} y1={scaleY(tick)} y2={scaleY(tick)} />
            <text className="series-tick" x={left - 10} y={scaleY(tick)} textAnchor="end" dominantBaseline="middle">
              {formatY(tick)}
            </text>
          </g>
        ))}
        {xTicks.map((tick) => (
          <text key={tick.x} className="series-tick" x={scaleX(tick.x)} y={bottom + 22} textAnchor="middle">
            {tick.label}
          </text>
        ))}
        {series.map((one) => {
          const points = one.points.map((point) => ({ cx: scaleX(point.x), cy: point.y === null ? null : scaleY(point.y), y: point.y }))
          const last = [...points].reverse().find((point) => point.cy !== null)
          return (
            <g key={one.id} className={`series series-${one.tone}`} data-series={one.id}>
              <path className="series-line" d={pathOf(points)} />
              {points.map((point, index) =>
                point.cy === null ? null : one.shape === 'circle' ? (
                  <circle key={index} className="series-mark" cx={point.cx} cy={point.cy} r={MARK_RADIUS} />
                ) : (
                  <rect
                    key={index}
                    className="series-mark"
                    x={point.cx - DIAMOND / 2}
                    y={point.cy - DIAMOND / 2}
                    width={DIAMOND}
                    height={DIAMOND}
                    transform={`rotate(45 ${point.cx} ${point.cy})`}
                  />
                ),
              )}
              {one.endLabel !== undefined && last !== undefined && last.cy !== null && (
                <text className="series-end-label" x={last.cx + MARK_RADIUS + 6} y={last.cy} dominantBaseline="middle">
                  {one.endLabel}
                </text>
              )}
            </g>
          )
        })}
      </svg>
    </div>
  )
}
