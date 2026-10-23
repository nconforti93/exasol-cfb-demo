import type { MapTeam } from '../api/types.ts'
import { RankTag, RecordLine, TeamMark } from '../ui/index.ts'

export interface HoverCardProps {
  team: MapTeam
  /** The marker's place in the view, in percent of the view's width and height. */
  left: number
  top: number
}

const EDGE_PERCENT = 22
const FLIP_PERCENT = 34

function placement(left: number, top: number): string {
  const side = left < EDGE_PERCENT ? 'start' : left > 100 - EDGE_PERCENT ? 'end' : 'middle'
  return `map-card-${side} map-card-${top < FLIP_PERCENT ? 'below' : 'above'}`
}

/** The card shown over a marker that is hovered or focused: school, mascot, conference, record and AP rank. */
export function HoverCard({ team, left, top }: HoverCardProps) {
  const detail = [team.mascot, team.conference].filter((part): part is string => part !== null && part !== '')
  return (
    <div className={`map-hover ${placement(left, top)}`} style={{ left: `${left}%`, top: `${top}%` }} aria-hidden="true">
      <TeamMark team={team} size={48} />
      <div className="map-hover-text">
        <p className="map-hover-school">{team.school}</p>
        {detail.length > 0 && <p className="map-hover-detail">{detail.join(' · ')}</p>}
        <p className="map-hover-record">
          <RecordLine wins={team.wins} losses={team.losses} confWins={team.conf_wins} confLosses={team.conf_losses} />
          {team.ap_rank !== null && (
            <>
              {' '}
              <span className="map-hover-rank">
                AP <RankTag rank={team.ap_rank} />
              </span>
            </>
          )}
        </p>
      </div>
    </div>
  )
}
