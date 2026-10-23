import { useCallback, useEffect, useMemo, useRef } from 'react'

export interface Polling {
  /** Polls at once (or right after the poll in flight), then goes on with the usual delay. */
  pollNow(): void
}

const FIRST_POLL_DELAY_MS = 0

/**
 * Runs `task` at mount and then `getDelay()` ms after each poll ends, so polls never overlap. One timer
 * exists per mounted effect: `StrictMode`'s second effect finds the first one cleared. A failing task does
 * not stop the polling, and unmounting aborts the poll in flight.
 */
export function usePolling(
  task: (signal: AbortSignal) => Promise<unknown>,
  getDelay: () => number,
  enabled = true,
): Polling {
  const taskRef = useRef(task)
  const delayRef = useRef(getDelay)
  const pollNowRef = useRef<() => void>(() => {})

  useEffect(() => {
    taskRef.current = task
    delayRef.current = getDelay
  })

  useEffect(() => {
    if (!enabled) {
      pollNowRef.current = () => {}
      return
    }
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | null = null
    let running = false
    let requested = false
    let stopped = false

    const schedule = (ms: number) => {
      if (timer !== null) clearTimeout(timer)
      timer = setTimeout(run, ms)
    }

    async function run() {
      timer = null
      running = true
      try {
        await taskRef.current(controller.signal)
      } catch {
        // A failed poll is reported by its task; the next one runs as usual.
      }
      running = false
      if (stopped) return
      const again = requested
      requested = false
      schedule(again ? FIRST_POLL_DELAY_MS : delayRef.current())
    }

    pollNowRef.current = () => {
      if (stopped) return
      if (running) requested = true
      else schedule(FIRST_POLL_DELAY_MS)
    }

    schedule(FIRST_POLL_DELAY_MS)
    return () => {
      stopped = true
      if (timer !== null) clearTimeout(timer)
      controller.abort()
      pollNowRef.current = () => {}
    }
  }, [enabled])

  const pollNow = useCallback(() => pollNowRef.current(), [])
  return useMemo(() => ({ pollNow }), [pollNow])
}
