import { geoPath } from 'd3-geo'
import type { GeometryCollection, Topology } from 'topojson-specification'
import { feature, mesh } from 'topojson-client'
import atlas from 'us-atlas/states-albers-10m.json'

export interface UsGeometry {
  /** One path per state, in map units. */
  states: Array<{ id: string; d: string }>
  /** The borders between states. */
  borders: string
  /** The outline of the nation. */
  outline: string
}

const topology = atlas as unknown as Topology<{ states: GeometryCollection; nation: GeometryCollection }>
const toPath = geoPath()

function build(): UsGeometry {
  const { states, nation } = topology.objects
  const stateShapes = feature(topology, states).features.map((shape) => ({
    id: String(shape.id ?? ''),
    d: toPath(shape) ?? '',
  }))
  return {
    states: stateShapes,
    borders: toPath(mesh(topology, states, (a, b) => a !== b)) ?? '',
    outline: toPath(mesh(topology, nation)) ?? '',
  }
}

let cached: UsGeometry | null = null

/** The states, their borders and the nation's outline, drawn from the atlas bundled with the app; built once. */
export function usGeometry(): UsGeometry {
  cached ??= build()
  return cached
}
