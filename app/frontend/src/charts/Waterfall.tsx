import { ChartFrame, type TableView } from './ChartFrame.tsx'
import { linearScale, niceTicks } from './scales.ts'

export type WaterfallSource = 'LIVE' | 'REPLAY' | 'STORED'

export interface WaterfallRow {
  id: string
  label: string
  source: WaterfallSource
  /** Milliseconds from the first request of the statement or run. */
  startMs: number
  durationMs: number
  status: number | null
}

interface WaterfallProps {
  title?: string
  rows: WaterfallRow[]
}

const STRIP_WIDTH = 360
const STRIP_HEIGHT = 14
const MIN_BAR = 2
const SOURCE_ORDER: readonly WaterfallSource[] = ['LIVE', 'REPLAY', 'STORED']

function directLabel(row: WaterfallRow): string {
  return row.source === 'STORED' ? 'stored' : `${Math.round(row.durationMs)} ms`
}

/** Requests as bars from their start over their milliseconds on one time axis; overlapping bars ran in parallel. */
export function Waterfall({ title = 'Requests', rows }: WaterfallProps) {
  const total = Math.max(...rows.map((row) => row.startMs + row.durationMs), 1)
  const ticks = niceTicks(total)
  const axisMax = ticks[ticks.length - 1] ?? total
  const scale = linearScale([0, axisMax], [0, STRIP_WIDTH])
  const sources = SOURCE_ORDER.filter((source) => rows.some((row) => row.source === source))
  const view: TableView = {
    columns: ['Request', 'Source', 'Start (ms)', 'Duration (ms)', 'Status'],
    rows: rows.map((row) => [row.label, row.source, Math.round(row.startMs), Math.round(row.durationMs), row.status ?? '']),
  }
  const replayed = rows.some((row) => row.source === 'REPLAY')
  return (
    <ChartFrame
      title={title}
      table={view}
      note={replayed ? 'Replayed rows carry recorded timings.' : undefined}
    >
      {sources.length >= 2 ? (
        <ul className="legend">
          {sources.map((source) => (
            <li key={source}>
              <span className={`swatch source-${source.toLowerCase()}`} aria-hidden="true" />
              <span>{source}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <ol className="waterfall">
        {rows.map((row) => (
          <li key={row.id} className="waterfall-row">
            <span className="waterfall-label">{row.label}</span>
            <svg
              className="waterfall-strip"
              aria-hidden="true"
              width="100%"
              viewBox={`0 0 ${STRIP_WIDTH} ${STRIP_HEIGHT}`}
              preserveAspectRatio="none"
            >
              {row.durationMs > 0 ? (
                <rect
                  className={`waterfall-bar source-${row.source.toLowerCase()}`}
                  x={scale(row.startMs)}
                  y={1}
                  width={Math.max(MIN_BAR, scale(row.startMs + row.durationMs) - scale(row.startMs))}
                  height={STRIP_HEIGHT - 2}
                  rx={4}
                />
              ) : null}
            </svg>
            <span className="waterfall-value num">{directLabel(row)}</span>
          </li>
        ))}
      </ol>
      <svg className="waterfall-axis axis-x" aria-hidden="true" width="100%" viewBox={`0 0 ${STRIP_WIDTH} 20`} preserveAspectRatio="none">
        {ticks.map((tick) => (
          <text key={tick} className="tick" x={scale(tick)} y={14} textAnchor={tick === 0 ? 'start' : 'middle'}>
            {`${tick} ms`}
          </text>
        ))}
      </svg>
    </ChartFrame>
  )
}
