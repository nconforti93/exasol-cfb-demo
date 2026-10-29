import type { Team } from '../api/types.ts'

export const MAX_SUGGESTIONS = 8

function plain(text: string): string {
  return text.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase().trim()
}

/** Teams whose school or abbreviation starts with the query first, then those that contain it; ties by school name. */
export function findTeams(teams: Iterable<Team>, query: string): Team[] {
  const needle = plain(query)
  if (needle === '') return []
  const ranked: Array<{ rank: number; team: Team }> = []
  for (const team of teams) {
    const school = plain(team.school)
    const abbreviation = plain(team.abbreviation ?? '')
    const conference = plain(team.conference ?? '')
    let rank = -1
    if (school.startsWith(needle) || abbreviation === needle) rank = 0
    else if (school.split(/\s+/).some((word) => word.startsWith(needle))) rank = 1
    else if (school.includes(needle) || conference.startsWith(needle)) rank = 2
    if (rank >= 0) ranked.push({ rank, team })
  }
  ranked.sort((a, b) => a.rank - b.rank || a.team.school.localeCompare(b.team.school))
  return ranked.slice(0, MAX_SUGGESTIONS).map((entry) => entry.team)
}
