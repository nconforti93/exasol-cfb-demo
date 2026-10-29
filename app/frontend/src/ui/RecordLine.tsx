import { formatRecord } from '../format/format.ts'

const SEPARATOR = ' · '

export interface RecordLineProps {
  wins: number
  losses: number
  confWins?: number
  confLosses?: number
  conference?: string | null
}

/** "7–1 · 4–1 Big Ten": the overall record, then the conference record with the conference's name. */
export function RecordLine({ wins, losses, confWins, confLosses, conference }: RecordLineProps) {
  const parts = [formatRecord(wins, losses, 0)]
  if (confWins !== undefined && confLosses !== undefined) {
    const record = formatRecord(confWins, confLosses, 0)
    parts.push(conference === undefined || conference === null ? record : `${record} ${conference}`)
  }
  return <span className="record-line num">{parts.join(SEPARATOR)}</span>
}
