import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { OddsResponse, WhatIfGamesResponse, WhatIfOddsResponse } from '../api/types.ts'
import { Reply } from '../test/backend.ts'
import { fixture } from '../test/fixtures/index.ts'
import { oddsBackend, renderOdds } from './odds.testkit.tsx'

async function ready(): Promise<void> {
  await screen.findByRole('table', { name: 'Playoff odds by team' })
}

function game(name: string): HTMLElement {
  return screen.getByRole('group', { name })
}

describe('WhatIfPanel', () => {
  it('lists the coming week first, then the later weeks, each game with its home win probability', async () => {
    renderOdds()
    await ready()
    const headings = screen.getAllByRole('heading', { level: 4 }).map((heading) => heading.textContent)
    expect(headings).toEqual(['Coming week (week 10)', 'Week 12'])
    expect(game('Dunmore at Carlton')).toHaveTextContent('home team wins 55.0 %')
    expect(game('Brookvale at Ashford')).toHaveTextContent('home team wins 41.0 %')
    expect(screen.getByText('0 of 16 games set')).toBeInTheDocument()
  })

  it('shows the effective sample, the low-sample flag and the conditional odds above the base value', async () => {
    const backend = oddsBackend()
    renderOdds(backend)
    await ready()
    await userEvent.click(within(game('Brookvale at Ashford')).getByRole('button', { name: 'Brookvale wins' }))
    expect(await screen.findByText(/612 of 2,000 seasons/)).toBeInTheDocument()
    expect(screen.getByText(/low sample: few seasons match/)).toBeInTheDocument()
    expect(screen.getByText(/conditioning: the simulated seasons in which these results happened/)).toBeInTheDocument()
    const brookvale = screen.getByRole('row', { name: /Brookvale/ })
    expect(brookvale).toHaveTextContent('81.2 %')
    expect(brookvale).toHaveTextContent('91.0 %')
    expect(within(brookvale).getByText('low sample')).toBeInTheDocument()
    const posts = backend.requests('POST /api/whatif/odds')
    expect(posts).toHaveLength(1)
    expect(posts[0]?.body).toEqual({ run_id: 7, conditions: [{ game_id: 9003, winner: 'away' }] })
  })

  it('sends one request for a burst of toggles', async () => {
    const backend = oddsBackend()
    renderOdds(backend)
    await ready()
    await userEvent.click(within(game('Dunmore at Carlton')).getByRole('button', { name: 'Carlton wins' }))
    await userEvent.click(within(game('Fairhaven at Eastgate')).getByRole('button', { name: 'Fairhaven wins' }))
    await userEvent.click(within(game('Brookvale at Ashford')).getByRole('button', { name: 'Ashford wins' }))
    await screen.findByText(/612 of 2,000 seasons/)
    const posts = backend.requests('POST /api/whatif/odds')
    expect(posts).toHaveLength(1)
    expect((posts[0]?.body as { conditions: unknown[] }).conditions).toHaveLength(3)
  })

  it('does not flag a sample that is large enough', async () => {
    const answer = { ...fixture<WhatIfOddsResponse>('whatif-odds.json'), effective_sample: 1234, low_sample: false }
    answer.rows = answer.rows.map((row) => ({ ...row, low_sample: false }))
    renderOdds(oddsBackend({ 'POST /api/whatif/odds': () => answer }))
    await ready()
    await userEvent.click(within(game('Brookvale at Ashford')).getByRole('button', { name: 'Brookvale wins' }))
    expect(await screen.findByText(/1,234 of 2,000 seasons/)).toBeInTheDocument()
    expect(screen.queryByText('low sample')).toBeNull()
    expect(screen.queryByText(/low sample: few seasons/)).toBeNull()
  })

  it('clears a result by clicking it again and all of them with "Clear all"', async () => {
    const { store } = renderOdds()
    await ready()
    const button = within(game('Brookvale at Ashford')).getByRole('button', { name: 'Brookvale wins' })
    await userEvent.click(button)
    expect(button).toHaveAttribute('aria-pressed', 'true')
    expect(store.getState().conditions).toEqual([{ game_id: 9003, winner: 'away' }])
    await userEvent.click(button)
    expect(store.getState().conditions).toEqual([])
    await userEvent.click(within(game('Dunmore at Carlton')).getByRole('button', { name: 'Dunmore wins' }))
    await userEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    expect(store.getState().conditions).toEqual([])
    expect(screen.queryByText(/of 2,000 seasons/)).toBeNull()
  })

  it('offers "Not set" only for a game that has a result', async () => {
    const { store } = renderOdds()
    await ready()
    const notSet = within(game('Brookvale at Ashford')).getByRole('button', { name: 'Not set' })
    expect(notSet).toBeDisabled()
    await userEvent.click(within(game('Brookvale at Ashford')).getByRole('button', { name: 'Ashford wins' }))
    expect(store.getState().conditions).toEqual([{ game_id: 9003, winner: 'home' }])
    await userEvent.click(notSet)
    expect(store.getState().conditions).toEqual([])
  })

  it('refuses a seventeenth result with a message', async () => {
    const full = Array.from({ length: 16 }, (_, index) => ({ game_id: 100 + index, winner: 'home' as const }))
    const { store } = renderOdds(oddsBackend(), { conditions: full })
    await ready()
    expect(screen.getByText('16 of 16 games set')).toBeInTheDocument()
    await userEvent.click(within(game('Dunmore at Carlton')).getByRole('button', { name: 'Carlton wins' }))
    expect(screen.getByRole('alert')).toHaveTextContent('At most 16 games can be set')
    expect(store.getState().conditions).toHaveLength(16)
  })

  it('reloads the games, clears the toggles and says why when the published run changed', async () => {
    let gamesLoads = 0
    const backend = oddsBackend({
      'GET /api/whatif/games': () => {
        gamesLoads += 1
        const games = fixture<WhatIfGamesResponse>('whatif-games.json')
        return gamesLoads <= 1 ? games : { ...games, run: games.run === null ? null : { ...games.run, run_id: 8 } }
      },
      'POST /api/whatif/odds': () => new Reply(409, fixture('error.whatif-run-changed.json')),
    })
    const { store } = renderOdds(backend)
    await ready()
    await userEvent.click(within(game('Brookvale at Ashford')).getByRole('button', { name: 'Brookvale wins' }))
    await waitFor(() => expect(store.getState().conditions).toEqual([]))
    expect(await screen.findByText(/The published run changed, so the games were reloaded/)).toBeInTheDocument()
    await ready()
    expect(gamesLoads).toBeGreaterThanOrEqual(2)
    expect(screen.queryByText(/of 2,000 seasons/)).toBeNull()
    expect(within(game('Brookvale at Ashford')).getByRole('button', { name: 'Brookvale wins' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('shows the library message when a condition is refused', async () => {
    const backend = oddsBackend({
      'POST /api/whatif/odds': () =>
        new Reply(400, { error: 'condition_error', message: 'game 9003 is not a game of run 7' }),
    })
    renderOdds(backend)
    await ready()
    await userEvent.click(within(game('Brookvale at Ashford')).getByRole('button', { name: 'Brookvale wins' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('game 9003 is not a game of run 7')
  })

  it('offers no toggles away from the what-if point and says which run the what-ifs use', async () => {
    const backend = oddsBackend({ 'GET /api/odds': () => fixture('odds.whatif-unavailable.json') })
    renderOdds(backend)
    await ready()
    expect(screen.getByText('what-ifs use the run published at 2026, after week 9')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: /Brookvale at Ashford/ })).toBeNull()
    expect(backend.count('GET /api/whatif/games')).toBe(0)
  })

  it('says the what-if views are being regenerated, offering no toggles', async () => {
    const odds = fixture<OddsResponse>('odds.json')
    odds.whatif = { available: false, reason: 'regenerating', message: null, run_id: null, run_label: null }
    renderOdds(oddsBackend({ 'GET /api/odds': () => odds }))
    await ready()
    expect(screen.getByText('the what-if views are being regenerated')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Clear all' })).toBeNull()
  })

  it('compares the what-if point by season and week sequence, not by the week alone', async () => {
    const otherSeason = fixture<WhatIfGamesResponse>('whatif-games.json')
    if (otherSeason.run !== null) otherSeason.run = { ...otherSeason.run, season: 2025, as_of_label: '2025, after week 9' }
    renderOdds(oddsBackend({ 'GET /api/whatif/games': () => otherSeason }))
    await ready()
    expect(screen.getByText('what-ifs use the run published at 2025, after week 9')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: /Brookvale at Ashford/ })).toBeNull()
  })

  it('offers the toggles when season and week sequence both match', async () => {
    renderOdds()
    await ready()
    expect(screen.getByRole('group', { name: 'Brookvale at Ashford' })).toBeInTheDocument()
  })

  it('compares the week sequence too', async () => {
    const otherWeek = fixture<WhatIfGamesResponse>('whatif-games.json')
    if (otherWeek.run !== null) otherWeek.run = { ...otherWeek.run, as_of_week_seq: 8, as_of_label: '2026, after week 8' }
    renderOdds(oddsBackend({ 'GET /api/whatif/games': () => otherWeek }))
    await ready()
    expect(screen.getByText('what-ifs use the run published at 2026, after week 8')).toBeInTheDocument()
  })
})
