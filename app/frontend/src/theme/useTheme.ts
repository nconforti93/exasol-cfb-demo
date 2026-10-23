import { useSyncExternalStore } from 'react'

import type { ThemeName } from './tokens.ts'

function read(): ThemeName {
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'
}

function subscribe(notify: () => void): () => void {
  const observer = new MutationObserver(notify)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  return () => observer.disconnect()
}

/** The theme the page shows now (`data-theme` on the root, set before the first paint); follows the switch. */
export function useTheme(): ThemeName {
  return useSyncExternalStore(subscribe, read, () => 'light')
}
