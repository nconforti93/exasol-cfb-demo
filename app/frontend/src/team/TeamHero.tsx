import type { CSSProperties } from 'react'

import type { PollPoint, SpRating, TeamHero as TeamHeroData } from '../api/types.ts'
import { formatRecord } from '../format/format.ts'
import { ChangeBadge, RankTag, SqlButton, StatTile, TeamMark } from '../ui/index.ts'
import { apMovement } from './teamModel.ts'

export interface TeamHeroProps {
  team: TeamHeroData
  accent: string
  polls: PollPoint[]
  rating: SpRating | null
  pointLabel: string
  sqlEntry: string | null
}

function rankTile(label: string, rank: number | null, detail: React.ReactNode) {
  return <StatTile label={label} value={rank === null ? '–' : <RankTag rank={rank} />} detail={detail} />
}

/** The top of a team page: its mark at 112 px, name, conference and record, and the headline ranks. The team color is the top stripe only. */
export function TeamHero({ team, accent, polls, rating, pointLabel, sqlEntry }: TeamHeroProps) {
  const movement = apMovement(polls)
  const confRecord = formatRecord(team.conf_wins, team.conf_losses, 0)
  return (
    <section
      className="card card-accent team-hero"
      style={{ '--card-accent': accent } as CSSProperties}
      aria-labelledby="team-hero-name"
    >
      <div className="team-hero-id">
        <TeamMark team={team} size={112} labelled />
        <div className="team-hero-text">
          <p className="card-eyebrow">
            {team.conference ?? 'Independent'} · {pointLabel}
          </p>
          <h2 id="team-hero-name" className="team-hero-name">
            {team.school}
          </h2>
          {team.mascot !== null && <p className="team-hero-mascot">{team.mascot}</p>}
          <div className="team-hero-sql">
            <SqlButton entryId={sqlEntry} />
          </div>
        </div>
      </div>
      <div className="team-hero-stats">
        <StatTile label="Record" value={<span className="num">{formatRecord(team.wins, team.losses, 0)}</span>} detail={`${confRecord} in conference`} />
        {rankTile(
          'AP rank',
          team.ap_rank,
          movement === null ? (team.ap_rank === null ? 'Unranked' : 'Same as last week') : <ChangeBadge direction={movement.direction} text={movement.text} />,
        )}
        {rankTile('CFP rank', team.cfp_rank, team.cfp_rank === null ? 'Not ranked' : 'Committee ranking')}
        <StatTile
          label="SP+ rating"
          value={rating === null ? '–' : <span className="num">{rating.overall.toFixed(1)}</span>}
          detail={rating === null ? 'No rating stored' : `#${rating.overall_rank} overall`}
        />
      </div>
    </section>
  )
}
