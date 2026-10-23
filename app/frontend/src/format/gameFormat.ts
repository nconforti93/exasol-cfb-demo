import type { WinProbSource } from '../api/types.ts'

const MINUS = '−'
const DAY_FORMAT = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric' })
const WEEKDAY_TIME_FORMAT = new Intl.DateTimeFormat('en-US', {
  timeZone: 'UTC',
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

/** The betting line as a signed number from one side's view: "−6.5" favored, "+3.0" underdog, "PK" for a pick'em. */
export function formatLine(spread: number | null): string {
  if (spread === null) return ''
  if (spread === 0) return 'PK'
  const magnitude = Math.abs(spread).toFixed(1)
  return spread < 0 ? `${MINUS}${magnitude}` : `+${magnitude}`
}

/** Where a win probability comes from, in words. */
export function winProbSourceLabel(source: WinProbSource | null): string {
  if (source === 'OURS') return 'our model'
  if (source === 'CFBD') return 'CFBD pregame'
  return ''
}

function parse(iso: string | null): Date | null {
  if (iso === null) return null
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date
}

/** A game day such as "Sep 5"; an unknown date is "TBD". */
export function formatGameDay(iso: string | null): string {
  const date = parse(iso)
  return date === null ? 'TBD' : DAY_FORMAT.format(date)
}

/** A kickoff such as "Sat 17:00 UTC"; an unknown time is "time to be set". */
export function formatKickoffShort(iso: string | null): string {
  const date = parse(iso)
  return date === null ? 'time to be set' : `${WEEKDAY_TIME_FORMAT.format(date).replace(',', '')} UTC`
}

/** A signed rating change with one decimal, such as "+14.5" or "−13.2". */
export function formatSigned(value: number): string {
  const text = Math.abs(value).toFixed(1)
  if (Number(text) === 0) return '0.0'
  return value > 0 ? `+${text}` : `${MINUS}${text}`
}

/** A rating with one decimal and a true minus sign, such as "−1.2". */
export function formatRating(value: number): string {
  const text = Math.abs(value).toFixed(1)
  return value < 0 && Number(text) !== 0 ? `${MINUS}${text}` : text
}
