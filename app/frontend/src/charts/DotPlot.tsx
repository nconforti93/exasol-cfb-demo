import { ChartFrame, type TableView } from './ChartFrame.tsx'
import { linearScale, niceTicks } from './scales.ts'

export interface DotPoint {
  id: string
  x: number
  y: number
  label?: string
}

interface DotPlotProps {
  title: string
  points: DotPoint[]
  xLabel: string
  yLabel: string
  table?: TableView
  size?: number
}

const MARGIN = { top: 12, right: 28, bottom: 44, left: 56 }
const DOT_RADIUS = 5

function percent(value: number): string {
  return `${Number((value * 100).toFixed(1))} %`
}

/** One series of dots on a diagonal reference line, one axis pair, no legend: the title names the series. */
export function DotPlot({ title, points, xLabel, yLabel, table, size = 360 }: DotPlotProps) {
  const ticks = niceTicks(1)
  const x = linearScale([0, 1], [MARGIN.left, size - MARGIN.right])
  const y = linearScale([0, 1], [size - MARGIN.bottom, MARGIN.top])
  const view: TableView = table ?? {
    columns: ['Point', xLabel, yLabel],
    rows: points.map((point) => [point.label ?? point.id, percent(point.x), percent(point.y)]),
  }
  return (
    <ChartFrame title={title} table={view}>
      <svg className="dot-plot" role="img" aria-label={title} viewBox={`0 0 ${size} ${size}`} width="100%" style={{ maxWidth: size }}>
        <g className="axis-x">
          {ticks.map((tick) => (
            <g key={tick}>
              <line className="grid" x1={x(tick)} x2={x(tick)} y1={y(0)} y2={y(1)} />
              <text className="tick" x={x(tick)} y={y(0) + 18} textAnchor="middle">
                {percent(tick)}
              </text>
            </g>
          ))}
          <text className="axis-label" x={(x(0) + x(1)) / 2} y={size - 6} textAnchor="middle">
            {xLabel}
          </text>
        </g>
        <g className="axis-y">
          {ticks.map((tick) => (
            <g key={tick}>
              <line className="grid" x1={x(0)} x2={x(1)} y1={y(tick)} y2={y(tick)} />
              <text className="tick" x={x(0) - 8} y={y(tick) + 4} textAnchor="end">
                {percent(tick)}
              </text>
            </g>
          ))}
          <text
            className="axis-label"
            transform={`translate(14 ${(y(0) + y(1)) / 2}) rotate(-90)`}
            textAnchor="middle"
          >
            {yLabel}
          </text>
        </g>
        <line className="reference" x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} />
        {points.map((point) => (
          <circle key={point.id} className="dot" cx={x(point.x)} cy={y(point.y)} r={DOT_RADIUS}>
            <title>{point.label ?? point.id}</title>
          </circle>
        ))}
      </svg>
    </ChartFrame>
  )
}
