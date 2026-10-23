import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { FakeBackend } from '../test/backend.ts'
import { fixture } from '../test/fixtures/index.ts'
import { renderInApp } from '../test/render.tsx'
import { ShellProviders } from '../shell/ShellData.tsx'
import { TeamPage } from './TeamPage.tsx'

vi.setConfig({ testTimeout: 20_000 })

const initial = { page: 'team', focusTeam: 102, chosen: { mode: 'explicit', season: 2026, week_seq: 9 } } as const

function renderTeam(backend: FakeBackend = new FakeBackend({ 'GET /api/whatif/games': () => fixture('whatif-games.json') })) {
  return renderInApp(
    <ShellProviders>
      <TeamPage />
    </ShellProviders>,
    { backend, initial },
  )
}

describe('TeamPage', () => {
  it('shows the hero, the whole schedule, both SP+ seasons and the polls', async () => {
    const { container } = renderTeam()

    expect(await screen.findByRole('heading', { level: 2, name: 'Brookvale' })).toBeInTheDocument()
    const hero = container.querySelector('.team-hero') as HTMLElement
    expect(within(hero).getByText('6–1')).toBeInTheDocument()
    expect(within(hero).getByText('Atlantic · 2026, after week 9')).toBeInTheDocument()

    expect(container.querySelectorAll('.team-schedule tbody tr')).toHaveLength(12)
    expect(screen.getAllByText('Win').length).toBeGreaterThan(0)
    expect(screen.getAllByText('CFBD pregame', { exact: false }).length).toBe(5)

    expect(screen.getByText('2025: 17.9 · #21')).toBeInTheDocument()

    const polls = screen.getByRole('region', { name: 'AP and CFP ranks' })
    await userEvent.click(within(polls).getByRole('button', { name: 'Table view' }))
    expect(within(polls).getByText('after week 9')).toBeInTheDocument()
  })

  it('shows the odds with their movement, this week, games to watch, rivalries, and leads to head-to-head', async () => {
    const { container } = renderTeam()

    const odds = await screen.findByRole('region', { name: 'Playoff odds' })
    expect(within(odds).getAllByText('81.2 %')).toHaveLength(2)
    expect(within(odds).getByRole('img', { name: 'up +2.3 percentage points' })).toBeInTheDocument()
    expect(odds.querySelectorAll('.series-mark')).toHaveLength(9)

    const week = screen.getByRole('region', { name: "This week's prediction" })
    expect(within(week).getByText('71.0 %')).toBeInTheDocument()
    expect(within(week).getByText('31.2–22.4')).toBeInTheDocument()

    const watch = screen.getByRole('region', { name: 'Games to watch' })
    expect(within(watch).getByText('Dunmore')).toBeInTheDocument()

    const rivalries = screen.getByRole('region', { name: 'Rivalries' })
    const link = within(rivalries).getByRole('link', { name: 'Head-to-head: Brookvale and Ashford' })
    expect(link).toHaveAttribute('href', expect.stringContaining('opponent=101'))
    expect(link).toHaveAttribute('href', expect.stringContaining('team=102'))
    expect(container.querySelectorAll('.card-accent')).toHaveLength(1)
  })

  it('says why each outlook panel is empty without an error, and keeps the rest of the page', async () => {
    const backend = new FakeBackend({ 'GET /api/team/102/outlook': () => fixture('team-outlook.empty.json') })
    renderTeam(backend)

    expect(await screen.findByText(/Odds appear after the first simulation run/)).toBeInTheDocument()
    expect(screen.getByText(/this week's picks come with the first simulation run/)).toBeInTheDocument()
    expect(screen.getByText(/Games to watch appear once a run is published/)).toBeInTheDocument()
    expect(screen.getByText(/Rivalries appear once the series are published/)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Brookvale' })).toBeInTheDocument()
    expect(screen.getByText('Schedule and results')).toBeInTheDocument()
  })

  it('names a team that does not exist and links back to the map', async () => {
    const backend = new FakeBackend()
    renderInApp(
      <ShellProviders>
        <TeamPage />
      </ShellProviders>,
      { backend, initial: { ...initial, focusTeam: 999 } },
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('No team with id 999')
    await waitFor(() => expect(screen.getByRole('link', { name: 'Back to the map' })).toBeInTheDocument())
  })
})
