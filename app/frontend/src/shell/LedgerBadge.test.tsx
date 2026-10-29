import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ConfigResponse } from '../api/types.ts'
import { fixture } from '../test/fixtures/index.ts'
import { LedgerBadge } from './LedgerBadge.tsx'

const healthy = fixture<ConfigResponse>('config.json')
const ledgerOff = fixture<ConfigResponse>('config.ledger-off.json')

describe('LedgerBadge', () => {
  it('shows nothing for a healthy ledger in replay mode', () => {
    const { container } = render(<LedgerBadge config={healthy} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the plan room for a healthy ledger in live mode', () => {
    render(<LedgerBadge config={{ ...healthy, fetch_mode: 'LIVE' }} />)
    expect(screen.getByText('ledger app: 20 of 20 calls free')).toBeInTheDocument()
  })

  it('says "ledger off" from the start', () => {
    render(<LedgerBadge config={ledgerOff} />)
    expect(screen.getByText('ledger off')).toBeInTheDocument()
  })

  it('names why live actions are disabled, without repeating "ledger off"', () => {
    render(<LedgerBadge config={ledgerOff} />)
    expect(screen.getByText('live actions disabled: live actions need the operator path')).toBeInTheDocument()
  })

  it('names a missing cap as the reason', () => {
    const config: ConfigResponse = {
      ...healthy,
      fetch_mode: 'LIVE',
      live: { enabled: false, reasons: ['no ledger cap for plan app'] },
    }
    render(<LedgerBadge config={config} />)
    expect(screen.getByText('live actions disabled: no ledger cap for plan app')).toBeInTheDocument()
    expect(screen.queryByText(/calls free/)).toBeNull()
  })

  it('warns that the read check was skipped', () => {
    render(<LedgerBadge config={{ ...healthy, read_check_skipped: true }} />)
    expect(screen.getByText('read check skipped')).toBeInTheDocument()
  })

  it('marks each state with an icon and a word, not by color alone', () => {
    const { container } = render(<LedgerBadge config={ledgerOff} />)
    const items = container.querySelectorAll('li')
    expect(items.length).toBeGreaterThanOrEqual(2)
    for (const item of items) expect(item.querySelector('svg')).not.toBeNull()
  })
})
