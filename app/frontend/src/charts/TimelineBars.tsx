import { ChartFrame, type TableView } from './ChartFrame.tsx'
import { linearScale } from './scales.ts'
import { StatusBadge, type BadgeStatus } from './StatusBadge.tsx'

export interface TimelineStep {
  key: string
  label: string
  status: BadgeStatus
  /** Seconds from the start of the run; null while the step has not started. */
  startSeconds: number | null
  /** Seconds from the start of the run; null while the step runs. */
  endSeconds: number | null
}

interface TimelineBarsProps {
  title?: string
  steps: TimelineStep[]
  /** The run's elapsed seconds, where a running step ends. */
  nowSeconds: number
}

const STRIP_WIDTH = 360
const STRIP_HEIGHT = 14
const MIN_BAR = 2

function seconds(value: number | null): string {
  return value === null ? '' : value.toFixed(1)
}

/** One row per step on a shared time axis, each with its status as an icon and a word. */
export function TimelineBars({ title = 'Refresh timeline', steps, nowSeconds }: TimelineBarsProps) {
  const total = Math.max(
    nowSeconds,
    ...steps.map((step) => step.endSeconds ?? step.startSeconds ?? 0),
    0.1,
  )
  const scale = linearScale([0, total], [0, STRIP_WIDTH])
  const view: TableView = {
    columns: ['Step', 'Status', 'Start (s)', 'End (s)'],
    rows: steps.map((step) => [step.label, step.status.toLowerCase(), seconds(step.startSeconds), seconds(step.endSeconds)]),
  }
  return (
    <ChartFrame title={title} table={view}>
      <ol className="timeline">
        {steps.map((step) => {
          const started = step.startSeconds !== null
          const end = step.endSeconds ?? nowSeconds
          const x = started ? scale(step.startSeconds ?? 0) : 0
          const width = started ? Math.max(MIN_BAR, scale(end) - x) : 0
          return (
            <li key={step.key} className="timeline-row">
              <span className="timeline-label">{step.label}</span>
              <StatusBadge status={step.status} />
              <svg
                className="timeline-strip"
                aria-hidden="true"
                width="100%"
                viewBox={`0 0 ${STRIP_WIDTH} ${STRIP_HEIGHT}`}
                preserveAspectRatio="none"
              >
                <line className="grid" x1={0} x2={STRIP_WIDTH} y1={STRIP_HEIGHT / 2} y2={STRIP_HEIGHT / 2} />
                {started ? (
                  <rect
                    className={`timeline-bar bar-${step.status.toLowerCase()}`}
                    x={x}
                    y={1}
                    width={width}
                    height={STRIP_HEIGHT - 2}
                    rx={4}
                  />
                ) : null}
              </svg>
            </li>
          )
        })}
      </ol>
    </ChartFrame>
  )
}
