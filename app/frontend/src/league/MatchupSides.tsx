import { formatPercent } from '../format/format.ts'
import { AppLink, RankTag, TeamMark } from '../ui/index.ts'
import type { Side } from './weekModel.ts'

export interface TeamSideProps {
  side: Side
  alerted: boolean
  size: 48 | 96
}

/** One team of a matchup: its mark, rank, name and win probability. */
export function TeamSide({ side, alerted, size }: TeamSideProps) {
  return (
    <div className={`matchup-side matchup-side-${size}`}>
      <TeamMark team={side.team} size={size} />
      <div className="matchup-side-text">
        <p className="matchup-side-name">
          <RankTag rank={side.rank} poll="AP" /> <AppLink to={{ page: 'team', team: side.team.team_id }}>{side.team.school}</AppLink>
        </p>
        {size === 96 && side.team.conference !== null && <p className="game-meta">{side.team.conference}</p>}
      </div>
      <p className={`matchup-side-prob num ${alerted ? 'matchup-side-prob-alert' : ''}`}>{formatPercent(side.winProb)}</p>
    </div>
  )
}

export interface SplitBarProps {
  away: Side
  home: Side
}

/** One bar for both teams' win probabilities: the favorite's share is the stronger hue. */
export function SplitBar({ away, home }: SplitBarProps) {
  if (home.winProb === null || away.winProb === null) return <p className="game-meta">No win probability yet.</p>
  const homeFavored = home.winProb >= away.winProb
  return (
    <span
      className={`split-bar ${homeFavored ? 'split-home-favored' : 'split-away-favored'}`}
      role="img"
      aria-label={`${away.team.school} ${formatPercent(away.winProb)}, ${home.team.school} ${formatPercent(home.winProb)}`}
      style={{ '--away-share': away.winProb } as React.CSSProperties}
    >
      <span className="split-away" />
      <span className="split-home" />
    </span>
  )
}
