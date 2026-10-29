import { createContext, useContext, useSyncExternalStore, type ReactNode } from 'react'

import type { AppActions, AppState, AppStore } from './store.ts'

const StoreContext = createContext<AppStore | null>(null)

export function AppStoreProvider({ store, children }: { store: AppStore; children: ReactNode }) {
  return <StoreContext.Provider value={store}>{children}</StoreContext.Provider>
}

export function useAppStore(): AppStore {
  const store = useContext(StoreContext)
  if (store === null) throw new Error('useAppStore needs an AppStoreProvider')
  return store
}

/** Re-renders when the selected part of the state changes; the selector must return stable values. */
export function useAppState<T>(selector: (state: AppState) => T): T {
  const store = useAppStore()
  return useSyncExternalStore(store.subscribe, () => selector(store.getState()))
}

export function useAppActions(): AppActions {
  return useAppStore().actions
}
