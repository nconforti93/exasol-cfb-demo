import type { FetchMode, QuotaResponse } from '../api/types.ts'
import { formatInteger } from '../format/format.ts'

export interface QuotaBadgeProps {
  mode: FetchMode | null
  quota: QuotaResponse | null
  /** The latest quota check failed. */
  failed: boolean
  onCheck(): void
}

function ageText(seconds: number | null): string | null {
  if (seconds === null) return null
  if (seconds < 60) return 'read just now'
  return `read ${Math.floor(seconds / 60)} min ago`
}

function remainingText(quota: QuotaResponse): string | null {
  if (quota.remaining_calls === null) return null
  const total = quota.project_budget === null ? '' : ` of ${formatInteger(quota.project_budget)}`
  return `${formatInteger(quota.remaining_calls)}${total} calls left`
}

/** The header's quota: "replay · no calls" in replay (no read at all), else the live reading and its age. */
export function QuotaBadge({ mode, quota, failed, onCheck }: QuotaBadgeProps) {
  if (mode === null) return null
  if (mode === 'REPLAY' || quota?.mode === 'replay') {
    return <span className="badge">replay · no calls</span>
  }
  const remaining = quota === null ? null : remainingText(quota)
  const age = quota === null ? null : ageText(quota.age_seconds)
  let summary = 'quota: checking'
  if (remaining !== null) summary = remaining
  else if (failed) summary = 'quota unavailable'
  return (
    <span className="badge badge-quota">
      <span>{summary}</span>
      {age !== null && <span className="badge-detail">{age}</span>}
      {failed && remaining !== null && <span className="badge-detail">latest check failed</span>}
      <button type="button" className="shell-button shell-button-small" onClick={onCheck}>
        Check quota now
      </button>
    </span>
  )
}
