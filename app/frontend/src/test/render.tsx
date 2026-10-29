import { render, type RenderResult } from '@testing-library/react'
import { StrictMode, type ReactElement } from 'react'

import { createApiClient, type ApiClient } from '../api/client.ts'
import { ApiProvider } from '../api/context.tsx'
import { AppStoreProvider } from '../state/react.tsx'
import { createAppStore, type AppState, type AppStore } from '../state/store.ts'
import { FakeBackend } from './backend.ts'

export interface RenderOptions {
  backend?: FakeBackend
  store?: AppStore
  initial?: Partial<AppState>
  client?: ApiClient
}

export interface Rendered extends RenderResult {
  backend: FakeBackend
  store: AppStore
  client: ApiClient
}

/** Renders in `StrictMode` under the API client (a fake backend by default) and the app store. */
export function renderInApp(ui: ReactElement, options: RenderOptions = {}): Rendered {
  const backend = options.backend ?? new FakeBackend()
  const client = options.client ?? createApiClient({ fetch: backend.fetch })
  const store = options.store ?? createAppStore(options.initial)
  const result = render(
    <StrictMode>
      <ApiProvider client={client}>
        <AppStoreProvider store={store}>{ui}</AppStoreProvider>
      </ApiProvider>
    </StrictMode>,
  )
  return Object.assign(result, { backend, store, client })
}
