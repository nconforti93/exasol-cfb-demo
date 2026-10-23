import type { UpsetRow } from '../api/types.ts'
import { formatPercent, formatProbSource } from '../format/format.ts'
import {
  formatAllTimeRank,
  formatLineEraRank,
  formatModelCall,
  formatSeasonRank,
  formatUpsetScore,
  formatWinnerSite,
} from './upsetFormat.ts'
import './upsets.css'

/** The upset of the week: the game, how unlikely it was, where it ranks since 1978 and what our model said. */
export function UpsetFeature({ row }: { row: UpsetRow }) {
  const allTime = formatAllTimeRank(row)
  const lineEra = formatLineEraRank(row)
  return (
    <section className="upset-feature" aria-label="Upset of the week">
      <h3>Upset of the week</h3>
      <p className="upset-score">{formatUpsetScore(row)}</p>
      <p className="upset-note">{`${row.winner_school} won ${formatWinnerSite(row)}.`}</p>
      <dl className="upset-facts">
        <div>
          <dt>Pregame chance of the winner</dt>
          <dd className="num">{`${formatPercent(row.winner_pregame_win_prob)} (${formatProbSource(row.prob_source)})`}</dd>
        </div>
        <div>
          <dt>Upset magnitude</dt>
          <dd className="num">{row.upset_magnitude.toFixed(2)}</dd>
        </div>
        <div>
          <dt>{`Rank since ${row.all_time_from_season}`}</dt>
          <dd className="num">{allTime ?? formatSeasonRank(row)}</dd>
        </div>
        <div>
          <dt>Betting-line era rank</dt>
          <dd className="num">{lineEra ?? '–'}</dd>
        </div>
        <div>
          <dt>Our model</dt>
          <dd>{formatModelCall(row)}</dd>
        </div>
      </dl>
      {allTime === null && (
        <p className="upset-note">{`Played before ${row.all_time_from_season}, so it has no rank since then, only its season rank.`}</p>
      )}
    </section>
  )
}
