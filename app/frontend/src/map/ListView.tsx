import type { MapTeam } from '../api/types.ts'
import { formatRecord } from '../format/format.ts'
import { AppLink, RankTag, TeamMark } from '../ui/index.ts'

const NO_CONFERENCE = 'No conference'

export interface ListViewProps {
  teams: readonly MapTeam[]
  /** The season's conferences, in the order they are listed. */
  conferences: readonly string[]
}

interface Group {
  name: string
  teams: MapTeam[]
}

function groupByConference(teams: readonly MapTeam[], conferences: readonly string[]): Group[] {
  const groups = new Map<string, MapTeam[]>(conferences.map((name) => [name, []]))
  for (const team of teams) {
    const name = team.conference ?? NO_CONFERENCE
    const members = groups.get(name) ?? []
    members.push(team)
    groups.set(name, members)
  }
  return [...groups.entries()]
    .filter(([, members]) => members.length > 0)
    .map(([name, members]) => ({ name, teams: members.sort((a, b) => a.school.localeCompare(b.school)) }))
}

/** The map's table view: the same teams grouped by conference, each with its record, AP rank and a link to its page. */
export function ListView({ teams, conferences }: ListViewProps) {
  return (
    <div className="map-list">
      {groupByConference(teams, conferences).map((group) => (
        <section key={group.name} className="map-list-group" aria-label={group.name}>
          <h4 className="map-list-title">
            {group.name} <span className="map-list-count num">{group.teams.length}</span>
          </h4>
          <table className="map-list-table">
            <thead className="sr-only">
              <tr>
                <th scope="col">Team</th>
                <th scope="col">Record</th>
                <th scope="col">Conference record</th>
                <th scope="col">AP rank</th>
              </tr>
            </thead>
            <tbody>
              {group.teams.map((team) => (
                <tr key={team.team_id}>
                  <td className="map-list-team">
                    <AppLink to={{ page: 'team', team: team.team_id }}>
                      <TeamMark team={team} size={24} />
                      <span>{team.school}</span>
                    </AppLink>
                  </td>
                  <td className="num">{formatRecord(team.wins, team.losses, 0)}</td>
                  <td className="num map-list-conf">{formatRecord(team.conf_wins, team.conf_losses, 0)}</td>
                  <td className="map-list-rank">
                    <RankTag rank={team.ap_rank} poll="AP" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  )
}
