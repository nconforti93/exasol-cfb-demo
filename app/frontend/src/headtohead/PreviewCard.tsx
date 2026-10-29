import { useEffect, useRef, useState } from 'react'

import { ApiError, isAbortError, isApiError } from '../api/client.ts'
import { useApi } from '../api/context.tsx'
import type { PreviewPlanResponse, PreviewResponse, SpendSourceKind, Team } from '../api/types.ts'
import { useShellData } from '../shell/ShellData.tsx'
import { WarningIcon } from '../shell/icons.tsx'
import { SpendButton, type SpendBlock } from '../spend/SpendButton.tsx'
import { SpendNotice, SpendReceipt } from '../spend/SpendNotice.tsx'
import { formatUtcTime } from '../spend/spendFormat.ts'
import { useOutage } from '../state/outage.tsx'
import { useAppState } from '../state/react.tsx'
import { PreviewResult } from './PreviewResult.tsx'
import './headtohead.css'

export interface PreviewCardProps {
  team: Team
  opponent: Team
}

type Running = 'live' | 'replay' | 'cached'

interface SpendRequest {
  force: boolean
  maxCalls: number
  running: Running
}

const RUNNING_TEXT: Record<Running, string> = {
  live: 'fetching from CFBD…',
  replay: 'replaying a recorded answer…',
  cached: 'reading the cached preview…',
}

function runningOf(source: SpendSourceKind): Running {
  return source === 'REPLAY' ? 'replay' : 'live'
}

const BUSY_TEXT = 'live previews resume when it ends'

function unexpected(): ApiError {
  return new ApiError({ status: 0, kind: 'server', code: 'unknown', message: 'The request failed.' })
}

function blockOf(plan: PreviewPlanResponse): SpendBlock | null {
  if (plan.disabled_reason !== null) return { kind: 'disabled', text: plan.disabled_reason }
  if (plan.busy_reason !== null) return { kind: 'busy', text: `${plan.busy_reason}: ${BUSY_TEXT}` }
  return null
}

function basisText(plan: PreviewPlanResponse, team: Team, opponent: Team): string {
  const pair = `${team.school} and ${opponent.school}`
  switch (plan.game_basis) {
    case 'next':
      return `Previews the next meeting of ${pair}, chosen by the server.`
    case 'latest':
      return `${pair} do not meet again this season. Previews their latest meeting, chosen by the server.`
    case 'none':
      return `${pair} have no game to preview.`
  }
}

interface PreviewControlsProps {
  plan: PreviewPlanResponse
  /** A preview is already on the page, so the control offers "Fetch again". */
  showing: boolean
  fetching: boolean
  onSpend(request: SpendRequest): void
}

function PreviewControls({ plan, showing, fetching, onSpend }: PreviewControlsProps) {
  if (plan.game_basis === 'none') return null
  const calls = plan.plan.live_calls
  const block = blockOf(plan)
  const running = runningOf(plan.plan.source)
  const again = (
    <SpendButton
      action="Fetch again"
      plan={plan.plan}
      busy={fetching}
      block={block}
      onSpend={() => onSpend({ force: true, maxCalls: calls, running })}
    />
  )
  if (showing) return <div className="preview-controls">{again}</div>
  if (plan.cached !== null) {
    return (
      <div className="preview-controls">
        <p className="preview-cached">
          {`cached ${formatUtcTime(plan.cached.cached_at)} · 0 calls`}
          {plan.cached.req_ms === null ? '' : `, fetched ${plan.cached.source} in ${plan.cached.req_ms} ms`}
        </p>
        <SpendButton
          action="Show cached preview"
          plan={{ source: 'CACHED', live_calls: 0 }}
          busy={fetching}
          onSpend={() => onSpend({ force: false, maxCalls: 0, running: 'cached' })}
        />
        {again}
      </div>
    )
  }
  return (
    <div className="preview-controls">
      <SpendButton
        action="Fetch preview"
        plan={plan.plan}
        busy={fetching}
        block={block}
        onSpend={() => onSpend({ force: false, maxCalls: calls, running })}
      />
    </div>
  )
}

/**
 * The live moment of the head-to-head page. It reads the free plan when it opens, and only a click makes the
 * request: the browser names the pair and the server picks the game. Its state belongs to the pair, so the
 * parent keys it by the pair.
 */
export function PreviewCard({ team, opponent }: PreviewCardProps) {
  const api = useApi()
  const shell = useShellData()
  const report = useOutage().report
  const reloadToken = useAppState((state) => state.reloadToken)
  const [plan, setPlan] = useState<PreviewPlanResponse | null>(null)
  const [planError, setPlanError] = useState<ApiError | null>(null)
  const [planLoads, setPlanLoads] = useState(0)
  const [shown, setShown] = useState<PreviewResponse | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [running, setRunning] = useState<Running | null>(null)
  const flight = useRef<AbortController | null>(null)
  const { refreshQuota, refreshConfig, quota } = shell
  const fetching = running !== null

  useEffect(() => {
    const controller = new AbortController()
    api.previewPlan({ team: team.team_id, opponent: opponent.team_id }, { signal: controller.signal }).then(
      (response) => {
        setPlan(response)
        setPlanError(null)
      },
      (failure: unknown) => {
        if (isAbortError(failure)) return
        report(failure)
        setPlanError(isApiError(failure) ? failure : unexpected())
      },
    )
    return () => controller.abort()
  }, [api, report, team.team_id, opponent.team_id, reloadToken, planLoads])

  useEffect(() => () => flight.current?.abort(), [])

  async function spend({ force, maxCalls, running: kind }: SpendRequest) {
    if (flight.current !== null) return
    const controller = new AbortController()
    flight.current = controller
    setRunning(kind)
    setError(null)
    try {
      const response = await api.preview(
        { team: team.team_id, opponent: opponent.team_id, max_calls: maxCalls, force },
        { signal: controller.signal },
      )
      setShown(response)
    } catch (failure) {
      if (!isAbortError(failure)) setError(isApiError(failure) ? failure : unexpected())
    } finally {
      flight.current = null
      setRunning(null)
      refreshQuota(true)
      refreshConfig()
      setPlanLoads((count) => count + 1)
    }
  }

  return (
    <section className="preview-card" aria-label="Game preview">
      <h3>Game preview from CFBD</h3>
      {planError !== null && (
        <p className="banner banner-warning" role="alert">
          <WarningIcon />
          <span className="banner-text">Could not plan the preview: {planError.message}</span>
          <button type="button" className="spend-button" onClick={() => setPlanLoads((count) => count + 1)}>
            Try again
          </button>
        </p>
      )}
      {plan === null && planError === null && <p className="preview-basis">Checking the cost (0 calls)…</p>}
      {plan !== null && (
        <>
          <p className="preview-basis">{basisText(plan, team, opponent)}</p>
          <PreviewControls plan={plan} showing={shown !== null} fetching={fetching} onSpend={spend} />
        </>
      )}
      {running !== null && (
        <p className="preview-fetching" role="status">
          {RUNNING_TEXT[running]}
        </p>
      )}
      {error !== null && <SpendNotice error={error} />}
      {shown !== null && (
        <>
          <SpendReceipt
            calls={shown.calls_spent}
            source={shown.source}
            cached={shown.cached}
            cachedAt={shown.fetched_at}
            remaining={quota?.remaining_calls ?? null}
          />
          {!shown.fetch_log_ok && (
            <p className="banner banner-warning" role="status">
              <WarningIcon />
              <span>The fetch-log row for this request could not be written. The ledger entry is kept.</span>
            </p>
          )}
          <PreviewResult preview={shown} />
        </>
      )}
    </section>
  )
}
