import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/client.ts'
import { SpendButton, type SpendButtonProps } from './SpendButton.tsx'
import { SpendNotice, SpendReceipt, describeSpendFailure } from './SpendNotice.tsx'
import { formatSpent, formatUtcTime } from './spendFormat.ts'

function renderButton(props: Partial<SpendButtonProps> = {}) {
  const onSpend = vi.fn()
  render(<SpendButton action="Fetch preview" plan={{ source: 'LIVE', live_calls: 1 }} busy={false} onSpend={onSpend} {...props} />)
  return onSpend
}

function failure(code: ApiError['code'], status: number, message: string, callsCharged: number | null = null): ApiError {
  const kind = status === 409 ? 'conflict' : 'bad_gateway'
  return new ApiError({ status, kind, code, message, callsCharged })
}

describe('SpendButton', () => {
  it.each([
    [{ source: 'LIVE', live_calls: 1 }, 'Fetch preview · 1 live call'],
    [{ source: 'LIVE', live_calls: 3 }, 'Fetch preview · up to 3 live calls'],
    [{ source: 'REPLAY', live_calls: 0 }, 'Fetch preview · replay · 0 calls'],
    [{ source: 'CACHED', live_calls: 0 }, 'Fetch preview · cached · 0 calls'],
    [{ source: 'STORED', live_calls: 0 }, 'Fetch preview · stored · 0 calls'],
  ] as const)('states the cost on the button for %j', (plan, name) => {
    renderButton({ plan })
    expect(screen.getByRole('button', { name })).toBeEnabled()
  })

  it('runs the action on a click', async () => {
    const onSpend = renderButton()
    await userEvent.click(screen.getByRole('button', { name: /Fetch preview/ }))
    expect(onSpend).toHaveBeenCalledTimes(1)
  })

  it('is disabled while its request runs', async () => {
    const onSpend = renderButton({ busy: true })
    const button = screen.getByRole('button', { name: /Fetch preview/ })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
    await userEvent.click(button)
    expect(onSpend).not.toHaveBeenCalled()
  })

  it('shows the reason instead of a cost when a guard refuses, and cannot be clicked', async () => {
    const onSpend = renderButton({ block: { kind: 'disabled', text: 'live actions disabled: no ledger cap for plan app' } })
    const button = screen.getByRole('button', { name: 'Fetch preview' })
    expect(button).toBeDisabled()
    expect(button).toHaveAccessibleDescription('live actions disabled: no ledger cap for plan app')
    expect(screen.queryByText(/1 live call/)).toBeNull()
    await userEvent.click(button)
    expect(onSpend).not.toHaveBeenCalled()
  })

  it('shows a busy reason the same way', () => {
    renderButton({ block: { kind: 'busy', text: 'a refresh is running (run 41): live previews resume when it ends' } })
    expect(screen.getByRole('button', { name: 'Fetch preview' })).toBeDisabled()
    expect(screen.getByText(/live previews resume when it ends/)).toBeInTheDocument()
  })

  it('asks for a confirmation naming "up to N live calls, plus retries" before it spends', async () => {
    const onSpend = renderButton({ action: 'Run refresh', plan: { source: 'LIVE', live_calls: 4 }, confirm: true })
    await userEvent.click(screen.getByRole('button', { name: 'Run refresh · up to 4 live calls' }))
    expect(onSpend).not.toHaveBeenCalled()
    const group = screen.getByRole('group', { name: 'Confirm: Run refresh' })
    expect(group).toHaveTextContent('up to 4 live calls, plus retries')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(onSpend).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('group')).toBeNull()
  })

  it('does not run when the confirmation is cancelled', async () => {
    const onSpend = renderButton({ plan: { source: 'LIVE', live_calls: 1 }, confirm: true })
    await userEvent.click(screen.getByRole('button', { name: /Fetch preview/ }))
    expect(screen.getByRole('group')).toHaveTextContent('up to 1 live call, plus retries')
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onSpend).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /Fetch preview/ })).toBeEnabled()
  })

  it('does not ask when the plan has no live calls', async () => {
    const onSpend = renderButton({ plan: { source: 'REPLAY', live_calls: 0 }, confirm: true })
    await userEvent.click(screen.getByRole('button', { name: /Fetch preview/ }))
    expect(onSpend).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('group')).toBeNull()
  })
})

