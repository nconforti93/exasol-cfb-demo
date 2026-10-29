import type { RequestLine } from '../api/types.ts'
import { formatHttpRequest } from '../format/format.ts'
import { clockTime, formatRequestLine } from './requests.ts'

function httpLine(slice: string): string {
  try {
    return formatHttpRequest(slice)
  } catch {
    return slice
  }
}

/** One line per slice: source, status, attempts, milliseconds and rows, then the HTTP request the slice key becomes. */
export function RequestLines({ lines, label }: { lines: RequestLine[]; label: string }) {
  return (
    <ul className="inspector-requests" aria-label={label}>
      {lines.map((line, index) => (
        <li key={`${index}-${line.slice}`}>
          <span className="num">{formatRequestLine(line)}</span>
          {line.requested_at === null ? null : <span className="inspector-muted num">{`at ${clockTime(line.requested_at)}`}</span>}
          <code className="inspector-http">{httpLine(line.slice)}</code>
        </li>
      ))}
    </ul>
  )
}
