import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ConfigResponse, PointsResponse } from '../api/types.ts'
import { FakeBackend, Reply } from '../test/backend.ts'
import { fixture } from '../test/fixtures/index.ts'
import { renderInApp, type RenderOptions } from '../test/render.tsx'
import { ShellProviders } from '../shell/ShellData.tsx'
import { HeadToHeadPage } from './HeadToHeadPage.tsx'

// Role queries over tables are slow in jsdom, and the suite shares the machine with other builds.
vi.setConfig({ testTimeout: 20_000 })

const current = fixture<PointsResponse>('points.json').current

function makeBackend(matchup = 'matchup.json', plan = 'preview-plan.json'): FakeBackend {
  return new FakeBackend({
    'GET /api/config': () => ({ ...fixture<ConfigResponse>('config.json'), fetch_mode: 'LIVE' }),
    'GET /api/matchup': () => fixture(matchup),
    'GET /api/preview/plan': () => fixture(plan),
    'POST /api/preview': () => fixture('preview.json'),
  })
}

const week9 = { chosen: { mode: 'explicit', season: 2026, week_seq: 9 } } as const
const pair = { team: 101, opponent: 102 }

function renderPage(backend: FakeBackend, initial: RenderOptions['initial'] = { ...week9, pair }) {
  return renderInApp(
    <ShellProviders>
      <HeadToHeadPage />
    </ShellProviders>,
    { backend, initial },
  )
}

