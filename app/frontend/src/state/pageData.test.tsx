import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { CurrentPoint, OddsResponse } from '../api/types.ts'
import { FakeBackend, NetworkFailure, Reply, outageReply } from '../test/backend.ts'
import { fixture } from '../test/fixtures/index.ts'
import { renderInApp, type Rendered } from '../test/render.tsx'
import { OutageProvider, useOutage } from './outage.tsx'
import { usePageData, usePoint } from './pageData.tsx'
import { createAppStore } from './store.ts'

const current = fixture<{ current: CurrentPoint }>('points.json').current

function OddsProbe() {
  const point = usePoint()
  const outage = useOutage()
  const page = usePageData(({ api, point: at, options }) => api.odds({ season: at.season, week: at.week_seq }, options))
  return (
    <div>
      <p data-testid="point">{`${point.season}/${point.week_seq}`}</p>
      <p data-testid="status">{page.status}</p>
      <p data-testid="rows">{page.data?.rows.length ?? '-'}</p>
      <p data-testid="code">{page.error?.code ?? '-'}</p>
      <p data-testid="outage">{outage.message ?? '-'}</p>
      <button onClick={page.retry}>retry</button>
      <button onClick={outage.retry}>retry outage</button>
    </div>
  )
}

function renderProbe(backend: FakeBackend, initial: Parameters<typeof renderInApp>[1] = {}): Rendered {
  return renderInApp(
    <OutageProvider>
      <OddsProbe />
    </OutageProvider>,
    { backend, ...initial },
  )
}

const week8 = { chosen: { mode: 'explicit', season: 2026, week_seq: 8 } } as const

function pending(): { promise: Promise<unknown>; resolve: (value: unknown) => void } {
  let resolve: (value: unknown) => void = () => {}
  const promise = new Promise<unknown>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

describe('usePageData', () => {
  it('loads at the effective point and reports loading, then ready', async () => {
    const backend = new FakeBackend({ 'GET /api/odds': () => fixture<OddsResponse>('odds.json') })
    renderProbe(backend, { initial: week8 })
    expect(screen.getByTestId('status')).toHaveTextContent('loading')
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('ready'))
    expect(screen.getByTestId('rows')).not.toHaveTextContent('-')
    const last = backend.requests('GET /api/odds').at(-1)
    expect(last?.query.get('season')).toBe('2026')
    expect(last?.query.get('week')).toBe('8')
  })

  it('follows the loaded follow key when no point was chosen', async () => {
    const backend = new FakeBackend({ 'GET /api/odds': () => fixture<OddsResponse>('odds.json') })
    const store = createAppStore()
    store.actions.setCurrent(current)
    renderProbe(backend, { store })
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('ready'))
    expect(screen.getByTestId('point')).toHaveTextContent('2026/9')
    expect(backend.requests('GET /api/odds').at(-1)?.query.get('week')).toBe('9')
  })

  it('sends one request for a StrictMode mount and aborts the duplicate effect', async () => {
    const backend = new FakeBackend({ 'GET /api/odds': () => fixture<OddsResponse>('odds.json') })
    renderProbe(backend, { initial: week8 })
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('ready'))
    const requests = backend.requests('GET /api/odds')
    expect(requests.length).toBe(2)
    expect(requests[0]?.signal?.aborted).toBe(true)
    expect(requests[1]?.signal?.aborted).toBe(false)
  })

  it('aborts the page load on a point change and loads the new point', async () => {
    const backend = new FakeBackend({ 'GET /api/odds': () => fixture<OddsResponse>('odds.json') })
    const { store } = renderProbe(backend, { initial: week8 })
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('ready'))
    const before = backend.requests('GET /api/odds').at(-1)
    act(() => store.actions.choosePoint(2026, 7))
    expect(before?.signal?.aborted).toBe(true)
    await waitFor(() => expect(backend.requests('GET /api/odds').at(-1)?.query.get('week')).toBe('7'))
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('ready'))
  })

  it('shows loading again and drops the old data while the next point loads', async () => {
    const gate = pending()
    const backend = new FakeBackend({
      'GET /api/odds': (request) => (request.query.get('week') === '7' ? gate.promise : fixture<OddsResponse>('odds.json')),
    })
    const { store } = renderProbe(backend, { initial: week8 })
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('ready'))
    act(() => store.actions.choosePoint(2026, 7))
    expect(screen.getByTestId('status')).toHaveTextContent('loading')
    expect(screen.getByTestId('rows')).toHaveTextContent('-')
    await act(async () => {
      gate.resolve(fixture<OddsResponse>('odds.json'))
    })
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('ready'))
  })

  it('reloads on the store reload token and does not show a superseded answer', async () => {
    const first = pending()
    let calls = 0
    const backend = new FakeBackend({
      'GET /api/odds': () => {
        calls += 1
        return calls <= 2 ? first.promise : { ...fixture<OddsResponse>('odds.json'), rows: [] }
      },
    })
    const { store } = renderProbe(backend, { initial: week8 })
    await waitFor(() => expect(calls).toBe(2))
    act(() => store.actions.reload())
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('ready'))
    await act(async () => {
      first.resolve(fixture<OddsResponse>('odds.json'))
    })
    expect(screen.getByTestId('rows')).toHaveTextContent('0')
  })

  it('turns a database outage into the outage state with the server message', async () => {
    const backend = new FakeBackend({ 'GET /api/odds': () => outageReply() })
    const { store } = renderProbe(backend, { initial: week8 })
    await waitFor(() => expect(store.getState().outage).toBe(true))
    expect(screen.getByTestId('outage')).toHaveTextContent('Exasol unreachable at 127.0.0.1:8563 (target local)')
    expect(screen.getByTestId('status')).toHaveTextContent('error')
    expect(screen.getByTestId('code')).toHaveTextContent('database_unreachable')
  })

  it('keeps other failures as page errors without the outage state', async () => {
    const backend = new FakeBackend({
      'GET /api/odds': () => new Reply(502, { error: 'cfbd_failed', message: 'a live statement failed' }),
    })
    const { store } = renderProbe(backend, { initial: week8 })
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('error'))
    expect(store.getState().outage).toBe(false)
    expect(screen.getByTestId('code')).toHaveTextContent('cfbd_failed')
  })

  it('retries a failed load on request', async () => {
    let fail = true
    const backend = new FakeBackend({
      'GET /api/odds': () => {
        if (fail) throw new NetworkFailure()
        return fixture<OddsResponse>('odds.json')
      },
    })
    renderProbe(backend, { initial: week8 })
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('error'))
    fail = false
    await userEvent.click(screen.getByRole('button', { name: 'retry' }))
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('ready'))
  })
})

describe('useOutage', () => {
  it('retry clears the outage and loads every page again', async () => {
    const backend = new FakeBackend({ 'GET /api/odds': () => outageReply() })
    const { store } = renderProbe(backend, { initial: week8 })
    await waitFor(() => expect(store.getState().outage).toBe(true))
    backend.on('GET /api/odds', () => fixture<OddsResponse>('odds.json'))
    await userEvent.click(screen.getByRole('button', { name: 'retry outage' }))
    expect(store.getState().outage).toBe(false)
    expect(store.getState().reloadToken).toBe(1)
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('ready'))
    expect(screen.getByTestId('outage')).toHaveTextContent('-')
  })
})
