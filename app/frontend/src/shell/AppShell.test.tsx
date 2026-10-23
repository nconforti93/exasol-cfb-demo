import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import type { ConfigResponse } from '../api/types.ts'
import { FakeBackend, Reply, outageReply } from '../test/backend.ts'
import { fixture } from '../test/fixtures/index.ts'
import { probePages } from '../test/probePages.tsx'
import { renderInApp } from '../test/render.tsx'
import { AppShell } from './AppShell.tsx'

function Refresh() {
  return <p>refresh panel</p>
}

function Inspector() {
  return <p>inspector body</p>
}

function renderShell(backend = new FakeBackend(), initial = {}) {
  return renderInApp(<AppShell pages={probePages} refreshPanel={Refresh} inspector={Inspector} />, {
    backend,
    initial: { page: 'odds', ...initial },
  })
}

function go(url: string): void {
  window.history.replaceState(null, '', url)
}

afterEach(() => {
  go('/')
})

describe('AppShell', () => {
  it('opens at the current point and loads the page there', async () => {
    go('/league/odds')
    renderShell()
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('odds@2026/9:ready'))
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Week' })).toHaveDisplayValue(/2026, after week 9/))
    expect(window.location.pathname).toBe('/league/odds')
  })

  it('renders the banner area, page, refresh panel and footer around the page', async () => {
    renderShell()
    await waitFor(() => expect(screen.getByTestId('probe')).toBeInTheDocument())
    expect(screen.getByRole('main')).toContainElement(screen.getByTestId('probe'))
    expect(screen.getByText('refresh panel')).toBeInTheDocument()
    expect(screen.getByRole('contentinfo')).toHaveTextContent('Data provided by CollegeFootballData.com')
  })

  it('shows the footer credit on each section', async () => {
    renderShell()
    for (const name of ['League', 'Head-to-head', 'Map']) {
      await userEvent.click(screen.getByRole('link', { name }))
      expect(screen.getByRole('contentinfo')).toHaveTextContent('Data provided by CollegeFootballData.com')
      expect(screen.getByRole('contentinfo')).toHaveTextContent('not official NCAA or CFP data')
    }
  })

  it('moves to another page and loads it at the same point', async () => {
    renderShell()
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('odds@2026/9:ready'))
    await userEvent.click(screen.getByRole('link', { name: 'League' }))
    expect(window.location.pathname).toBe('/league/week')
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('week@2026/9:ready'))
  })

  it('opens a link at the page and point it names', async () => {
    go('/league/upsets?season=2026&week=8')
    renderShell(new FakeBackend(), { page: 'upsets', chosen: { mode: 'explicit', season: 2026, week_seq: 8 } })
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('upsets@2026/8:ready'))
  })

  it('moves every page to an earlier week and puts it in the address', async () => {
    renderShell()
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('odds@2026/9:ready'))
    const week = screen.getByRole('combobox', { name: 'Week' })
    await userEvent.selectOptions(week, screen.getByRole('option', { name: /after week 8/ }))
    expect(window.location.search).toBe('?season=2026&week=8')
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('odds@2026/8:ready'))
  })

  it('waits for the point before it loads a page', async () => {
    const backend = new FakeBackend({
      'GET /api/points': () => new Promise(() => {}),
      'GET /api/timeline': () => new Promise(() => {}),
    })
    renderShell(backend)
    expect(screen.getByText(/Loading the current point/)).toBeInTheDocument()
    expect(screen.queryByTestId('probe')).toBeNull()
    expect(backend.count('GET /api/odds')).toBe(0)
  })

  it('shows the outage banner with a retry instead of the page, then recovers without a restart', async () => {
    const backend = new FakeBackend({ 'GET /api/odds': () => outageReply() })
    renderShell(backend)
    const banner = await screen.findByRole('alert')
    expect(banner).toHaveTextContent('Exasol unreachable at 127.0.0.1:8563 (target local)')
    expect(screen.queryByTestId('probe')).toBeNull()
    expect(screen.getByRole('contentinfo')).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Pages' })).toBeInTheDocument()

    backend.on('GET /api/odds', () => fixture('odds.json'))
    await userEvent.click(within(banner).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('odds@2026/9:ready'))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('shows another load failure as a notice and keeps the page', async () => {
    const backend = new FakeBackend({ 'GET /api/config': () => new Reply(500, { message: 'config exploded' }) })
    renderShell(backend)
    expect(await screen.findByRole('alert')).toHaveTextContent('config exploded')
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('ready'))
  })

  it('opens and closes the inspector drawer', async () => {
    renderShell()
    expect(screen.queryByRole('complementary', { name: 'Inspector' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Inspector' }))
    const drawer = screen.getByRole('complementary', { name: 'Inspector' })
    expect(within(drawer).getByText('inspector body')).toBeInTheDocument()
    await userEvent.click(within(drawer).getByRole('button', { name: 'Close inspector' }))
    expect(screen.queryByRole('complementary', { name: 'Inspector' })).toBeNull()
  })

  it('does not render the chat region with this phase\'s configuration', async () => {
    renderShell()
    await waitFor(() => expect(screen.getByTestId('probe')).toBeInTheDocument())
    expect(screen.queryByRole('complementary', { name: 'Chat' })).toBeNull()
  })

  it('reserves the chat region when the configuration enables it', async () => {
    const backend = new FakeBackend({ 'GET /api/config': () => fixture<ConfigResponse>('config.chat-enabled.json') })
    renderShell(backend)
    expect(await screen.findByRole('complementary', { name: 'Chat' })).toBeInTheDocument()
  })

  it('has a link that skips to the page', () => {
    renderShell()
    expect(screen.getByRole('link', { name: 'Skip to the page' })).toHaveAttribute('href', '#main')
  })
})
