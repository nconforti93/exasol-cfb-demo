import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { TeamsMapResponse } from '../api/types.ts'
import { OutageProvider } from '../state/outage.tsx'
import { FakeBackend, Reply } from '../test/backend.ts'
import { fixture } from '../test/fixtures/index.ts'
import { renderInApp } from '../test/render.tsx'
import { MapPage } from './MapPage.tsx'

const map = fixture<TeamsMapResponse>('teams-map.json')
const withCoordinates = map.teams.filter((team) => team.latitude !== null)
const withoutCoordinates = map.teams.filter((team) => team.latitude === null)
const atlantic = withCoordinates.filter((team) => team.conference === 'Atlantic')

function renderMap(backend = new FakeBackend(), initial = {}) {
  return renderInApp(
    <OutageProvider>
      <MapPage />
    </OutageProvider>,
    { backend, initial: { chosen: { mode: 'explicit', season: 2026, week_seq: 9 }, ...initial } },
  )
}

async function markers(): Promise<HTMLElement[]> {
  const list = await screen.findByRole('list', { name: 'Teams' })
  return within(list).getAllByRole('listitem')
}

function viewBox(): number[] {
  const svg = document.querySelector('.map-svg')
  return (svg?.getAttribute('viewBox') ?? '').split(' ').map(Number)
}

describe('MapPage', { timeout: 15_000 }, () => {
  it('draws a marker for every team with coordinates, Honolulu in the inset, and lists the rest under "Not on the map"', async () => {
    renderMap()
    const drawn = await markers()
    expect(drawn).toHaveLength(withCoordinates.length)
    const honolulu = screen.getByRole('link', { name: /^Tidewater, / }).closest('li')!
    expect(parseFloat(honolulu.style.left)).toBeLessThan(40)
    expect(parseFloat(honolulu.style.top)).toBeGreaterThan(70)

    const tray = screen.getByRole('region', { name: 'Not on the map' })
    expect(withoutCoordinates).toHaveLength(1)
    expect(within(tray).getByRole('link', { name: /Garrick/ })).toHaveAttribute('href', '/team/147?season=2026&week=9')
    expect(screen.queryByRole('link', { name: /^Garrick, / })).toBeNull()
  })

  it('links each marker to its team page, reachable by keyboard, and opens it on a click', async () => {
    const { store } = renderMap()
    await markers()
    const brookvale = screen.getByRole('link', { name: /^Brookvale, 6–1, AP rank 6/ })
    expect(brookvale).toHaveAttribute('href', '/team/102?season=2026&week=9')
    expect(brookvale).not.toHaveAttribute('tabindex', '-1')
    brookvale.focus()
    expect(document.activeElement).toBe(brookvale)
    await userEvent.click(brookvale)
    expect(store.getState().page).toBe('team')
    expect(store.getState().focusTeam).toBe(102)
  })

  it('shows the school, mascot, conference, record and AP rank on hover and on focus', async () => {
    renderMap()
    await markers()
    const brookvale = screen.getByRole('link', { name: /^Brookvale, / })
    await userEvent.hover(brookvale)
    const card = document.querySelector('.map-hover') as HTMLElement
    expect(card).toHaveTextContent('Brookvale')
    expect(card).toHaveTextContent('Herons · Atlantic')
    expect(card).toHaveTextContent('6–1 · 3–1')
    expect(card).toHaveTextContent('AP #6')
    await userEvent.unhover(brookvale)
    expect(document.querySelector('.map-hover')).toBeNull()
    brookvale.focus()
    expect(await screen.findByText('Herons · Atlantic')).toBeInTheDocument()
  })

  it('narrows to a conference and zooms to fit it, then shows every team again at the national zoom', async () => {
    const { store } = renderMap()
    expect(await markers()).toHaveLength(withCoordinates.length)
    expect(viewBox()).toEqual([0, 0, 975, 610])

    await userEvent.click(screen.getByRole('button', { name: /^Atlantic/ }))
    expect(store.getState().mapConference).toBe('Atlantic')
    expect(await markers()).toHaveLength(atlantic.length)
    expect(viewBox()[2]).toBeLessThan(975)

    await userEvent.click(screen.getByRole('button', { name: /^All FBS/ }))
    expect(store.getState().mapConference).toBeNull()
    expect(await markers()).toHaveLength(withCoordinates.length)
    expect(viewBox()).toEqual([0, 0, 975, 610])
  })

  it('opens on the conference the address names', async () => {
    renderMap(new FakeBackend(), { mapConference: 'Lakes' })
    const lakes = withCoordinates.filter((team) => team.conference === 'Lakes')
    expect(await markers()).toHaveLength(lakes.length)
    expect(screen.getByRole('button', { name: /^Lakes/ })).toHaveAttribute('aria-pressed', 'true')
  })

  it('zooms with the buttons and goes back to the whole country', async () => {
    renderMap()
    await markers()
    const zoomOut = screen.getByRole('button', { name: 'Zoom out' })
    expect(zoomOut).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Zoom in' }))
    expect(viewBox()[2]).toBeCloseTo(650, 0)
    expect(zoomOut).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: 'Reset zoom to the whole country' }))
    expect(viewBox()).toEqual([0, 0, 975, 610])
  })

  it('has a list view with the same teams grouped by conference, with records and links', async () => {
    renderMap()
    await markers()
    await userEvent.click(screen.getByRole('button', { name: 'List' }))
    expect(document.querySelector('.map-svg')).toBeNull()
    const groups = screen.getAllByRole('region').filter((region) => region.classList.contains('map-list-group'))
    expect(groups.map((group) => group.getAttribute('aria-label'))).toEqual(map.conferences)
    const atlanticGroup = screen.getByRole('region', { name: 'Atlantic' })
    expect(within(atlanticGroup).getAllByRole('row')).toHaveLength(map.teams.filter((team) => team.conference === 'Atlantic').length + 1)
    const row = within(atlanticGroup).getByRole('row', { name: /Brookvale/ })
    expect(row).toHaveTextContent('6–1')
    expect(within(row).getByRole('link', { name: 'Brookvale' })).toHaveAttribute('href', '/team/102?season=2026&week=9')
    const listed = screen.getAllByRole('link').filter((link) => link.getAttribute('href')?.startsWith('/team/'))
    expect(listed.length).toBeGreaterThanOrEqual(map.teams.length)
  })

  it('says so when the load fails', async () => {
    const backend = new FakeBackend({ 'GET /api/teams/map': () => new Reply(500, { error: 'internal', message: 'broken' }) })
    renderMap(backend)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the teams')
  })
})
