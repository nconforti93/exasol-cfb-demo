import type { TimelineRun, TimelineStep as ApiStep } from '../api/types.ts'
import type { StepFact, TimelineStep as ChartStep } from '../charts/TimelineBars.tsx'
import { formatCalls, formatInteger } from '../format/format.ts'

export const FACT_COLUMNS = ['Seconds', 'Slices', 'Rows', 'Calls'] as const

const MS_PER_SECOND = 1000

export function parseTime(text: string | null): number | null {
  if (text === null) return null
  const ms = Date.parse(text)
  return Number.isNaN(ms) ? null : ms
}

export function elapsedSeconds(startedAt: string | null, nowMs: number): number | null {
  const start = parseTime(startedAt)
  return start === null ? null : Math.max(0, (nowMs - start) / MS_PER_SECOND)
}

function count(value: number | null, singular: string, plural: string): string {
  if (value === null) return ''
  return value === 1 ? `1 ${singular}` : `${formatInteger(value)} ${plural}`
}

function secondsFact(step: ApiStep, elapsed: number | null): string {
  if (step.step_status === 'RUNNING') return elapsed === null ? '' : `${Math.floor(elapsed)} s elapsed`
  return step.seconds === null ? '' : `${step.seconds.toFixed(1)} s`
}

/** The four figures a step reports; a running step reports its elapsed seconds instead of its duration. */
export function stepFacts(step: ApiStep, elapsed: number | null): StepFact[] {
  return [
    { column: FACT_COLUMNS[0], value: secondsFact(step, elapsed) },
    { column: FACT_COLUMNS[1], value: count(step.slices, 'slice', 'slices') },
    { column: FACT_COLUMNS[2], value: count(step.rows_loaded, 'row', 'rows') },
    { column: FACT_COLUMNS[3], value: step.calls === null ? '' : formatCalls(step.calls) },
  ]
}

function offsetSeconds(time: string | null, runStartMs: number): number | null {
  const ms = parseTime(time)
  return ms === null ? null : Math.max(0, (ms - runStartMs) / MS_PER_SECOND)
}

function chartStep(step: ApiStep, index: number, runStartMs: number, nowMs: number): ChartStep {
  const started = step.step_status === 'PENDING' ? null : offsetSeconds(step.started_at, runStartMs)
  let end: number | null = null
  if (started !== null && step.step_status !== 'RUNNING') {
    end = step.seconds !== null ? started + step.seconds : offsetSeconds(step.ended_at, runStartMs)
  }
  return {
    key: `${index}-${step.step_no}`,
    label: step.step_name,
    status: step.step_status,
    startSeconds: started,
    endSeconds: end,
    facts: stepFacts(step, elapsedSeconds(step.started_at, nowMs)),
    detail: step.detail,
  }
}

export interface ChartModel {
  steps: ChartStep[]
  /** Seconds since the run began; frozen at the last step's end once the run is over. */
  nowSeconds: number
}

export function chartModel(run: TimelineRun, nowMs: number): ChartModel {
  const startMs = parseTime(run.started_at) ?? parseTime(run.steps.find((step) => step.started_at !== null)?.started_at ?? null) ?? nowMs
  const steps = run.steps.map((step, index) => chartStep(step, index, startMs, nowMs))
  const ends = steps.map((step) => step.endSeconds ?? step.startSeconds ?? 0)
  const nowSeconds = run.run_status === 'RUNNING' ? Math.max(0, (nowMs - startMs) / MS_PER_SECOND) : Math.max(0, ...ends)
  return { steps, nowSeconds }
}
