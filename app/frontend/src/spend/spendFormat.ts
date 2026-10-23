import { formatSpendLabel } from '../format/format.ts'
import { formatUtcClock } from '../format/utcTime.ts'
import type { SpendSourceKind } from '../api/types.ts'

export interface SpentInput {
  calls: number
  source: SpendSourceKind
  cached: boolean
}

/** What an action cost once it ran: "1 live call spent", "replay · 0 calls" or "cached · 0 calls". */
export function formatSpent({ calls, source, cached }: SpentInput): string {
  if (cached) return formatSpendLabel({ source: 'CACHED', live_calls: 0 })
  if (calls > 0) return `${calls} live ${calls === 1 ? 'call' : 'calls'} spent`
  return formatSpendLabel({ source, live_calls: 0 })
}

/** A UTC clock time such as "09:00 UTC"; an unreadable timestamp is returned as it came. */
export function formatUtcTime(timestamp: string): string {
  const clock = formatUtcClock(timestamp)
  return clock === '' ? timestamp : clock
}
