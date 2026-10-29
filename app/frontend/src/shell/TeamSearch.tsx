import { useId, useMemo, useState, type KeyboardEvent } from 'react'

import { useAppActions } from '../state/react.tsx'
import { TeamMark } from '../ui/index.ts'
import { useTeamDirectory } from './TeamDirectory.tsx'
import { findTeams } from './teamSearch.ts'

/** A search box in the header: type a school, pick it (mouse or keyboard) and its team page opens. */
export function TeamSearch() {
  const directory = useTeamDirectory()
  const actions = useAppActions()
  const listId = useId()
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)

  const matches = useMemo(() => (directory === null ? [] : findTeams(directory.values(), query)), [directory, query])
  const showing = open && matches.length > 0

  function pick(teamId: number) {
    actions.navigate({ page: 'team', team: teamId })
    setQuery('')
    setOpen(false)
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      setOpen(true)
      const step = event.key === 'ArrowDown' ? 1 : -1
      setActive((current) => (current + step + matches.length) % Math.max(matches.length, 1))
    } else if (event.key === 'Enter' && showing) {
      event.preventDefault()
      const chosen = matches[active]
      if (chosen !== undefined) pick(chosen.team_id)
    } else if (event.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div className="team-search">
      <input
        type="search"
        role="combobox"
        className="team-search-input"
        placeholder="Find a team"
        aria-label="Find a team"
        aria-expanded={showing}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showing ? `${listId}-${active}` : undefined}
        autoComplete="off"
        disabled={directory === null}
        value={query}
        onChange={(event) => {
          setQuery(event.target.value)
          setActive(0)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
      />
      {showing && (
        <ul id={listId} role="listbox" className="team-search-list" aria-label="Teams">
          {matches.map((team, index) => (
            <li
              key={team.team_id}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              className="team-search-option"
              onMouseDown={(event) => {
                event.preventDefault()
                pick(team.team_id)
              }}
              onMouseEnter={() => setActive(index)}
            >
              <TeamMark team={team} size={24} />
              <span className="team-search-school">{team.school}</span>
              {team.conference !== null && <span className="team-search-conference">{team.conference}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
