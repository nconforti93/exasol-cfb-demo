import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { GuideResponse, OddsResponse } from '../api/types.ts'
import { NetworkFailure, Reply, outageReply } from '../test/backend.ts'
import { fixture } from '../test/fixtures/index.ts'
import { oddsBackend, renderOdds } from './odds.testkit.tsx'

async function ready(): Promise<void> {
  await screen.findByRole('table', { name: 'Playoff odds by team' })
}

describe('OddsPage', () => {
  it('shows the published run with the rows in rank order', async () => {
    renderOdds()
    await ready()
    expect(screen.getByRole('heading', { level: 2, name: 'Odds' })).toBeInTheDocument()
    expect(screen.getByText(/Chance to make the playoff and to win the title · 2026, after week 9/)).toBeInTheDocument()
    const rows = screen.getAllByRole('row').slice(1)
    expect(rows.map((row) => within(row).getByRole('button').textContent)).toEqual(['Brookvale', 'Ashford', 'Carlton', 'Dunmore'])
  })

  it('states the run: seasons, seconds, nodes and instances', async () => {
    renderOdds()
    await ready()
    const banner = screen.getByRole('region', { name: 'Published run' })
    expect(banner).toHaveTextContent('2,000 seasons in 3.2 s on 1 node × 8 instances')
    expect(banner).toHaveTextContent('625 seasons per second')
    expect(banner).toHaveTextContent('refresh run')
    expect(banner).toHaveTextContent('published Oct 18, 2026, 22:04 UTC')
  })

  it('loads the odds and the what-if games, once for a StrictMode mount', async () => {
    const backend = oddsBackend()
    renderOdds(backend)
    await ready()
    const odds = backend.requests('GET /api/odds')
    expect(odds).toHaveLength(2)
    expect(odds[0]?.signal?.aborted).toBe(true)
    expect(odds[1]?.query.get('season')).toBe('2026')
    expect(odds[1]?.query.get('week')).toBe('9')
    expect(backend.count('GET /api/whatif/games')).toBe(1)
    expect(backend.count('GET /api/guide')).toBe(0)
    expect(backend.count('POST /api/whatif/odds')).toBe(0)
  })

  it('shows the server notice for a point after the current one', async () => {
    const odds = fixture<OddsResponse>('odds.json')
    odds.notice = { code: 'after_current', message: 'Week 12 is after the current point; showing 2026, after week 9.' }
    renderOdds(oddsBackend({ 'GET /api/odds': () => odds }))
    await ready()
    expect(screen.getByText('Week 12 is after the current point; showing 2026, after week 9.')).toBeInTheDocument()
  })

  it('says when a point has no published run', async () => {
    renderOdds(oddsBackend({ 'GET /api/odds': () => fixture('odds.empty.json') }))
    expect(await screen.findByText('No published run at 2026, after week 3.')).toBeInTheDocument()
    expect(screen.getByText('There are no odds to show at 2026, after week 3.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('shows a load failure with a retry that loads again', async () => {
    let failing = true
    const backend = oddsBackend({
      'GET /api/odds': () => {
        if (failing) return new Reply(502, { error: 'cfbd_failed', message: 'the data source failed' })
        return fixture('odds.json')
      },
    })
    renderOdds(backend)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the odds: the data source failed')
    failing = false
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await ready()
  })

  it('treats a network failure as a page error, not a crash', async () => {
    renderOdds(
      oddsBackend({
        'GET /api/odds': () => {
          throw new NetworkFailure()
        },
      }),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the odds')
  })

  it('raises the outage state on a database outage', async () => {
    const { store } = renderOdds(oddsBackend({ 'GET /api/odds': () => outageReply() }))
    await waitFor(() => expect(store.getState().outage).toBe(true))
  })
})

describe('OddsPage focus team and rooting guide', () => {
  it('opens the focus team\'s guide from the state (the link\'s ?team=)', async () => {
    const backend = oddsBackend()
    renderOdds(backend, { focusTeam: 102 })
    const guide = await screen.findByRole('region', { name: 'Rooting guide: Brookvale' })
    expect(await within(guide).findByRole('table', { name: 'Games that matter most this season' })).toBeInTheDocument()
    const requests = backend.requests('GET /api/guide')
    expect(requests.at(-1)?.query.get('team')).toBe('102')
    expect(requests.at(-1)?.query.get('season')).toBe('2026')
    expect(requests.at(-1)?.query.get('week')).toBe('9')
    const odds = screen.getByRole('table', { name: 'Playoff odds by team' })
    expect(within(odds).getByRole('row', { name: /Brookvale/ })).toHaveAttribute('aria-current', 'true')
  })

  it('shows the top games and the coming week with whom to root for', async () => {
    renderOdds(oddsBackend(), { focusTeam: 102 })
    const top = await screen.findByRole('table', { name: 'Games that matter most this season' })
    const topRow = within(top).getAllByRole('row')[1]
    expect(topRow).toHaveTextContent('Brookvale at Ashford')
    expect(topRow).toHaveTextContent('Brookvale')
    expect(topRow).toHaveTextContent('70.0 %')
    expect(topRow).toHaveTextContent('93.0 %')
    expect(topRow).toHaveTextContent('23.0 pp')
    const coming = screen.getByRole('table', { name: 'Coming week' })
    expect(within(coming).getAllByRole('row')[1]).toHaveTextContent('Dunmore at Carlton')
    expect(within(coming).getAllByRole('row')[1]).toHaveTextContent('Dunmore')
    expect(within(coming).getAllByRole('row')[1]).toHaveTextContent('77.0 %')
  })

  it('sets the focus team from the table and closes the guide', async () => {
    const backend = oddsBackend()
    const { store } = renderOdds(backend)
    await ready()
    expect(screen.getByText(/Pick a team in the table/)).toBeInTheDocument()
    await userEvent.click(within(within(screen.getByRole('table', { name: 'Playoff odds by team' })).getByRole('row', { name: /Ashford/ })).getByRole('button', { name: 'Ashford' }))
    expect(store.getState().focusTeam).toBe(101)
    expect(await screen.findByRole('region', { name: 'Rooting guide: Ashford' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Close guide' }))
    expect(store.getState().focusTeam).toBeNull()
    expect(screen.queryByRole('region', { name: /Rooting guide/ })).toBeNull()
  })

  it('says so when the run is no longer kept and the guide has no rows', async () => {
    const empty = { ...fixture<GuideResponse>('guide.json'), top: [], coming_week: [], message: 'the run of this point is no longer kept' }
    renderOdds(oddsBackend({ 'GET /api/guide': () => empty }), { focusTeam: 102 })
    expect(await screen.findByText('the run of this point is no longer kept')).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: 'Coming week' })).toBeNull()
  })

  it('shows the guide conditional on the what-if when results are set', async () => {
    const backend = oddsBackend()
    renderOdds(backend, { focusTeam: 102 })
    await screen.findByRole('table', { name: 'Games that matter most this season' })
    await userEvent.click(within(screen.getByRole('group', { name: 'Brookvale at Ashford' })).getByRole('button', { name: 'Brookvale wins' }))
    expect(await screen.findByText(/Conditional on your what-if/)).toBeInTheDocument()
    const posts = backend.requests('POST /api/whatif/guide')
    expect(posts).toHaveLength(1)
    expect(posts[0]?.body).toEqual({ run_id: 7, team_id: 102, conditions: [{ game_id: 9003, winner: 'away' }] })
    const coming = screen.getByRole('table', { name: 'Coming week' })
    expect(within(coming).getAllByRole('row')[1]).toHaveTextContent('Fairhaven at Eastgate')
    expect(within(coming).getAllByRole('row')[1]).not.toHaveTextContent('Brookvale at Ashford')
    expect(screen.queryByRole('table', { name: 'Games that matter most this season' })).toBeNull()
  })

  it('goes back to the unconditional guide when the results are cleared', async () => {
    const backend = oddsBackend()
    renderOdds(backend, { focusTeam: 102 })
    await screen.findByRole('table', { name: 'Games that matter most this season' })
    await userEvent.click(within(screen.getByRole('group', { name: 'Brookvale at Ashford' })).getByRole('button', { name: 'Brookvale wins' }))
    await screen.findByText(/Conditional on your what-if/)
    await userEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    expect(await screen.findByRole('table', { name: 'Games that matter most this season' })).toBeInTheDocument()
    expect(screen.queryByText(/Conditional on your what-if/)).toBeNull()
  })

  it('shows the unconditional guide away from the what-if point, whatever is in the state', async () => {
    const backend = oddsBackend({ 'GET /api/odds': () => fixture('odds.whatif-unavailable.json') })
    renderOdds(backend, { focusTeam: 101, conditions: [{ game_id: 9003, winner: 'away' }] })
    expect(await screen.findByRole('table', { name: 'Games that matter most this season' })).toBeInTheDocument()
    expect(backend.count('POST /api/whatif/guide')).toBe(0)
  })
})
