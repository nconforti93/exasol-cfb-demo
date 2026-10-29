import type { TeamGame } from '../api/types.ts'
import { CheckIcon, CrossIcon, DashIcon } from '../charts/icons.tsx'
import { formatPercent } from '../format/format.ts'
import { formatGameDay, formatLine, winProbSourceLabel } from '../format/gameFormat.ts'
import { AppLink, Card, ProbBar, RankTag, TeamMark } from '../ui/index.ts'

const SITE_WORD = { HOME: 'vs', AWAY: 'at', NEUTRAL: 'vs' } as const

function Result({ game }: { game: TeamGame }) {
  const score = `${game.team_points ?? '–'}–${game.opponent_points ?? '–'}`
  const Icon = game.result === 'W' ? CheckIcon : game.result === 'L' ? CrossIcon : DashIcon
  const word = game.result === 'W' ? 'Win' : game.result === 'L' ? 'Loss' : 'Tie'
  return (
    <span className={`game-result game-result-${(game.result ?? 't').toLowerCase()}`}>
      <Icon />
      <strong>{word}</strong>
      <span className="num">{score}</span>
    </span>
  )
}

function Upcoming({ game }: { game: TeamGame }) {
  const line = formatLine(game.spread)
  if (game.win_prob === null) return <span className="game-meta">{line === '' ? 'No line yet' : `Line ${line}`}</span>
  const percent = formatPercent(game.win_prob)
  return (
    <span className="game-upcoming">
      <span className="game-upcoming-main">
        <ProbBar value={game.win_prob} label={`Win probability ${percent}`} />
        <strong className="num">{percent}</strong>
      </span>
      <span className="game-meta">
        {winProbSourceLabel(game.win_prob_source)}
        {line !== '' && <span className="num"> · line {line}</span>}
      </span>
    </span>
  )
}

export interface ScheduleCardProps {
  teamId: number
  school: string
  season: number
  games: TeamGame[]
  nextId: number | null
  sqlEntry: string | null
}

/** Every game of the season: played ones with the score, coming ones with the line and a labelled win probability. */
export function ScheduleCard({ teamId, school, season, games, nextId, sqlEntry }: ScheduleCardProps) {
  return (
    <Card
      title="Schedule and results"
      eyebrow={`${season} season`}
      span={8}
      sqlEntry={sqlEntry}
      footer="Lines are from this team's side: a minus sign means favored. Win probabilities name their source."
    >
      <div className="team-table-scroll">
        <table className="team-schedule">
          <caption className="sr-only">{`${school} schedule and results`}</caption>
          <thead>
            <tr>
              <th scope="col">Wk</th>
              <th scope="col">Date</th>
              <th scope="col">Opponent</th>
              <th scope="col">Result or outlook</th>
              <th scope="col">
                <span className="sr-only">Head-to-head</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {games.map((game) => (
              <tr key={game.game_id} className={`${game.status === 'UPCOMING' ? 'game-upcoming-row' : ''} ${game.game_id === nextId ? 'game-next-row' : ''}`}>
                <td className="num game-week">{game.week}</td>
                <td className="game-date">
                  {formatGameDay(game.start_at)}
                  {game.game_id === nextId && <span className="game-next">Next</span>}
                </td>
                <td>
                  <span className="game-opponent">
                    <span className="game-site">{SITE_WORD[game.site]}</span>
                    <TeamMark team={game.opponent} size={32} />
                    <AppLink to={{ page: 'team', team: game.opponent.team_id }}>{game.opponent.school}</AppLink>
                    <RankTag rank={game.opponent_rank} poll="AP" />
                    {game.site === 'NEUTRAL' && <span className="game-meta">neutral</span>}
                  </span>
                </td>
                <td>{game.status === 'FINAL' ? <Result game={game} /> : <Upcoming game={game} />}</td>
                <td className="game-link">
                  <AppLink
                    to={{ page: 'head-to-head', team: teamId, opponent: game.opponent.team_id }}
                    aria-label={`Head-to-head: ${school} and ${game.opponent.school}`}
                  >
                    H2H
                  </AppLink>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
