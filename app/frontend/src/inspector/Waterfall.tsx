import type { RequestLine } from '../api/types.ts'
import { Waterfall } from '../charts/Waterfall.tsx'
import { waterfallRows } from './requests.ts'

/** The slices of a statement, console run or refresh as a waterfall by source; nothing when there are none. */
export function RequestWaterfall({ requests, title = 'Requests' }: { requests: RequestLine[]; title?: string }) {
  const rows = waterfallRows(requests)
  if (rows.length === 0) return null
  return <Waterfall title={title} rows={rows} />
}
