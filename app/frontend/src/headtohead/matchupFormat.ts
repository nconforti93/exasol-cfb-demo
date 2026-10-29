import type { MatchupNext, MatchupSeries, SiteKind } from '../api/types.ts'
import { formatRecord } from '../format/format.ts'

const KICKOFF = new Intl.DateTimeFormat('en-US', {
  weekday: 'short',
  month: 'short',
  day: 'numeric',
  year: 'numeric',
  timeZone: 'UTC',
})

/** A kickoff date such as "Sat, Nov 21, 2026" (UTC), or "date not set" when the schedule has none. */
export function formatKickoffDate(startAt: string | null): string {
  if (startAt === null) return 'date not set'
  const parsed = new Date(startAt)
  return Number.isNaN(parsed.getTime()) ? 'date not set' : KICKOFF.format(parsed)
}

/** The site of a game from the first team's side: "Ashford hosts", "Brookvale hosts" or "neutral site". */
export function formatSite(site: SiteKind, school: string, opponentSchool: string): string {
  if (site === 'NEUTRAL') return 'neutral site'
  return `${site === 'HOME' ? school : opponentSchool} hosts`
}

export function formatSiteShort(site: SiteKind): string {
  return site === 'HOME' ? 'home' : site === 'AWAY' ? 'away' : 'neutral'
}

export function formatSeriesRecord(series: MatchupSeries): string {
  return `${series.school} ${formatRecord(series.wins, series.losses, series.ties)}`
}

function times(count: number, singular: string): string {
  return `${count} ${count === 1 ? singular : `${singular}s`}`
}

/** "Brookvale, last 1 meeting" style streak, naming the side that holds it. */
export function formatStreak(series: MatchupSeries): string {
  const length = series.streak_length
  switch (series.streak_kind) {
    case 'W':
      return `${series.school} has won the last ${times(length, 'meeting')}`
    case 'L':
      return `${series.opponent_school} has won the last ${times(length, 'meeting')}`
    case 'T':
      return `the last ${times(length, 'meeting')} ended in a tie`
  }
}

export function formatMargin(margin: number | null, season: number | null): string {
  if (margin === null) return 'none'
  return `${times(margin, 'point')}${season === null ? '' : `, ${season}`}`
}

export function formatPredictedScore(next: MatchupNext): string {
  if (next.pred_team_points === null || next.pred_opponent_points === null) return 'score unavailable'
  return `${next.pred_team_points.toFixed(1)}–${next.pred_opponent_points.toFixed(1)}`
}
