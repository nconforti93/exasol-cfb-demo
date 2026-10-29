import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { describe, expect, it } from 'vitest'

import type { ScorecardResponse } from '../api/types.ts'
import { fixture } from '../test/fixtures/index.ts'
import { CalibrationChart } from './CalibrationChart.tsx'

const buckets = fixture<ScorecardResponse>('scorecard.json').calibration

function renderChart(rows = buckets) {
  return render(
    <StrictMode>
      <CalibrationChart buckets={rows} />
    </StrictMode>,
  )
}

describe('CalibrationChart', () => {
  it('draws one dot per bucket over a diagonal reference line, with no legend', () => {
    const { container } = renderChart()
    expect(container.querySelectorAll('circle.dot')).toHaveLength(5)
    expect(container.querySelectorAll('line.reference')).toHaveLength(1)
    expect(screen.queryByRole('list')).toBeNull()
    expect(screen.getByRole('img', { name: /Calibration/ })).toBeInTheDocument()
  })

  it('places each dot at its claimed probability and its actual win share', () => {
    const { container } = renderChart()
    const dots = [...container.querySelectorAll('circle.dot')]
    const xs = dots.map((dot) => Number(dot.getAttribute('cx')))
    expect(xs).toEqual([...xs].sort((a, b) => a - b))
    const reference = container.querySelector('line.reference')
    const dotAt = (index: number) => ({ x: Number(dots[index]?.getAttribute('cx')), y: Number(dots[index]?.getAttribute('cy')) })
    const slope =
      (Number(reference?.getAttribute('y2')) - Number(reference?.getAttribute('y1'))) /
      (Number(reference?.getAttribute('x2')) - Number(reference?.getAttribute('x1')))
    expect(slope).toBeLessThan(0)
    expect(dotAt(0).y).toBeGreaterThan(dotAt(4).y)
  })

  it('lists the same buckets with their games, claimed probabilities and win shares in the table view', async () => {
    renderChart()
    await userEvent.click(screen.getByRole('button', { name: 'Table view' }))
    const table = screen.getByRole('table')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(5)
    expect(within(table).getAllByRole('columnheader').map((head) => head.textContent)).toEqual([
      'Bucket',
      'Games graded',
      'Claimed win probability',
      'Actual win share',
    ])
    expect(rows[0]).toHaveTextContent('10')
    expect(rows[0]).toHaveTextContent('10.0 %')
    expect(rows[0]).toHaveTextContent('6.0 %')
    expect(rows[4]).toHaveTextContent('14')
    expect(rows[4]).toHaveTextContent('90.0 %')
    expect(rows[4]).toHaveTextContent('86.0 %')
  })

  it('says so when there is nothing to calibrate', () => {
    renderChart([])
    expect(screen.getByText('No graded games to calibrate.')).toBeInTheDocument()
    expect(screen.queryByRole('img')).toBeNull()
  })
})
