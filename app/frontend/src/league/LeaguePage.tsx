import type { ComponentType } from 'react'

import { useAppState } from '../state/react.tsx'
import { LEAGUE_TABS, type LeagueTab } from '../state/store.ts'
import { AppLink } from '../ui/index.ts'
import './tabs.css'

const TAB_LABELS: Readonly<Record<LeagueTab, string>> = {
  week: 'This week',
  odds: 'Odds',
  predictions: 'Predictions',
  upsets: 'Upsets',
  stats: 'Stats',
}

export interface LeaguePageProps {
  /** The page of each tab; the one for the store's current page is shown under the tab bar. */
  tabs: Readonly<Record<LeagueTab, ComponentType>>
}

/** The league section: a tab bar (This week, Odds, Predictions, Upsets, Stats) over the page of the chosen tab. */
export function LeaguePage({ tabs }: LeaguePageProps) {
  const page = useAppState((state) => state.page)
  const current = LEAGUE_TABS.find((tab) => tab === page) ?? 'week'
  const Tab = tabs[current]
  return (
    <div className="league-page">
      <nav aria-label="League" className="league-tabs">
        {LEAGUE_TABS.map((tab) => (
          <AppLink key={tab} to={{ page: tab }} aria-current={tab === current ? 'page' : undefined}>
            {TAB_LABELS[tab]}
          </AppLink>
        ))}
      </nav>
      <Tab />
    </div>
  )
}
