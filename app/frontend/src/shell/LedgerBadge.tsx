import type { ReactNode } from 'react'

import type { ConfigResponse } from '../api/types.ts'
import { formatInteger } from '../format/format.ts'
import { OkIcon, WarningIcon } from './icons.tsx'

interface Item {
  key: string
  tone: 'ok' | 'warn'
  text: string
}

const LEDGER_OFF_REASON = 'ledger off'

function items(config: ConfigResponse): Item[] {
  const result: Item[] = []
  const { ledger, live } = config
  if (!ledger.on) result.push({ key: 'ledger-off', tone: 'warn', text: 'ledger off' })
  if (!live.enabled) {
    const reasons = ledger.on ? live.reasons : live.reasons.filter((reason) => reason !== LEDGER_OFF_REASON)
    if (reasons.length > 0) {
      result.push({ key: 'live-disabled', tone: 'warn', text: `live actions disabled: ${reasons.join('; ')}` })
    }
  } else if (ledger.on && config.fetch_mode === 'LIVE' && ledger.plan_room !== null && ledger.plan_cap !== null) {
    const room = `${formatInteger(ledger.plan_room)} of ${formatInteger(ledger.plan_cap)}`
    const name = ledger.plan === null ? 'ledger' : `ledger ${ledger.plan}`
    result.push({ key: 'room', tone: 'ok', text: `${name}: ${room} calls free` })
  }
  if (config.read_check_skipped) result.push({ key: 'read-check', tone: 'warn', text: 'read check skipped' })
  return result
}

function icon(tone: Item['tone']): ReactNode {
  return tone === 'ok' ? <OkIcon /> : <WarningIcon />
}

/** The ledger and access state from `GET /api/config`: "ledger off", why live actions are off, a skipped read check. */
export function LedgerBadge({ config }: { config: ConfigResponse }) {
  const shown = items(config)
  if (shown.length === 0) return null
  return (
    <ul className="badge-list" aria-label="Ledger and access state">
      {shown.map((item) => (
        <li key={item.key} className={`badge badge-${item.tone}`}>
          {icon(item.tone)}
          <span>{item.text}</span>
        </li>
      ))}
    </ul>
  )
}
