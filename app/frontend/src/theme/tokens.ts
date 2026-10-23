export type ThemeName = 'light' | 'dark'

export const BASE_FONT_SIZE_PX = 18
export const FONT_STACK = 'system-ui, -apple-system, "Segoe UI", sans-serif'

export const TEXT_CONTRAST_MIN = 4.5
export const MARK_CONTRAST_MIN = 3

export interface ThemeTokens {
  page: string
  surface: string
  surfaceRaised: string
  borderSubtle: string
  shadowCard: string
  shadowRaised: string
  ink: { primary: string; secondary: string; muted: string }
  accentText: string
  accent: string
  onAccent: string
  border: string
  grid: string
  axis: string
  seq: readonly string[]
  seqBar: string
  seqTrack: string
  movement: { up: string; down: string; mid: string }
  moveText: { up: string; down: string }
  status: { good: string; warning: string; serious: string; critical: string }
  statusText: { good: string; critical: string }
  series: { live: string; replay: string; stored: string }
  focusRing: string
}

const SEQUENTIAL_BLUE = [
  '#cde2fb',
  '#9ec5f4',
  '#6da7ec',
  '#3987e5',
  '#256abf',
  '#184f95',
  '#0d366b',
] as const

export const LIGHT: ThemeTokens = {
  page: '#f9f9f7',
  surface: '#fcfcfb',
  surfaceRaised: '#ffffff',
  borderSubtle: '#e1e0d9',
  shadowCard: '0 1px 2px rgb(0 0 0 / 8%), 0 8px 24px rgb(0 0 0 / 7%)',
  shadowRaised: '0 4px 14px rgb(0 0 0 / 20%)',
  ink: { primary: '#0b0b0b', secondary: '#52514e', muted: '#6b6a65' },
  accentText: '#1c5cab',
  accent: '#256abf',
  onAccent: '#ffffff',
  border: '#7b7a74',
  grid: '#e1e0d9',
  axis: '#c3c2b7',
  seq: SEQUENTIAL_BLUE,
  seqBar: '#2a78d6',
  seqTrack: '#e1e0d9',
  movement: { up: '#2a78d6', down: '#e34948', mid: '#f0efec' },
  moveText: { up: '#1c5cab', down: '#a52a2a' },
  status: { good: '#0ca30c', warning: '#fab219', serious: '#ec835a', critical: '#d03b3b' },
  statusText: { good: '#006300', critical: '#a52a2a' },
  series: { live: '#2a78d6', replay: '#eb6834', stored: '#7b7a74' },
  focusRing: '#256abf',
}

export const DARK: ThemeTokens = {
  page: '#0d0d0d',
  surface: '#1a1a19',
  surfaceRaised: '#222220',
  borderSubtle: '#2c2c2a',
  shadowCard: 'none',
  shadowRaised: '0 6px 20px rgb(0 0 0 / 55%)',
  ink: { primary: '#f0efec', secondary: '#c3c2b7', muted: '#a09e96' },
  accentText: '#86b6ef',
  accent: '#3987e5',
  onAccent: '#0d0d0d',
  border: '#898781',
  grid: '#2c2c2a',
  axis: '#383835',
  seq: SEQUENTIAL_BLUE,
  seqBar: '#3987e5',
  seqTrack: '#2c2c2a',
  movement: { up: '#3987e5', down: '#e66767', mid: '#383835' },
  moveText: { up: '#86b6ef', down: '#ff9a9a' },
  status: { good: '#0ca30c', warning: '#fab219', serious: '#ec835a', critical: '#d03b3b' },
  statusText: { good: '#0ca30c', critical: '#ff9a9a' },
  series: { live: '#3987e5', replay: '#d95926', stored: '#898781' },
  focusRing: '#86b6ef',
}

export const THEMES: Record<ThemeName, ThemeTokens> = { light: LIGHT, dark: DARK }

/** Spacing steps `--space-1` to `--space-8`, in px. */
export const SPACE_PX = [4, 8, 12, 16, 24, 32, 48, 64] as const

export interface TypeStyle {
  sizePx: number
  weight: number
  lineHeight: number
}

/** The type scale (`--text-<name>` is the `font` shorthand; every number also sets `tabular-nums` in `ui.css`). */
export const TYPE_SCALE = {
  label: { sizePx: 16, weight: 600, lineHeight: 1.3 },
  small: { sizePx: 16, weight: 400, lineHeight: 1.4 },
  body: { sizePx: 18, weight: 400, lineHeight: 1.45 },
  title: { sizePx: 22, weight: 700, lineHeight: 1.25 },
  page: { sizePx: 30, weight: 700, lineHeight: 1.15 },
  hero: { sizePx: 40, weight: 800, lineHeight: 1.1 },
  kpi: { sizePx: 48, weight: 700, lineHeight: 1 },
} as const satisfies Record<string, TypeStyle>

export const RADIUS_PX = { card: 14, control: 10, pill: 999 } as const

/** Layout constants. The two breakpoints are repeated as literals in `@media` rules, where a variable cannot go. */
export const LAYOUT = {
  maxWidthPx: 1320,
  pagePadPx: 24,
  pagePadWidePx: 32,
  gridGapPx: 24,
  cardPadPx: 20,
  cardPadWidePx: 24,
  stackBelowPx: 1100,
  widePadFromPx: 1440,
  wideCardFromPx: 1200,
} as const

