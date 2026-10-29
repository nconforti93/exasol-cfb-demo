import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { afterEach, describe, expect, it } from 'vitest'

import { App } from './App.tsx'
import { createApiClient } from './api/client.ts'
import { FakeBackend } from './test/backend.ts'

function renderApp(backend = new FakeBackend()) {
  render(
    <StrictMode>
      <App client={createApiClient({ fetch: backend.fetch })} />
    </StrictMode>,
  )
  return backend
}

afterEach(() => {
  window.history.replaceState(null, '', '/')
})

describe('App', () => {
  it('opens on the map home at the root path', async () => {
    renderApp()
    expect(screen.getByRole('heading', { level: 1, name: 'CFB playoff odds' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { level: 2, name: 'Every FBS team, at home' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/')
  })

  it('opens a team page at /team/<id> and a league tab at /league/<tab>', async () => {
    window.history.replaceState(null, '', '/team/102')
    const { unmount } = render(
      <StrictMode>
        <App client={createApiClient({ fetch: new FakeBackend().fetch })} />
      </StrictMode>,
    )
    expect(await screen.findByRole('heading', { level: 2, name: 'Brookvale' })).toBeInTheDocument()
    unmount()
    window.history.replaceState(null, '', '/league/stats')
    renderApp()
    expect(await screen.findByRole('heading', { level: 2, name: 'Stats' })).toBeInTheDocument()
  })

  it('shows an old path as its league tab and replaces the address, keeping the query', async () => {
    window.history.replaceState(null, '', '/odds?team=102')
    const length = window.history.length
    renderApp()
    expect(await screen.findByRole('heading', { level: 2, name: 'Odds' })).toBeInTheDocument()
    expect(window.location.pathname + window.location.search).toBe('/league/odds?team=102')
    expect(window.history.length).toBe(length)
  })

  it('opens the page a link names', async () => {
    window.history.replaceState(null, '', '/head-to-head?team=101&opponent=102')
    renderApp()
    expect(await screen.findByRole('heading', { level: 2, name: 'Head-to-head' })).toBeInTheDocument()
  })

  it('navigates between the sections and the league tabs', async () => {
    renderApp()
    await userEvent.click(screen.getByRole('link', { name: 'League' }))
    const tabs = await screen.findByRole('navigation', { name: 'League' })
    for (const name of ['Predictions', 'Upsets', 'Odds']) {
      await userEvent.click(within(tabs).getByRole('link', { name }))
      expect(await screen.findByRole('heading', { level: 2, name })).toBeInTheDocument()
    }
    await waitFor(() => expect(within(tabs).getByRole('link', { name: 'Odds' })).toHaveAttribute('aria-current', 'page'))
    expect(screen.getByRole('link', { name: 'League' })).toHaveAttribute('aria-current', 'page')
    await userEvent.click(screen.getByRole('link', { name: 'Head-to-head' }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Head-to-head' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('link', { name: 'Map' }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Every FBS team, at home' })).toBeInTheDocument()
  })

  it('puts the map\'s conference in the address and takes it out again', async () => {
    renderApp()
    await userEvent.click(await screen.findByRole('button', { name: /^Atlantic/ }))
    expect(window.location.search).toBe('?conf=Atlantic')
    await userEvent.click(screen.getByRole('button', { name: /^All FBS/ }))
    expect(window.location.search).toBe('')
  })

  it('shows the refresh panel in the header and, once opened, the inspector in the drawer', async () => {
    renderApp()
    expect(screen.getByRole('region', { name: 'Refresh' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Inspector' }))
    expect(within(screen.getByRole('complementary', { name: 'Inspector' })).getByRole('tablist', { name: 'Inspector views' })).toBeInTheDocument()
  })
})