describe('HeadToHeadPage', () => {
  it('shows the series, the meetings and the next meeting of the pair in the store, without a preview request', async () => {
    const backend = makeBackend()
    renderPage(backend)

    const series = await screen.findByRole('region', { name: 'The series' })
    expect(within(series).getByText('Ashford vs Brookvale')).toBeInTheDocument()
    expect(within(series).getByText('6 (1950–1995)')).toBeInTheDocument()
    expect(within(series).getByText('Ashford 3–2–1')).toBeInTheDocument()
    expect(within(series).getByText('150 / 131')).toBeInTheDocument()
    expect(within(series).getByText('Brookvale has won the last 1 meeting')).toBeInTheDocument()
    expect(within(series).getByText('2 / 1 / 0')).toBeInTheDocument()
    expect(within(series).getByText('21 points, 1985')).toBeInTheDocument()
    expect(within(series).getByText('10 points, 1995')).toBeInTheDocument()

    const meetings = screen.getByRole('region', { name: 'The meetings' })
    const rows = within(meetings).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(within(rows[0]!).getByText('17–27')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('lost')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('50.0 % (Elo)')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('14–14')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('tied')).toBeInTheDocument()

    const next = screen.getByRole('region', { name: 'The next meeting' })
    expect(within(next).getByText('2026, week 12')).toBeInTheDocument()
    expect(within(next).getByText('Sat, Nov 21, 2026, Ashford hosts')).toBeInTheDocument()
    expect(within(next).getByText('Ashford 41.0 %, 24.1–27.3')).toBeInTheDocument()
    expect(within(next).getByText(/Our prediction \(frozen before kickoff\)/)).toBeInTheDocument()
    const stakes = within(next).getByRole('table')
    expect(within(stakes).getByRole('row', { name: /Ashford 70\.0 % 30\.0 %/ })).toBeInTheDocument()
    expect(within(stakes).getByRole('row', { name: /Brookvale 93\.0 % 60\.0 %/ })).toBeInTheDocument()

    expect(await screen.findByRole('button', { name: 'Fetch preview · 1 live call' })).toBeEnabled()
    expect(backend.count('POST /api/preview')).toBe(0)
  })

  it('reads teams and the matchup in one page load, each with the load signal', async () => {
    const backend = makeBackend()
    renderPage(backend)
    await screen.findByRole('region', { name: 'The series' })
    const matchup = backend.requests('GET /api/matchup').at(-1)!
    expect(matchup.query.get('team')).toBe('101')
    expect(matchup.query.get('opponent')).toBe('102')
    expect(matchup.signal).toBeDefined()
    expect(backend.requests('GET /api/teams').at(-1)!.signal).toBeDefined()
  })

  it('asks for two teams when the pair is empty, and reads no matchup', async () => {
    const backend = makeBackend()
    renderPage(backend, week9)
    expect(await screen.findByText(/Pick two teams to see every meeting since 1869/)).toBeInTheDocument()
    await waitFor(() => expect(backend.count('GET /api/teams')).toBeGreaterThan(0))
    expect(backend.count('GET /api/matchup')).toBe(0)
    expect(backend.count('GET /api/preview/plan')).toBe(0)
    expect(screen.getByRole('combobox', { name: 'Team' })).toBeEnabled()
  })

  it('puts the chosen teams in the store and reads their matchup', async () => {
    const backend = makeBackend()
    const view = renderPage(backend, week9)
    const team = await screen.findByRole('combobox', { name: 'Team' })
    await waitFor(() => expect(within(team).getAllByRole('option').length).toBeGreaterThan(1))
    await userEvent.selectOptions(team, 'Ashford')
    expect(view.store.getState().pair).toEqual({ team: 101, opponent: null })
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Opponent' }), 'Brookvale')
    expect(view.store.getState().pair).toEqual({ team: 101, opponent: 102 })
    await screen.findByRole('region', { name: 'The series' })
    expect(backend.requests('GET /api/matchup').at(-1)!.query.get('opponent')).toBe('102')
  })

  it('narrows the lists by school, and never offers a team against itself', async () => {
    renderPage(makeBackend())
    const team = await screen.findByRole('combobox', { name: 'Team' })
    await waitFor(() => expect(within(team).getAllByRole('option').length).toBeGreaterThan(1))
    expect(within(team).queryByRole('option', { name: 'Brookvale' })).toBeNull()
    expect(within(screen.getByRole('combobox', { name: 'Opponent' })).queryByRole('option', { name: 'Ashford' })).toBeNull()
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search schools' }), 'car')
    const opponent = screen.getByRole('combobox', { name: 'Opponent' })
    expect(within(opponent).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Choose a team',
      'Brookvale',
      'Carlton',
    ])
  })

  it('swaps the teams in the store and reads the pair from the other side', async () => {
    const backend = makeBackend()
    const view = renderPage(backend)
    await screen.findByRole('region', { name: 'The series' })
    await userEvent.click(screen.getByRole('button', { name: 'Swap teams' }))
    expect(view.store.getState().pair).toEqual({ team: 102, opponent: 101 })
    await waitFor(() => expect(backend.requests('GET /api/matchup').at(-1)!.query.get('team')).toBe('102'))
  })

  it('keeps the pickers in place while another pair loads', async () => {
    const view = renderPage(makeBackend())
    await screen.findByRole('region', { name: 'The series' })
    const team = screen.getByRole('combobox', { name: 'Team' })
    act(() => view.store.actions.setPair(103, 104))
    expect(screen.getByRole('combobox', { name: 'Team' })).toBe(team)
    expect(within(team).getAllByRole('option').length).toBeGreaterThan(1)
  })

  it('says teams that never met have no series and no preview, and asks for no plan', async () => {
    const backend = makeBackend('matchup.never-met.json')
    renderPage(backend, { ...week9, pair: { team: 105, opponent: 103 } })
    expect(await screen.findByText('Eastgate and Carlton have never met.')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'The series' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Game preview' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Fetch preview/ })).toBeNull()
    expect(backend.count('GET /api/preview/plan')).toBe(0)
    expect(backend.count('POST /api/preview')).toBe(0)
  })

  it('says a pair that does not meet again still has its latest meeting to preview', async () => {
    const backend = makeBackend('matchup.latest.json', 'preview-plan.latest.json')
    renderPage(backend)
    expect(await screen.findByText('Ashford and Brookvale do not meet again this season.')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'The next meeting' })).toBeNull()
    expect(await screen.findByText(/Previews their latest meeting/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Fetch preview/ })).toBeEnabled()
  })

  it('labels the next meeting "as of" the current point when the picker is on an earlier week', async () => {
    renderPage(makeBackend(), { chosen: { mode: 'explicit', season: 2026, week_seq: 8 }, pair, current })
    const next = await screen.findByRole('region', { name: 'The next meeting' })
    expect(within(next).getByText('as of 2026, after week 9')).toBeInTheDocument()
  })

  it('does not label the next meeting at the current point', async () => {
    renderPage(makeBackend(), { ...week9, pair, current })
    const next = await screen.findByRole('region', { name: 'The next meeting' })
    expect(within(next).queryByText(/^as of/)).toBeNull()
  })

  it('says a next meeting without published odds has no stakes', async () => {
    const backend = makeBackend()
    backend.on('GET /api/matchup', () => {
      const data = fixture<{ next: Record<string, unknown> }>('matchup.json')
      return { ...data, next: { ...data.next, stakes_run_id: null, team_p_in_cfp_if_win: null } }
    })
    renderPage(backend)
    const next = await screen.findByRole('region', { name: 'The next meeting' })
    expect(within(next).queryByRole('table')).toBeNull()
    expect(within(next).getByText(/No published playoff odds/)).toBeInTheDocument()
  })

  it('shows a load failure with a retry', async () => {
    const backend = makeBackend()
    backend.on('GET /api/matchup', () => new Reply(500, { message: 'matchup exploded' }))
    renderPage(backend)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the head-to-head: matchup exploded')
    backend.on('GET /api/matchup', () => fixture('matchup.json'))
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await screen.findByRole('region', { name: 'The series' })
  })

  it('fetches the preview from the card of the pair, naming only the pair', async () => {
    const backend = makeBackend()
    renderPage(backend)
    await userEvent.click(await screen.findByRole('button', { name: 'Fetch preview · 1 live call' }))
    expect(await screen.findByText('1 live call spent')).toBeInTheDocument()
    expect(backend.requests('POST /api/preview')[0]!.body).toEqual({ team: 101, opponent: 102, max_calls: 1, force: false })
  })
})
