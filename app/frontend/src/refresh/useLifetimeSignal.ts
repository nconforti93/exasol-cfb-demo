import { useCallback, useEffect, useRef } from 'react'

/**
 * The component's own `AbortController`, as a getter for its signal: aborted when the component unmounts, and
 * renewed on mount so `StrictMode`'s simulated unmount does not leave a dead signal behind.
 */
export function useLifetimeSignal(): () => AbortSignal {
  const controller = useRef<AbortController | null>(null)
  useEffect(() => {
    const own = new AbortController()
    controller.current = own
    return () => {
      own.abort()
      controller.current = null
    }
  }, [])
  return useCallback(() => (controller.current ?? new AbortController()).signal, [])
}
