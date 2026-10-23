import type { CSSProperties } from 'react'

import type { OutlookRivalries, MatchupSeries, Team } from '../api/types.ts'
import { AppLink, Card, TeamMark } from '../ui/index.ts'
import { EmptyPanel } from './EmptyPanel.tsx'
import { markTeamFor } from './teamModel.ts'

function streakText(series: MatchupSeries): string {
  const word = series.streak_kind === 'W' ? 'won' : series.streak_kind === 'L' ? 'lost' : 'tied'
  return `${word} the last ${series.streak_length === 1 ? 'meeting' : `${series.streak_length} meetings`}`
}

function SeriesBar({ series }: { series: MatchupSeries }) {
  const style = { '--wins': series.wins, '--ties': series.ties, '--losses': series.losses } as CSSProperties
  return (
    <span className="series-split" style={style} role="img" aria-label={`${series.wins} wins, ${series.losses} losses, ${series.ties} ties`}>
      <span className="series-split-wins" />
      <span className="series-split-ties" />
      <span className="series-split-losses" />
    </span>
  )
}

export interface RivalriesCardProps {
  teamId: number
  panel: OutlookRivalries
  teams: ReadonlyMap<number, Team>
  sqlEntry: string | null
}

/** The most-played series of the team, each leading to head-to-head. */
export function RivalriesCard({ teamId, panel, teams, sqlEntry }: RivalriesCardProps) {
  if (!panel.available || panel.series.length === 0) {
    return <EmptyPanel title="Rivalries" eyebrow="Outlook" span={12} reason={panel.reason} fallback="Rivalries appear once the series are published." />
  }
  return (
    <Card title="Rivalries" eyebrow="Most-played series" span={12} sqlEntry={sqlEntry}>
      <ul className="rivalry-list">
        {panel.series.map((series) => (
          <li key={series.opponent_team_id} className="rivalry">
            <div className="rivalry-id">
              <TeamMark team={markTeamFor(series.opponent_team_id, series.opponent_school, teams)} size={48} />
              <div>
                <p className="rivalry-name">
                  <AppLink to={{ page: 'team', team: series.opponent_team_id }}>{series.opponent_school}</AppLink>
                </p>
                <p className="game-meta">
                  {series.meetings} meetings · {series.first_season}–{series.last_season}
                </p>
              </div>
            </div>
            <p className="rivalry-record num">
              {series.wins}–{series.losses}
              {series.ties > 0 ? `–${series.ties}` : ''}
            </p>
            <SeriesBar series={series} />
            <p className="game-meta">{series.school} {streakText(series)}</p>
            <AppLink
              to={{ page: 'head-to-head', team: teamId, opponent: series.opponent_team_id }}
              aria-label={`Head-to-head: ${series.school} and ${series.opponent_school}`}
            >
              Head-to-head
            </AppLink>
          </li>
        ))}
      </ul>
    </Card>
  )
}
