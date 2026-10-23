import { ChartFrame, type TableView } from './ChartFrame.tsx'
import { linearScale } from './scales.ts'
import { StatusBadge, type BadgeStatus } from './StatusBadge.tsx'

/** One figure of a step, e.g. `{ column: 'Rows', value: '62 rows' }`; an empty value is skipped beside the bar. */
export interface StepFact {
  column: string
  value: string
}

export interface TimelineStep {
  key: string
  label: string
  status: BadgeStatus
  /** Seconds from the start of the run; null while the step has not started. */
  startSeconds: number | null
  /** Seconds from the start of the run; null while the step runs. */
  endSeconds: number | null
  facts?: StepFact[]
  /** A line under the step, such as the reason a step failed. */
  detail?: string | null
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

function factColumns(steps: TimelineStep[]): string[] {
  const columns: string[] = []
  for (const step of steps) {
    for (const fact of step.facts ?? []) if (!columns.includes(fact.column)) columns.push(fact.column)
  }
  return columns
}

function factsLine(step: TimelineStep): string {
  return (step.facts ?? [])
    .map((fact) => fact.value)
    .filter((value) => value !== '')
    .join(' · ')
}

function barTitle(step: TimelineStep): string {
  const range = step.endSeconds === null ? `from ${seconds(step.startSeconds)} s` : `${seconds(step.startSeconds)} s to ${seconds(step.endSeconds)} s`
  return `${step.label}: ${step.status.toLowerCase()}, ${range}`
}

/** One row per step on a shared time axis, each with its status as an icon and a word. */
export function TimelineBars({ title = 'Refresh timeline', steps, nowSeconds }: TimelineBarsProps) {
  const total = Math.max(
    nowSeconds,
    ...steps.map((step) => step.endSeconds ?? step.startSeconds ?? 0),
    0.1,
  )
  const scale = linearScale([0, total], [0, STRIP_WIDTH])
  const extra = factColumns(steps)
  const hasDetail = steps.some((step) => step.detail !== undefined && step.detail !== null && step.detail !== '')
  const view: TableView = {
    columns: ['Step', 'Status', 'Start (s)', 'End (s)', ...extra, ...(hasDetail ? ['Detail'] : [])],
    rows: steps.map((step) => [
      step.label,
      step.status.toLowerCase(),
      seconds(step.startSeconds),
      seconds(step.endSeconds),
      ...extra.map((column) => step.facts?.find((fact) => fact.column === column)?.value ?? ''),
      ...(hasDetail ? [step.detail ?? ''] : []),
    ]),
  }
  return (
    <ChartFrame title={title} table={view}>
      <ol className={extra.length > 0 ? 'timeline timeline-facts-on' : 'timeline'}>
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
                  >
                    <title>{barTitle(step)}</title>
                  </rect>
                ) : null}
              </svg>
              {extra.length > 0 ? <span className="timeline-facts num">{factsLine(step)}</span> : null}
              {step.detail ? <span className="timeline-detail">{step.detail}</span> : null}
            </li>
          )
        })}
      </ol>
    </ChartFrame>
  )
}
