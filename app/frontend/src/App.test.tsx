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
  it('renders the shell with the odds page placeholder at the root path', async () => {
    renderApp()
    expect(screen.getByRole('heading', { level: 1, name: 'CFB playoff odds' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { level: 2, name: 'Odds' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/odds')
  })

  it('opens the page a link names', async () => {
    window.history.replaceState(null, '', '/head-to-head?team=101&opponent=102')
    renderApp()
    expect(await screen.findByRole('heading', { level: 2, name: 'Head-to-head' })).toBeInTheDocument()
  })

  it('navigates between the four pages', async () => {
    renderApp()
    for (const name of ['Predictions', 'Upsets', 'Head-to-head', 'Odds']) {
      await userEvent.click(screen.getByRole('link', { name }))
      expect(await screen.findByRole('heading', { level: 2, name })).toBeInTheDocument()
    }
    await waitFor(() => expect(screen.getByRole('link', { name: 'Odds' })).toHaveAttribute('aria-current', 'page'))
  })

  it('shows the refresh panel in the header and, once opened, the inspector in the drawer', async () => {
    renderApp()
    expect(screen.getByRole('region', { name: 'Refresh' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Inspector' }))
    expect(within(screen.getByRole('complementary', { name: 'Inspector' })).getByRole('tablist', { name: 'Inspector views' })).toBeInTheDocument()
  })
})