export type Ground = 'page' | 'surface' | 'surfaceRaised'

export const TEXT_PAIRS: ReadonlyArray<readonly [string, (t: ThemeTokens) => string, Ground]> = [
  ['ink.primary on page', (t) => t.ink.primary, 'page'],
  ['ink.primary on surface', (t) => t.ink.primary, 'surface'],
  ['ink.secondary on page', (t) => t.ink.secondary, 'page'],
  ['ink.secondary on surface', (t) => t.ink.secondary, 'surface'],
  ['ink.muted on page', (t) => t.ink.muted, 'page'],
  ['ink.muted on surface', (t) => t.ink.muted, 'surface'],
  ['ink.primary on surfaceRaised', (t) => t.ink.primary, 'surfaceRaised'],
  ['ink.secondary on surfaceRaised', (t) => t.ink.secondary, 'surfaceRaised'],
  ['ink.muted on surfaceRaised', (t) => t.ink.muted, 'surfaceRaised'],
  ['accentText on page', (t) => t.accentText, 'page'],
  ['accentText on surface', (t) => t.accentText, 'surface'],
  ['accentText on surfaceRaised', (t) => t.accentText, 'surfaceRaised'],
  ['moveText.up on surface', (t) => t.moveText.up, 'surface'],
  ['moveText.down on surface', (t) => t.moveText.down, 'surface'],
  ['statusText.good on surface', (t) => t.statusText.good, 'surface'],
  ['statusText.critical on surface', (t) => t.statusText.critical, 'surface'],
]

export const MARK_PAIRS: ReadonlyArray<readonly [string, (t: ThemeTokens) => string]> = [
  ['accent', (t) => t.accent],
  ['border', (t) => t.border],
  ['seqBar', (t) => t.seqBar],
  ['movement.up', (t) => t.movement.up],
  ['movement.down', (t) => t.movement.down],
  ['status.good', (t) => t.status.good],
  ['status.critical', (t) => t.status.critical],
  ['series.live', (t) => t.series.live],
  ['series.replay', (t) => t.series.replay],
  ['series.stored', (t) => t.series.stored],
  ['focusRing', (t) => t.focusRing],
]

/** Status colors below 3:1 on a surface; they always ship with an icon and a label. */
export const RELIEF_PAIRS: ReadonlyArray<readonly [string, (t: ThemeTokens) => string]> = [
  ['status.warning', (t) => t.status.warning],
  ['status.serious', (t) => t.status.serious],
]

export function cssVariables(tokens: ThemeTokens): Record<string, string> {
  const variables: Record<string, string> = {
    '--page': tokens.page,
    '--surface': tokens.surface,
    '--surface-raised': tokens.surfaceRaised,
    '--border-subtle': tokens.borderSubtle,
    '--shadow-card': tokens.shadowCard,
    '--shadow-raised': tokens.shadowRaised,
    '--ink-primary': tokens.ink.primary,
    '--ink-secondary': tokens.ink.secondary,
    '--ink-muted': tokens.ink.muted,
    '--accent-text': tokens.accentText,
    '--accent': tokens.accent,
    '--on-accent': tokens.onAccent,
    '--border': tokens.border,
    '--grid': tokens.grid,
    '--axis': tokens.axis,
    '--seq-bar': tokens.seqBar,
    '--seq-track': tokens.seqTrack,
    '--move-up': tokens.movement.up,
    '--move-down': tokens.movement.down,
    '--move-mid': tokens.movement.mid,
    '--move-up-text': tokens.moveText.up,
    '--move-down-text': tokens.moveText.down,
    '--status-good': tokens.status.good,
    '--status-warning': tokens.status.warning,
    '--status-serious': tokens.status.serious,
    '--status-critical': tokens.status.critical,
    '--status-good-text': tokens.statusText.good,
    '--status-critical-text': tokens.statusText.critical,
    '--series-live': tokens.series.live,
    '--series-replay': tokens.series.replay,
    '--series-stored': tokens.series.stored,
    '--focus-ring': tokens.focusRing,
  }
  tokens.seq.forEach((hex, index) => {
    variables[`--seq-${index + 1}`] = hex
  })
  return variables
}

function px(value: number): string {
  return `${value}px`
}

/** The tokens that do not change with the theme: spacing, type, radii and layout. */
export function staticVariables(): Record<string, string> {
  const variables: Record<string, string> = { '--font-stack': FONT_STACK }
  SPACE_PX.forEach((value, index) => {
    variables[`--space-${index + 1}`] = px(value)
  })
  for (const [name, style] of Object.entries(TYPE_SCALE)) {
    variables[`--text-${name}`] = `${style.weight} ${px(style.sizePx)}/${style.lineHeight} ${FONT_STACK}`
  }
  for (const [name, value] of Object.entries(RADIUS_PX)) variables[`--radius-${name}`] = px(value)
  variables['--layout-max'] = px(LAYOUT.maxWidthPx)
  variables['--page-pad'] = px(LAYOUT.pagePadPx)
  variables['--grid-gap'] = px(LAYOUT.gridGapPx)
  variables['--card-pad'] = px(LAYOUT.cardPadPx)
  return variables
}
