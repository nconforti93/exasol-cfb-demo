export interface Box {
  x: number
  y: number
  w: number
  h: number
}

export interface Anchor {
  id: number
  x: number
  y: number
}

export interface Placed {
  id: number
  /** Where the marker is drawn. */
  x: number
  y: number
  /** The true location. */
  ax: number
  ay: number
  /** True when the marker moved more than half its size, so a leader line joins it to its true location. */
  leader: boolean
}

export interface LayoutOptions {
  /** The marker's size in map units (screen size divided by the view's scale). */
  diameter: number
  /** The view box the markers must stay inside. */
  box: Box
}

const MAX_PASSES = 80
const POLISH_PASSES = 12
const PULL_TO_ANCHOR = 0.1
const SETTLED_OVERLAP = 0.02
const GOLDEN_ANGLE = 2.399963229728653

interface Body {
  id: number
  ax: number
  ay: number
  x: number
  y: number
}

function pushApart(bodies: Body[], diameter: number): number {
  const shifts = bodies.map(() => ({ dx: 0, dy: 0 }))
  let deepest = 0
  for (let i = 0; i < bodies.length; i += 1) {
    const a = bodies[i]!
    for (let j = i + 1; j < bodies.length; j += 1) {
      const b = bodies[j]!
      const dx = b.x - a.x
      const dy = b.y - a.y
      const gap = Math.hypot(dx, dy)
      if (gap >= diameter) continue
      deepest = Math.max(deepest, diameter - gap)
      const angle = gap < 1e-6 ? i * GOLDEN_ANGLE + j : Math.atan2(dy, dx)
      const half = (diameter - gap) / 2
      const sx = Math.cos(angle) * half
      const sy = Math.sin(angle) * half
      shifts[i]!.dx -= sx
      shifts[i]!.dy -= sy
      shifts[j]!.dx += sx
      shifts[j]!.dy += sy
    }
  }
  bodies.forEach((body, index) => {
    body.x += shifts[index]!.dx
    body.y += shifts[index]!.dy
  })
  return deepest
}

function clampTo(bodies: Body[], box: Box, radius: number): void {
  const loX = box.x + Math.min(radius, box.w / 2)
  const hiX = box.x + box.w - Math.min(radius, box.w / 2)
  const loY = box.y + Math.min(radius, box.h / 2)
  const hiY = box.y + box.h - Math.min(radius, box.h / 2)
  for (const body of bodies) {
    body.x = Math.min(hiX, Math.max(loX, body.x))
    body.y = Math.min(hiY, Math.max(loY, body.y))
  }
}

/**
 * Spreads overlapping markers apart, deterministically: markers start at their anchors in id order, and each of at most
 * 80 passes pushes overlapping pairs apart along the line between their centers, pulls every marker 10 % back toward
 * its anchor and keeps it inside the box. A few push-only passes then settle what the pull left touching. A marker
 * that ends up more than half its size from its anchor is flagged for a leader line.
 */
export function layoutMarkers(anchors: readonly Anchor[], { diameter, box }: LayoutOptions): Placed[] {
  const radius = diameter / 2
  const bodies: Body[] = [...anchors]
    .sort((a, b) => a.id - b.id)
    .map((anchor) => ({ id: anchor.id, ax: anchor.x, ay: anchor.y, x: anchor.x, y: anchor.y }))
  clampTo(bodies, box, radius)

  for (let pass = 0; pass < MAX_PASSES; pass += 1) {
    const deepest = pushApart(bodies, diameter)
    for (const body of bodies) {
      body.x += (body.ax - body.x) * PULL_TO_ANCHOR
      body.y += (body.ay - body.y) * PULL_TO_ANCHOR
    }
    clampTo(bodies, box, radius)
    if (deepest <= diameter * SETTLED_OVERLAP && pass > 0) break
  }
  for (let pass = 0; pass < POLISH_PASSES; pass += 1) {
    const deepest = pushApart(bodies, diameter)
    clampTo(bodies, box, radius)
    if (deepest <= diameter * SETTLED_OVERLAP) break
  }

  return bodies.map((body) => ({
    id: body.id,
    x: body.x,
    y: body.y,
    ax: body.ax,
    ay: body.ay,
    leader: Math.hypot(body.x - body.ax, body.y - body.ay) > radius,
  }))
}
