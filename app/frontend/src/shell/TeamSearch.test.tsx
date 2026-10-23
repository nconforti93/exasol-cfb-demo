import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { Team } from '../api/types.ts'
import { FakeBackend, NetworkFailure } from '../test/backend.ts'
import { renderInApp } from '../test/render.tsx'
import { ShellProviders } from './ShellData.tsx'
import { TeamSearch } from './TeamSearch.tsx'
import { findTeams } from './teamSearch.ts'

function team(id: number, school: string, extra: Partial<Team> = {}): Team {
  return { team_id: id, school, conference: null, abbreviation: null, color: null, alternate_color: null, has_logo: false, ...extra }
}

function renderSearch(backend = new FakeBackend()) {
  return renderInApp(
    <ShellProviders>
      <TeamSearch />
    </ShellProviders>,
    { backend },
  )
}

async function box(): Promise<HTMLElement> {
  const input = screen.getByRole('combobox', { name: 'Find a team' })
  await waitFor(() => expect(input).toBeEnabled())
  return input
}

describe('findTeams', () => {
  const teams = [
    team(1, 'Ohio State', { abbreviation: 'OSU' }),
    team(2, 'Ohio'),
    team(3, 'Miami (OH)', { conference: 'Mid-American' }),
    team(4, 'San José State'),
    team(5, 'Boise State'),
  ]

  it('puts schools that start with the query before schools that contain it', () => {
    expect(findTeams(teams, 'ohio').map((found) => found.school)).toEqual(['Ohio', 'Ohio State'])
    expect(findTeams(teams, 'state').map((found) => found.school)).toEqual(['Boise State', 'Ohio State', 'San José State'])
  })

  it('ignores case and accents and matches an abbreviation exactly', () => {
    expect(findTeams(teams, 'san jose').map((found) => found.school)).toEqual(['San José State'])
    expect(findTeams(teams, 'osu').map((found) => found.school)).toEqual(['Ohio State'])
    expect(findTeams(teams, '  ')).toEqual([])
  })
})

describe('TeamSearch', () => {
  it('suggests teams as you type and opens the chosen team page with Enter', async () => {
    const { store } = renderSearch()
    const input = await box()
    await userEvent.type(input, 'brook')
    expect(screen.getByRole('option', { name: /Brookvale/ })).toHaveAttribute('aria-selected', 'true')
    await userEvent.keyboard('{Enter}')
    expect(store.getState().page).toBe('team')
    expect(store.getState().focusTeam).toBe(102)
    expect(input).toHaveValue('')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('moves through the suggestions with the arrow keys and opens one with a click', async () => {
    const { store } = renderSearch()
    const input = await box()
    await userEvent.type(input, 'a')
    const options = screen.getAllByRole('option')
    expect(options.length).toBeGreaterThan(1)
    await userEvent.keyboard('{ArrowDown}')
    expect(options[1]).toHaveAttribute('aria-selected', 'true')
    await userEvent.click(options[1] as HTMLElement)
    expect(store.getState().page).toBe('team')
    expect(store.getState().focusTeam).not.toBeNull()
  })

  it('closes on Escape and shows nothing for a query no team matches', async () => {
    renderSearch()
    const input = await box()
    await userEvent.type(input, 'brook')
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).toBeNull()
    await userEvent.clear(input)
    await userEvent.type(input, 'zzzz')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('stays disabled when the team list cannot be loaded', async () => {
    renderSearch(new FakeBackend({ 'GET /api/teams': () => { throw new NetworkFailure() } }))
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Find a team' })).toBeDisabled())
  })
})
