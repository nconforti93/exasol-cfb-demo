import type { Team, WeekGame } from '../api/types.ts'

const CLOSE_LINE_POINTS = 7

export interface Side {
  team: Team
  rank: number | null
  winProb: number | null
}

export function sides(game: WeekGame): { away: Side; home: Side } {
  return {
    away: { team: game.away, rank: game.away_rank, winProb: game.home_win_prob === null ? null : 1 - game.home_win_prob },
    home: { team: game.home, rank: game.home_rank, winProb: game.home_win_prob },
  }
}

/** Why a game is interesting, in a few words; null when nothing sets it apart. */
export function interestLabel(game: WeekGame): string | null {
  if (game.stakes !== null && game.stakes > 0) return 'Playoff stakes'
  if (game.home_rank !== null && game.away_rank !== null) return 'Top-25 matchup'
  if (game.spread !== null && Math.abs(game.spread) <= CLOSE_LINE_POINTS) return 'Close line'
  return null
}

export interface UpsetAlert {
  at: Side
  other: Side
}

/** The ranked team the server flagged, with its opponent. */
export function upsetAlertOf(game: WeekGame): UpsetAlert | null {
  if (game.upset_alert_team_id === null) return null
  const { away, home } = sides(game)
  if (home.team.team_id === game.upset_alert_team_id) return { at: home, other: away }
  if (away.team.team_id === game.upset_alert_team_id) return { at: away, other: home }
  return null
}

/** Pinecrest −4.5: the line from the home side's view, named for the favored team. */
export function lineText(game: WeekGame, format: (spread: number | null) => string): string {
  if (game.spread === null) return 'no line yet'
  if (game.spread === 0) return 'pick’em'
  const favorite = game.spread < 0 ? game.home : game.away
  return `${favorite.school} ${format(-Math.abs(game.spread))}`
}
