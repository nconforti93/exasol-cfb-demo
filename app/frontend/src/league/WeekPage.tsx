import type { CSSProperties } from 'react'

import type { WeekGame } from '../api/types.ts'
import { formatKickoff } from '../format/utcTime.ts'
import { formatKickoffShort, formatLine, winProbSourceLabel } from '../format/gameFormat.ts'
import { formatPercent } from '../format/format.ts'
import { usePageData } from '../state/pageData.tsx'
import { AppLink, Card, Chip, Note, PageHeader, SqlButton } from '../ui/index.ts'
import './league.css'
import { TeamSide, SplitBar } from './MatchupSides.tsx'
import { UpsetAlertTag } from './UpsetAlert.tsx'
import { interestLabel, lineText, sides, upsetAlertOf } from './weekModel.ts'

function GameFacts({ game }: { game: WeekGame }) {
  const source = winProbSourceLabel(game.win_prob_source)
  return (
    <p className="game-meta">
      {formatKickoffShort(game.start_at)}
      {game.venue !== null && ` · ${game.venue}`}
      <br />
      Line: {lineText(game, formatLine)}
      {source !== '' && ` · win probability: ${source}`}
    </p>
  )
}

function Swing({ game }: { game: WeekGame }) {
  if (game.stakes === null || game.stakes <= 0) return null
  return <Chip>{`Swing ${(game.stakes * 100).toFixed(1)} pp of playoff chance`}</Chip>
}

function GameOfTheWeek({ game }: { game: WeekGame }) {
  const { away, home } = sides(game)
  const alert = upsetAlertOf(game)
  const label = interestLabel(game)
  return (
    <Card title="Game of the week" eyebrow="Most interesting game" span={8} className="gotw">
      <div className="gotw-teams">
        <TeamSide side={away} alerted={alert?.at === away} size={96} />
        <p className="gotw-at">at</p>
        <TeamSide side={home} alerted={alert?.at === home} size={96} />
      </div>
      <SplitBar away={away} home={home} />
      <div className="gotw-foot">
        <GameFacts game={game} />
        <div className="gotw-tags">
          {label !== null && <Chip>{label}</Chip>}
          <Swing game={game} />
          {alert !== null && <UpsetAlertTag alert={alert} />}
          <AppLink to={{ page: 'head-to-head', team: game.away.team_id, opponent: game.home.team_id }} aria-label={`Head-to-head: ${game.away.school} and ${game.home.school}`}>
            Head-to-head
          </AppLink>
        </div>
      </div>
    </Card>
  )
}

function UpsetAlerts({ games }: { games: WeekGame[] }) {
  const alerts = games.flatMap((game) => {
    const alert = upsetAlertOf(game)
    return alert === null ? [] : [{ game, alert }]
  })
  return (
    <Card title="Upset alerts" eyebrow="Ranked teams under 65 %" span={4}>
      {alerts.length === 0 ? (
        <Note>No ranked team is under 65 % to win this week.</Note>
      ) : (
        <ul className="upset-list">
          {alerts.map(({ game, alert }) => (
            <li key={game.game_id}>
              <UpsetAlertTag alert={alert} />
              <p className="upset-text">
                <AppLink to={{ page: 'team', team: alert.at.team.team_id }}>
                  #{alert.at.rank} {alert.at.team.school}
                </AppLink>{' '}
                is only <strong className="num">{formatPercent(alert.at.winProb)}</strong> to beat {alert.other.team.school}.
              </p>
              <p className="game-meta">{formatKickoff(game.start_at)}</p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function MatchupCard({ game, order }: { game: WeekGame; order: number }) {
  const { away, home } = sides(game)
  const alert = upsetAlertOf(game)
  const label = interestLabel(game)
  return (
    <article className="card week-card span-4" aria-label={`${game.away.school} at ${game.home.school}`}>
      <p className="card-eyebrow">{label ?? `Matchup ${order}`}</p>
      <TeamSide side={away} alerted={alert?.at === away} size={48} />
      <p className="week-card-at">at</p>
      <TeamSide side={home} alerted={alert?.at === home} size={48} />
      <SplitBar away={away} home={home} />
      <GameFacts game={game} />
      <div className="gotw-tags">
        <Swing game={game} />
        {alert !== null && <UpsetAlertTag alert={alert} />}
        <AppLink to={{ page: 'head-to-head', team: game.away.team_id, opponent: game.home.team_id }} aria-label={`Head-to-head: ${game.away.school} and ${game.home.school}`}>
          Head-to-head
        </AppLink>
      </div>
    </article>
  )
}

/** The coming week's games, most interesting first: the game of the week, upset alerts, and every other matchup with its line and a source-labelled win probability. */
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
  const { games, queries, point, week_seq: week, run_id: runId } = page.data
  const featured = games.find((game) => game.game_of_week) ?? games[0]
  const rest = games.filter((game) => game !== featured)
  const style = { '--week-games': games.length } as CSSProperties
  return (
    <section className="page-stack league-week" aria-labelledby="week-title" style={style}>
      <PageHeader
        id="week-title"
        title="This week"
        subtitle={`${week === null ? 'No coming week' : `Week ${week}`} · ${point.label} · ${runId === null ? 'ordered by rankings and closeness of the line' : `ordered by playoff stakes (run ${runId})`}`}
        actions={<SqlButton entryId={queries['games'] ?? null} />}
      />
      {featured === undefined ? (
        <Card title="Matchups">
          <Note>There are no FBS games to show for the coming week at {point.label}.</Note>
        </Card>
      ) : (
        <>
          <div className="grid">
            <GameOfTheWeek game={featured} />
            <UpsetAlerts games={games} />
          </div>
          {rest.length > 0 && (
            <>
              <div className="section-head">
                <h3 className="section-title">More matchups</h3>
                <Chip count={rest.length}>games</Chip>
              </div>
              <div className="grid">
                {rest.map((game, index) => (
                  <MatchupCard key={game.game_id} game={game} order={index + 2} />
                ))}
              </div>
            </>
          )}
        </>
      )}
    </section>
  )
}
