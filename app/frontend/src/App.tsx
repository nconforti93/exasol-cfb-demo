import { useState } from 'react'

import { createApiClient, type ApiClient } from './api/client.ts'
import { ApiProvider } from './api/context.tsx'
import { HeadToHeadPage } from './headtohead/HeadToHeadPage.tsx'
import { Inspector } from './inspector/Inspector.tsx'
import { OddsPage } from './odds/OddsPage.tsx'
import { PredictionsPage } from './predictions/PredictionsPage.tsx'
import { RefreshPanel } from './refresh/RefreshPanel.tsx'
import { AppShell } from './shell/AppShell.tsx'
import { AppStoreProvider } from './state/react.tsx'
import { createAppStore, type AppStore, type PageId } from './state/store.ts'
import { parseLocation } from './state/url.ts'
import { UpsetsPage } from './upsets/UpsetsPage.tsx'

const PAGE_COMPONENTS: Record<PageId, () => React.JSX.Element> = {
  odds: OddsPage,
  predictions: PredictionsPage,
  upsets: UpsetsPage,
  'head-to-head': HeadToHeadPage,
}

export interface AppProps {
  /** For tests: a client over a fake `fetch`. */
  client?: ApiClient
  /** For tests: a store to inspect. */
  store?: AppStore
}

function storeFromLocation(): AppStore {
  const { page, chosen, focusTeam, pair } = parseLocation(window.location.pathname, window.location.search)
  return createAppStore({ page, chosen, focusTeam, pair })
}

export function App({ client, store }: AppProps = {}) {
  const [services] = useState(() => ({
    client: client ?? createApiClient(),
    store: store ?? storeFromLocation(),
  }))
  return (
    <ApiProvider client={services.client}>
      <AppStoreProvider store={services.store}>
        <AppShell pages={PAGE_COMPONENTS} refreshPanel={RefreshPanel} inspector={Inspector} />
      </AppStoreProvider>
    </ApiProvider>
  )
}
