import { geoAlbersUsa } from 'd3-geo'

import type { MapTeam } from '../api/types.ts'
import type { Anchor, Box } from './layout.ts'

/** The view box of the bundled atlas (`states-albers-10m.json`, projected for a 975 × 610 canvas). */
export const NATIONAL_BOX: Box = { x: 0, y: 0, w: 975, h: 610 }

const project = geoAlbersUsa().scale(1300).translate([487.5, 305])

const ALASKA_MIN_LATITUDE = 51
const ALASKA_MAX_LONGITUDE = -129
const HAWAII_MAX_LATITUDE = 23
const HAWAII_MAX_LONGITUDE = -154

export interface MapPoint extends Anchor {
  team: MapTeam
  /** Drawn in the Alaska or Hawaii inset, far from the contiguous states. */
  inset: boolean
}

export interface MapPlacement {
  points: MapPoint[]
  /** Teams without usable coordinates, or whose coordinates the projection rejects. */
  unplaced: MapTeam[]
}

function inInset(latitude: number, longitude: number): boolean {
  const alaska = latitude > ALASKA_MIN_LATITUDE && longitude < ALASKA_MAX_LONGITUDE
  const hawaii = latitude < HAWAII_MAX_LATITUDE && longitude < HAWAII_MAX_LONGITUDE
  return alaska || hawaii
}

/** A team's stadium in map units, or null when it has no coordinates or lies outside the projection. */
export function projectStadium(team: Pick<MapTeam, 'latitude' | 'longitude'>): [number, number] | null {
  const { latitude, longitude } = team
  if (latitude === null || longitude === null || !Number.isFinite(latitude) || !Number.isFinite(longitude)) return null
  return project([longitude, latitude])
}

/** Splits the teams into those that sit on the map, with their anchors, and those that go to "Not on the map". */
export function placeTeams(teams: readonly MapTeam[]): MapPlacement {
  const points: MapPoint[] = []
  const unplaced: MapTeam[] = []
  for (const team of teams) {
    const spot = projectStadium(team)
    if (spot === null || team.latitude === null || team.longitude === null) unplaced.push(team)
    else points.push({ id: team.team_id, x: spot[0], y: spot[1], team, inset: inInset(team.latitude, team.longitude) })
  }
  return { points, unplaced }
}
