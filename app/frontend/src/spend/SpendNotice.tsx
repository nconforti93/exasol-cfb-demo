import type { ApiError } from '../api/client.ts'
import type { SpendSourceKind } from '../api/types.ts'
import { formatInteger } from '../format/format.ts'
import { InfoIcon, OkIcon, WarningIcon } from '../shell/icons.tsx'
import { formatSpent, formatUtcTime } from './spendFormat.ts'

function sentence(message: string): string {
  return message.trim().replace(/[.\s]+$/, '')
}

export interface SpendFailure {
  tone: 'refused' | 'failed'
  headline: string
  detail: string
  /** Calls charged to the ledger although the action failed. */
  charged: number | null
}

/** Maps a failed live action to what the page says: a refusal costs nothing, a failed fetch is charged. */
export function describeSpendFailure(error: ApiError): SpendFailure {
  const message = error.message
  switch (error.code) {
    case 'live_busy':
      return {
        tone: 'refused',
        headline: 'Live requests are busy. Nothing was spent.',
        detail: `${sentence(message)}. Try again when it has finished.`,
        charged: null,
      }
    case 'reconcile_pending':
      return {
        tone: 'refused',
        headline: 'Live requests are paused until a finished run is recorded. Nothing was spent.',
        detail: message,
        charged: null,
      }
    case 'max_calls_exceeded':
      return {
        tone: 'refused',
        headline: 'The cost changed since the label was shown. Nothing was spent.',
        detail: `${sentence(message)}. The plan was read again; check the new cost before you retry.`,
        charged: null,
      }
    case 'cfbd_failed': {
      const charged = error.callsCharged
      const headline =
        charged === null
          ? 'The live request failed and may have been charged.'
          : `The live request failed. ${charged} ${charged === 1 ? 'call was' : 'calls were'} charged.`
      return { tone: 'failed', headline, detail: message, charged }
    }
    case 'ledger_refused':
    case 'quota_refused':
    case 'live_disabled':
    case 'operator_disabled':
    case 'forbidden':
      return { tone: 'refused', headline: 'The server refused the live request. Nothing was spent.', detail: message, charged: null }
    default:
      return { tone: 'failed', headline: 'The request failed.', detail: message, charged: null }
  }
}

/** The failed or refused live action, in words, with the calls it was charged. */
export function SpendNotice({ error }: { error: ApiError }) {
  const failure = describeSpendFailure(error)
  return (
    <div className={`banner spend-notice spend-notice-${failure.tone}`} role="alert">
      {failure.tone === 'failed' ? <WarningIcon /> : <InfoIcon />}
      <div className="banner-text">
        <p className="spend-notice-headline">{failure.headline}</p>
        <p className="spend-notice-detail">{failure.detail}</p>
      </div>
    </div>
  )
}

export interface SpendReceiptProps {
  calls: number
  source: SpendSourceKind
  cached: boolean
  /** When the cached answer was fetched (ISO timestamp); a cached receipt reads "cached <time> · 0 calls". */
  cachedAt?: string | null
  /** The quota's remaining calls after the action, when known (live mode). */
  remaining: number | null
}

/** What a finished live action cost: "1 live call spent" or "cached · 0 calls", with the quota's remaining calls. */
export function SpendReceipt({ calls, source, cached, cachedAt = null, remaining }: SpendReceiptProps) {
  const text = cached && cachedAt !== null ? `cached ${formatUtcTime(cachedAt)} · 0 calls` : formatSpent({ calls, source, cached })
  return (
    <p className="spend-receipt" role="status">
      <OkIcon />
      <span>{text}</span>
      {remaining !== null && <span className="spend-receipt-detail">{formatInteger(remaining)} calls left</span>}
    </p>
  )
}
