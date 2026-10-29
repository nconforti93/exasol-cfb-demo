import { describe, expect, it } from 'vitest'

import { layoutMarkers, type Anchor, type Box } from './layout.ts'

const NATIONAL: Box = { x: 0, y: 0, w: 975, h: 610 }
const DIAMETER = 28

function cluster(count: number): Anchor[] {
  return Array.from({ length: count }, (_, index) => ({
    id: 200 + index,
    x: 600 + (index % 4) * 2,
    y: 300 + Math.floor(index / 4) * 2,
  }))
}

function distance(a: { x: number; y: number }, b: { x: number; y: number }): number {
  return Math.hypot(a.x - b.x, a.y - b.y)
}

describe('layoutMarkers', () => {
  it('keeps a lone marker on its anchor with no leader line', () => {
    const [placed] = layoutMarkers([{ id: 1, x: 300, y: 200 }], { diameter: DIAMETER, box: NATIONAL })
    expect(placed).toMatchObject({ id: 1, x: 300, y: 200, ax: 300, ay: 200, leader: false })
  })

  it('pushes twelve crowded markers apart and gives the moved ones a leader line', () => {
    const placed = layoutMarkers(cluster(12), { diameter: DIAMETER, box: NATIONAL })
    for (const [index, a] of placed.entries()) {
      for (const b of placed.slice(index + 1)) {
        expect(distance(a, b)).toBeGreaterThanOrEqual(DIAMETER * 0.9)
      }
    }
    for (const marker of placed) {
      const moved = distance(marker, { x: marker.ax, y: marker.ay })
      expect(marker.leader).toBe(moved > DIAMETER / 2)
    }
    expect(placed.filter((marker) => marker.leader).length).toBeGreaterThan(0)
  })

  it('gives the same layout for the same input, whatever the order it arrives in', () => {
    const anchors = cluster(12)
    const first = layoutMarkers(anchors, { diameter: DIAMETER, box: NATIONAL })
    const second = layoutMarkers([...anchors].reverse(), { diameter: DIAMETER, box: NATIONAL })
    expect(second).toEqual(first)
    expect(layoutMarkers(anchors, { diameter: DIAMETER, box: NATIONAL })).toEqual(first)
  })

  it('separates markers that share one location', () => {
    const placed = layoutMarkers(
      [
        { id: 1, x: 400, y: 300 },
        { id: 2, x: 400, y: 300 },
      ],
      { diameter: DIAMETER, box: NATIONAL },
    )
    expect(distance(placed[0]!, placed[1]!)).toBeGreaterThanOrEqual(DIAMETER * 0.9)
  })

  it('keeps every marker inside the view box', () => {
    const corner = Array.from({ length: 6 }, (_, index) => ({ id: index, x: 2, y: 2 }))
    for (const marker of layoutMarkers(corner, { diameter: DIAMETER, box: NATIONAL })) {
      expect(marker.x).toBeGreaterThanOrEqual(DIAMETER / 2)
      expect(marker.y).toBeGreaterThanOrEqual(DIAMETER / 2)
    }
  })
})
