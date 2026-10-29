import { useMemo, useState } from 'react'

import type { TeamsMapResponse } from '../api/types.ts'
import { usePageData } from '../state/pageData.tsx'
import { useAppActions, useAppState } from '../state/react.tsx'
import { AppLink, Card, Chip, Note, SqlButton, TeamMark } from '../ui/index.ts'
import { FitIcon, ListIcon, MapIcon, MinusIcon, PlusIcon } from './icons.tsx'
import { ListView } from './ListView.tsx'
import type { Box } from './layout.ts'
import { NATIONAL_BOX, placeTeams } from './projection.ts'
import { USMap } from './USMap.tsx'
import { coreSpots, fitBox, sameBox, zoomBy, ZOOM_STEP } from './view.ts'
import './map.css'

type Mode = 'map' | 'list'

interface ManualZoom {
  conference: string | null
  box: Box
}

function within(box: Box, spot: { x: number; y: number }): boolean {
  return spot.x >= box.x && spot.x <= box.x + box.w && spot.y >= box.y && spot.y <= box.y + box.h
}

const NAMED_OUTSIDE_MAX = 3

function outsideNote(outside: readonly { team: { school: string } }[]): string {
  if (outside.length > NAMED_OUTSIDE_MAX) return `${outside.length} outside this view`
  return `${outside.map((candidate) => candidate.team.school).join(', ')} outside this view`
}

function ZoomButton({ label, onClick, disabled, children }: { label: string; onClick: () => void; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button type="button" className="map-zoom-button" aria-label={label} title={label} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  )
}

function ModeSwitch({ mode, onChange }: { mode: Mode; onChange: (mode: Mode) => void }) {
  return (
    <div className="map-modes" role="group" aria-label="View">
      <button type="button" className="map-mode" aria-pressed={mode === 'map'} onClick={() => onChange('map')}>
        <MapIcon />
        <span>Map</span>
      </button>
      <button type="button" className="map-mode" aria-pressed={mode === 'list'} onClick={() => onChange('list')}>
        <ListIcon />
        <span>List</span>
      </button>
    </div>
  )
}

function MapHome({ data }: { data: TeamsMapResponse }) {
  const { teams, conferences, queries, point } = data
  const conference = useAppState((state) => state.mapConference)
  const actions = useAppActions()
  const [mode, setMode] = useState<Mode>('map')
  const [manual, setManual] = useState<ManualZoom | null>(null)

  const shown = useMemo(() => teams.filter((team) => conference === null || team.conference === conference), [teams, conference])
  const placement = useMemo(() => placeTeams(shown), [shown])
  const fitted = useMemo(() => {
    if (conference === null) return NATIONAL_BOX
    const mainland = placement.points.filter((candidate) => !candidate.inset)
    return fitBox(coreSpots(mainland.length > 0 ? mainland : placement.points))
  }, [conference, placement])
  const target = manual !== null && manual.conference === conference ? manual.box : fitted
  const inView = useMemo(() => placement.points.filter((candidate) => within(target, candidate)), [placement, target])
  const outside = useMemo(() => placement.points.filter((candidate) => !within(target, candidate)), [placement, target])
  const counts = useMemo(() => {
    const byConference = new Map<string, number>()
    for (const team of teams) if (team.conference !== null) byConference.set(team.conference, (byConference.get(team.conference) ?? 0) + 1)
    return byConference
  }, [teams])

  const zoom = (box: Box) => setManual({ conference, box })
  const choose = (name: string | null) => {
    setManual(null)
    actions.setMapConference(name)
  }
  const national = sameBox(target, NATIONAL_BOX)
  const footer = [
    `${shown.length} of ${teams.length} teams`,
    placement.unplaced.length > 0 ? `${placement.unplaced.length} not on the map` : null,
    outside.length > 0 ? outsideNote(outside) : null,
  ]
    .filter((part): part is string => part !== null)
    .join(' · ')

  return (
    <section className="page-stack map-page" aria-labelledby="map-title">
      <Card className="map-card" footer={footer}>
        <header className="map-top">
          <div>
            <h2 id="map-title" className="map-title">
              Every FBS team, at home
            </h2>
            <p className="map-subtitle">{`${point.label} · ${teams.length} teams`}</p>
          </div>
          <div className="map-top-actions">
            <ModeSwitch mode={mode} onChange={setMode} />
            <SqlButton entryId={queries['teams'] ?? null} />
          </div>
        </header>
        <div className="map-chips" role="group" aria-label="Conference">
          <Chip selected={conference === null} onClick={() => choose(null)} count={teams.length}>
            All FBS
          </Chip>
          {conferences.map((name) => (
            <Chip key={name} selected={conference === name} onClick={() => choose(name)} count={counts.get(name) ?? 0}>
              {name}
            </Chip>
          ))}
        </div>
        {shown.length === 0 && <Note>No FBS team is listed for {conference} at this point.</Note>}
        {mode === 'map' ? (
          <USMap points={inView} box={target}>
            <div className="map-zoom" role="group" aria-label="Zoom">
              <ZoomButton label="Zoom in" onClick={() => zoom(zoomBy(target, ZOOM_STEP))}>
                <PlusIcon />
              </ZoomButton>
              <ZoomButton label="Zoom out" onClick={() => zoom(zoomBy(target, 1 / ZOOM_STEP))} disabled={national}>
                <MinusIcon />
              </ZoomButton>
              <ZoomButton label="Reset zoom to the whole country" onClick={() => zoom(NATIONAL_BOX)} disabled={national}>
                <FitIcon />
              </ZoomButton>
            </div>
          </USMap>
        ) : (
          <ListView teams={shown} conferences={conferences} />
        )}
        {placement.unplaced.length > 0 && (
          <section className="map-tray" aria-label="Not on the map">
            <h4 className="map-tray-title">Not on the map</h4>
            <ul className="map-tray-list">
              {placement.unplaced.map((team) => (
                <li key={team.team_id}>
                  <AppLink to={{ page: 'team', team: team.team_id }}>
                    <TeamMark team={team} size={24} />
                    <span>{team.school}</span>
                  </AppLink>
                </li>
              ))}
            </ul>
            <p className="map-tray-note">No stadium location is stored for these teams.</p>
          </section>
        )}
      </Card>
    </section>
  )
}

/** The home page: a map of the FBS teams at their stadiums, with conference chips, zoom buttons and a list view. */
export function MapPage() {
  const page = usePageData(({ api, point, options }) => api.teamsMap({ season: point.season, week: point.week_seq }, options))
  if (page.status === 'loading') return <p role="status">Loading the teams…</p>
  if (page.data === null) {
    return (
      <p className="banner banner-warning" role="alert">
        Could not load the teams: {page.error?.message}
      </p>
    )
  }
  return <MapHome data={page.data} />
}
