import { act, render } from '@testing-library/react'
import { StrictMode, useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { usePolling, type Polling } from './usePolling.ts'

let delay = 5000
let polling: Polling | null = null

function Probe({ task, enabled = true }: { task: (signal: AbortSignal) => Promise<unknown>; enabled?: boolean }) {
  const current = usePolling(task, () => delay, enabled)
  useEffect(() => {
    polling = current
  })
  return null
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  delay = 5000
  polling = null
})

afterEach(() => {
  vi.useRealTimers()
})

describe('usePolling', () => {
  it('polls once at mount and then every delay, with one timer per mount in StrictMode', async () => {
    const task = vi.fn(() => Promise.resolve())
    render(
      <StrictMode>
        <Probe task={task} />
      </StrictMode>,
    )
    await advance(0)
    expect(task).toHaveBeenCalledTimes(1)
    await advance(4999)
    expect(task).toHaveBeenCalledTimes(1)
    await advance(1)
    expect(task).toHaveBeenCalledTimes(2)
    await advance(10_000)
    expect(task).toHaveBeenCalledTimes(4)
  })

  it('reads the delay again after each poll', async () => {
    const task = vi.fn(() => Promise.resolve())
    render(
      <StrictMode>
        <Probe task={task} />
      </StrictMode>,
    )
    await advance(0)
    delay = 1000
    await advance(5000)
    expect(task).toHaveBeenCalledTimes(2)
    await advance(3000)
    expect(task).toHaveBeenCalledTimes(5)
  })

  it('never overlaps a slow poll with the next one', async () => {
    let release: () => void = () => {}
    const task = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve
        }),
    )
    render(<Probe task={task} />)
    await advance(0)
    await advance(30_000)
    expect(task).toHaveBeenCalledTimes(1)
    release()
    await advance(0)
    await advance(5000)
    expect(task).toHaveBeenCalledTimes(2)
  })

  it('keeps polling after a failed poll', async () => {
    const task = vi.fn(() => Promise.reject(new Error('down')))
    render(<Probe task={task} />)
    await advance(0)
    await advance(5000)
    expect(task).toHaveBeenCalledTimes(2)
  })

  it('stops on unmount and aborts the poll in flight', async () => {
    let signal: AbortSignal | null = null
    const task = vi.fn((s: AbortSignal) => {
      signal = s
      return new Promise<void>(() => {})
    })
    const { unmount } = render(<Probe task={task} />)
    await advance(0)
    unmount()
    expect(signal).not.toBeNull()
    expect((signal as unknown as AbortSignal).aborted).toBe(true)
    await advance(60_000)
    expect(task).toHaveBeenCalledTimes(1)
  })

  it('does not poll while disabled and starts when enabled', async () => {
    const task = vi.fn(() => Promise.resolve())
    const { rerender } = render(<Probe task={task} enabled={false} />)
    await advance(20_000)
    expect(task).not.toHaveBeenCalled()
    rerender(<Probe task={task} enabled />)
    await advance(0)
    expect(task).toHaveBeenCalledTimes(1)
  })

  it('polls now on request, without a second timer', async () => {
    const task = vi.fn(() => Promise.resolve())
    render(<Probe task={task} />)
    await advance(0)
    act(() => polling?.pollNow())
    await advance(0)
    expect(task).toHaveBeenCalledTimes(2)
    await advance(5000)
    expect(task).toHaveBeenCalledTimes(3)
    await advance(5000)
    expect(task).toHaveBeenCalledTimes(4)
  })

  it('folds a request made during a poll into one follow-up poll', async () => {
    let release: () => void = () => {}
    const task = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve
        }),
    )
    render(<Probe task={task} />)
    await advance(0)
    act(() => polling?.pollNow())
    act(() => polling?.pollNow())
    release()
    await advance(0)
    expect(task).toHaveBeenCalledTimes(2)
    release()
    await advance(0)
    await advance(5000)
    expect(task).toHaveBeenCalledTimes(3)
  })
})
