import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { describe, expect, it } from 'vitest'

import { ChartFrame } from './ChartFrame.tsx'
import { DotPlot } from './DotPlot.tsx'
import { InlineBar } from './InlineBar.tsx'
import { MoveBadge } from './MoveBadge.tsx'
import { StatusBadge } from './StatusBadge.tsx'
import { TimelineBars } from './TimelineBars.tsx'
import { Waterfall } from './Waterfall.tsx'
import { linearScale, niceTicks } from './scales.ts'

describe('scales', () => {
  it('maps a domain onto a range', () => {
    const scale = linearScale([0, 1], [10, 110])
    expect(scale(0)).toBe(10)
    expect(scale(0.5)).toBe(60)
    expect(scale(1)).toBe(110)
  })

  it('maps a degenerate domain to the start of the range', () => {
    expect(linearScale([5, 5], [0, 100])(5)).toBe(0)
  })

  it('gives round ticks that start at 0 and cover the maximum', () => {
    expect(niceTicks(1)).toEqual([0, 0.25, 0.5, 0.75, 1])
    expect(niceTicks(412)).toEqual([0, 100, 200, 300, 400, 500])
    expect(niceTicks(0)).toEqual([0, 1])
  })
})

describe('InlineBar', () => {
  it('draws a track and one bar scaled to the value, and names the value', () => {
    render(<InlineBar value={0.5} label="50.0 %" />)
    const image = screen.getByRole('img', { name: '50.0 %' })
    const bar = image.querySelector('.bar-fill')
    expect(bar).not.toBeNull()
    expect(Number(bar?.getAttribute('data-fraction'))).toBe(0.5)
    expect(image.querySelector('.bar-track')).not.toBeNull()
  })

  it('keeps a visible sliver for a value above zero and nothing for zero', () => {
    const { rerender } = render(<InlineBar value={0.0004} label="< 0.1 %" />)
    expect(Number(screen.getByRole('img').querySelector('.bar-fill')?.getAttribute('width'))).toBeGreaterThanOrEqual(1)
    rerender(<InlineBar value={0} label="0.0 %" />)
    expect(screen.getByRole('img').querySelector('.bar-fill')).toBeNull()
  })

  it('clamps to the track', () => {
    render(<InlineBar value={1.4} label="over" />)
    expect(Number(screen.getByRole('img').querySelector('.bar-fill')?.getAttribute('data-fraction'))).toBe(1)
  })
})

