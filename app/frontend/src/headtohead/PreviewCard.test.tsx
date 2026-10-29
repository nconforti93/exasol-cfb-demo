import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ConfigResponse, PreviewResponse, TeamsResponse } from '../api/types.ts'
import { FakeBackend, Reply } from '../test/backend.ts'
import { fixture } from '../test/fixtures/index.ts'
import { renderInApp } from '../test/render.tsx'
import { ShellProviders } from '../shell/ShellData.tsx'
import { PreviewCard } from './PreviewCard.tsx'

// Role queries over tables are slow in jsdom, and the suite shares the machine with other builds.
vi.setConfig({ testTimeout: 20_000 })

const teams = fixture<TeamsResponse>('teams.json').teams
const ashford = teams[0]!
const brookvale = teams[1]!

function liveConfig(): ConfigResponse {
  return { ...fixture<ConfigResponse>('config.json'), fetch_mode: 'LIVE' }
}

function makeBackend(plan = 'preview-plan.json', overrides: Record<string, () => unknown> = {}): FakeBackend {
  return new FakeBackend({
    'GET /api/config': liveConfig,
    'GET /api/preview/plan': () => fixture(plan),
    'POST /api/preview': () => fixture('preview.json'),
    ...overrides,
  })
}

function renderCard(backend: FakeBackend) {
  return renderInApp(
    <ShellProviders>
      <PreviewCard team={ashford} opponent={brookvale} />
    </ShellProviders>,
    { backend, initial: { chosen: { mode: 'explicit', season: 2026, week_seq: 9 } } },
  )
}

