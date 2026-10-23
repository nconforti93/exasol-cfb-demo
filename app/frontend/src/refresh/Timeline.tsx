import { useEffect, useState } from 'react'

import type { TimelineRun } from '../api/types.ts'
import { StatusBadge } from '../charts/StatusBadge.tsx'
import { TimelineBars } from '../charts/TimelineBars.tsx'
import { chartModel, elapsedSeconds } from './timelineModel.ts'

const TICK_MS = 1000

function useClock(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => setNow(Date.now()), TICK_MS)
    return () => clearInterval(timer)
  }, [active])
  return now
}

/** The run's step timeline: bars with each step's status, seconds, slices, rows and calls, and a live elapsed counter. */
export function Timeline({ run }: { run: TimelineRun }) {
  const running = run.run_status === 'RUNNING' || run.steps.some((step) => step.step_status === 'RUNNING')
  const now = useClock(running)
  const model = chartModel(run, now)
  const runElapsed = run.run_status === 'RUNNING' ? elapsedSeconds(run.started_at, now) : null
  return (
    <section className="timeline-section" aria-label="Refresh run">
      <h3 className="timeline-heading">
        Run {run.run_id}
        {run.run_args === null ? null : <span className="timeline-args num">{run.run_args}</span>}
        <StatusBadge status={run.run_status} />
        {runElapsed === null ? null : <span className="timeline-elapsed num">{`run: ${Math.floor(runElapsed)} s elapsed`}</span>}
      </h3>
      <TimelineBars title="Refresh timeline" steps={model.steps} nowSeconds={model.nowSeconds} />
    </section>
  )
}
