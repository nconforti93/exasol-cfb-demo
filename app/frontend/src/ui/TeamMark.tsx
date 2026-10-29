import { useState, type CSSProperties } from 'react'

import { badgeLabel, badgeLook } from '../theme/teamColor.ts'
import { useTheme } from '../theme/useTheme.ts'

export const MARK_SIZES = [24, 32, 48, 96, 112] as const
export type MarkSize = (typeof MARK_SIZES)[number]

const SMALL_MARK_PX = 32
const SMALL_LETTERS = 3
const BADGE_FONT_RATIO = 0.3
const BADGE_FONT_MIN_PX = 9

export interface MarkTeam {
  team_id: number
  school: string
  abbreviation: string | null
  color: string | null
  alternate_color: string | null
  has_logo: boolean
}

export interface TeamMarkProps {
  team: MarkTeam
  size?: MarkSize
  /** Names the mark for assistive technology; leave it off when the school's name is written beside it. */
  labelled?: boolean
}

export function logoUrl(teamId: number, theme: 'light' | 'dark'): string {
  return `/api/logos/${teamId}.png${theme === 'dark' ? '?variant=dark' : ''}`
}

function badgeStyle(team: MarkTeam, size: MarkSize): CSSProperties {
  const look = badgeLook(team)
  return {
    '--mark-size': `${size}px`,
    '--badge-bg': look.background,
    '--badge-ring': look.ring,
    '--badge-ink': look.ink,
    '--badge-font': `${Math.max(BADGE_FONT_MIN_PX, Math.round(size * BADGE_FONT_RATIO))}px`,
  } as CSSProperties
}

/**
 * A team's logo from the app's own cache, or a badge when it has none, when the image fails, and when the
 * response says it has none (`has_logo`, false for every team under `APP_LOGOS=off`): a disc in the team's
 * color, ringed in its alternate, with the abbreviation in whichever of white and near-black reads on it.
 */
export function TeamMark({ team, size = 32, labelled = false }: TeamMarkProps) {
  const theme = useTheme()
  const url = logoUrl(team.team_id, theme)
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const name = labelled ? team.school : ''

  if (team.has_logo && failedUrl !== url) {
    return (
      <img
        className="team-mark team-mark-logo"
        src={url}
        width={size}
        height={size}
        alt={name}
        decoding="async"
        onError={() => setFailedUrl(url)}
      />
    )
  }
  const letters = size <= SMALL_MARK_PX ? badgeLabel(team).slice(0, SMALL_LETTERS) : badgeLabel(team)
  return (
    <span
      className="team-mark team-mark-badge"
      style={badgeStyle(team, size)}
      {...(labelled ? { role: 'img', 'aria-label': name } : { 'aria-hidden': true })}
    >
      {letters}
    </span>
  )
}
