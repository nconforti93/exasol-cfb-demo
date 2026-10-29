import { describe, expect, it } from 'vitest'

import { NATIONAL_BOX, placeTeams, projectStadium } from './projection.ts'
import { coreSpots, fitBox, zoomBy, zoomOf } from './view.ts'

describe('projection', () => {
  it('puts the contiguous states, Honolulu and Anchorage inside the 975 x 610 canvas', () => {
    for (const [latitude, longitude] of [
      [35.78, -78.64],
      [21.31, -157.86],
      [61.2, -149.9],
    ] as const) {
      const spot = projectStadium({ latitude, longitude })
      expect(spot).not.toBeNull()
      expect(spot![0]).toBeGreaterThan(0)
      expect(spot![0]).toBeLessThan(NATIONAL_BOX.w)
      expect(spot![1]).toBeLessThan(NATIONAL_BOX.h)
    }
  })

  it('rejects missing coordinates and places in other countries', () => {
    expect(projectStadium({ latitude: null, longitude: -90 })).toBeNull()
    expect(projectStadium({ latitude: 51.5, longitude: -0.12 })).toBeNull()
    const { points, unplaced } = placeTeams([
      { team_id: 1, latitude: 33, longitude: -84 },
      { team_id: 2, latitude: null, longitude: null },
    ] as never)
    expect(points.map((point) => point.id)).toEqual([1])
    expect(unplaced.map((team) => team.team_id)).toEqual([2])
  })
})

describe('fitBox', () => {
  it('shows the whole country when there is nothing to fit', () => {
    expect(fitBox([])).toEqual(NATIONAL_BOX)
  })

  it('never zooms closer than the minimum box, whatever the footprint', () => {
    const box = fitBox([{ x: 600, y: 300 }])
    expect(box.w).toBeGreaterThanOrEqual(240)
    expect(box.h).toBeGreaterThanOrEqual(150)
    expect(box.w / box.h).toBeCloseTo(975 / 610, 5)
  })

  it('fits a spread footprint with 60 units around it, inside the country', () => {
    const box = fitBox([
      { x: 700, y: 200 },
      { x: 850, y: 330 },
    ])
    expect(box.x).toBeLessThanOrEqual(640)
    expect(box.x + box.w).toBeGreaterThanOrEqual(910)
    expect(box.x + box.w).toBeLessThanOrEqual(NATIONAL_BOX.w)
  })

  it('steps in and back out to the whole country', () => {
    const closer = zoomBy(NATIONAL_BOX, 1.5)
    expect(zoomOf(closer)).toBeCloseTo(1.5, 5)
    expect(zoomBy(closer, 1 / 1.5)).toEqual(NATIONAL_BOX)
    expect(zoomBy(NATIONAL_BOX, 1 / 1.5)).toEqual(NATIONAL_BOX)
  })
})

describe('coreSpots', () => {
  const midwest = Array.from({ length: 7 }, (_, index) => ({ x: 650 + index * 6, y: 250 + (index % 3) * 8 }))
  const farWest = { x: 120, y: 300 }

  it('leaves out a lone far-off member when that lets the rest fill the view', () => {
    const core = coreSpots([...midwest, farWest])
    expect(core).toHaveLength(midwest.length)
    expect(zoomOf(fitBox(core))).toBeGreaterThan(zoomOf(fitBox([...midwest, farWest])))
  })

  it('keeps every spot when dropping a few would not zoom in', () => {
    const spread = [
      { x: 100, y: 300 },
      { x: 300, y: 200 },
      { x: 500, y: 400 },
      { x: 700, y: 250 },
      { x: 900, y: 350 },
    ]
    expect(coreSpots(spread)).toEqual(spread)
  })

  it('does not call anything an outlier in a group of three or fewer', () => {
    const few = [midwest[0]!, midwest[1]!, farWest]
    expect(coreSpots(few)).toEqual(few)
  })
})
