import { useCallback, useRef, useState } from 'react'

import { isAbortError, isApiError, type ApiError } from '../api/client.ts'
import { useApi } from '../api/context.tsx'
import type {
  ActionResponse,
  CurrentPoint,
  RefreshPlanResponse,
  RefreshTarget,
  SeasonType,
  TimelineRun,
} from '../api/types.ts'
import { SpendButton, type SpendBlock } from '../spend/SpendButton.tsx'
import { SpendNotice } from '../spend/SpendNotice.tsx'
import { InfoIcon, SyncIcon, WarningIcon } from '../shell/icons.tsx'
import { useShellData } from '../shell/ShellData.tsx'
import { useOutage } from '../state/outage.tsx'
import { useAppState } from '../state/react.tsx'
import { useTimeline } from '../state/timeline.tsx'
import { usePolling } from '../state/usePolling.ts'
import { DEV_LIVE_REASON, PlanSummary } from './PlanSummary.tsx'
import { Timeline } from './Timeline.tsx'
import { useLifetimeSignal } from './useLifetimeSignal.ts'
import './refresh.css'

const ACTION_POLL_MS = 1000
const MAX_WEEK = 99
const FOLLOWABLE_REFUSALS: ReadonlySet<string> = new Set(['refresh_locked', 'refresh_running'])

interface TargetFields {
  season: string
  week: string
  seasonType: SeasonType
}

function proposedTarget(current: CurrentPoint | null): TargetFields | null {
  if (current === null) return null
  return {
    season: String(current.as_of_season),
    week: String((current.as_of_week ?? 0) + 1),
    seasonType: current.as_of_season_type,
  }
}

function parseTarget(fields: TargetFields | null): RefreshTarget | null {
  if (fields === null) return null
  const season = Number(fields.season)
  const week = Number(fields.week)
  if (!Number.isInteger(season) || season < 1 || !Number.isInteger(week) || week < 1 || week > MAX_WEEK) return null
  return { season, week, season_type: fields.seasonType }
}

function blockOf(plan: RefreshPlanResponse, devLive: boolean, actionRunning: boolean): SpendBlock | null {
  if (actionRunning) return { kind: 'busy', text: 'a refresh is running' }
  if (devLive || plan.dev_blocks_live) return { kind: 'disabled', text: 'development server' }
  if (!plan.can_run) return { kind: 'disabled', text: 'see the plan' }
  return null
}

function summaryOf(run: TimelineRun | null): string {
  if (run === null) return 'no refresh run yet'
  if (run.run_status === 'RUNNING') {
    const step = run.steps.find((candidate) => candidate.step_status === 'RUNNING')
    return `run ${run.run_id} is running${step === undefined ? '' : ` · step ${step.step_name}`}`
  }
  return `run ${run.run_id} ${run.run_status.toLowerCase()}`
}

function doneText(action: ActionResponse): string {
  return action.run_id === null ? 'The refresh is done.' : `Refresh of run ${action.run_id} is done.`
}

function ActionOutcome({ action }: { action: ActionResponse | null }) {
  if (action === null || action.state === 'RUNNING') {
    const runId = action?.run_id ?? null
    return (
      <p className="refresh-action" role="status">
        <SyncIcon />
        <span>{runId === null ? 'Refresh starting…' : `Refresh running as run ${runId}`}</span>
      </p>
    )
  }
  const failed = action.state === 'FAILED'
  return (
    <div className={failed ? 'refresh-outcome refresh-outcome-failed' : 'refresh-outcome'} role="status">
      <p className="refresh-outcome-head">
        {failed ? <WarningIcon /> : <InfoIcon />}
        <span>{failed ? (action.error ?? 'The refresh failed.') : doneText(action)}</span>
      </p>
      {action.point_message !== null && <p className="refresh-outcome-point">{action.point_message}</p>}
      {action.point_before !== null && (
        <p className="refresh-outcome-points num">
          {action.point_after === null || action.point_after.label === action.point_before.label
            ? action.point_before.label
            : `${action.point_before.label} → ${action.point_after.label}`}
        </p>
      )}
      {action.calls_spent !== null && <p className="refresh-outcome-calls num">{`${action.calls_spent} ${action.calls_spent === 1 ? 'call' : 'calls'} spent`}</p>}
    </div>
  )
}

