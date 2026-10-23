import { usePageData } from '../state/pageData.tsx'
import { useAppActions, useAppState } from '../state/react.tsx'
import { AppLink, Card, Chip, Note, PageHeader, RankTag, RecordLine, TeamMark } from '../ui/index.ts'

/** Placeholder for the home map (task 9.2): loads `/api/teams/map` and lists the teams with their marks. */
export function MapPage() {
  const page = usePageData(({ api, point, options }) => api.teamsMap({ season: point.season, week: point.week_seq }, options))
  const conference = useAppState((state) => state.mapConference)
  const actions = useAppActions()

  if (page.status === 'loading') return <p role="status">Loading the teams…</p>
  if (page.data === null) {
    return (
      <p className="banner banner-warning" role="alert">
        Could not load the teams: {page.error?.message}
      </p>
    )
  }
  const { teams, conferences, queries, point } = page.data
  const shown = teams.filter((team) => conference === null || team.conference === conference)
  return (
    <section className="page-stack" aria-labelledby="map-title">
      <PageHeader id="map-title" title="Map" subtitle={point.label} />
      <Card title="FBS teams" sqlEntry={queries['teams'] ?? null} footer={`${shown.length} of ${teams.length} teams`}>
        <Note>The map itself arrives with the map home page; this is the team list it will draw.</Note>
        <div className="placeholder-row">
          <Chip selected={conference === null} onClick={() => actions.setMapConference(null)}>
            All FBS
          </Chip>
          {conferences.map((name) => (
            <Chip key={name} selected={conference === name} onClick={() => actions.setMapConference(name)}>
              {name}
            </Chip>
          ))}
        </div>
        <ul className="placeholder-list">
          {shown.map((team) => (
            <li key={team.team_id}>
              <AppLink to={{ page: 'team', team: team.team_id }}>
                <TeamMark team={team} size={32} /> {team.school}
              </AppLink>{' '}
              <RecordLine wins={team.wins} losses={team.losses} /> <RankTag rank={team.ap_rank} poll="AP" />
            </li>
          ))}
        </ul>
      </Card>
    </section>
  )
}
