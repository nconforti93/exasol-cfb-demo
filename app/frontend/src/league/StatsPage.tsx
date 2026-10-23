import { formatInteger } from '../format/format.ts'
import { usePageData } from '../state/pageData.tsx'
import { Card, Note, PageHeader, TeamMark } from '../ui/index.ts'

/** Placeholder for "Stats" (task 9.4): loads `/api/league/stats` and shows the road warriors with their SQL button. */
export function StatsPage() {
  const page = usePageData(({ api, point, options }) => api.leagueStats({ season: point.season, week: point.week_seq }, options))
  if (page.status === 'loading') return <p role="status">Loading the stats…</p>
  if (page.data === null) {
    return (
      <p className="banner banner-warning" role="alert">
        Could not load the stats: {page.error?.message}
      </p>
    )
  }
  const { road_warriors: travel, queries, point } = page.data
  return (
    <section className="page-stack" aria-labelledby="stats-title">
      <PageHeader id="stats-title" title="Stats" subtitle={point.label} />
      <Card title="Road warriors" sqlEntry={queries['road_warriors'] ?? null}>
        <Note>The four stat cards arrive with the Stats tab.</Note>
        <ul className="placeholder-list">
          {travel.map((row) => (
            <li key={row.team.team_id}>
              <TeamMark team={row.team} size={24} /> {row.team.school}: {formatInteger(row.km)} km
            </li>
          ))}
        </ul>
      </Card>
    </section>
  )
}
