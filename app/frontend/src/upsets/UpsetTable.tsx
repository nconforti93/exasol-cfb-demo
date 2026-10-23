import type { UpsetRow } from '../api/types.ts'
import { formatPercent, formatRank, formatProbSource } from '../format/format.ts'
import {
  formatAllTimeRank,
  formatLineEraRank,
  formatModelCall,
  formatSeasonRank,
  formatUpsetScore,
  formatWhen,
} from './upsetFormat.ts'
import './upsets.css'

export type UpsetTableKind = 'week' | 'all-time' | 'line-era'

export interface UpsetTableProps {
  kind: UpsetTableKind
  rows: readonly UpsetRow[]
  caption: string
  /** The first season of the all-time window, which heads the "since" column. */
  allTimeFrom: number
}

function rankCell(kind: UpsetTableKind, row: UpsetRow): string {
  switch (kind) {
    case 'week':
      return formatRank(row.week_rank, false)
    case 'all-time':
      return formatAllTimeRank(row) ?? formatSeasonRank(row)
    case 'line-era':
      return formatLineEraRank(row) ?? '–'
  }
}

function rankHeading(kind: UpsetTableKind, allTimeFrom: number): string {
  switch (kind) {
    case 'week':
      return 'Rank this week'
    case 'all-time':
      return `Rank since ${allTimeFrom}`
    case 'line-era':
      return 'Rank in the betting-line era'
  }
}

/** Upsets as a table: the rank with its probability source beside it ("T-37 (line)"), the game and our own call. */
export function UpsetTable({ kind, rows, caption, allTimeFrom }: UpsetTableProps) {
  const showSinceColumn = kind === 'week'
  return (
    <div className="upset-scroll">
      <table className="upset-table">
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{rankHeading(kind, allTimeFrom)}</th>
            <th scope="col">Game</th>
            {kind !== 'week' && <th scope="col">When</th>}
            <th scope="col">Winner&apos;s pregame chance</th>
            {showSinceColumn && <th scope="col">{`Rank since ${allTimeFrom}`}</th>}
            {showSinceColumn && <th scope="col">Betting-line era</th>}
            <th scope="col">Our model</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.game_id}>
              <td className="num">{rankCell(kind, row)}</td>
              <td>{formatUpsetScore(row)}</td>
              {kind !== 'week' && <td className="num">{formatWhen(row)}</td>}
              <td className="num">{`${formatPercent(row.winner_pregame_win_prob)} (${formatProbSource(row.prob_source)})`}</td>
              {showSinceColumn && <td className="num">{formatAllTimeRank(row) ?? formatSeasonRank(row)}</td>}
              {showSinceColumn && <td className="num">{formatLineEraRank(row) ?? '–'}</td>}
              <td>{formatModelCall(row)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
