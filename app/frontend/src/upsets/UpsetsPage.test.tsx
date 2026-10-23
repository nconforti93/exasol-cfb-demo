import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { UpsetRow, UpsetsResponse } from '../api/types.ts'
import { FakeBackend, Reply } from '../test/backend.ts'
import { fixture } from '../test/fixtures/index.ts'
import { renderInApp } from '../test/render.tsx'
import { OutageProvider } from '../state/outage.tsx'
import { UpsetsPage } from './UpsetsPage.tsx'

// Role queries over tables are slow in jsdom, and the suite shares the machine with other builds.
vi.setConfig({ testTimeout: 20_000 })

const week9 = { chosen: { mode: 'explicit', season: 2026, week_seq: 9 } } as const

function renderPage(backend: FakeBackend, initial: Parameters<typeof renderInApp>[1] = { initial: week9 }) {
  return renderInApp(
    <OutageProvider>
      <UpsetsPage />
    </OutageProvider>,
    { backend, ...initial },
  )
}

function backendWith(file: string): FakeBackend {
  return new FakeBackend({ 'GET /api/upsets': () => fixture(file) })
}

describe('UpsetsPage', () => {
  it('shows the upset of the week with its rank since 1978, source and our model', async () => {
    renderPage(backendWith('upsets.json'))
    const feature = await screen.findByRole('region', { name: 'Upset of the week' })
    expect(within(feature).getByText('Dunmore 27, Ashford 24')).toBeInTheDocument()
    expect(within(feature).getByText('Rank since 1978')).toBeInTheDocument()
    expect(within(feature).getByText('T-37 (line)')).toBeInTheDocument()
    expect(within(feature).getByText('12 (line)')).toBeInTheDocument()
    expect(within(feature).getByText('18.0 % (line)')).toBeInTheDocument()
    expect(within(feature).getByText('0.82')).toBeInTheDocument()
    expect(within(feature).getByText('our model gave them 18.0 %')).toBeInTheDocument()
  })

  it('follows with the week\'s other upsets in rank order, saying "not predicted" for a game we never called', async () => {
    renderPage(backendWith('upsets.json'))
    const others = await screen.findByRole('region', { name: "The week's other upsets" })
    const rows = within(others).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(1)
    expect(within(rows[0]!).getByText('Fairhaven 20, Brookvale 17')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('38 (Elo)')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('not predicted')).toBeInTheDocument()
  })

  it('reads a shared rank "T-n" and a single rank plainly, each with its probability source', async () => {
    renderPage(backendWith('upsets.json'))
    await screen.findByRole('region', { name: 'Upset of the week' })
    expect(screen.getAllByText('T-37 (line)').length).toBeGreaterThan(0)
    expect(screen.getAllByText('38 (Elo)').length).toBeGreaterThan(0)
    expect(screen.queryByText('T-38 (Elo)')).toBeNull()
    expect(screen.queryByText('37 (line)')).toBeNull()
  })

  it('shows a game before the window with its season rank and no rank since 1978', async () => {
    renderPage(backendWith('upsets.pre-window.json'), { initial: { chosen: { mode: 'explicit', season: 1898, week_seq: 10 } } })
    const feature = await screen.findByRole('region', { name: 'Upset of the week' })
    expect(within(feature).getByText('Dunmore 6, Fairhaven 5')).toBeInTheDocument()
    expect(within(feature).getByText('season rank 3 (Elo)')).toBeInTheDocument()
    expect(within(feature).getByText(/Played before 1978/)).toBeInTheDocument()
    expect(within(feature).queryByText(/^T-\d+|^\d+ \(/)).toBeNull()
    const allTime = screen.getByRole('region', { name: 'All-time upsets' })
    expect(within(allTime).queryByText(/Dunmore 6, Fairhaven 5/)).toBeNull()
    const games = within(allTime)
      .getAllByRole('row')
      .slice(1)
      .map((row) => within(row).getAllByRole('cell')[0]?.nextElementSibling?.textContent)
    expect(games).toEqual(['Brookvale 24, Ashford 21', 'Eastgate 17, Carlton 14'])
  })

  it('lists the all-time and line-era rows in rank order', async () => {
    const backend = new FakeBackend({
      'GET /api/upsets': () => {
        const data = fixture<UpsetsResponse>('upsets.json')
        const extra: UpsetRow = { ...data.all_time[0]!, game_id: 6002, all_time_rank: 2, line_era_rank: 2, winner_school: 'Fairhaven' }
        return { ...data, all_time: [...data.all_time, extra].sort((a, b) => (a.all_time_rank ?? 0) - (b.all_time_rank ?? 0)) }
      },
    })
    renderPage(backend)
    const allTime = await screen.findByRole('region', { name: 'All-time upsets' })
    const ranks = within(allTime)
      .getAllByRole('row')
      .slice(1)
      .map((row) => within(row).getAllByRole('cell')[0]?.textContent)
    expect(ranks).toEqual(['1 (line)', '2 (line)', 'T-37 (line)'])
    const lineEra = screen.getByRole('region', { name: 'Betting-line era upsets' })
    expect(within(lineEra).getByText('1 (line)')).toBeInTheDocument()
    expect(within(lineEra).getByText(/Betting-line era/, { selector: 'h3' })).toBeInTheDocument()
  })

  it('labels the lists "since 1978" and "Betting-line era"', async () => {
    renderPage(backendWith('upsets.json'))
    expect(await screen.findByRole('heading', { name: 'Biggest upsets since 1978' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Betting-line era' })).toBeInTheDocument()
  })

  it('says the week had no upset and still shows the lists', async () => {
    renderPage(backendWith('upsets.empty-week.json'), { initial: { chosen: { mode: 'explicit', season: 2026, week_seq: 3 } } })
    expect(await screen.findByText(/The week had no upset \(2026, after week 3\)/)).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Upset of the week' })).toBeNull()
    const allTime = screen.getByRole('region', { name: 'All-time upsets' })
    expect(within(allTime).getByText('Carlton 21, Eastgate 20')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Betting-line era upsets' })).getByText('No upsets are ranked yet.')).toBeInTheDocument()
  })

  it('shows the server notice for a point after the current one', async () => {
    const backend = new FakeBackend({
      'GET /api/upsets': () => ({
        ...fixture<UpsetsResponse>('upsets.json'),
        notice: { code: 'after_current', message: 'Week 12 is after the current point; showing week 9.' },
      }),
    })
    renderPage(backend)
    expect(await screen.findByText('Week 12 is after the current point; showing week 9.')).toBeInTheDocument()
  })

  it('asks for the effective point with the page load signal and reads once per load', async () => {
    const backend = backendWith('upsets.json')
    renderPage(backend)
    await screen.findByRole('region', { name: 'Upset of the week' })
    const requests = backend.requests('GET /api/upsets')
    expect(requests.length).toBeGreaterThan(0)
    const last = requests.at(-1)!
    expect(last.query.get('season')).toBe('2026')
    expect(last.query.get('week')).toBe('9')
    expect(last.signal).toBeDefined()
    expect(backend.count('POST /api/preview')).toBe(0)
  })

  it('shows a load failure with a retry that loads again', async () => {
    const backend = new FakeBackend({ 'GET /api/upsets': () => new Reply(500, { message: 'upsets exploded' }) })
    renderPage(backend)
    expect(await screen.findByRole('alert')).toHaveTextContent('upsets exploded')
    backend.on('GET /api/upsets', () => fixture('upsets.json'))
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(screen.getByRole('region', { name: 'Upset of the week' })).toBeInTheDocument())
  })
})
