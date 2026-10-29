import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react'

import { isAbortError, isApiError, type ApiError } from '../api/client.ts'
import { useApi } from '../api/context.tsx'
import type { TimelineResponse } from '../api/types.ts'
import { useOutage } from './outage.tsx'
import { useAppActions } from './react.tsx'
import { usePolling } from './usePolling.ts'

export const TIMELINE_RUNNING_MS = 1000
export const TIMELINE_IDLE_MS = 5000

export interface TimelineState {
  /** The latest reading of the newest refresh run, with its fetches; null before the first answer. */
  timeline: TimelineResponse | null
  /** The failure of the latest poll, cleared by the next good one. */
  error: ApiError | null
  /** Polls at once, e.g. right after the app started a refresh. */
  pollNow(): void
}

const TimelineContext = createContext<TimelineState | null>(null)

function isRunning(timeline: TimelineResponse | null): boolean {
  return timeline?.run?.run_status === 'RUNNING'
}

/**
 * The one timeline poll of the app: every second while the newest refresh run is `RUNNING`, else every five
 * seconds. Each reading carries the follow key, which goes to the store (`applyFollow`) and so drives follow
 * mode. The refresh panel reads the same readings through `useTimeline`, instead of polling itself.
 */
export function TimelineProvider({ children }: { children: ReactNode }) {
  const api = useApi()
  const actions = useAppActions()
  const outage = useOutage()
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const latest = useRef<TimelineResponse | null>(null)
  const report = outage.report

  const poll = useCallback(
    async (signal: AbortSignal) => {
      try {
        const reading = await api.timeline({}, { signal })
        latest.current = reading
        setTimeline(reading)
        setError(null)
        actions.applyFollow(reading.follow)
      } catch (failure) {
        if (isAbortError(failure)) return
        report(failure)
        if (isApiError(failure)) setError(failure)
      }
    },
    [api, actions, report],
  )

  const polling = usePolling(poll, () => (isRunning(latest.current) ? TIMELINE_RUNNING_MS : TIMELINE_IDLE_MS))
  const pollNow = polling.pollNow

  const value = useMemo(() => ({ timeline, error, pollNow }), [timeline, error, pollNow])
  return <TimelineContext.Provider value={value}>{children}</TimelineContext.Provider>
}

export function useTimeline(): TimelineState {
  const state = useContext(TimelineContext)
  if (state === null) throw new Error('useTimeline needs a TimelineProvider')
  return state
}
