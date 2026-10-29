import type { SpRating } from '../api/types.ts'
import { Card, ChangeBadge } from '../ui/index.ts'
import { rankMovement } from './teamModel.ts'

interface Facet {
  label: string
  value: (rating: SpRating) => number
  rank: (rating: SpRating) => number
}

const FACETS: Facet[] = [
  { label: 'Overall', value: (rating) => rating.overall, rank: (rating) => rating.overall_rank },
  { label: 'Offense', value: (rating) => rating.offense, rank: (rating) => rating.offense_rank },
  { label: 'Defense', value: (rating) => rating.defense, rank: (rating) => rating.defense_rank },
]

export interface RatingsCardProps {
  current: SpRating | null
  previous: SpRating | null
  sqlEntry: string | null
}

/** SP+ overall, offense and defense with national ranks, this season beside the one before. */
export function RatingsCard({ current, previous, sqlEntry }: RatingsCardProps) {
  return (
    <Card
      title="SP+ ratings"
      eyebrow={current === null ? 'Not rated' : `${current.season}${previous === null ? '' : ` vs ${previous.season}`}`}
      sqlEntry={sqlEntry}
      footer="Ranks are national; a smaller rank is better, on offense and defense alike."
    >
      {current === null ? (
        <p className="note">No SP+ rating is stored for this team yet.</p>
      ) : (
        <ul className="rating-list" aria-label="SP+ ratings">
          {FACETS.map((facet) => {
            const movement = previous === null ? null : rankMovement(facet.rank(current), facet.rank(previous))
            return (
              <li key={facet.label} className="rating-row">
                <p>
                  <span className="rating-label">{facet.label}</span>
                  <br />
                  <span className="rating-now num">{facet.value(current).toFixed(1)}</span>
                  <span className="rating-rank num">#{facet.rank(current)}</span>
                </p>
                {previous !== null && (
                  <p className="rating-before">
                    <span className="num">
                      {previous.season}: {facet.value(previous).toFixed(1)} · #{facet.rank(previous)}
                    </span>
                    {movement === null ? <span>no change in rank</span> : <ChangeBadge direction={movement.direction} text={movement.text} />}
                  </p>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}
