import type { Box } from './layout.ts'
import { NATIONAL_BOX } from './projection.ts'

export const FIT_PADDING = 60
export const MIN_FIT_WIDTH = 240
export const MIN_FIT_HEIGHT = 150
export const ZOOM_STEP = 1.5
const MAX_ZOOM = 12
const USEFUL_ZOOM = 1.8
const WORTH_DROPPING = 1.3
const OUTLIER_SHARE = 0.15
const MIN_FOR_OUTLIERS = 4
const ASPECT = NATIONAL_BOX.w / NATIONAL_BOX.h

export interface Spot {
  x: number
  y: number
}

/** How far a view is zoomed in: 1 for the whole country. */
export function zoomOf(box: Box): number {
  return NATIONAL_BOX.w / box.w
}

function inside(box: Box): Box {
  if (box.w >= NATIONAL_BOX.w) return NATIONAL_BOX
  const x = Math.min(NATIONAL_BOX.x + NATIONAL_BOX.w - box.w, Math.max(NATIONAL_BOX.x, box.x))
  const y = Math.min(NATIONAL_BOX.y + NATIONAL_BOX.h - box.h, Math.max(NATIONAL_BOX.y, box.y))
  return { x, y, w: box.w, h: box.h }
}

function centeredOn(cx: number, cy: number, w: number): Box {
  const h = w / ASPECT
  return inside({ x: cx - w / 2, y: cy - h / 2, w, h })
}

/**
 * The view that fits the spots, with 60 units around them and a minimum of 240 × 150 so a small footprint is not
 * zoomed too far, at the map's aspect ratio and inside the country. Nothing to fit shows the whole country.
 */
export function fitBox(spots: readonly Spot[]): Box {
  if (spots.length === 0) return NATIONAL_BOX
  const xs = spots.map((spot) => spot.x)
  const ys = spots.map((spot) => spot.y)
  const left = Math.min(...xs) - FIT_PADDING
  const right = Math.max(...xs) + FIT_PADDING
  const top = Math.min(...ys) - FIT_PADDING
  const bottom = Math.max(...ys) + FIT_PADDING
  const w = Math.max(right - left, MIN_FIT_WIDTH)
  const h = Math.max(bottom - top, MIN_FIT_HEIGHT)
  return centeredOn((left + right) / 2, (top + bottom) / 2, Math.max(w, h * ASPECT))
}

function median(values: readonly number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 1 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2
}

/**
 * The spots a fit should be drawn around. When a few far-off teams (a conference with one member on the other coast)
 * would make the fit show the whole country anyway, up to 15 % of the spots, the farthest from the median first, are
 * left out so the rest fill the view; if that does not zoom in much further, every spot is kept.
 */
export function coreSpots<T extends Spot>(spots: readonly T[]): T[] {
  const whole = zoomOf(fitBox(spots))
  if (spots.length < MIN_FOR_OUTLIERS || whole >= USEFUL_ZOOM) return [...spots]
  const centerX = median(spots.map((spot) => spot.x))
  const centerY = median(spots.map((spot) => spot.y))
  const byDistance = [...spots].sort(
    (a, b) => Math.hypot(b.x - centerX, b.y - centerY) - Math.hypot(a.x - centerX, a.y - centerY),
  )
  const allowed = Math.max(1, Math.floor(spots.length * OUTLIER_SHARE))
  for (let dropped = 1; dropped <= allowed; dropped += 1) {
    const core = byDistance.slice(dropped)
    const zoom = zoomOf(fitBox(core))
    if (zoom >= USEFUL_ZOOM && zoom >= whole * WORTH_DROPPING) return core
  }
  return [...spots]
}

/** The view after a zoom step around its center: a factor above 1 zooms in. */
export function zoomBy(box: Box, factor: number): Box {
  const w = Math.min(NATIONAL_BOX.w, Math.max(NATIONAL_BOX.w / MAX_ZOOM, box.w / factor))
  return centeredOn(box.x + box.w / 2, box.y + box.h / 2, w)
}

export function sameBox(a: Box, b: Box): boolean {
  return a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h
}

export function blendBoxes(from: Box, to: Box, t: number): Box {
  return {
    x: from.x + (to.x - from.x) * t,
    y: from.y + (to.y - from.y) * t,
    w: from.w + (to.w - from.w) * t,
    h: from.h + (to.h - from.h) * t,
  }
}
