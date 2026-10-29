import { useEffect, useRef, useState } from 'react'

import { isAbortError, isApiError } from '../api/client.ts'
import { useApi } from '../api/context.tsx'
import type { Api } from '../api/endpoints.ts'
import { useOutage } from '../state/outage.tsx'

export interface SecondaryRead<T> {
  status: 'idle' | 'loading' | 'ready' | 'error'
  data: T | null
  message: string | null
}

interface Settled<T> {
  key: string
  data: T | null
  message: string | null
}

const IDLE: SecondaryRead<never> = { status: 'idle', data: null, message: null }

/**
 * A read that follows a user action rather than a page load (a what-if, a rooting guide): it starts after
 * `delayMs` of quiet, aborts the one it supersedes, and tells `onRunChanged` when the server answers 409
 * `whatif_run_changed`. A null `key` means there is nothing to read.
 */
export function useSecondaryRead<T>(
  key: string | null,
  delayMs: number,
  read: (api: Api, signal: AbortSignal) => Promise<T>,
  onRunChanged?: () => void,
): SecondaryRead<T> {
  const api = useApi()
  const { report } = useOutage()
  const [settled, setSettled] = useState<Settled<T> | null>(null)
  const readRef = useRef(read)
  const changedRef = useRef(onRunChanged)

  useEffect(() => {
    readRef.current = read
    changedRef.current = onRunChanged
  })

  useEffect(() => {
    if (key === null) return
    const controller = new AbortController()
    const start = () => {
      readRef.current(api, controller.signal).then(
        (data) => {
          if (!controller.signal.aborted) setSettled({ key, data, message: null })
        },
        (failure: unknown) => {
          if (controller.signal.aborted || isAbortError(failure)) return
          if (isApiError(failure) && failure.code === 'whatif_run_changed') {
            changedRef.current?.()
            return
          }
          report(failure)
          setSettled({ key, data: null, message: isApiError(failure) ? failure.message : 'The request failed.' })
        },
      )
    }
    if (delayMs <= 0) {
      start()
      return () => controller.abort()
    }
    const timer = setTimeout(start, delayMs)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [api, key, delayMs, report])

  if (key === null) return IDLE
  if (settled === null || settled.key !== key) return { status: 'loading', data: null, message: null }
  if (settled.message !== null) return { status: 'error', data: null, message: settled.message }
  return { status: 'ready', data: settled.data, message: null }
}
