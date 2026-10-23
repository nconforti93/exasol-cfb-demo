import { Waterfall } from '../charts/Waterfall.tsx'
import { formatCalls } from '../format/format.ts'
import { useTimeline } from '../state/timeline.tsx'
import { RequestLines } from './RequestLines.tsx'
import { requestOfFetch, waterfallRows } from './requests.ts'

/**
 * The requests of the newest refresh run, next to its per-step calls. They come with the app's one timeline
 * poll, so the requests of a fetch step appear as soon as the step commits, while the run goes on.
 */
export function RefreshRequests() {
  const { timeline } = useTimeline()
  const run = timeline?.run ?? null
  if (run === null) return <p className="inspector-muted">No refresh run yet.</p>
  const requests = (timeline?.fetches ?? []).map(requestOfFetch)
  const rows = waterfallRows(requests)
  const steps = run.steps.filter((step) => step.calls !== null)
  return (
    <div className="inspector-refresh">
      <h4 className="inspector-query-title">{`Run ${run.run_id} · REFRESH_WEEK#${run.run_id}`}</h4>
      <p className="inspector-muted num">{`${run.run_status.toLowerCase()} · ${requests.length} ${requests.length === 1 ? 'request' : 'requests'} so far`}</p>
      {steps.length > 0 && (
        <ul className="inspector-facts" aria-label="Calls per step">
          {steps.map((step, index) => (
            <li key={`${index}-${step.step_no}`} className="num">{`${step.step_name}: ${formatCalls(step.calls ?? 0)}`}</li>
          ))}
        </ul>
      )}
      {requests.length > 0 && <RequestLines lines={requests} label="Requests of the run" />}
      {rows.length > 0 && <Waterfall title="Requests of the run" rows={rows} />}
    </div>
  )
}
