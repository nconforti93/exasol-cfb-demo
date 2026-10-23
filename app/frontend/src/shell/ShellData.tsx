import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import { isAbortError, isApiError, type ApiError } from '../api/client.ts'
import { useApi } from '../api/context.tsx'
import type { PointsResponse, QuotaResponse } from '../api/types.ts'
import { OutageProvider, useOutage } from '../state/outage.tsx'
import { useAppActions, useAppState } from '../state/react.tsx'
import { effectivePoint } from '../state/store.ts'
import { usePolling } from '../state/usePolling.ts'
import { TimelineProvider } from '../state/timeline.tsx'
import { TeamDirectoryProvider } from './TeamDirectory.tsx'

export const QUOTA_POLL_MS = 5 * 60 * 1000

export interface ShellData {
  /** The points of the picker's season (`GET /api/points`), the current point included. */
  points: PointsResponse | null
  /** Shows the points of another season in the picker. */
  selectSeason(season: number): void
  /** The latest quota reading; always null in replay mode, which never reads it. */
  quota: QuotaResponse | null
  /** The latest quota read failed (the previous reading, if any, stays in `quota`). */
  quotaFailed: boolean
  /** Reads the quota now; `fresh` asks the server for a new `/info` reading instead of its five-minute cache. */
  refreshQuota(fresh?: boolean): void
  /** Loads `GET /api/config` again: call it after an action that changed the ledger or the fetch mode. */
  refreshConfig(): void
  /** The failure of the configuration or points load, if any. */
  loadError: ApiError | null
}

const ShellDataContext = createContext<ShellData | null>(null)

function offeredKeyId(state: { follow: { offeredKey: { as_of_season: number; as_of_week_seq: number } | null } }): string {
  const key = state.follow.offeredKey
  return key === null ? '' : `${key.as_of_season}/${key.as_of_week_seq}`
}

/** Loads what the header needs: the configuration, the points of the picker and, live only, the quota. */
export function ShellDataProvider({ children }: { children: ReactNode }) {
  const api = useApi()
  const actions = useAppActions()
  const outage = useOutage()
  const report = outage.report
  const reloadToken = useAppState((state) => state.reloadToken)
  const openedSeason = useAppState((state) => effectivePoint(state)?.season ?? null)
  const fetchMode = useAppState((state) => state.config?.fetch_mode ?? null)
  const offered = useAppState(offeredKeyId)

  const [points, setPoints] = useState<PointsResponse | null>(null)
  const [pickedSeason, setPickedSeason] = useState<number | null>(null)
  const [quota, setQuota] = useState<QuotaResponse | null>(null)
  const [quotaFailed, setQuotaFailed] = useState(false)
  const [configLoads, setConfigLoads] = useState(0)
  const [configError, setConfigError] = useState<ApiError | null>(null)
  const [pointsError, setPointsError] = useState<ApiError | null>(null)
  const mounted = useRef(false)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const season = pickedSeason ?? openedSeason

  useEffect(() => {
    const controller = new AbortController()
    api.config({ signal: controller.signal }).then(
      (config) => {
        actions.setConfig(config)
        setConfigError(null)
      },
      (failure: unknown) => {
        if (isAbortError(failure)) return
        report(failure)
        if (isApiError(failure)) setConfigError(failure)
      },
    )
    return () => controller.abort()
  }, [api, actions, report, reloadToken, configLoads])

  useEffect(() => {
    const controller = new AbortController()
    api.points(season ?? undefined, { signal: controller.signal }).then(
      (response) => {
        setPoints(response)
        setPointsError(null)
        actions.setCurrent(response.current)
      },
      (failure: unknown) => {
        if (isAbortError(failure)) return
        report(failure)
        if (isApiError(failure)) setPointsError(failure)
      },
    )
    return () => controller.abort()
  }, [api, actions, report, season, reloadToken, offered])

  const readQuota = useCallback(
    async (fresh: boolean, signal?: AbortSignal) => {
      try {
        const reading = await api.quota(fresh ? { fresh: true } : {}, signal === undefined ? undefined : { signal })
        if (!mounted.current || signal?.aborted === true) return
        setQuota(reading)
        setQuotaFailed(false)
      } catch (failure) {
        if (isAbortError(failure) || !mounted.current) return
        report(failure)
        setQuotaFailed(true)
      }
    },
    [api, report],
  )

  usePolling((signal) => readQuota(false, signal), () => QUOTA_POLL_MS, fetchMode === 'LIVE')

  const refreshQuota = useCallback(
    (fresh = false) => {
      if (fetchMode === 'LIVE') void readQuota(fresh)
    },
    [fetchMode, readQuota],
  )
  const refreshConfig = useCallback(() => setConfigLoads((count) => count + 1), [])
  const selectSeason = useCallback((next: number) => setPickedSeason(next), [])

  const value = useMemo<ShellData>(
    () => ({
      points,
      selectSeason,
      quota: fetchMode === 'LIVE' ? quota : null,
      quotaFailed,
      refreshQuota,
      refreshConfig,
      loadError: configError ?? pointsError,
    }),
    [points, selectSeason, quota, fetchMode, quotaFailed, refreshQuota, refreshConfig, configError, pointsError],
  )
  return <ShellDataContext.Provider value={value}>{children}</ShellDataContext.Provider>
}

export function useShellData(): ShellData {
  const data = useContext(ShellDataContext)
  if (data === null) throw new Error('useShellData needs a ShellDataProvider')
  return data
}

/** The providers of the shell's data: the outage state, the header's data, the team list and the one timeline poll. */
export function ShellProviders({ children }: { children: ReactNode }) {
  return (
    <OutageProvider>
      <ShellDataProvider>
        <TeamDirectoryProvider>
          <TimelineProvider>{children}</TimelineProvider>
        </TeamDirectoryProvider>
      </ShellDataProvider>
    </OutageProvider>
  )
}
