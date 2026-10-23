import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

import { isAbortError } from '../api/client.ts'
import { useApi } from '../api/context.tsx'
import type { Team } from '../api/types.ts'
import { TeamMark, type MarkSize } from '../ui/index.ts'

export type TeamDirectory = ReadonlyMap<number, Team>

const DirectoryContext = createContext<TeamDirectory | null>(null)

/** Loads the team list once (`GET /api/teams`) for the header search and for the marks in table rows. A failure leaves it empty. */
export function TeamDirectoryProvider({ children }: { children: ReactNode }) {
  const api = useApi()
  const [directory, setDirectory] = useState<TeamDirectory | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    api.teams({ signal: controller.signal }).then(
      (response) => setDirectory(new Map(response.teams.map((team) => [team.team_id, team]))),
      (failure: unknown) => {
        if (!isAbortError(failure)) setDirectory(null)
      },
    )
    return () => controller.abort()
  }, [api])

  return <DirectoryContext.Provider value={directory}>{children}</DirectoryContext.Provider>
}

/** The loaded teams by id; null while loading, when loading failed, and outside the shell. */
export function useTeamDirectory(): TeamDirectory | null {
  return useContext(DirectoryContext)
}

export interface TeamLabelProps {
  teamId: number
  school: string
  size?: MarkSize
}

/** A school's name with its mark, for table rows; just the name until the team list is known. */
export function TeamLabel({ teamId, school, size = 24 }: TeamLabelProps) {
  const team = useTeamDirectory()?.get(teamId)
  return (
    <span className="team-label">
      {team !== undefined && <TeamMark team={team} size={size} />}
      <span>{school}</span>
    </span>
  )
}

export interface TeamMarksProps {
  teamIds: readonly number[]
  size?: MarkSize
}

/** The marks of some teams side by side, with no text; nothing until the team list is known. */
export function TeamMarks({ teamIds, size = 24 }: TeamMarksProps) {
  const directory = useTeamDirectory()
  const teams = teamIds.flatMap((id) => directory?.get(id) ?? [])
  if (teams.length === 0) return null
  return (
    <span className="team-marks">
      {teams.map((team) => (
        <TeamMark key={team.team_id} team={team} size={size} />
      ))}
    </span>
  )
}
