import { useCallback, useState } from 'react'

import { isAbortError, isApiError } from '../api/client.ts'
import type { ExplainResponse } from '../api/types.ts'
import { useOutage } from '../state/outage.tsx'

export interface ExplainState {
  explain: ExplainResponse | null
  /** The failure of the explain request (a refusal comes back as an error answer too). */
  error: string | null
  loading: boolean
  run(): void
}

/** Runs one explain request on demand and keeps its answer or its failure. */
export function useExplain(request: (signal?: AbortSignal) => Promise<ExplainResponse>, signalOf: () => AbortSignal): ExplainState {
  const { report } = useOutage()
  const [explain, setExplain] = useState<ExplainResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const run = useCallback(() => {
    const signal = signalOf()
    setLoading(true)
    setError(null)
    request(signal).then(
      (answer) => {
        if (signal.aborted) return
        setExplain(answer)
        setLoading(false)
      },
      (failure: unknown) => {
        if (isAbortError(failure) || signal.aborted) return
        report(failure)
        setExplain(null)
        setError(isApiError(failure) ? failure.message : 'The plan failed.')
        setLoading(false)
      },
    )
  }, [request, signalOf, report])

  return { explain, error, loading, run }
}
