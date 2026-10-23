import { useCallback } from 'react'

import { useApi } from '../api/context.tsx'
import type { QueryLogEntry } from '../api/types.ts'
import { formatCalls, formatInteger } from '../format/format.ts'
import { WarningIcon } from '../shell/icons.tsx'
import { useLifetimeSignal } from '../refresh/useLifetimeSignal.ts'
import { CopySql } from './CopySql.tsx'
import { PlanView } from './PlanView.tsx'
import { RequestLines } from './RequestLines.tsx'
import { clockTime, rowsLabel } from './requests.ts'
import { useExplain } from './useExplain.ts'
import { RequestWaterfall } from './Waterfall.tsx'

function metrics(entry: QueryLogEntry): string {
  const parts = [`${formatInteger(entry.ms)} ms`]
  if (entry.row_count !== null) parts.push(rowsLabel(entry.row_count))
  parts.push(formatCalls(entry.calls_spent))
  return parts.join(' · ')
}

function EntryDetail({ entry }: { entry: QueryLogEntry }) {
  const api = useApi()
  const signalOf = useLifetimeSignal()
  const request = useCallback((signal?: AbortSignal) => api.inspectorExplain(entry.entry_id, signal === undefined ? undefined : { signal }), [api, entry.entry_id])
  const plan = useExplain(request, signalOf)
  return (
    <div className="inspector-detail">
      <pre className="inspector-sql">{entry.sql}</pre>
      <div className="inspector-actions">
        <CopySql sql={entry.sql} />
        <button type="button" className="shell-button shell-button-small" disabled={plan.loading} onClick={plan.run}>
          Show plan · 0 calls
        </button>
      </div>
      {plan.error !== null && (
        <p className="banner banner-warning" role="alert">
          <WarningIcon />
          <span>{plan.error}</span>
        </p>
      )}
      {plan.explain !== null && <PlanView explain={plan.explain} />}
      {entry.requests.length > 0 && <RequestLines lines={entry.requests} label="Requests of this statement" />}
      <RequestWaterfall requests={entry.requests} />
    </div>
  )
}

interface QueryLogProps {
  entries: QueryLogEntry[]
  selectedId: string | null
  onSelect(entryId: string | null): void
}

/** This session's statements, newest first; selecting one shows its exact SQL, its plan on demand and its requests. */
export function QueryLog({ entries, selectedId, onSelect }: QueryLogProps) {
  if (entries.length === 0) return <p className="inspector-muted">No statements yet: open a page.</p>
  return (
    <ul className="inspector-log" aria-label="Query log">
      {entries.map((entry) => {
        const open = entry.entry_id === selectedId
        return (
          <li key={entry.entry_id} className="inspector-entry">
            <button type="button" className="inspector-entry-head" aria-expanded={open} onClick={() => onSelect(open ? null : entry.entry_id)}>
              <span className="inspector-entry-id num">{entry.entry_id}</span>
              <span className={`inspector-principal inspector-principal-${entry.principal}`}>{`${entry.principal} · ${entry.user}`}</span>
              {entry.page !== null && <span className="inspector-page">{entry.page}</span>}
              <span className="inspector-metrics num">{metrics(entry)}</span>
              <span className="inspector-muted num">{clockTime(entry.started_at)}</span>
            </button>
            {entry.error !== null && (
              <p className="inspector-error">
                <WarningIcon />
                <span>{entry.error}</span>
              </p>
            )}
            {open && <EntryDetail entry={entry} />}
          </li>
        )
      })}
    </ul>
  )
}
