import { act, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { PointsResponse, QuotaResponse } from '../api/types.ts'
import { OutageProvider, useOutage } from '../state/outage.tsx'
import { FakeBackend, Reply, outageReply } from '../test/backend.ts'
import { fixture } from '../test/fixtures/index.ts'
import { renderInApp } from '../test/render.tsx'
import { ShellDataProvider, useShellData } from './ShellData.tsx'

const FIVE_MINUTES = 5 * 60 * 1000

function Probe() {
  const data = useShellData()
  const outage = useOutage()
  return (
    <div>
      <p data-testid="points">{data.points?.points.length ?? '-'}</p>
      <p data-testid="season">{data.points?.season ?? '-'}</p>
      <p data-testid="quota">{data.quota?.remaining_calls ?? '-'}</p>
      <p data-testid="quota-failed">{String(data.quotaFailed)}</p>
      <p data-testid="load-error">{data.loadError?.message ?? '-'}</p>
      <p data-testid="outage">{outage.message ?? '-'}</p>
      <button onClick={() => data.selectSeason(2025)}>season 2025</button>
      <button onClick={() => data.refreshQuota(true)}>fresh quota</button>
      <button onClick={() => data.refreshConfig()}>reconfigure</button>
    </div>
  )
}

function renderData(backend: FakeBackend, initial = {}) {
  return renderInApp(
    <OutageProvider>
      <ShellDataProvider>
        <Probe />
      </ShellDataProvider>
    </OutageProvider>,
    { backend, initial },
  )
}

async function settle(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('ShellDataProvider', () => {
  it('loads the configuration and the points into the store', async () => {
    const { store } = renderData(new FakeBackend())
    await settle()
    expect(store.getState().config?.target).toBe('local')
    expect(store.getState().current?.as_of_label).toBe('2026, after week 9')
    expect(screen.getByTestId('points')).toHaveTextContent('4')
  })

  it('asks for the season of the opened point', async () => {
    const backend = new FakeBackend()
    renderData(backend, { chosen: { mode: 'explicit', season: 2025, week_seq: 4 } })
    await settle()
    expect(backend.requests('GET /api/points').at(-1)?.query.get('season')).toBe('2025')
  })

  it('loads the points of another season on request', async () => {
    const backend = new FakeBackend()
    renderData(backend)
    await settle()
    act(() => screen.getByRole('button', { name: 'season 2025' }).click())
    await settle()
    expect(backend.requests('GET /api/points').at(-1)?.query.get('season')).toBe('2025')
  })

  it('never reads the quota in replay mode', async () => {
    const backend = new FakeBackend()
    renderData(backend)
    await settle(2 * FIVE_MINUTES)
    expect(backend.count('GET /api/quota')).toBe(0)
    expect(screen.getByTestId('quota')).toHaveTextContent('-')
  })

  it('reads the quota in live mode at once and then every five minutes', async () => {
    const backend = new FakeBackend({
      'GET /api/config': () => ({ ...fixture<object>('config.json'), fetch_mode: 'LIVE' }),
    })
    renderData(backend)
    await settle()
    expect(backend.count('GET /api/quota')).toBe(1)
    expect(screen.getByTestId('quota')).toHaveTextContent('640')
    await settle(FIVE_MINUTES - 1)
    expect(backend.count('GET /api/quota')).toBe(1)
    await settle(1)
    expect(backend.count('GET /api/quota')).toBe(2)
  })

  it('reads a fresh quota on request, and keeps the reading when the call fails', async () => {
    const backend = new FakeBackend({
      'GET /api/config': () => ({ ...fixture<object>('config.json'), fetch_mode: 'LIVE' }),
    })
    renderData(backend)
    await settle()
    const fresh = { ...fixture<QuotaResponse>('quota.json'), remaining_calls: 600, age_seconds: 0 }
    backend.on('GET /api/quota', () => fresh)
    act(() => screen.getByRole('button', { name: 'fresh quota' }).click())
    await settle()
    expect(backend.requests('GET /api/quota').at(-1)?.query.get('fresh')).toBe('true')
    expect(screen.getByTestId('quota')).toHaveTextContent('600')
    backend.on('GET /api/quota', () => new Reply(502, { error: 'cfbd_failed', message: 'info failed' }))
    act(() => screen.getByRole('button', { name: 'fresh quota' }).click())
    await settle()
    expect(screen.getByTestId('quota')).toHaveTextContent('600')
    expect(screen.getByTestId('quota-failed')).toHaveTextContent('true')
  })

  it('loads the configuration and the points again when every page reloads', async () => {
    const backend = new FakeBackend()
    const { store } = renderData(backend)
    await settle()
    const before = backend.count('GET /api/config')
    act(() => store.actions.reload())
    await settle()
    expect(backend.count('GET /api/config')).toBe(before + 1)
    expect(backend.count('GET /api/points')).toBeGreaterThan(1)
  })

  it('loads the configuration again on request, for a page that changed the ledger', async () => {
    const backend = new FakeBackend()
    renderData(backend)
    await settle()
    const before = backend.count('GET /api/config')
    act(() => screen.getByRole('button', { name: 'reconfigure' }).click())
    await settle()
    expect(backend.count('GET /api/config')).toBe(before + 1)
  })

  it('loads the points again to label a newer point offered to a browser that chose its own', async () => {
    const backend = new FakeBackend()
    const { store } = renderData(backend, { chosen: { mode: 'explicit', season: 2026, week_seq: 8 } })
    await settle()
    const before = backend.count('GET /api/points')
    const newer = fixture<PointsResponse>('points.json').current
    act(() => {
      store.actions.setCurrent(newer)
      store.actions.applyFollow({
        key: {
          as_of_season: 2026,
          as_of_week_seq: 10,
          publication_id: 13,
          whatif_run_id: 7,
          whatif_views_current: true,
        },
        hold: false,
        stuck: false,
      })
    })
    await settle()
    expect(store.getState().follow.offeredKey?.as_of_week_seq).toBe(10)
    expect(backend.count('GET /api/points')).toBe(before + 1)
  })

  it('raises the outage state and keeps the message when the configuration cannot load', async () => {
    const backend = new FakeBackend({ 'GET /api/config': () => outageReply() })
    const { store } = renderData(backend)
    await settle()
    expect(store.getState().outage).toBe(true)
    expect(screen.getByTestId('outage')).toHaveTextContent('Exasol unreachable')
    expect(screen.getByTestId('load-error')).toHaveTextContent('Exasol unreachable')
  })

  it('reports another failure as a load error, not as an outage', async () => {
    const backend = new FakeBackend({ 'GET /api/points': () => new Reply(500, { message: 'broken' }) })
    const { store } = renderData(backend)
    await settle()
    expect(store.getState().outage).toBe(false)
    expect(screen.getByTestId('load-error')).toHaveTextContent('broken')
  })

  it('shows the data once the first load has finished', async () => {
    vi.useRealTimers()
    renderData(new FakeBackend())
    await waitFor(() => expect(screen.getByTestId('points')).toHaveTextContent('4'))
  })
})
