import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { TimelineResponse } from '../api/types.ts'
import { fixture } from '../test/fixtures/index.ts'
import { Timeline } from './Timeline.tsx'
import { stepFacts } from './timelineModel.ts'

function runningRun() {
  return fixture<TimelineResponse>('timeline.json').run!
}

describe('Timeline', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: false })
    vi.setSystemTime(new Date('2026-10-23T09:10:10Z'))
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows every step with its status, seconds, slices, rows and calls', () => {
    render(
      <StrictMode>
        <Timeline run={runningRun()} />
      </StrictMode>,
    )
    const figure = screen.getByRole('figure', { name: 'Refresh timeline' })
    for (const name of ['CHECK', 'FETCH', 'MERGE', 'WAIT', 'SIMULATE']) {
      expect(within(figure).getByText(name)).toBeInTheDocument()
    }
    expect(within(figure).getByText('0.2 s · 0 calls')).toBeInTheDocument()
    expect(within(figure).getByText('1.1 s · 3 slices · 62 rows · 0 calls')).toBeInTheDocument()
    expect(within(figure).getAllByText('done')).toHaveLength(3)
    expect(within(figure).getByText('running')).toBeInTheDocument()
    expect(within(figure).getByText('pending')).toBeInTheDocument()
  })

  it('names the run and its status', () => {
    render(<Timeline run={runningRun()} />)
    expect(screen.getByRole('heading', { name: /run 41/i })).toBeInTheDocument()
    expect(screen.getByText('2026, 10, regular')).toBeInTheDocument()
  })

  it('counts the elapsed seconds of a running step and of the run, and keeps counting', () => {
    render(
      <StrictMode>
        <Timeline run={runningRun()} />
      </StrictMode>,
    )
    expect(screen.getByText('10 s elapsed · 0 calls')).toBeInTheDocument()
    expect(screen.getByText('run: 10 s elapsed')).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(screen.getByText('13 s elapsed · 0 calls')).toBeInTheDocument()
    expect(screen.getByText('run: 13 s elapsed')).toBeInTheDocument()
  })

  it('stops the counter once the run has ended', () => {
    const done = fixture<TimelineResponse>('timeline.done.json').run!
    render(<Timeline run={done} />)
    expect(screen.queryByText(/elapsed/)).toBeNull()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('shows a failed step with its detail', () => {
    const failed = fixture<TimelineResponse>('timeline.failed.json').run!
    render(<Timeline run={failed} />)
    expect(screen.getAllByText('failed', { selector: '.status-word' })).toHaveLength(2)
    expect(screen.getByText('simulated failure')).toBeInTheDocument()
  })

  it('has a table view with the same figures', async () => {
    vi.useRealTimers()
    render(<Timeline run={runningRun()} />)
    await userEvent.setup().click(screen.getByRole('button', { name: 'Table view' }))
    const table = screen.getByRole('table')
    expect(within(table).getByRole('columnheader', { name: 'Slices' })).toBeInTheDocument()
    expect(within(table).getByText('3 slices')).toBeInTheDocument()
  })
})

describe('stepFacts', () => {
  it('leaves out what a step does not report', () => {
    const [seconds, slices, rows, calls] = stepFacts(
      { step_no: 1, step_name: 'CHECK', step_status: 'DONE', started_at: null, ended_at: null, seconds: 0.25, slices: null, rows_loaded: null, calls: 0, detail: null },
      0,
    )
    expect([seconds?.value, slices?.value, rows?.value, calls?.value]).toEqual(['0.3 s', '', '', '0 calls'])
  })

  it('uses singular words for one slice and one call', () => {
    const facts = stepFacts(
      { step_no: 2, step_name: 'FETCH', step_status: 'DONE', started_at: null, ended_at: null, seconds: 1, slices: 1, rows_loaded: 1000, calls: 1, detail: null },
      0,
    )
    expect(facts.map((fact) => fact.value)).toEqual(['1.0 s', '1 slice', '1,000 rows', '1 call'])
  })
})
