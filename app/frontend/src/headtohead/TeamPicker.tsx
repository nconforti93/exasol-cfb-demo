import { useId, useMemo, useState } from 'react'

import type { Team } from '../api/types.ts'

export interface TeamPickersProps {
  teams: readonly Team[]
  team: number | null
  opponent: number | null
  onChange(team: number | null, opponent: number | null): void
  onSwap(): void
}

function matches(team: Team, query: string): boolean {
  return team.school.toLowerCase().includes(query)
}

interface SelectProps {
  label: string
  teams: readonly Team[]
  value: number | null
  taken: number | null
  query: string
  onPick(teamId: number | null): void
}

function TeamSelect({ label, teams, value, taken, query, onPick }: SelectProps) {
  const id = useId()
  const options = teams.filter(
    (candidate) => candidate.team_id !== taken && (candidate.team_id === value || matches(candidate, query)),
  )
  return (
    <div className="h2h-field">
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        value={value === null ? '' : String(value)}
        onChange={(event) => onPick(event.target.value === '' ? null : Number(event.target.value))}
      >
        <option value="">Choose a team</option>
        {options.map((candidate) => (
          <option key={candidate.team_id} value={candidate.team_id}>
            {candidate.school}
          </option>
        ))}
      </select>
    </div>
  )
}

/** Two team lists with one search box for both, and a swap button. The pair itself lives in the app store. */
export function TeamPickers({ teams, team, opponent, onChange, onSwap }: TeamPickersProps) {
  const searchId = useId()
  const [search, setSearch] = useState('')
  const query = useMemo(() => search.trim().toLowerCase(), [search])
  return (
    <div className="h2h-pickers" role="group" aria-label="Pick two teams">
      <div className="h2h-field">
        <label htmlFor={searchId}>Search schools</label>
        <input id={searchId} type="search" value={search} onChange={(event) => setSearch(event.target.value)} />
      </div>
      <TeamSelect
        label="Team"
        teams={teams}
        value={team}
        taken={opponent}
        query={query}
        onPick={(picked) => onChange(picked, opponent)}
      />
      <TeamSelect
        label="Opponent"
        teams={teams}
        value={opponent}
        taken={team}
        query={query}
        onPick={(picked) => onChange(team, picked)}
      />
      <button type="button" className="shell-button" onClick={onSwap} disabled={team === null && opponent === null}>
        Swap teams
      </button>
    </div>
  )
}
