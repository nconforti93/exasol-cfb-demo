import { useCallback, useEffect, useState } from 'react'

import { isAbortError, isApiError } from '../api/client.ts'
import { useApi } from '../api/context.tsx'
import type { QueryLogEntry } from '../api/types.ts'
import { WarningIcon } from '../shell/icons.tsx'
import { useOutage } from '../state/outage.tsx'
import { useAppActions, useAppState } from '../state/react.tsx'
import { Console } from './Console.tsx'
import { QueryLog } from './QueryLog.tsx'
import { RefreshRequests } from './RefreshRequests.tsx'
import './inspector.css'

type View = 'queries' | 'console' | 'refresh'

const VIEW_LABELS: Record<View, string> = {
  queries: 'Queries',
  console: 'Console',
  refresh: 'Refresh requests',
}

/**
 * Beat 2: the SQL behind the page, the plan the virtual schema made for it, and the HTTP requests that plan became.
 * The drawer's open state and selected entry live in `store.inspector`.
 */
export function Inspector() {
  const api = useApi()
  const actions = useAppActions()
  const { report } = useOutage()
  const entryId = useAppState((state) => state.inspector.entryId)
  const page = useAppState((state) => state.page)
  const reloadToken = useAppState((state) => state.reloadToken)
  const consoleCap = useAppState((state) => state.config?.console ?? null)

  const [view, setView] = useState<View>('queries')
  const [entries, setEntries] = useState<QueryLogEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loads, setLoads] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    api.inspector({ signal: controller.signal }).then(
      (answer) => {
        setEntries(answer.entries)
        setError(null)
      },
      (failure: unknown) => {
        if (isAbortError(failure)) return
        report(failure)
        setError(isApiError(failure) ? failure.message : 'The query log could not be loaded.')
      },
    )
    return () => controller.abort()
  }, [api, report, page, reloadToken, loads])

  const reload = useCallback(() => setLoads((count) => count + 1), [])
  const select = useCallback((id: string | null) => actions.setInspector({ entryId: id }), [actions])

  const consoleOn = consoleCap?.enabled === true
  const shownView: View = view === 'console' && !consoleOn ? 'queries' : view
  const views: View[] = consoleOn ? ['queries', 'console', 'refresh'] : ['queries', 'refresh']

  return (
    <div className="inspector">
      <div role="tablist" aria-label="Inspector views" className="inspector-tabs">
        {views.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            id={`inspector-tab-${name}`}
            aria-selected={shownView === name}
            aria-controls="inspector-panel"
            className="shell-button"
            onClick={() => setView(name)}
          >
            {VIEW_LABELS[name]}
          </button>
        ))}
      </div>
      {consoleCap !== null && !consoleCap.enabled && consoleCap.reason !== null && <p className="inspector-muted">{consoleCap.reason}</p>}
      <div role="tabpanel" id="inspector-panel" aria-labelledby={`inspector-tab-${shownView}`} className="inspector-panel">
        {shownView === 'queries' && (
          <>
            <div className="inspector-actions">
              <button type="button" className="shell-button shell-button-small" onClick={reload}>
                Reload log
              </button>
            </div>
            {error !== null && (
              <p className="banner banner-warning" role="alert">
                <WarningIcon />
                <span>{error}</span>
              </p>
            )}
            {entries !== null && <QueryLog entries={entries} selectedId={entryId} onSelect={select} />}
          </>
        )}
        {shownView === 'console' && <Console onRan={reload} />}
        {shownView === 'refresh' && <RefreshRequests />}
      </div>
    </div>
  )
}
