import { usePageData } from '../state/pageData.tsx'
import { Card, Note, PageHeader, RankTag, TeamMark } from '../ui/index.ts'

/** Placeholder for "This week" (task 9.4): loads `/api/league/week` and lists the games in the order served. */
export function WeekPage() {
  const page = usePageData(({ api, point, options }) => api.leagueWeek({ season: point.season, week: point.week_seq }, options))
  if (page.status === 'loading') return <p role="status">Loading the week…</p>
  if (page.data === null) {
    return (
      <p className="banner banner-warning" role="alert">
        Could not load the week: {page.error?.message}
      </p>
    )
  }
  const { games, queries, point } = page.data
  return (
    <section className="page-stack" aria-labelledby="week-title">
      <PageHeader id="week-title" title="This week" subtitle={point.label} />
      <Card title="Matchups" sqlEntry={queries['games'] ?? null}>
        <Note>The matchup cards arrive with the This week tab.</Note>
        <ul className="placeholder-list">
          {games.map((game) => (
            <li key={game.game_id}>
              <TeamMark team={game.away} size={24} /> <RankTag rank={game.away_rank} /> {game.away.school} at <TeamMark team={game.home} size={24} />{' '}
              <RankTag rank={game.home_rank} /> {game.home.school}
            </li>
          ))}
        </ul>
      </Card>
    </section>
  )
}
