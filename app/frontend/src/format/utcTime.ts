const KICKOFF_FORMAT = new Intl.DateTimeFormat('en-US', {
  timeZone: 'UTC',
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

const STAMP_FORMAT = new Intl.DateTimeFormat('en-US', {
  timeZone: 'UTC',
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

const CLOCK_FORMAT = new Intl.DateTimeFormat('en-US', {
  timeZone: 'UTC',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

function parse(iso: string | null): Date | null {
  if (iso === null) return null
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date
}

/** A kickoff time such as "Sat, Nov 7, 17:00 UTC"; an unknown time reads "time to be set". */
export function formatKickoff(iso: string | null): string {
  const date = parse(iso)
  return date === null ? 'time to be set' : `${KICKOFF_FORMAT.format(date).replace(' at ', ', ')} UTC`
}

/** A moment such as "Oct 18, 2026, 22:04 UTC". */
export function formatUtcStamp(iso: string | null): string {
  const date = parse(iso)
  return date === null ? '' : `${STAMP_FORMAT.format(date).replace(' at ', ', ')} UTC`
}

/** A UTC clock time such as "09:00 UTC"; an unknown time is an empty string. */
export function formatUtcClock(iso: string | null): string {
  const date = parse(iso)
  return date === null ? '' : `${CLOCK_FORMAT.format(date)} UTC`
}
