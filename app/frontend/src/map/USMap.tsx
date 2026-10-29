import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import type { MapTeam } from '../api/types.ts'
import { formatRecord } from '../format/format.ts'
import { teamAccent } from '../theme/teamColor.ts'
import { useTheme } from '../theme/useTheme.ts'
import { AppLink, TeamMark } from '../ui/index.ts'
import { usGeometry } from './geometry.ts'
import { HoverCard } from './HoverCard.tsx'
import { layoutMarkers, type Box, type Placed } from './layout.ts'
import { NATIONAL_BOX, type MapPoint } from './projection.ts'
import { blendScenes, type Scene } from './Scene.ts'
import { useTransition } from './useTransition.ts'
import { zoomOf } from './view.ts'

export const NATIONAL_MARKER_PX = 36
export const ZOOMED_MARKER_PX = 44
const LEADER_DOT_PX = 4
const WIDTH_STEP_PX = 16
const ZOOMED_FROM = 1.05

export function markerSize(box: Box): number {
  return zoomOf(box) > ZOOMED_FROM ? ZOOMED_MARKER_PX : NATIONAL_MARKER_PX
}

function useWidth(): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(NATIONAL_BOX.w)
  useEffect(() => {
    const element = ref.current
    if (element === null || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver((entries) => {
      const measured = entries[0]?.contentRect.width ?? 0
      if (measured > 0) setWidth(measured)
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  return [ref, width]
}

function label(team: MapTeam): string {
  const rank = team.ap_rank === null ? '' : `, AP rank ${team.ap_rank}`
  return `${team.school}, ${formatRecord(team.wins, team.losses, 0)}${rank}. Open the team page`
}

export interface USMapProps {
  /** The teams to draw, already limited to those inside the view. */
  points: readonly MapPoint[]
  /** The view to show; the map eases to it. */
  box: Box
  /** Overlaid on the map (the zoom buttons). */
  children?: ReactNode
}

/**
 * The US map: bundled state shapes under an SVG view box, and every team as a marker of constant screen size, placed
 * by `layoutMarkers`. Markers are real links, so the map works with a keyboard.
 */
export function USMap({ points, box, children }: USMapProps) {
  const theme = useTheme()
  const geometry = usGeometry()
  const [stage, width] = useWidth()
  const [hovered, setHovered] = useState<number | null>(null)
  const [focused, setFocused] = useState<number | null>(null)

  const sizePx = markerSize(box)
  const layoutWidth = Math.max(WIDTH_STEP_PX, Math.round(width / WIDTH_STEP_PX) * WIDTH_STEP_PX)
  const target = useMemo<Scene>(() => {
    const diameter = (sizePx * box.w) / layoutWidth
    const placed = layoutMarkers(points, { diameter, box })
    return { box, markers: new Map<number, Placed>(placed.map((marker) => [marker.id, marker])) }
  }, [points, box, sizePx, layoutWidth])
  const scene = useTransition(target, blendScenes)

  const accents = useMemo(() => new Map(points.map((point) => [point.id, teamAccent(point.team, theme)])), [points, theme])
  const ordered = useMemo(() => [...points].sort((a, b) => a.team.school.localeCompare(b.team.school)), [points])

  const unitsPerPx = scene.box.w / width
  const activeId = hovered ?? focused
  const active = activeId === null ? undefined : points.find((point) => point.id === activeId)
  const activeAt = active === undefined ? undefined : scene.markers.get(active.id)
  const percentX = (x: number) => ((x - scene.box.x) / scene.box.w) * 100
  const percentY = (y: number) => ((y - scene.box.y) / scene.box.h) * 100

  return (
    <div className="map-stage" ref={stage} style={{ '--marker-size': `${sizePx}px` } as React.CSSProperties}>
      <svg
        className="map-svg"
        viewBox={`${scene.box.x} ${scene.box.y} ${scene.box.w} ${scene.box.h}`}
        preserveAspectRatio="none"
        aria-hidden="true"
        focusable="false"
      >
        <g className="map-land">
          {geometry.states.map((state) => (
            <path key={state.id} d={state.d} />
          ))}
        </g>
        <path className="map-borders" d={geometry.borders} />
        <path className="map-outline" d={geometry.outline} />
        <g className="map-leaders">
          {[...scene.markers.values()]
            .filter((marker) => marker.leader)
            .map((marker) => (
              <g key={marker.id}>
                <line x1={marker.ax} y1={marker.ay} x2={marker.x} y2={marker.y} />
                <circle cx={marker.ax} cy={marker.ay} r={(LEADER_DOT_PX / 2) * unitsPerPx} />
              </g>
            ))}
        </g>
      </svg>
      <ul className="map-markers" aria-label="Teams">
        {ordered.map((point) => {
          const at = scene.markers.get(point.id)
          if (at === undefined) return null
          const ranked = point.team.ap_rank !== null
          return (
            <li
              key={point.id}
              className={`map-marker${ranked ? ' map-marker-ranked' : ''}${activeId === point.id ? ' map-marker-active' : ''}`}
              style={
                {
                  left: `${percentX(at.x)}%`,
                  top: `${percentY(at.y)}%`,
                  '--marker-ring': accents.get(point.id),
                } as React.CSSProperties
              }
              onMouseEnter={() => setHovered(point.id)}
              onMouseLeave={() => setHovered(null)}
              onFocus={() => setFocused(point.id)}
              onBlur={() => setFocused(null)}
            >
              <AppLink to={{ page: 'team', team: point.id }} aria-label={label(point.team)}>
                <TeamMark team={point.team} size={32} />
              </AppLink>
            </li>
          )
        })}
      </ul>
      {active !== undefined && activeAt !== undefined && (
        <HoverCard team={active.team} left={percentX(activeAt.x)} top={percentY(activeAt.y)} />
      )}
      {children}
    </div>
  )
}