function deferred<T>() {
  let resolve: (value: T) => void = () => {}
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

async function ready(name: RegExp | string) {
  return screen.findByRole('button', { name })
}

describe('PreviewCard', () => {
  it('shows the free plan and the cost label when it opens, and sends no preview request', async () => {
    const backend = makeBackend()
    renderCard(backend)
    const button = await ready('Fetch preview · 1 live call')
    expect(button).toBeEnabled()
    expect(screen.getByText(/Previews the next meeting of Ashford and Brookvale/)).toBeInTheDocument()
    expect(backend.count('POST /api/preview')).toBe(0)
    const plan = backend.requests('GET /api/preview/plan').at(-1)!
    expect(plan.query.get('team')).toBe('101')
    expect(plan.query.get('opponent')).toBe('102')
    expect(plan.query.get('game_id')).toBeNull()
  })

  it('sends no preview request while it stays open', async () => {
    const backend = makeBackend()
    renderCard(backend)
    await ready(/Fetch preview/)
    await act(async () => {
      await new Promise((done) => setTimeout(done, 300))
    })
    expect(backend.count('POST /api/preview')).toBe(0)
  })

  it('fetches on a click: names only the pair, shows "fetching from CFBD" with the button disabled, then the preview', async () => {
    const answer = deferred<unknown>()
    const backend = makeBackend('preview-plan.json', { 'POST /api/preview': () => answer.promise })
    renderCard(backend)
    await userEvent.click(await ready('Fetch preview · 1 live call'))

    expect(await screen.findByText(/fetching from CFBD/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Fetch preview/ })).toBeDisabled()
    const request = backend.requests('POST /api/preview')[0]!
    expect(request.body).toEqual({ team: 101, opponent: 102, max_calls: 1, force: false })
    expect(request.headers.get('X-CFB-Action')).toBe('1')

    await act(async () => answer.resolve(fixture('preview.json')))
    const receipt = await screen.findByText('1 live call spent')
    expect(receipt).toBeInTheDocument()
    expect(screen.queryByText(/fetching from CFBD/)).toBeNull()
    const table = screen.getByRole('table', { name: 'Teams and ratings from CFBD' })
    expect(within(table).getByRole('row', { name: /Ashford \(home\) 7–2 14\.2 9\.1 1710 8\.4 –/ })).toBeInTheDocument()
    expect(within(table).getByRole('row', { name: /Brookvale \(away\) 8–1 18\.9 12\.3 1790 12\.1 –/ })).toBeInTheDocument()
    expect(screen.getByText('Sim Book')).toBeInTheDocument()
    expect(screen.getByText('+135 / -155')).toBeInTheDocument()
    expect(screen.getByText('Ashford Field · scheduled')).toBeInTheDocument()
    expect(screen.getByText('Source LIVE · 412 ms')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Fetch again · 1 live call' })).toBeEnabled()
  })

  it('sends one request for two quick clicks', async () => {
    const answer = deferred<unknown>()
    const backend = makeBackend('preview-plan.json', { 'POST /api/preview': () => answer.promise })
    renderCard(backend)
    const button = await ready(/Fetch preview/)
    await userEvent.click(button)
    await userEvent.click(button)
    expect(backend.count('POST /api/preview')).toBe(1)
    await act(async () => answer.resolve(fixture('preview.json')))
    await screen.findByText('1 live call spent')
  })

  it('refreshes the quota (fresh), the configuration and the plan after the action', async () => {
    const backend = makeBackend()
    renderCard(backend)
    await ready(/Fetch preview/)
    await waitFor(() => expect(backend.count('GET /api/config')).toBeGreaterThan(0))
    const before = { config: backend.count('GET /api/config'), plan: backend.count('GET /api/preview/plan') }
    await userEvent.click(screen.getByRole('button', { name: /Fetch preview/ }))
    await screen.findByText('1 live call spent')
    await waitFor(() => expect(backend.requests('GET /api/quota').some((call) => call.query.get('fresh') === 'true')).toBe(true))
    await waitFor(() => expect(backend.count('GET /api/config')).toBeGreaterThan(before.config))
    await waitFor(() => expect(backend.count('GET /api/preview/plan')).toBeGreaterThan(before.plan))
  })

  it('shows the quota left beside the calls spent', async () => {
    renderCard(makeBackend())
    await userEvent.click(await ready(/Fetch preview/))
    expect(await screen.findByText('640 calls left')).toBeInTheDocument()
  })

  it('offers a fresh fetch again, which forces a new request under its own label', async () => {
    const backend = makeBackend()
    renderCard(backend)
    await userEvent.click(await ready(/Fetch preview/))
    await userEvent.click(await ready('Fetch again · 1 live call'))
    await waitFor(() => expect(backend.count('POST /api/preview')).toBe(2))
    expect(backend.requests('POST /api/preview')[1]!.body).toEqual({ team: 101, opponent: 102, max_calls: 1, force: true })
  })

  it('shows a cached plan as "cached · 0 calls" with when and how it was fetched, and serves it on a click', async () => {
    const backend = makeBackend('preview-plan.cached.json', {
      'POST /api/preview': () => fixture<PreviewResponse>('preview.cached.json'),
    })
    renderCard(backend)
    expect(await screen.findByText('cached 09:00 UTC · 0 calls, fetched LIVE in 412 ms')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Fetch again · 1 live call' })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: 'Show cached preview · cached · 0 calls' }))
    expect(await screen.findByText('cached 09:00 UTC · 0 calls', { selector: 'span' })).toBeInTheDocument()
    expect(backend.requests('POST /api/preview')[0]!.body).toEqual({ team: 101, opponent: 102, max_calls: 0, force: false })
    expect(screen.getByText('No betting line in CFBD\'s answer.')).toBeInTheDocument()
    expect(screen.queryByText(/calls spent/)).toBeNull()
  })

  it('states a replay plan as "replay · 0 calls" and confirms 0 calls', async () => {
    const answer = deferred<unknown>()
    const backend = makeBackend('preview-plan.replay.json', { 'POST /api/preview': () => answer.promise })
    renderCard(backend)
    await userEvent.click(await ready('Fetch preview · replay · 0 calls'))
    expect(await screen.findByText(/replaying a recorded answer/)).toBeInTheDocument()
    expect(backend.requests('POST /api/preview')[0]!.body).toMatchObject({ max_calls: 0, force: false })
    await act(async () => answer.resolve({ ...fixture<PreviewResponse>('preview.json'), source: 'REPLAY', calls_spent: 0, req_source: 'REPLAY' }))
    expect(await screen.findByText('replay · 0 calls', { selector: 'span' })).toBeInTheDocument()
    expect(screen.getByText('Source REPLAY · 412 ms')).toBeInTheDocument()
  })

  it('shows why live actions are disabled instead of a cost, and cannot fetch', async () => {
    const backend = makeBackend('preview-plan.disabled.json')
    renderCard(backend)
    const button = await ready('Fetch preview')
    expect(button).toBeDisabled()
    expect(screen.getByText('live actions disabled: no ledger cap for plan app')).toBeInTheDocument()
    expect(screen.queryByText(/1 live call/)).toBeNull()
    await userEvent.click(button)
    expect(backend.count('POST /api/preview')).toBe(0)
  })

  it('shows the busy reason while a refresh runs, and cannot fetch', async () => {
    const backend = makeBackend('preview-plan.busy.json')
    renderCard(backend)
    const button = await ready('Fetch preview')
    expect(button).toBeDisabled()
    expect(screen.getByText('a refresh is running (run 41): live previews resume when it ends')).toBeInTheDocument()
    await userEvent.click(button)
    expect(backend.count('POST /api/preview')).toBe(0)
  })

  it('does not block a replayed preview with a busy reason, since 0-call plans never take the lock', async () => {
    const plan = { ...fixture<Record<string, unknown>>('preview-plan.replay.json'), busy_reason: 'a refresh is running (run 41)' }
    renderCard(makeBackend('preview-plan.replay.json', { 'GET /api/preview/plan': () => plan }))
    expect(await ready('Fetch preview · replay · 0 calls')).toBeEnabled()
  })

  it('says it previews the latest meeting when the pair does not meet again', async () => {
    const backend = makeBackend('preview-plan.latest.json')
    renderCard(backend)
    await ready(/Fetch preview/)
    expect(screen.getByText(/do not meet again this season. Previews their latest meeting/)).toBeInTheDocument()
  })

  it('offers no fetch when there is no game to preview', async () => {
    const plan = { ...fixture<Record<string, unknown>>('preview-plan.json'), game_id: null, game_basis: 'none' }
    renderCard(makeBackend('preview-plan.json', { 'GET /api/preview/plan': () => plan }))
    expect(await screen.findByText(/have no game to preview/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('names a refused live request as busy and spends nothing', async () => {
    const backend = makeBackend('preview-plan.json', {
      'POST /api/preview': () => new Reply(409, fixture('error.live-busy.json')),
    })
    renderCard(backend)
    await userEvent.click(await ready(/Fetch preview/))
    const notice = await screen.findByRole('alert')
    expect(notice).toHaveTextContent('Live requests are busy. Nothing was spent.')
    expect(notice).toHaveTextContent('another process holds the ledger lock')
    expect(screen.getByRole('button', { name: /Fetch preview/ })).toBeEnabled()
    expect(screen.queryByText(/calls spent/)).toBeNull()
  })

  it('says live requests are paused while a finished run is unrecorded', async () => {
    const backend = makeBackend('preview-plan.json', {
      'POST /api/preview': () => new Reply(409, fixture('error.reconcile-pending.json')),
    })
    renderCard(backend)
    await userEvent.click(await ready(/Fetch preview/))
    expect(await screen.findByRole('alert')).toHaveTextContent('paused until a finished run is recorded. Nothing was spent.')
  })

  it('reads the plan again when the cost changed, and says so', async () => {
    const backend = makeBackend('preview-plan.json', {
      'POST /api/preview': () => new Reply(409, fixture('error.max-calls.json')),
    })
    renderCard(backend)
    await userEvent.click(await ready(/Fetch preview/))
    expect(await screen.findByRole('alert')).toHaveTextContent('The cost changed since the label was shown.')
    await waitFor(() => expect(backend.count('GET /api/preview/plan')).toBeGreaterThan(1))
  })

  it('reports a failed request as charged, still refreshing the quota and the ledger state', async () => {
    const backend = makeBackend('preview-plan.json', {
      'POST /api/preview': () => new Reply(502, fixture('error.cfbd-failed.json')),
    })
    renderCard(backend)
    await ready(/Fetch preview/)
    await waitFor(() => expect(backend.count('GET /api/config')).toBeGreaterThan(0))
    const configBefore = backend.count('GET /api/config')
    await userEvent.click(screen.getByRole('button', { name: /Fetch preview/ }))
    const notice = await screen.findByRole('alert')
    expect(notice).toHaveTextContent('The live request failed. 4 calls were charged.')
    expect(notice).toHaveTextContent('the fetch failed: timeout')
    expect(screen.queryByRole('table', { name: 'Teams and ratings from CFBD' })).toBeNull()
    await waitFor(() => expect(backend.requests('GET /api/quota').some((call) => call.query.get('fresh') === 'true')).toBe(true))
    await waitFor(() => expect(backend.count('GET /api/config')).toBeGreaterThan(configBefore))
    expect(backend.count('POST /api/preview')).toBe(1)
  })

  it('warns when the fetch-log row could not be written', async () => {
    const backend = makeBackend('preview-plan.json', {
      'POST /api/preview': () => ({ ...fixture<PreviewResponse>('preview.json'), fetch_log_ok: false }),
    })
    renderCard(backend)
    await userEvent.click(await ready(/Fetch preview/))
    expect(await screen.findByText(/fetch-log row for this request could not be written/)).toBeInTheDocument()
    expect(screen.getByText('1 live call spent')).toBeInTheDocument()
  })

  it('shows a plan failure with a retry', async () => {
    const backend = makeBackend('preview-plan.json', {
      'GET /api/preview/plan': () => new Reply(500, { message: 'plan exploded' }),
    })
    renderCard(backend)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not plan the preview: plan exploded')
    backend.on('GET /api/preview/plan', () => fixture('preview-plan.json'))
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await ready('Fetch preview · 1 live call')
  })

  it('is not aborted by a page change, which only aborts the page loads', async () => {
    const answer = deferred<unknown>()
    const backend = makeBackend('preview-plan.json', { 'POST /api/preview': () => answer.promise })
    const view = renderCard(backend)
    await userEvent.click(await ready(/Fetch preview/))
    await screen.findByText(/fetching from CFBD/)
    const signal = backend.requests('POST /api/preview')[0]!.signal!
    view.client.beginPageLoad()
    expect(signal.aborted).toBe(false)
    await act(async () => answer.resolve(fixture('preview.json')))
    expect(await screen.findByText('1 live call spent')).toBeInTheDocument()
  })

  it('ends its own request when the card goes away', async () => {
    const backend = makeBackend('preview-plan.json', { 'POST /api/preview': () => new Promise(() => {}) })
    const view = renderCard(backend)
    await userEvent.click(await ready(/Fetch preview/))
    await screen.findByText(/fetching from CFBD/)
    const signal = backend.requests('POST /api/preview')[0]!.signal!
    view.unmount()
    expect(signal.aborted).toBe(true)
  })
})
