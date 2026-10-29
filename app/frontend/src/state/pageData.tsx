import { useEffect, useMemo, useRef, useState } from 'react'

import { ApiError, isAbortError, isApiError, type RequestOptions } from '../api/client.ts'
import { useApi, useApiClient } from '../api/context.tsx'
import type { Api } from '../api/endpoints.ts'
import { useOutage } from './outage.tsx'
import { useAppState } from './react.tsx'
import { effectivePoint, type PointId } from './store.ts'

/** The point every page loads. Only call it under the shell's page host, which renders pages once the point is known. */
export function usePoint(): PointId {
  const season = useAppState((state) => effectivePoint(state)?.season ?? null)
  const weekSeq = useAppState((state) => effectivePoint(state)?.week_seq ?? null)
  if (season === null || weekSeq === null) throw new Error('usePoint needs a known point')
  return useMemo(() => ({ season, week_seq: weekSeq }), [season, weekSeq])
}

export interface PageFetchContext {
  api: Api
  point: PointId
  /** The signal of this page load, aborted on a point or page change; pass it as `options` to every read. */
  signal: AbortSignal
  options: RequestOptions
}

export interface PageData<T> {
  status: 'loading' | 'ready' | 'error'
  data: T | null
  error: ApiError | null
  /** Loads again, e.g. from a page's own retry button. */
  retry(): void
}

function unexpectedFailure(): ApiError {
  return new ApiError({ status: 0, kind: 'server', code: 'unknown', message: 'The page failed to load.' })
}

interface Settled<T> {
  key: string
  data: T | null
  error: ApiError | null
}

/**
 * Loads a page's data at the effective point, one page load at a time: starting it aborts the previous page
 * load (`ApiClient.beginPageLoad`), and so does a point change, a reload of every page (follow mode, the outage
 * retry) or unmounting. A page makes one call to this hook and fetches everything it needs in `fetcher`.
 * `deps` must be JSON-serializable values that should also trigger a load. A database outage raises the
 * shell's outage banner; the page itself then holds an `error`.
 */
export function usePageData<T>(
  fetcher: (context: PageFetchContext) => Promise<T>,
  deps: readonly unknown[] = [],
): PageData<T> {
  const api = useApi()
  const client = useApiClient()
  const outage = useOutage()
  const point = usePoint()
  const reloadToken = useAppState((state) => state.reloadToken)
  const [attempt, setAttempt] = useState(0)
  const [settled, setSettled] = useState<Settled<T> | null>(null)
  const fetcherRef = useRef(fetcher)
  const report = outage.report

  useEffect(() => {
    fetcherRef.current = fetcher
  })

  const key = `${point.season}|${point.week_seq}|${reloadToken}|${attempt}|${JSON.stringify(deps)}`

  useEffect(() => {
    const load = client.beginPageLoad()
    let current = true
    const context: PageFetchContext = { api, point, signal: load.signal, options: { signal: load.signal } }
    fetcherRef.current(context).then(
      (data) => {
        if (current) setSettled({ key, data, error: null })
      },
      (failure: unknown) => {
        if (!current || isAbortError(failure)) return
        report(failure)
        setSettled({ key, data: null, error: isApiError(failure) ? failure : unexpectedFailure() })
      },
    )
    return () => {
      current = false
      load.abort()
    }
  }, [api, client, point, key, report])

  const retry = useMemo(() => () => setAttempt((count) => count + 1), [])
  if (settled === null || settled.key !== key) return { status: 'loading', data: null, error: null, retry }
  if (settled.error !== null) return { status: 'error', data: null, error: settled.error, retry }
  return { status: 'ready', data: settled.data, error: null, retry }
}
