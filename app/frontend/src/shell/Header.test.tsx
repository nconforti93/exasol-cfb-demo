import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { ConfigResponse } from '../api/types.ts'
import { FakeBackend } from '../test/backend.ts'
import { fixture } from '../test/fixtures/index.ts'
import { renderInApp } from '../test/render.tsx'
import { Header } from './Header.tsx'
import { ShellProviders } from './ShellData.tsx'

function renderHeader(backend = new FakeBackend(), initial = {}) {
  return renderInApp(
    <ShellProviders>
      <Header refreshPanel={<p>refresh slot</p>} />
    </ShellProviders>,
    { backend, initial: { page: 'odds', ...initial } },
  )
}

describe('Header', () => {
  it('has the app name, the page tabs, the controls and the refresh slot', async () => {
    renderHeader()
    expect(screen.getByRole('heading', { level: 1, name: 'CFB playoff odds' })).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Pages' })
    expect(within(nav).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'Map',
      'This week',
      'Odds',
      'Predictions',
      'Upsets',
      'Stats',
      'Head-to-head',
    ])
    expect(screen.getByRole('button', { name: 'Inspector' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Switch to (dark|light) theme/ })).toBeInTheDocument()
    expect(screen.getByText('refresh slot')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Week' })).toBeEnabled())
  })

  it('marks the current page and switches pages without a reload', async () => {
    const { store } = renderHeader()
    expect(screen.getByRole('link', { name: 'Odds' })).toHaveAttribute('aria-current', 'page')
    await userEvent.click(screen.getByRole('link', { name: 'Upsets' }))
    expect(store.getState().page).toBe('upsets')
    expect(screen.getByRole('link', { name: 'Upsets' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Odds' })).not.toHaveAttribute('aria-current')
  })

  it('links each tab to a path that keeps the chosen point', async () => {
    renderHeader(new FakeBackend(), { chosen: { mode: 'explicit', season: 2026, week_seq: 8 } })
    expect(screen.getByRole('link', { name: 'Predictions' })).toHaveAttribute(
      'href',
      '/league/predictions?season=2026&week=8',
    )
  })

  it('leaves modified clicks to the browser', async () => {
    const { store } = renderHeader()
    const link = screen.getByRole('link', { name: 'Upsets' })
    link.addEventListener('click', (event) => event.preventDefault())
    fireEvent.click(link, { ctrlKey: true })
    expect(store.getState().page).toBe('odds')
  })

  it('toggles the inspector switch', async () => {
    const { store } = renderHeader()
    const button = screen.getByRole('button', { name: 'Inspector' })
    expect(button).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(button)
    expect(store.getState().inspector.open).toBe(true)
    expect(button).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(button)
    expect(store.getState().inspector.open).toBe(false)
  })

  it('shows "replay · no calls" in replay and does not read the quota', async () => {
    const { backend } = renderHeader()
    await waitFor(() => expect(screen.getByText('replay · no calls')).toBeInTheDocument())
    expect(backend.count('GET /api/quota')).toBe(0)
  })

  it('shows the quota in live mode and reads it fresh on request', async () => {
    const backend = new FakeBackend({
      'GET /api/config': () => ({ ...fixture<ConfigResponse>('config.json'), fetch_mode: 'LIVE' }),
    })
    renderHeader(backend)
    await waitFor(() => expect(screen.getByText('640 of 1,000 calls left')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Check quota now' }))
    await waitFor(() => expect(backend.requests('GET /api/quota').at(-1)?.query.get('fresh')).toBe('true'))
  })

  it('shows the ledger state from the configuration', async () => {
    const backend = new FakeBackend({ 'GET /api/config': () => fixture<ConfigResponse>('config.ledger-off.json') })
    renderHeader(backend)
    await waitFor(() => expect(screen.getByText('ledger off')).toBeInTheDocument())
    expect(screen.getByText('read check skipped')).toBeInTheDocument()
  })

  it('lists the points of the season and picks an earlier week into the store', async () => {
    const { store } = renderHeader()
    const week = screen.getByRole('combobox', { name: 'Week' })
    await waitFor(() => expect(week).toBeEnabled())
    expect(week).toHaveDisplayValue(/2026, after week 9/)
    await userEvent.selectOptions(week, screen.getByRole('option', { name: /after week 8/ }))
    expect(store.getState().chosen).toEqual({ mode: 'explicit', season: 2026, week_seq: 8 })
    expect(week).toHaveDisplayValue(/2026, after week 8/)
  })

  it('goes back to following when the current point is picked again', async () => {
    const { store } = renderHeader(new FakeBackend(), { chosen: { mode: 'explicit', season: 2026, week_seq: 8 } })
    const week = screen.getByRole('combobox', { name: 'Week' })
    await waitFor(() => expect(week).toBeEnabled())
    await userEvent.selectOptions(week, screen.getByRole('option', { name: /after week 9/ }))
    expect(store.getState().chosen).toEqual({ mode: 'follow' })
  })

  it('follows the current point from the picker button', async () => {
    const { store } = renderHeader(new FakeBackend(), { chosen: { mode: 'explicit', season: 2026, week_seq: 8 } })
    await userEvent.click(screen.getByRole('button', { name: 'Follow current' }))
    expect(store.getState().chosen).toEqual({ mode: 'follow' })
    await act(async () => {})
  })
})
