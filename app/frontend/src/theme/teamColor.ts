import { contrastRatio } from './contrast.ts'
import { MARK_CONTRAST_MIN, TEXT_CONTRAST_MIN, THEMES, type ThemeName } from './tokens.ts'

export interface TeamColors {
  color: string | null
  alternate_color: string | null
}

export interface BadgeLook {
  background: string
  ring: string
  ink: string
}

const HEX = /^#?([0-9a-f]{6})$/i
const WHITE = '#ffffff'
const NEAR_BLACK = '#0b0b0b'
const BLACK = '#000000'
const NEUTRAL_BADGE = '#52514e'
const BADGE_LETTERS = 4

/** `#rrggbb` in lower case from CFBD's value, which comes with or without the `#`; anything else is missing. */
export function normalizeHex(value: string | null | undefined): string | null {
  const match = HEX.exec((value ?? '').trim())
  return match === null ? null : `#${(match[1] ?? '').toLowerCase()}`
}

/**
 * The color a team contributes to a theme as an accent (stripes, rings, chart marks; never text): its color if
 * that reaches 3:1 against the theme's surface, else its alternate, else the app accent.
 */
export function teamAccent(colors: TeamColors, theme: ThemeName): string {
  const tokens = THEMES[theme]
  for (const candidate of [colors.color, colors.alternate_color]) {
    const hex = normalizeHex(candidate)
    if (hex !== null && contrastRatio(hex, tokens.surface) >= MARK_CONTRAST_MIN) return hex
  }
  return tokens.accent
}

/** White or near-black text, whichever reaches 4.5:1 on the background; pure black for the mid-tones neither does. */
export function readableInk(background: string): string {
  const best = contrastRatio(WHITE, background) >= contrastRatio(NEAR_BLACK, background) ? WHITE : NEAR_BLACK
  return contrastRatio(best, background) >= TEXT_CONTRAST_MIN ? best : BLACK
}

/** The fallback badge's text: the abbreviation, else the first four letters of the school. */
export function badgeLabel(team: { abbreviation: string | null; school: string }): string {
  const abbreviation = (team.abbreviation ?? '').trim()
  return abbreviation === '' ? team.school.trim().slice(0, BADGE_LETTERS).toUpperCase() : abbreviation
}

/** The badge's disc, ring and text colors; a team without usable colors gets a neutral disc. */
export function badgeLook(colors: TeamColors): BadgeLook {
  const color = normalizeHex(colors.color)
  const alternate = normalizeHex(colors.alternate_color)
  const background = color ?? alternate ?? NEUTRAL_BADGE
  const ring = alternate !== null && alternate !== background ? alternate : readableInk(background)
  return { background, ring, ink: readableInk(background) }
}
