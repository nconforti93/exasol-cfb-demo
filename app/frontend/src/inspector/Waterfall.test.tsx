import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { InspectorResponse, RequestLine } from '../api/types.ts'
import { fixture } from '../test/fixtures/index.ts'
import { RequestWaterfall } from './Waterfall.tsx'
import { waterfallRows } from './requests.ts'

function fanOut(): RequestLine[] {
  return fixture<InspectorResponse>('inspector.json').entries[0]!.requests
}

describe('waterfallRows', () => {
  it('starts every bar at its request time, counted from the first request', () => {
    const rows = waterfallRows(fanOut())
    expect(rows.map((row) => row.startMs)).toEqual([0, 100, 100])
    expect(rows.map((row) => row.durationMs)).toEqual([400, 410, 420])
    expect(rows.map((row) => row.source)).toEqual(['LIVE', 'LIVE', 'LIVE'])
  })

  it('labels a row by its slice key and keeps its status', () => {
    const [first] = waterfallRows(fanOut())
    expect(first?.label).toBe('/games/teams?seasonType=both&team=Michigan&year=2022')
    expect(first?.status).toBe(200)
  })

  it('puts rows without a request time at 0, and a missing duration at 0', () => {
    const rows = waterfallRows([
      { slice: '/games?year=1997', source: 'STORED', status: 200, attempts: 1, ms: null, rows: 1, requested_at: null },
      { slice: '/games?year=1998', source: 'REPLAY', status: 200, attempts: 1, ms: 50, rows: 1, requested_at: '2026-10-23T09:00:00Z' },
    ])
    expect(rows.map((row) => [row.startMs, row.durationMs])).toEqual([[0, 0], [0, 50]])
  })

  it('leaves out a source the chart has no color for', () => {
    expect(waterfallRows([{ slice: '/x', source: 'MYSTERY', status: null, attempts: null, ms: 1, rows: null, requested_at: null }])).toEqual([])
  })
})

describe('RequestWaterfall', () => {
  it('draws overlapping bars for parallel slices', () => {
    const { container } = render(<RequestWaterfall requests={fanOut()} />)
    const bars = [...container.querySelectorAll('rect.waterfall-bar')]
    expect(bars).toHaveLength(3)
    const [first, second, third] = bars as [Element, Element, Element]
    const firstEnd = Number(first.getAttribute('x')) + Number(first.getAttribute('width'))
    expect(Number(second.getAttribute('x'))).toBeLessThan(firstEnd)
    expect(Number(third.getAttribute('x'))).toBeLessThan(firstEnd)
  })

  it('says that replayed rows carry recorded timings', () => {
    const replayed = fanOut().map((line) => ({ ...line, source: 'REPLAY' }))
    render(<RequestWaterfall requests={replayed} />)
    expect(screen.getByText(/replayed rows carry recorded timings/i)).toBeInTheDocument()
  })

  it('renders nothing without requests', () => {
    const { container } = render(<RequestWaterfall requests={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
