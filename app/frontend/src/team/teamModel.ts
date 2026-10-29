import type { PollPoint, SpRating, TeamGame, Team } from '../api/types.ts'
import type { MarkTeam } from '../ui/index.ts'

export interface Movement {
  direction: 'up' | 'down'
  text: string
}

function places(count: number): string {
  return `${count} ${count === 1 ? 'place' : 'places'}`
}

/** The AP rank against the week before: up when the number got smaller. Null when it did not change or there is no pair. */
export function apMovement(polls: PollPoint[]): Movement | null {
  const now = polls[polls.length - 1]?.ap_rank ?? null
  const before = polls[polls.length - 2]?.ap_rank ?? null
  if (now === null || before === null || now === before) return null
  return now < before ? { direction: 'up', text: places(before - now) } : { direction: 'down', text: places(now - before) }
}

/** How many places a rank moved between two seasons; a smaller number is better. */
export function rankMovement(now: number, before: number): Movement | null {
  if (now === before) return null
  return now < before ? { direction: 'up', text: places(before - now) } : { direction: 'down', text: places(now - before) }
}

/** The rating of the point's season and, when stored, the season before. */
export function ratingPair(sp: SpRating[]): { current: SpRating | null; previous: SpRating | null } {
  const sorted = [...sp].sort((a, b) => b.season - a.season)
  return { current: sorted[0] ?? null, previous: sorted[1] ?? null }
}

export function nextGameId(games: TeamGame[]): number | null {
  return games.find((game) => game.status === 'UPCOMING')?.game_id ?? null
}

/** The mark of a team the response names only by id and school: its schedule or the team list gives the colors. */
export function markTeamFor(teamId: number, school: string, known: ReadonlyMap<number, Team>): MarkTeam {
  const found = known.get(teamId)
  if (found !== undefined) return found
  return { team_id: teamId, school, abbreviation: null, color: null, alternate_color: null, has_logo: false }
}

/** The schedule's opponents, overridden by the loaded team list when there is one. */
export function teamLookup(games: TeamGame[], directory: ReadonlyMap<number, Team> | null): Map<number, Team> {
  const lookup = new Map<number, Team>()
  for (const game of games) lookup.set(game.opponent.team_id, game.opponent)
  for (const [id, team] of directory ?? []) lookup.set(id, team)
  return lookup
}