function RefusalNotice({ error, onFollow }: { error: ApiError; onFollow(): void }) {
  if (!FOLLOWABLE_REFUSALS.has(error.code)) return <SpendNotice error={error} />
  return (
    <div className="banner banner-warning" role="alert">
      <WarningIcon />
      <span className="banner-text">{`${error.message.replace(/[.\s]+$/, '')}. The refresh was not started and no call was made.`}</span>
      <button type="button" className="shell-button" onClick={onFollow}>
        Follow the running refresh
      </button>
    </div>
  )
}

/**
 * Beat 3: plan the refresh at 0 calls, confirm, run it in the background and follow its steps. The step timeline is the
 * app's single timeline poll (`useTimeline`), so a run started by `make refresh` or a SQL client shows here too.
 */
export function RefreshPanel() {
  const api = useApi()
  const outage = useOutage()
  const report = outage.report
  const config = useAppState((state) => state.config)
  const current = useAppState((state) => state.current)
  const { timeline, pollNow } = useTimeline()
  const shell = useShellData()

  const [toggled, setToggled] = useState<boolean | null>(null)
  const [openedFor, setOpenedFor] = useState<number | null>(null)
  const [edited, setEdited] = useState<Partial<TargetFields>>({})
  const [plan, setPlan] = useState<RefreshPlanResponse | null>(null)
  const [planError, setPlanError] = useState<string | null>(null)
  const [planning, setPlanning] = useState(false)
    const [starting, setStarting] = useState(false)
  const [refusal, setRefusal] = useState<ApiError | null>(null)
  const [actionId, setActionId] = useState<string | null>(null)
  const [action, setAction] = useState<ActionResponse | null>(null)
  const [actionLost, setActionLost] = useState(false)

  const signalOf = useLifetimeSignal()
  const finished = useRef<string | null>(null)

  const run = timeline?.run ?? null
  const runId = run?.run_id ?? null
  const runRunning = run?.run_status === 'RUNNING'
  if (runRunning && runId !== null && openedFor !== runId) {
    setOpenedFor(runId)
    setToggled(null)
  }

  const proposed = proposedTarget(current)
  const fields: TargetFields | null = proposed === null ? null : { ...proposed, ...edited }
  const target = parseTarget(fields)

  const operator = config?.operator ?? null
  const operatorOff = operator !== null && !operator.enabled
  const expanded = toggled ?? (operatorOff || openedFor !== null)
  const devLive = config !== null && config.dev && config.fetch_mode === 'LIVE'
  const actionRunning = actionId !== null && !actionLost && (action === null || action.state === 'RUNNING')

  const editField = useCallback((patch: Partial<TargetFields>) => {
    setEdited((previous) => ({ ...previous, ...patch }))
    setPlan(null)
    setPlanError(null)
    setRefusal(null)
  }, [])

  const finish = useCallback(
    (ended: ActionResponse) => {
      if (finished.current === ended.action_id) return
      finished.current = ended.action_id
      shell.refreshQuota(true)
      shell.refreshConfig()
      pollNow()
    },
    [shell, pollNow],
  )

  usePolling(
    async (signal) => {
      if (actionId === null) return
      try {
        const next = await api.action(actionId, { signal })
        if (signal.aborted) return
        setAction(next)
        if (next.state !== 'RUNNING') finish(next)
      } catch (failure) {
        if (isAbortError(failure)) return
        report(failure)
        if (isApiError(failure) && failure.kind === 'not_found') setActionLost(true)
      }
    },
    () => ACTION_POLL_MS,
    actionRunning,
  )

  async function planRefresh() {
    if (target === null) return
    setToggled(true)
    setPlanning(true)
    setPlanError(null)
    setRefusal(null)
    const signal = signalOf()
    try {
      const answer = await api.refreshPlan(target, { signal })
      if (!signal.aborted) setPlan(answer)
    } catch (failure) {
      if (signal.aborted || isAbortError(failure)) return
      report(failure)
      setPlan(null)
      setPlanError(isApiError(failure) ? failure.message : 'The plan failed.')
    } finally {
      if (!signal.aborted) setPlanning(false)
    }
  }

  async function startRefresh(planned: RefreshPlanResponse) {
    setStarting(true)
    setRefusal(null)
    const signal = signalOf()
    try {
      const started = await api.refreshRun({ ...planned.target, max_calls: planned.estimate.total_calls }, { signal })
      if (signal.aborted) return
      finished.current = null
      setAction(null)
      setActionLost(false)
      setActionId(started.action_id)
      pollNow()
    } catch (failure) {
      if (signal.aborted || isAbortError(failure)) return
      report(failure)
      if (isApiError(failure)) setRefusal(failure)
    } finally {
      if (!signal.aborted) setStarting(false)
    }
  }

  const block: SpendBlock | null = plan === null ? null : blockOf(plan, devLive, actionRunning)
  const operatorOn = operator?.enabled === true

  return (
    <section className="refresh-panel" aria-label="Refresh">
      <div className="refresh-bar">
        <h2 className="refresh-heading">Refresh</h2>
        <span className="refresh-summary num">{summaryOf(run)}</span>
        {operatorOn && (
          <button type="button" className="shell-button" disabled={target === null || planning || actionRunning} onClick={() => void planRefresh()}>
            Plan refresh
          </button>
        )}
        <button type="button" className="shell-button" aria-expanded={expanded} onClick={() => setToggled(!expanded)}>
          {expanded ? 'Hide details' : 'Show details'}
        </button>
      </div>
      {expanded && (
        <div className="refresh-body">
          {operator !== null && operatorOff && (
            <p className="refresh-reason">
              <InfoIcon />
              <span>{operator.reason ?? 'The refresh is not available.'}</span>
            </p>
          )}
          {operatorOn && fields !== null && (
            <div className="refresh-target" role="group" aria-label="Refresh target">
              <label>
                <span className="picker-label">Season</span>
                <input type="number" className="refresh-input num" value={fields.season} onChange={(event) => editField({ season: event.target.value })} />
              </label>
              <label>
                <span className="picker-label">Week</span>
                <input type="number" className="refresh-input num" min={1} max={MAX_WEEK} value={fields.week} onChange={(event) => editField({ week: event.target.value })} />
              </label>
              <label>
                <span className="picker-label">Season type</span>
                <select value={fields.seasonType} onChange={(event) => editField({ seasonType: event.target.value as SeasonType })}>
                  <option value="regular">regular</option>
                  <option value="postseason">postseason</option>
                </select>
              </label>
              {target === null && <span className="refresh-invalid">a season, and a week from 1 to {MAX_WEEK}</span>}
            </div>
          )}
          {operatorOn && devLive && (
            <p className="refresh-reason">
              <WarningIcon />
              <span>{DEV_LIVE_REASON}</span>
            </p>
          )}
          {planError !== null && (
            <p className="banner banner-warning" role="alert">
              <WarningIcon />
              <span>{planError}</span>
            </p>
          )}
          {plan !== null && operatorOn && (
            <>
              <PlanSummary plan={plan} devLive={devLive} />
              <div className="refresh-run">
                <SpendButton
                  action="Run refresh"
                  plan={{ source: plan.mode, live_calls: plan.estimate.total_calls }}
                  busy={starting}
                  block={block}
                  confirm={plan.needs_confirmation}
                  onSpend={() => void startRefresh(plan)}
                />
              </div>
            </>
          )}
          {refusal !== null && <RefusalNotice error={refusal} onFollow={pollNow} />}
          {(action !== null || actionRunning) && <ActionOutcome action={action} />}
          {actionLost && <p className="refresh-reason">The app no longer knows this refresh; the timeline shows what the database recorded.</p>}
          {run !== null && <Timeline run={run} />}
        </div>
      )}
    </section>
  )
}
