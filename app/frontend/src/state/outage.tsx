import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

import { isApiError, isOutage } from '../api/client.ts'
import { useAppActions } from './react.tsx'

export interface Outage {
  /** The server's message (it names the address and the target), while the database is unreachable. */
  message: string | null
  /** Records a failed call; returns true, and raises the outage state, when it was a database outage. */
  report(error: unknown): boolean
  /** Clears the outage and loads every page again. */
  retry(): void
}

const OutageContext = createContext<Outage | null>(null)

export function OutageProvider({ children }: { children: ReactNode }) {
  const actions = useAppActions()
  const [message, setMessage] = useState<string | null>(null)

  const report = useCallback(
    (error: unknown) => {
      if (!isApiError(error) || !isOutage(error)) return false
      setMessage(error.message)
      actions.setOutage(true)
      return true
    },
    [actions],
  )

  const retry = useCallback(() => {
    setMessage(null)
    actions.setOutage(false)
    actions.reload()
  }, [actions])

  const value = useMemo(() => ({ message, report, retry }), [message, report, retry])
  return <OutageContext.Provider value={value}>{children}</OutageContext.Provider>
}

export function useOutage(): Outage {
  const outage = useContext(OutageContext)
  if (outage === null) throw new Error('useOutage needs an OutageProvider')
  return outage
}
