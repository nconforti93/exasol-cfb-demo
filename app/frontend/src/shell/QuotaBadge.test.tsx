import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { QuotaResponse } from '../api/types.ts'
import { fixture } from '../test/fixtures/index.ts'
import { QuotaBadge } from './QuotaBadge.tsx'

const live = fixture<QuotaResponse>('quota.json')

describe('QuotaBadge', () => {
  it('reads "replay · no calls" in replay mode, with no check button', () => {
    render(<QuotaBadge mode="REPLAY" quota={null} failed={false} onCheck={() => {}} />)
    expect(screen.getByText('replay · no calls')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('stays "replay · no calls" even if a reading says otherwise', () => {
    render(<QuotaBadge mode="REPLAY" quota={live} failed={false} onCheck={() => {}} />)
    expect(screen.getByText('replay · no calls')).toBeInTheDocument()
  })

  it('shows the remaining calls and the age of the reading in live mode', () => {
    render(<QuotaBadge mode="LIVE" quota={live} failed={false} onCheck={() => {}} />)
    expect(screen.getByText('640 of 1,000 calls left')).toBeInTheDocument()
    expect(screen.getByText('read 2 min ago')).toBeInTheDocument()
  })

  it('says "just now" for a reading under a minute old', () => {
    render(<QuotaBadge mode="LIVE" quota={{ ...live, age_seconds: 12 }} failed={false} onCheck={() => {}} />)
    expect(screen.getByText('read just now')).toBeInTheDocument()
  })

  it('asks for a fresh reading on request', async () => {
    const onCheck = vi.fn()
    render(<QuotaBadge mode="LIVE" quota={live} failed={false} onCheck={onCheck} />)
    await userEvent.click(screen.getByRole('button', { name: 'Check quota now' }))
    expect(onCheck).toHaveBeenCalledTimes(1)
  })

  it('says it is checking before the first reading', () => {
    render(<QuotaBadge mode="LIVE" quota={null} failed={false} onCheck={() => {}} />)
    expect(screen.getByText('quota: checking')).toBeInTheDocument()
  })

  it('says the quota is unavailable when the read failed and nothing was read', () => {
    render(<QuotaBadge mode="LIVE" quota={null} failed onCheck={() => {}} />)
    expect(screen.getByText('quota unavailable')).toBeInTheDocument()
  })

  it('keeps the last reading and notes the failure', () => {
    render(<QuotaBadge mode="LIVE" quota={live} failed onCheck={() => {}} />)
    expect(screen.getByText('640 of 1,000 calls left')).toBeInTheDocument()
    expect(screen.getByText('latest check failed')).toBeInTheDocument()
  })

  it('shows nothing about the quota before the fetch mode is known', () => {
    const { container } = render(<QuotaBadge mode={null} quota={null} failed={false} onCheck={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })
})
