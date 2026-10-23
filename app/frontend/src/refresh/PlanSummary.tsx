import type { LedgerConfig, QuotaResponse, RefreshPlanResponse } from '../api/types.ts'
import { formatCalls, formatConfirmation, formatInteger, formatSpendLabel } from '../format/format.ts'
import { InfoIcon, OkIcon, WarningIcon } from '../shell/icons.tsx'

export const DEV_LIVE_REASON = 'live refresh needs `make app-serve`'

function modeLabel(plan: RefreshPlanResponse): string {
  const calls = plan.estimate.total_calls
  if (plan.mode === 'REPLAY') return formatSpendLabel({ source: 'REPLAY', live_calls: 0 })
  return plan.plus_retries ? formatConfirmation(calls) : formatSpendLabel({ source: 'LIVE', live_calls: calls })
}

function quotaLine(quota: QuotaResponse): string {
  const left = quota.remaining_calls === null ? 'quota read' : `quota: ${formatInteger(quota.remaining_calls)} calls left`
  if (quota.refresh_spendable === null) return left
  return `${left}, ${formatInteger(quota.refresh_spendable)} spendable for a refresh`
}

function ledgerLine(ledger: LedgerConfig): string {
  if (!ledger.on) return 'ledger off'
  const name = ledger.plan === null ? 'ledger' : `ledger ${ledger.plan}`
  if (ledger.plan_room === null || ledger.plan_cap === null) return name
  return `${name}: ${formatInteger(ledger.plan_room)} of ${formatInteger(ledger.plan_cap)} calls free`
}

function targetLabel(plan: RefreshPlanResponse): string {
  const { season, week, season_type: type } = plan.target
  return `${season} week ${week} (${type})`
}

/** What a refresh would do, from the 0-call plan: mode and cost, quota, ledger room, other runs, and whether the point moves. */
export function PlanSummary({ plan, devLive }: { plan: RefreshPlanResponse; devLive: boolean }) {
  const tables = Object.entries(plan.estimate.per_table)
  const week = plan.week
  const blockedByDev = devLive || plan.dev_blocks_live
  const reasons = blockedByDev && !plan.reasons.some((reason) => reason.includes('make app-serve')) ? [...plan.reasons, DEV_LIVE_REASON] : plan.reasons
  return (
    <div className="refresh-plan" aria-label="Refresh plan" role="group">
      <h3 className="refresh-plan-title">Plan for {targetLabel(plan)}</h3>
      <p className="refresh-plan-cost">
        <strong>{modeLabel(plan)}</strong>
        {plan.mode === 'LIVE' ? <span className="refresh-plan-total num">{` · ${formatCalls(plan.estimate.total_calls)} estimated`}</span> : null}
      </p>
      {tables.length > 0 && (
        <ul className="refresh-plan-tables">
          {tables.map(([table, calls]) => (
            <li key={table} className="num">{`${table}: ${formatCalls(calls)}`}</li>
          ))}
        </ul>
      )}
      <ul className="refresh-plan-facts">
        {plan.quota !== null && <li>{quotaLine(plan.quota)}</li>}
        <li>{ledgerLine(plan.ledger)}</li>
        {plan.running.map((run) => (
          <li key={run.run_id} className="refresh-plan-running">
            <WarningIcon />
            <span>{`run ${run.run_id} (${run.run_kind}) is running`}</span>
          </li>
        ))}
      </ul>
      <p className="refresh-plan-week">
        {week.point_will_move ? <OkIcon /> : <InfoIcon />}
        <span>{week.message}</span>
      </p>
      <p className="refresh-plan-week-counts num">
        {`${formatInteger(week.games)} games in storage: ${formatInteger(week.completed)} completed, ${formatInteger(week.not_kicked_off)} not kicked off`}
      </p>
      {!plan.can_run && reasons.length > 0 && (
        <ul className="refresh-plan-reasons" aria-label="Why the refresh cannot run">
          {reasons.map((reason) => (
            <li key={reason}>
              <WarningIcon />
              <span>{reason}</span>
            </li>
          ))}
        </ul>
      )}
      {plan.can_run && blockedByDev && (
        <p className="refresh-plan-reasons">
          <WarningIcon />
          <span>{DEV_LIVE_REASON}</span>
        </p>
      )}
    </div>
  )
}
