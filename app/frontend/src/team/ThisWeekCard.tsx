import type { OutlookThisWeek } from '../api/types.ts'
import { formatPercent } from '../format/format.ts'
import { formatKickoffShort, formatLine } from '../format/gameFormat.ts'
import { AppLink, Card, ProbBar, RankTag, StatTile, TeamMark } from '../ui/index.ts'
import { EmptyPanel } from './EmptyPanel.tsx'

export interface ThisWeekCardProps {
  teamId: number
  school: string
  panel: OutlookThisWeek
  sqlEntry: string | null
}

/** The team's coming game: our win probability and predicted score, with the line beside them. */
export function ThisWeekCard({ teamId, school, panel, sqlEntry }: ThisWeekCardProps) {
  const game = panel.game
  if (!panel.available || game === null) {
    return <EmptyPanel title="This week's prediction" eyebrow="Outlook" span={4} reason={panel.reason} fallback="Picks appear after the first simulation run." />
  }
  const word = game.site === 'AWAY' ? 'at' : 'vs'
  const percent = formatPercent(game.team_win_prob)
  const line = formatLine(game.spread)
  return (
    <Card title="This week's prediction" eyebrow="Outlook" span={4} sqlEntry={sqlEntry} footer={game.prediction_kind === 'FROZEN' ? 'Pick frozen before kickoff week' : 'Pick refreshed with the latest run'}>
      <div className="week-opponent">
        <TeamMark team={game.opponent} size={48} />
        <div>
          <p className="week-opponent-name">
            <span className="game-site">{word}</span> <AppLink to={{ page: 'team', team: game.opponent.team_id }}>{game.opponent.school}</AppLink> <RankTag rank={game.opponent_rank} poll="AP" />
          </p>
          <p className="game-meta">
            Week {game.week_seq} · {formatKickoffShort(game.start_at)}
            {game.site === 'NEUTRAL' ? ' · neutral site' : ''}
          </p>
        </div>
      </div>
      <StatTile label={`${school} win probability`} value={<span className="num">{percent}</span>} detail="our model" />
      <ProbBar value={game.team_win_prob} label={`${school} win probability ${percent}`} />
      <dl className="week-facts">
        <div>
          <dt>Predicted score</dt>
          <dd className="num">
            {game.pred_team_points === null || game.pred_opponent_points === null
              ? 'unavailable'
              : `${game.pred_team_points.toFixed(1)}–${game.pred_opponent_points.toFixed(1)}`}
          </dd>
        </div>
        <div>
          <dt>Line</dt>
          <dd className="num">{line === '' ? 'none yet' : line}</dd>
        </div>
      </dl>
      <AppLink to={{ page: 'head-to-head', team: teamId, opponent: game.opponent.team_id }} aria-label={`Head-to-head: ${school} and ${game.opponent.school}`}>
        Head-to-head with {game.opponent.school}
      </AppLink>
    </Card>
  )
}
