import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { renderInApp } from '../test/render.tsx'
import { ShellProviders } from '../shell/ShellData.tsx'
import { StatsPage } from './StatsPage.tsx'
import { WeekPage } from './WeekPage.tsx'

vi.setConfig({ testTimeout: 20_000 })

const week9 = { chosen: { mode: 'explicit', season: 2026, week_seq: 9 } } as const

describe('WeekPage', () => {
  it('puts the game of the week first, then the rest in the order served, with a labelled source and an upset alert', async () => {
    renderInApp(
      <ShellProviders>
        <WeekPage />
      </ShellProviders>,
      { initial: { page: 'week', ...week9 } },
    )

    const featured = await screen.findByRole('region', { name: 'Game of the week' })
    expect(within(featured).getByText('Oakridge')).toBeInTheDocument()
    expect(within(featured).getByText('Pinecrest')).toBeInTheDocument()
    expect(within(featured).getByText('62.0 %')).toBeInTheDocument()
    expect(within(featured).getByText('Upset alert')).toBeInTheDocument()
    expect(within(featured).getByText(/win probability: CFBD pregame/)).toBeInTheDocument()

    const cards = screen.getAllByRole('article')
    expect(cards.map((card) => card.getAttribute('aria-label'))).toEqual([
      'Zephyr at Westbrook',
      'Glenmoor at Brookvale',
      'Dunmore at Carlton',
      'Fairhaven at Eastgate',
      'Garland Hill at Fenwick',
      'Norwick at Irwin',
    ])
    expect(within(cards[0] as HTMLElement).getByText(/Line: Westbrook −1\.5/)).toBeInTheDocument()
  })
})

describe('StatsPage', () => {
  it('shows the four cards, and the SQL button of a card opens the inspector at its query', async () => {
    const { store } = renderInApp(
      <ShellProviders>
        <StatsPage />
      </ShellProviders>,
      { initial: { page: 'stats', ...week9 } },
    )

    const roads = await screen.findByRole('region', { name: 'Road warriors' })
    for (const name of ['SP+ movers', 'Conference strength', 'Hottest streaks']) {
      expect(screen.getByRole('region', { name })).toBeInTheDocument()
    }
    expect(within(roads).getByText('21,480 km')).toBeInTheDocument()
    expect(within(roads).getByText('4,240 km')).toBeInTheDocument()

    await userEvent.click(within(roads).getByRole('button', { name: 'SQL' }))
    expect(store.getState().inspector).toEqual({ open: true, entryId: 'q-0001' })
  })
})
