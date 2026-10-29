import type { FetchRow, RequestLine } from '../api/types.ts'
import type { WaterfallRow, WaterfallSource } from '../charts/Waterfall.tsx'
import { formatInteger } from '../format/format.ts'

const SOURCES: ReadonlySet<string> = new Set<WaterfallSource>(['LIVE', 'REPLAY', 'STORED'])
const CLOCK_PATTERN = /T(\d{2}:\d{2}:\d{2}(?:\.\d+)?)/

function parseTime(text: string | null): number | null {
  if (text === null) return null
  const ms = Date.parse(text)
  return Number.isNaN(ms) ? null : ms
}

function isWaterfallSource(source: string): source is WaterfallSource {
  return SOURCES.has(source)
}

/** Bars from each request's time, counted from the first one; a source the chart has no color for is left out. */
export function waterfallRows(requests: RequestLine[]): WaterfallRow[] {
  const starts = requests.map((request) => parseTime(request.requested_at))
  const known = starts.filter((start): start is number => start !== null)
  const origin = known.length === 0 ? 0 : Math.min(...known)
  const rows: WaterfallRow[] = []
  requests.forEach((request, index) => {
    const source = request.source.toUpperCase()
    if (!isWaterfallSource(source)) return
    const start = starts[index] ?? null
    rows.push({
      id: `${index}-${request.slice}`,
      label: request.slice,
      source,
      startMs: start === null ? 0 : start - origin,
      durationMs: request.ms ?? 0,
      status: request.status,
    })
  })
  return rows
}

export function requestOfFetch(row: FetchRow): RequestLine {
  return {
    slice: row.slice_key,
    source: row.source,
    status: row.status,
    attempts: row.attempts,
    ms: row.ms,
    rows: row.rows,
    requested_at: row.requested_at,
  }
}

/** "LIVE · 200 · 1 attempt · 400 ms · 20 rows", leaving out what the request did not report. */
export function formatRequestLine(line: RequestLine): string {
  const parts: string[] = [line.source, line.status === null ? 'no status' : String(line.status)]
  if (line.attempts !== null) parts.push(line.attempts === 1 ? '1 attempt' : `${line.attempts} attempts`)
  if (line.ms !== null) parts.push(`${formatInteger(line.ms)} ms`)
  if (line.rows !== null) parts.push(line.rows === 1 ? '1 row' : `${formatInteger(line.rows)} rows`)
  return parts.join(' · ')
}

export function clockTime(timestamp: string | null): string {
  if (timestamp === null) return ''
  return CLOCK_PATTERN.exec(timestamp)?.[1] ?? timestamp
}

export function rowsLabel(count: number): string {
  return count === 1 ? '1 row' : `${formatInteger(count)} rows`
}
