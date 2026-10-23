import { useEffect } from 'react'

import { useAppStore } from './react.tsx'
import { buildPath, syncStoreFromLocation } from './url.ts'

function currentPath(): string {
  return window.location.pathname + window.location.search
}

/**
 * Keeps the address bar and the store in step: a store change that alters the page, point or team choices
 * adds a history entry, the first canonical form replaces the entry the app opened at, and back or forward
 * moves the store.
 */
export function useUrlSync(): void {
  const store = useAppStore()

  useEffect(() => {
    let applying = false

    const write = (replace: boolean) => {
      const next = buildPath(store.getState())
      if (next === currentPath()) return
      if (replace) window.history.replaceState(null, '', next)
      else window.history.pushState(null, '', next)
    }

    const onPopState = () => {
      applying = true
      try {
        syncStoreFromLocation(store, window.location.pathname, window.location.search)
      } finally {
        applying = false
      }
      write(true)
    }

    write(true)
    const unsubscribe = store.subscribe(() => {
      if (!applying) write(false)
    })
    window.addEventListener('popstate', onPopState)
    return () => {
      unsubscribe()
      window.removeEventListener('popstate', onPopState)
    }
  }, [store])
}
