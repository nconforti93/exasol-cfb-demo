import { usePageData } from '../state/pageData.tsx'
import { useAppState } from '../state/react.tsx'
import { teamAccent } from '../theme/teamColor.ts'
import { useTheme } from '../theme/useTheme.ts'
import { AppLink, Note } from '../ui/index.ts'
import { Outlook } from './Outlook.tsx'
import { PollsCard } from './PollsCard.tsx'
import { RatingsCard } from './RatingsCard.tsx'
import { ScheduleCard } from './ScheduleCard.tsx'
import './team.css'
import { TeamHero } from './TeamHero.tsx'
import { nextGameId, ratingPair } from './teamModel.ts'

/** A team's own page at `/team/<id>`: hero, schedule and results, ratings and polls, then the outlook from the published run. */
export function TeamPage() {
  const teamId = useAppState((state) => state.focusTeam)
  const theme = useTheme()
  const page = usePageData(
    ({ api, point, options }) => api.team(teamId ?? 0, { season: point.season, week: point.week_seq }, options),
    [teamId],
  )

  if (teamId === null) {
    return (
      <Note>
        No team chosen. <AppLink to={{ page: 'home' }}>Back to the map</AppLink>
      </Note>
    )
  }
  if (page.status === 'loading') return <p role="status">Loading the team…</p>
  if (page.data === null) {
    return (
      <p className="banner banner-warning" role="alert">
        No team with id {teamId}, or it could not be loaded: {page.error?.message}. <AppLink to={{ page: 'home' }}>Back to the map</AppLink>
      </p>
    )
  }
  const { team, games, sp, polls, queries, point } = page.data
  const accent = teamAccent(team, theme)
  const { current, previous } = ratingPair(sp)
  return (
    <section className="page-stack team-page" aria-label={team.school}>
      <TeamHero team={team} accent={accent} polls={polls} rating={current} pointLabel={point.label} sqlEntry={queries['hero'] ?? null} />
      <div className="grid">
        <ScheduleCard teamId={team.team_id} school={team.school} season={point.season} games={games} nextId={nextGameId(games)} sqlEntry={queries['schedule'] ?? null} />
        <div className="span-4 team-side">
          <RatingsCard current={current} previous={previous} sqlEntry={queries['ratings'] ?? null} />
          <PollsCard polls={polls} accent={accent} sqlEntry={queries['polls'] ?? null} />
        </div>
      </div>
      <Outlook teamId={team.team_id} school={team.school} accent={accent} games={games} />
    </section>
  )
}
