import { useCallback, useEffect, useState } from 'react'

import { isAbortError, isApiError, type ApiError } from '../api/client.ts'
import { useApi } from '../api/context.tsx'
import type { ConsoleQuery, ConsoleResponse, ConsoleRunResponse, ExplainResponse } from '../api/types.ts'
import { useLifetimeSignal } from '../refresh/useLifetimeSignal.ts'
import { WarningIcon } from '../shell/icons.tsx'
import { useShellData } from '../shell/ShellData.tsx'
import { useOutage } from '../state/outage.tsx'
import { SpendButton } from '../spend/SpendButton.tsx'
import { SpendNotice, SpendReceipt } from '../spend/SpendNotice.tsx'
import { CopySql } from './CopySql.tsx'
import { PlanView } from './PlanView.tsx'
import { RequestLines } from './RequestLines.tsx'
import { rowsLabel } from './requests.ts'
import { useExplain } from './useExplain.ts'
import { RequestWaterfall } from './Waterfall.tsx'

const RESULT_ROWS_SHOWN = 10

function liveCalls(explain: ExplainResponse): number {
  return explain.spend.source === 'LIVE' ? explain.spend.live_calls : 0
}

function cell(value: unknown): string {
  if (value === null || value === undefined) return ''
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

function ResultTable({ result }: { result: ConsoleRunResponse }) {
  if (result.columns.length === 0) return null
  const shown = result.rows.slice(0, RESULT_ROWS_SHOWN)
  return (
    <div className="inspector-result-scroll">
      <table className="chart-table" aria-label="Result">
        <thead>
          <tr>
            {result.columns.map((column) => (
              <th key={column} scope="col">{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, index) => (
            <tr key={index}>
              {row.map((value, position) => (
                <td key={position} className="num">{cell(value)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {result.row_count > shown.length && <p className="inspector-muted">{`showing ${shown.length} of ${result.row_count} rows`}</p>}
    </div>
  )
}

function RunResult({ result }: { result: ConsoleRunResponse }) {
  return (
    <div className="inspector-run-result">
      <p className="num">{rowsLabel(result.row_count)}</p>
      <SpendReceipt calls={result.calls_spent} source={result.source} cached={false} remaining={null} />
      {!result.fetch_log_ok && (
        <p className="inspector-refused">
          <WarningIcon />
          <span>The fetch log could not be written; the ledger entry stands.</span>
        </p>
      )}
      <ResultTable result={result} />
      {result.requests.length > 0 && <RequestLines lines={result.requests} label="Requests of this run" />}
      <RequestWaterfall requests={result.requests} />
    </div>
  )
}

function QueryCard({ query, onRan }: { query: ConsoleQuery; onRan(): void }) {
  const api = useApi()
  const shell = useShellData()
  const { report } = useOutage()
  const signalOf = useLifetimeSignal()
  const request = useCallback((signal?: AbortSignal) => api.consoleExplain(query.id, signal === undefined ? undefined : { signal }), [api, query.id])
  const plan = useExplain(request, signalOf)
    const [running, setRunning] = useState(false)
  const [result, setResult] = useState<ConsoleRunResponse | null>(null)
  const [failure, setFailure] = useState<ApiError | null>(null)

  const explain = plan.explain
  const refused = explain?.refused ?? null
  const canRun = explain !== null && refused === null

  async function run(planned: ExplainResponse) {
    const signal = signalOf()
    setRunning(true)
    setFailure(null)
    try {
      const answer = await api.consoleRun(query.id, { max_calls: liveCalls(planned) }, { signal })
      if (signal.aborted) return
      setResult(answer)
    } catch (error) {
      if (isAbortError(error) || signal.aborted) return
      report(error)
      setResult(null)
      if (isApiError(error)) setFailure(error)
    }
    setRunning(false)
    shell.refreshQuota(true)
    shell.refreshConfig()
    onRan()
  }

  return (
    <div className="inspector-query" role="group" aria-label={query.title}>
      <h4 className="inspector-query-title">{query.title}</h4>
      <p className="inspector-muted">{query.summary}</p>
      <pre className="inspector-sql">{query.sql}</pre>
      <div className="inspector-actions">
        <CopySql sql={query.sql} />
        <button type="button" className="shell-button shell-button-small" disabled={plan.loading} onClick={plan.run}>
          Explain · 0 calls
        </button>
        {explain === null ? (
          <button type="button" className="shell-button shell-button-small" disabled>
            Run
          </button>
        ) : (
          <SpendButton
            action="Run"
            plan={explain.spend}
            busy={running}
            block={canRun || running ? null : { kind: 'disabled', text: 'refused at plan time' }}
            confirm
            onSpend={() => void run(explain)}
          />
        )}
      </div>
      {plan.error !== null && (
        <p className="banner banner-warning" role="alert">
          <WarningIcon />
          <span>{plan.error}</span>
        </p>
      )}
      {explain !== null && <PlanView explain={explain} />}
      {failure !== null && <SpendNotice error={failure} />}
      {result !== null && <RunResult result={result} />}
    </div>
  )
}

/** The four curated queries over the virtual schema, explained and run as the reader principal; no free text. */
export function Console({ onRan }: { onRan(): void }) {
  const api = useApi()
  const { report } = useOutage()
  const [consoleInfo, setConsole] = useState<ConsoleResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    api.console({ signal: controller.signal }).then(
      (answer) => {
        setConsole(answer)
        setError(null)
      },
      (failure: unknown) => {
        if (isAbortError(failure)) return
        report(failure)
        setError(isApiError(failure) ? failure.message : 'The console could not be loaded.')
      },
    )
    return () => controller.abort()
  }, [api, report])

  if (error !== null) {
    return (
      <p className="banner banner-warning" role="alert">
        <WarningIcon />
        <span>{error}</span>
      </p>
    )
  }
  if (consoleInfo === null) return <p className="inspector-muted">Loading the queries…</p>
  return (
    <div className="inspector-console">
      <p className="inspector-principal-line">
        <span className="inspector-principal inspector-principal-reader">{`${consoleInfo.principal} · ${consoleInfo.user}`}</span>
      </p>
      <p className="inspector-muted">{consoleInfo.help}</p>
      {consoleInfo.queries.map((query) => (
        <QueryCard key={query.id} query={query} onRan={onRan} />
      ))}
    </div>
  )
}
