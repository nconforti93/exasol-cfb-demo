import type { MatchupNext, Team } from '../api/types.ts'
import { formatPercent } from '../format/format.ts'
import { formatKickoffDate, formatPredictedScore, formatSite } from './matchupFormat.ts'

export interface NextMeetingProps {
  next: MatchupNext
  team: Team
  opponent: Team
  /** The label of the current point, shown as "as of …" when the picker is on another point. */
  asOfLabel: string | null
}

const PREDICTION_WORD = { FROZEN: 'frozen before kickoff', CURRENT: 'current' } as const

function Stakes({ next, team, opponent }: Pick<NextMeetingProps, 'next' | 'team' | 'opponent'>) {
  return (
    <table className="h2h-table">
      <caption className="sr-only">Playoff chance after this game</caption>
      <thead>
        <tr>
          <th scope="col">Team</th>
          <th scope="col">Playoff chance if it wins</th>
          <th scope="col">Playoff chance if it loses</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <th scope="row">{team.school}</th>
          <td className="num">{formatPercent(next.team_p_in_cfp_if_win)}</td>
          <td className="num">{formatPercent(next.team_p_in_cfp_if_lose)}</td>
        </tr>
        <tr>
          <th scope="row">{opponent.school}</th>
          <td className="num">{formatPercent(next.opponent_p_in_cfp_if_win)}</td>
          <td className="num">{formatPercent(next.opponent_p_in_cfp_if_lose)}</td>
        </tr>
      </tbody>
    </table>
  )
}

/** The next meeting at the current point: when and where, our prediction, and what it means for both teams' playoff odds. */
export function NextMeeting({ next, team, opponent, asOfLabel }: NextMeetingProps) {
  const hasStakes = next.stakes_run_id !== null
  return (
    <section className="h2h-card" aria-label="The next meeting">
      <h3>Next meeting</h3>
      {asOfLabel !== null && <p className="h2h-muted">{`as of ${asOfLabel}`}</p>}
      <dl className="h2h-facts">
        <div>
          <dt>Game</dt>
          <dd>{`${next.season}, week ${next.week_seq}`}</dd>
        </div>
        <div>
          <dt>When and where</dt>
          <dd>{`${formatKickoffDate(next.start_at)}, ${formatSite(next.site, team.school, opponent.school)}`}</dd>
        </div>
        <div>
          <dt>{next.prediction_kind === null ? 'Our prediction' : `Our prediction (${PREDICTION_WORD[next.prediction_kind]})`}</dt>
          <dd className="num">{next.team_win_prob === null ? 'unavailable' : `${team.school} ${formatPercent(next.team_win_prob)}, ${formatPredictedScore(next)}`}</dd>
        </div>
      </dl>
      {hasStakes ? (
        <Stakes next={next} team={team} opponent={opponent} />
      ) : (
        <p className="h2h-muted">No published playoff odds to compare for this game.</p>
      )}
    </section>
  )
}
