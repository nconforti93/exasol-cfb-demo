import type { Box, Placed } from './layout.ts'
import { blendBoxes } from './view.ts'

/** What the map shows at one moment: the view box and where each marker is drawn in it. */
export interface Scene {
  box: Box
  markers: ReadonlyMap<number, Placed>
}

export function blendScenes(from: Scene, to: Scene, t: number): Scene {
  const markers = new Map<number, Placed>()
  for (const [id, end] of to.markers) {
    const start = from.markers.get(id)
    markers.set(
      id,
      start === undefined ? end : { ...end, x: start.x + (end.x - start.x) * t, y: start.y + (end.y - start.y) * t },
    )
  }
  return { box: blendBoxes(from.box, to.box, t), markers }
}
