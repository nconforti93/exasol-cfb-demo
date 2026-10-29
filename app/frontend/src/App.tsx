import { useState } from 'react'

import { createApiClient, type ApiClient } from './api/client.ts'
import { ApiProvider } from './api/context.tsx'
import { HeadToHeadPage } from './headtohead/HeadToHeadPage.tsx'
import { Inspector } from './inspector/Inspector.tsx'
import { LeaguePage } from './league/LeaguePage.tsx'
import { StatsPage } from './league/StatsPage.tsx'
import { WeekPage } from './league/WeekPage.tsx'
import { MapPage } from './map/MapPage.tsx'
import { OddsPage } from './odds/OddsPage.tsx'
import { PredictionsPage } from './predictions/PredictionsPage.tsx'
import { RefreshPanel } from './refresh/RefreshPanel.tsx'
import { AppShell } from './shell/AppShell.tsx'
import { AppStoreProvider } from './state/react.tsx'
import { createAppStore, type AppStore, type LeagueTab, type PageId } from './state/store.ts'
import { parseLocation } from './state/url.ts'
import { TeamPage } from './team/TeamPage.tsx'
import { UpsetsPage } from './upsets/UpsetsPage.tsx'

const LEAGUE_TABS: Record<LeagueTab, () => React.JSX.Element> = {
  week: WeekPage,
  odds: OddsPage,
  predictions: PredictionsPage,
  upsets: UpsetsPage,
  stats: StatsPage,
}

function League() {
  return <LeaguePage tabs={LEAGUE_TABS} />
}

const PAGE_COMPONENTS: Record<PageId, () => React.JSX.Element> = {
  home: MapPage,
  team: TeamPage,
  week: League,
  odds: League,
  predictions: League,
  upsets: League,
  stats: League,
  'head-to-head': HeadToHeadPage,
}

export interface AppProps {
  /** For tests: a client over a fake `fetch`. */
  client?: ApiClient
  /** For tests: a store to inspect. */
  store?: AppStore
}

function storeFromLocation(): AppStore {
  const { page, chosen, focusTeam, mapConference, pair } = parseLocation(
    window.location.pathname,
    window.location.search,
  )
  return createAppStore({ page, chosen, focusTeam, mapConference, pair })
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