describe('SpendNotice', () => {
  it('says a busy refusal cost nothing and names the holder', () => {
    render(<SpendNotice error={failure('live_busy', 409, 'another process holds the ledger lock')} />)
    const notice = screen.getByRole('alert')
    expect(notice).toHaveTextContent('Live requests are busy. Nothing was spent.')
    expect(notice).toHaveTextContent('another process holds the ledger lock. Try again when it has finished.')
  })

  it('says live requests are paused while a finished run is unrecorded', () => {
    render(<SpendNotice error={failure('reconcile_pending', 409, 'finished run 38 is not recorded in the ledger yet')} />)
    expect(screen.getByRole('alert')).toHaveTextContent('paused until a finished run is recorded. Nothing was spent.')
    expect(screen.getByRole('alert')).toHaveTextContent('finished run 38')
  })

  it('names the new cost when the plan changed since the label', () => {
    render(<SpendNotice error={failure('max_calls_exceeded', 409, 'the plan needs 3 calls, more than the 1 you saw')} />)
    expect(screen.getByRole('alert')).toHaveTextContent('The cost changed since the label was shown. Nothing was spent.')
  })

  it('reports a failed request with the calls it was charged', () => {
    render(<SpendNotice error={failure('cfbd_failed', 502, 'the fetch failed: timeout', 4)} />)
    expect(screen.getByRole('alert')).toHaveTextContent('The live request failed. 4 calls were charged.')
    expect(screen.getByRole('alert')).toHaveTextContent('the fetch failed: timeout')
  })

  it('says one charged call in the singular and an unknown charge plainly', () => {
    expect(describeSpendFailure(failure('cfbd_failed', 502, 'x', 1)).headline).toBe('The live request failed. 1 call was charged.')
    expect(describeSpendFailure(failure('cfbd_failed', 502, 'x')).headline).toBe('The live request failed and may have been charged.')
  })

  it.each(['ledger_refused', 'quota_refused', 'live_disabled', 'operator_disabled'] as const)('treats %s as a refusal that cost nothing', (code) => {
    const result = describeSpendFailure(failure(code, 409, 'no room'))
    expect(result.tone).toBe('refused')
    expect(result.headline).toContain('Nothing was spent.')
  })
})

describe('SpendReceipt and formatting', () => {
  it('reads "1 live call spent" with the quota left', () => {
    render(<SpendReceipt calls={1} source="LIVE" cached={false} remaining={639} />)
    expect(screen.getByRole('status')).toHaveTextContent('1 live call spent')
    expect(screen.getByRole('status')).toHaveTextContent('639 calls left')
  })

  it('reads "cached <time> · 0 calls" for a cached answer', () => {
    render(<SpendReceipt calls={0} source="LIVE" cached cachedAt="2026-10-23T09:00:00Z" remaining={null} />)
    expect(screen.getByRole('status')).toHaveTextContent('cached 09:00 UTC · 0 calls')
    expect(screen.queryByText(/calls left/)).toBeNull()
  })

  it('formats what an action spent', () => {
    expect(formatSpent({ calls: 3, source: 'LIVE', cached: false })).toBe('3 live calls spent')
    expect(formatSpent({ calls: 0, source: 'REPLAY', cached: false })).toBe('replay · 0 calls')
    expect(formatSpent({ calls: 0, source: 'LIVE', cached: true })).toBe('cached · 0 calls')
    expect(formatUtcTime('2026-10-23T21:05:00Z')).toBe('21:05 UTC')
    expect(formatUtcTime('not a time')).toBe('not a time')
  })
})
