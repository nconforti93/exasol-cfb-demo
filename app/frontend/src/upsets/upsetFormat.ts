import type { UpsetRow } from '../api/types.ts'
import { formatPercent, formatProbSource, formatRankWithSource } from '../format/format.ts'

const SITE_WORD = { HOME: 'at home', AWAY: 'away', NEUTRAL: 'on a neutral site' } as const

export function formatUpsetScore(row: UpsetRow): string {
  return `${row.winner_school} ${row.winner_points}, ${row.loser_school} ${row.loser_points}`
}

export function formatWinnerSite(row: UpsetRow): string {
  return SITE_WORD[row.winner_site]
}

/** The rank since the first Division I-A season, "T-37 (line)"; a game before the window has none. */
export function formatAllTimeRank(row: UpsetRow): string | null {
  if (row.all_time_rank === null) return null
  return formatRankWithSource(row.all_time_rank, row.all_time_tied, row.prob_source)
}

export function formatLineEraRank(row: UpsetRow): string | null {
  if (row.line_era_rank === null) return null
  return formatRankWithSource(row.line_era_rank, row.line_era_tied, row.prob_source)
}

/** Only the season rank, for a game before the all-time window: "season rank 3 (Elo)". */
export function formatSeasonRank(row: UpsetRow): string {
  if (row.season_rank === null) return '–'
  return `season rank ${row.season_rank} (${formatProbSource(row.prob_source)})`
}

/** "our model gave them 18.0 %" or "not predicted". */
export function formatModelCall(row: UpsetRow): string {
  if (row.model_winner_win_prob === null) return 'not predicted'
  return `our model gave them ${formatPercent(row.model_winner_win_prob)}`
}

export function formatWhen(row: UpsetRow): string {
  const week = row.season_type === 'postseason' ? `postseason ${row.week}` : `week ${row.week}`
  return `${row.season}, ${week}`
}
