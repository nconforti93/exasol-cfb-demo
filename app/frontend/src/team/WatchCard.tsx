import type { GuideResponse, GuideRow, OutlookWatch, Team } from '../api/types.ts'
import { CheckIcon } from '../charts/icons.tsx'
import { InlineBar } from '../charts/InlineBar.tsx'
import { formatPercent } from '../format/format.ts'
import { WarningIcon } from '../shell/icons.tsx'
import type { SecondaryRead } from '../odds/useSecondaryRead.ts'
import { Card, Chip, Note, TeamMark } from '../ui/index.ts'
import { EmptyPanel } from './EmptyPanel.tsx'
import { markTeamFor } from './teamModel.ts'

function leverageText(value: number): string {
  return `${(value * 100).toFixed(1)} pp`
}

function GuideList({ title, rows, maxLeverage, teams }: { title: string; rows: GuideRow[]; maxLeverage: number; teams: ReadonlyMap<number, Team> }) {
  if (rows.length === 0) return null
  return (
    <div className="watch-group">
      <h4 className="watch-group-title">{title}</h4>
      <ul className="watch-list">
        {rows.map((row) => (
          <li key={row.game_id} className="watch-row">
            <div className="watch-game">
              <span className="watch-marks">
                <TeamMark team={markTeamFor(row.away_team_id, row.away_school, teams)} size={32} />
                <TeamMark team={markTeamFor(row.home_team_id, row.home_school, teams)} size={32} />
              </span>
              <div>
                <p className="watch-matchup">
                  {row.away_school} at {row.home_school}
                  <span className="game-meta"> · week {row.week_seq}</span>
                </p>
                <p className="watch-root">
                  <CheckIcon /> Root for <strong>{row.root_for_school}</strong>
                </p>
              </div>
            </div>
            <div className="watch-effect">
              <InlineBar value={row.leverage} max={maxLeverage} label={`Leverage ${leverageText(row.leverage)}`} width={96} />
              <span className="num watch-leverage">{leverageText(row.leverage)}</span>
              <span className="game-meta num">
                {formatPercent(row.p_if_home_win)} if {row.home_school} wins · {formatPercent(row.p_if_away_win)} if {row.away_school} wins
              </span>
              {row.low_sample && (
                <span className="flag-low">
                  <WarningIcon /> <span>low sample</span>
                </span>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

export interface WatchCardProps {
  panel: OutlookWatch
  /** The conditional guide when what-ifs are set, else the one the outlook carries. */
  conditional: SecondaryRead<GuideResponse>
  teams: ReadonlyMap<number, Team>
  sqlEntry: string | null
  span: 8 | 12
}

/** The games that move this team's playoff chance most, and whom to root for. */
export function WatchCard({ panel, conditional, teams, sqlEntry, span }: WatchCardProps) {
  if (!panel.available || panel.guide === null) {
    return <EmptyPanel title="Games to watch" eyebrow="Outlook" span={12} reason={panel.reason} fallback="Games to watch appear once a run is published." />
  }
  const showingConditional = conditional.status !== 'idle'
  const guide = conditional.status === 'ready' && conditional.data !== null ? conditional.data : panel.guide
  const rows = [...guide.coming_week, ...guide.top]
  const maxLeverage = Math.max(0.01, ...rows.map((row) => row.leverage))
  const empty = guide.top.length === 0 && guide.coming_week.length === 0
  return (
    <Card title="Games to watch" eyebrow="Outlook" span={span} sqlEntry={sqlEntry}>
      {showingConditional && (
        <p className="watch-conditional" role="status">
          <Chip>What-if</Chip>
          <span>
            {conditional.status === 'loading'
              ? 'Recomputing the guide with your what-ifs…'
              : conditional.status === 'error'
                ? (conditional.message ?? 'The conditional guide could not be computed.')
                : 'Conditional on your what-ifs: the games you set are left out.'}
          </span>
        </p>
      )}
      {empty && <Note>{guide.message ?? 'There are no rooting guide rows for this team at this point.'}</Note>}
      <GuideList title="Coming week" rows={guide.coming_week} maxLeverage={maxLeverage} teams={teams} />
      {guide.conditional ? null : <GuideList title="Biggest games of the season" rows={guide.top} maxLeverage={maxLeverage} teams={teams} />}
    </Card>
  )
}
