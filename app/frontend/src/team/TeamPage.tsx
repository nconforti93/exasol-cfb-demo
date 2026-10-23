import { useAppState } from '../state/react.tsx'
import { usePageData } from '../state/pageData.tsx'
import { teamAccent } from '../theme/teamColor.ts'
import { useTheme } from '../theme/useTheme.ts'
import { AppLink, Card, Note, RankTag, RecordLine, StatTile, TeamMark } from '../ui/index.ts'
import { formatInteger } from '../format/format.ts'

/** Placeholder for the team page (task 9.3): loads `/api/team/<id>` and shows the hero and the ratings. */
export function TeamPage() {
  const teamId = useAppState((state) => state.focusTeam)
  const theme = useTheme()
  const page = usePageData(
    ({ api, point, options }) => api.team(teamId ?? 0, { season: point.season, week: point.week_seq }, options),
    [teamId],
  )

  if (teamId === null) return <Note>No team chosen. <AppLink to={{ page: 'home' }}>Back to the map</AppLink></Note>
  if (page.status === 'loading') return <p role="status">Loading the team…</p>
  if (page.data === null) {
    return (
      <p className="banner banner-warning" role="alert">
        No team with id {teamId}, or it could not be loaded: {page.error?.message}. <AppLink to={{ page: 'home' }}>Back to the map</AppLink>
      </p>
    )
  }
  const { team, sp, queries } = page.data
  const latest = sp[0]
  return (
    <section className="page-stack" aria-label={team.school}>
      <Card accent={teamAccent(team, theme)} sqlEntry={queries['hero'] ?? null}>
        <div className="placeholder-row">
          <TeamMark team={team} size={96} labelled />
          <div>
            <h2 className="page-title">{team.school}</h2>
            <p className="page-subtitle">
              {team.mascot} · <RecordLine wins={team.wins} losses={team.losses} confWins={team.conf_wins} confLosses={team.conf_losses} conference={team.conference} />{' '}
              <RankTag rank={team.ap_rank} poll="AP" />
            </p>
          </div>
          {latest !== undefined && <StatTile label={`SP+ ${latest.season}`} value={formatInteger(Math.round(latest.overall))} detail={`rank ${latest.overall_rank}`} />}
        </div>
        <Note>The schedule, polls and outlook arrive with the team page.</Note>
      </Card>
    </section>
  )
}
