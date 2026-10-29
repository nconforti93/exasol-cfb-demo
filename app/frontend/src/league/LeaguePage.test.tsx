import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { renderInApp } from '../test/render.tsx'
import { LeaguePage } from './LeaguePage.tsx'

const tabs = {
  week: () => <p>week body</p>,
  odds: () => <p>odds body</p>,
  predictions: () => <p>predictions body</p>,
  upsets: () => <p>upsets body</p>,
  stats: () => <p>stats body</p>,
}

describe('LeaguePage', () => {
  it('shows the tab bar and the page of the current tab', () => {
    renderInApp(<LeaguePage tabs={tabs} />, { initial: { page: 'predictions' } })
    const bar = screen.getByRole('navigation', { name: 'League' })
    expect(within(bar).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'This week',
      'Odds',
      'Predictions',
      'Upsets',
      'Stats',
    ])
    expect(within(bar).getByRole('link', { name: 'Predictions' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByText('predictions body')).toBeInTheDocument()
    expect(screen.queryByText('odds body')).toBeNull()
  })

  it('switches tab without a reload and keeps the chosen point in the links', async () => {
    const { store } = renderInApp(<LeaguePage tabs={tabs} />, {
      initial: { page: 'week', chosen: { mode: 'explicit', season: 2026, week_seq: 8 } },
    })
    const odds = screen.getByRole('link', { name: 'Odds' })
    expect(odds).toHaveAttribute('href', '/league/odds?season=2026&week=8')
    await userEvent.click(odds)
    expect(store.getState().page).toBe('odds')
    expect(screen.getByText('odds body')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Odds' })).toHaveAttribute('aria-current', 'page')
  })
})
