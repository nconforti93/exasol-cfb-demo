import type { ComponentType } from 'react'

import { usePageData, usePoint } from '../state/pageData.tsx'
import type { PageId } from '../state/store.ts'

function ProbeBody({ name }: { name: string }) {
  const point = usePoint()
  const page = usePageData(({ api, point: at, options }) => api.odds({ season: at.season, week: at.week_seq }, options))
  return (
    <section>
      <h2>{name} probe</h2>
      <p data-testid="probe">{`${name}@${point.season}/${point.week_seq}:${page.status}`}</p>
    </section>
  )
}

const Home = () => <ProbeBody name="home" />
const Team = () => <ProbeBody name="team" />
const Week = () => <ProbeBody name="week" />
const Stats = () => <ProbeBody name="stats" />
const Odds = () => <ProbeBody name="odds" />
const Predictions = () => <ProbeBody name="predictions" />
const Upsets = () => <ProbeBody name="upsets" />
const HeadToHead = () => <ProbeBody name="head-to-head" />

/** Test pages for the shell: each loads `/api/odds` at the effective point and shows `page@season/week:status`. */
export const probePages: Record<PageId, ComponentType> = {
  home: Home,
  team: Team,
  week: Week,
  stats: Stats,
  odds: Odds,
  predictions: Predictions,
  upsets: Upsets,
  'head-to-head': HeadToHead,
}
