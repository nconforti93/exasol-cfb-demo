import type { MatchupGame, MatchupSeries } from '../api/types.ts'
import { formatInteger, formatPercent, formatProbSource } from '../format/format.ts'
import { formatMargin, formatSeriesRecord, formatStreak } from './matchupFormat.ts'

const GAME_RESULT_WORD = { W: 'won', L: 'lost', T: 'tied' } as const

function Fact({ term, children }: { term: string; children: string }) {
  return (
    <div>
      <dt>{term}</dt>
      <dd className="num">{children}</dd>
    </div>
  )
}

/** The series from the first team's side: record, points, first and last season, streak, sites and extremes. */
export function SeriesSummary({ series }: { series: MatchupSeries }) {
  const seasons =
    series.first_season === series.last_season
      ? String(series.first_season)
      : `${series.first_season}–${series.last_season}`
  return (
    <section className="h2h-card" aria-label="The series">
      <h3>
        {series.school} vs {series.opponent_school}
      </h3>
      <dl className="h2h-facts">
        <Fact term="Meetings">{`${series.meetings} (${seasons})`}</Fact>
        <Fact term="Record">{formatSeriesRecord(series)}</Fact>
        <Fact term="Points for / against">{`${formatInteger(series.points_for)} / ${formatInteger(series.points_against)}`}</Fact>
        <Fact term="Streak">{formatStreak(series)}</Fact>
        <Fact term={`${series.school} wins at home / away / neutral`}>
          {`${series.wins_home} / ${series.wins_away} / ${series.wins_neutral}`}
        </Fact>
        <Fact term="Biggest win">{formatMargin(series.biggest_win_margin, series.biggest_win_season)}</Fact>
        <Fact term="Biggest loss">{formatMargin(series.biggest_loss_margin, series.biggest_loss_season)}</Fact>
      </dl>
    </section>
  )
}

function siteLabel(game: MatchupGame): string {
  return game.site === 'HOME' ? 'home' : game.site === 'AWAY' ? 'away' : 'neutral'
}

/** Every meeting, newest first as the server sends them: when, where, score, result and our pregame probability. */
export function MeetingsTable({ series, games }: { series: MatchupSeries; games: readonly MatchupGame[] }) {
  return (
    <section className="h2h-card" aria-label="The meetings">
      <h3>The meetings</h3>
      <div className="h2h-scroll">
        <table className="h2h-table">
          <caption className="sr-only">{`Every meeting of ${series.school} and ${series.opponent_school}, from ${series.school}'s side`}</caption>
          <thead>
            <tr>
              <th scope="col">Season</th>
              <th scope="col">Week</th>
              <th scope="col">Site</th>
              <th scope="col">Score</th>
              <th scope="col">Result</th>
              <th scope="col">{`${series.school}'s pregame chance`}</th>
              <th scope="col">Upset magnitude</th>
            </tr>
          </thead>
          <tbody>
            {games.map((game) => (
              <tr key={game.game_id}>
                <td className="num">{game.season}</td>
                <td className="num">{game.season_type === 'postseason' ? `postseason ${game.week}` : game.week}</td>
                <td>{siteLabel(game)}</td>
                <td className="num">{`${game.team_points}–${game.opponent_points}`}</td>
                <td>{GAME_RESULT_WORD[game.game_result]}</td>
                <td className="num">
                  {game.team_pregame_win_prob === null
                    ? '–'
                    : `${formatPercent(game.team_pregame_win_prob)}${game.prob_source === null ? '' : ` (${formatProbSource(game.prob_source)})`}`}
                </td>
                <td className="num">{game.upset_magnitude === null ? '–' : game.upset_magnitude.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