describe('MoveBadge', () => {
  it('shows an arrow icon, the word and the signed change for UP and DOWN', () => {
    const { container, rerender } = render(<MoveBadge move="UP" delta={0.023} />)
    expect(screen.getByLabelText('up +2.3 percentage points')).toBeInTheDocument()
    expect(container.querySelector('svg')).not.toBeNull()
    expect(screen.getByText('+2.3')).toBeInTheDocument()
    rerender(<MoveBadge move="DOWN" delta={-0.011} />)
    expect(screen.getByLabelText('down −1.1 percentage points')).toBeInTheDocument()
    expect(screen.getByText('−1.1')).toBeInTheDocument()
  })

  it('shows a "new" badge for NEW and nothing for SAME', () => {
    const { container, rerender } = render(<MoveBadge move="NEW" delta={null} />)
    expect(screen.getByText('new')).toBeInTheDocument()
    rerender(<MoveBadge move="SAME" delta={0.002} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('StatusBadge', () => {
  it.each([
    ['DONE', 'done'],
    ['RUNNING', 'running'],
    ['FAILED', 'failed'],
    ['PENDING', 'pending'],
    ['SKIPPED', 'skipped'],
    ['ABANDONED', 'abandoned'],
  ] as const)('shows an icon and the word for %s', (status, word) => {
    const { container } = render(<StatusBadge status={status} />)
    expect(screen.getByText(word)).toBeInTheDocument()
    expect(container.querySelector('svg')).not.toBeNull()
  })
})

describe('ChartFrame', () => {
  const table = { columns: ['Bucket', 'Games'], rows: [['10 %', 12], ['30 %', 14]] as Array<Array<string | number>> }

  it('shows the chart first and the same numbers in a table on request', async () => {
    const user = userEvent.setup()
    render(
      <StrictMode>
        <ChartFrame title="Calibration" table={table}>
          <svg role="img" aria-label="chart" />
        </ChartFrame>
      </StrictMode>,
    )
    expect(screen.getByRole('img', { name: 'chart' })).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Table view' }))
    const view = screen.getByRole('table')
    expect(within(view).getAllByRole('row')).toHaveLength(3)
    expect(within(view).getByText('14')).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: 'chart' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Chart view' }))
    expect(screen.getByRole('img', { name: 'chart' })).toBeInTheDocument()
  })

  it('names the chart with its title', () => {
    render(
      <ChartFrame title="Calibration" table={table}>
        <svg />
      </ChartFrame>,
    )
    expect(screen.getByRole('figure', { name: 'Calibration' })).toBeInTheDocument()
  })
})

describe('DotPlot', () => {
  const points = [
    { id: 'b1', x: 0.1, y: 0.06, label: '10 %: 6 %' },
    { id: 'b2', x: 0.5, y: 0.55, label: '50 %: 55 %' },
    { id: 'b3', x: 0.9, y: 0.95, label: '90 %: 95 %' },
  ]

  it('draws one dot per point over a diagonal reference line', () => {
    const { container } = render(<DotPlot title="Calibration" points={points} xLabel="Claimed" yLabel="Actual" />)
    expect(container.querySelectorAll('circle.dot')).toHaveLength(3)
    expect(container.querySelectorAll('line.reference')).toHaveLength(1)
    const dot = container.querySelector('circle.dot')
    expect(dot?.querySelector('title')?.textContent).toBe('10 %: 6 %')
  })

  it('has one pair of axes and no legend for a single series', () => {
    const { container } = render(<DotPlot title="Calibration" points={points} xLabel="Claimed" yLabel="Actual" />)
    expect(container.querySelectorAll('g.axis-x')).toHaveLength(1)
    expect(container.querySelectorAll('g.axis-y')).toHaveLength(1)
    expect(container.querySelector('.legend')).toBeNull()
  })

  it('places a dot higher when its y is higher', () => {
    const { container } = render(<DotPlot title="t" points={points} xLabel="x" yLabel="y" />)
    const cys = [...container.querySelectorAll('circle.dot')].map((c) => Number(c.getAttribute('cy')))
    expect(cys[0]).toBeGreaterThan(cys[1] as number)
    expect(cys[1]).toBeGreaterThan(cys[2] as number)
  })
})

describe('TimelineBars', () => {
  const steps = [
    { key: 'check', label: 'CHECK', status: 'DONE' as const, startSeconds: 0, endSeconds: 0.2 },
    { key: 'fetch', label: 'FETCH', status: 'DONE' as const, startSeconds: 0.2, endSeconds: 1.3 },
    { key: 'wait', label: 'WAIT', status: 'RUNNING' as const, startSeconds: 1.3, endSeconds: null },
    { key: 'sim', label: 'SIMULATE', status: 'PENDING' as const, startSeconds: null, endSeconds: null },
  ]

  it('draws a bar per started step, each with its status as an icon and a word', () => {
    const { container } = render(<TimelineBars steps={steps} nowSeconds={4} />)
    expect(container.querySelectorAll('rect.timeline-bar')).toHaveLength(3)
    expect(screen.getByText('FETCH')).toBeInTheDocument()
    expect(screen.getByText('running')).toBeInTheDocument()
    expect(screen.getByText('pending')).toBeInTheDocument()
  })

  it('extends a running step to now', () => {
    const { container } = render(<TimelineBars steps={steps} nowSeconds={4} />)
    const bars = [...container.querySelectorAll('rect.timeline-bar')]
    const widths = bars.map((bar) => Number(bar.getAttribute('width')))
    expect(widths[2]).toBeGreaterThan(widths[1] as number)
  })

  it('has a table view', async () => {
    render(<TimelineBars steps={steps} nowSeconds={4} />)
    await userEvent.setup().click(screen.getByRole('button', { name: 'Table view' }))
    expect(within(screen.getByRole('table')).getByText('SIMULATE')).toBeInTheDocument()
  })

  describe('with facts', () => {
    const facts = (seconds: string, rows: string) => [
      { column: 'Seconds', value: seconds },
      { column: 'Rows', value: rows },
    ]
    const withFacts = [
      { ...steps[0]!, facts: facts('0.2 s', '') },
      { ...steps[1]!, facts: facts('1.1 s', '62 rows') },
      { ...steps[2]!, facts: facts('3 s elapsed', ''), detail: 'waiting for the test hook' },
    ]

    it('shows each step\'s facts and detail beside its bar, skipping empty facts', () => {
      render(<TimelineBars steps={withFacts} nowSeconds={4} />)
      expect(screen.getByText('1.1 s · 62 rows')).toBeInTheDocument()
      expect(screen.getByText('0.2 s')).toBeInTheDocument()
      expect(screen.getByText('3 s elapsed')).toBeInTheDocument()
      expect(screen.getByText('waiting for the test hook')).toBeInTheDocument()
    })

    it('adds the fact columns and the detail to the table view', async () => {
      render(<TimelineBars steps={withFacts} nowSeconds={4} />)
      await userEvent.setup().click(screen.getByRole('button', { name: 'Table view' }))
      const table = screen.getByRole('table')
      expect(within(table).getByRole('columnheader', { name: 'Rows' })).toBeInTheDocument()
      expect(within(table).getByRole('columnheader', { name: 'Detail' })).toBeInTheDocument()
      expect(within(table).getByText('62 rows')).toBeInTheDocument()
    })

    it('names each bar for hover with its step, status and times', () => {
      const { container } = render(<TimelineBars steps={withFacts} nowSeconds={4} />)
      expect(container.querySelector('rect.timeline-bar title')?.textContent).toBe('CHECK: done, 0.0 s to 0.2 s')
    })
  })
})

describe('Waterfall', () => {
  const rows = [
    { id: '2022', label: 'year 2022', source: 'LIVE' as const, startMs: 0, durationMs: 400, status: 200 },
    { id: '2023', label: 'year 2023', source: 'LIVE' as const, startMs: 20, durationMs: 410, status: 200 },
    { id: '1997', label: 'year 1997', source: 'STORED' as const, startMs: 0, durationMs: 0, status: 200 },
    { id: '2024', label: 'year 2024', source: 'REPLAY' as const, startMs: 25, durationMs: 380, status: 200 },
  ]

  it('draws overlapping bars for parallel requests', () => {
    const { container } = render(<Waterfall title="Requests" rows={rows} />)
    const bars = [...container.querySelectorAll('rect.waterfall-bar')]
    expect(bars).toHaveLength(3)
    const [first, second] = bars as [Element, Element]
    const firstEnd = Number(first.getAttribute('x')) + Number(first.getAttribute('width'))
    expect(Number(second.getAttribute('x'))).toBeLessThan(firstEnd)
  })

  it('colors bars by source, labels them directly and carries a legend', () => {
    const { container } = render(<Waterfall title="Requests" rows={rows} />)
    expect(container.querySelector('rect.waterfall-bar.source-live')).not.toBeNull()
    expect(container.querySelector('rect.waterfall-bar.source-replay')).not.toBeNull()
    expect(screen.getAllByText('400 ms').length).toBeGreaterThan(0)
    const legend = container.querySelector('.legend')
    expect(legend).not.toBeNull()
    expect(within(legend as HTMLElement).getByText('LIVE')).toBeInTheDocument()
    expect(within(legend as HTMLElement).getByText('REPLAY')).toBeInTheDocument()
  })

  it('says that replayed rows carry recorded timings', () => {
    render(<Waterfall title="Requests" rows={rows} />)
    expect(screen.getByText(/replayed rows carry recorded timings/i)).toBeInTheDocument()
  })

  it('omits the replay note and a single-source legend when nothing is replayed', () => {
    const { container } = render(<Waterfall title="Requests" rows={rows.slice(0, 2)} />)
    expect(screen.queryByText(/recorded timings/i)).toBeNull()
    expect(container.querySelector('.legend')).toBeNull()
  })

  it('names each bar for hover with its request, source and times', () => {
    const { container } = render(<Waterfall title="Requests" rows={rows} />)
    expect(container.querySelector('rect.waterfall-bar title')?.textContent).toBe('year 2022: LIVE, 200, starts at 0 ms, takes 400 ms')
  })

  it('has a table view with the milliseconds', async () => {
    render(<Waterfall title="Requests" rows={rows} />)
    await userEvent.setup().click(screen.getByRole('button', { name: 'Table view' }))
    expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(rows.length + 1)
  })
})
