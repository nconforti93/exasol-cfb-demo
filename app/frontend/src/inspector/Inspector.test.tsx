import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type {
  ConfigResponse,
  ConsoleRunRequest,
  ConsoleRunResponse,
  ExplainResponse,
  InspectorResponse,
  TimelineResponse,
} from '../api/types.ts'
import { ShellProviders } from '../shell/ShellData.tsx'
import { FakeBackend, Reply } from '../test/backend.ts'
import { fixture } from '../test/fixtures/index.ts'
import { renderInApp } from '../test/render.tsx'
import type { AppState } from '../state/store.ts'
import { Inspector } from './Inspector.tsx'

const FAN_OUT_SQL = 'SELECT * FROM "CFBD_VS"."GAMES_TEAMS" WHERE "P_YEAR" BETWEEN 2022 AND 2024 AND "P_TEAM" = \'Michigan\''
const HTTP_2022 = 'GET https://api.collegefootballdata.com/games/teams?seasonType=both&team=Michigan&year=2022'

function backendWith(overrides: ConstructorParameters<typeof FakeBackend>[0] = {}) {
  return new FakeBackend({
    'GET /api/inspector': () => fixture('inspector.json'),
    'GET /api/console': () => fixture('console.json'),
    ...overrides,
  })
}

function renderInspector(backend = backendWith(), initial: Partial<AppState> = {}) {
  return renderInApp(
    <ShellProviders>
      <Inspector />
    </ShellProviders>,
    { backend, initial },
  )
}

async function openEntry(entryId: string) {
  const user = userEvent.setup()
  const rendered = renderInspector()
  const row = await screen.findByRole('button', { name: new RegExp(entryId) })
  await user.click(row)
  return { user, ...rendered }
}

describe('Inspector query log', () => {
  it('lists the session\'s statements with principal, milliseconds, rows and calls', async () => {
    const backend = backendWith()
    renderInspector(backend)
    const log = await screen.findByRole('list', { name: 'Query log' })
    const items = within(log).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(within(items[0]!).getByText('reader · CFB_CONSOLE_WEB')).toBeInTheDocument()
    expect(within(items[0]!).getByText('900 ms · 60 rows · 3 calls')).toBeInTheDocument()
    expect(within(items[1]!).getByText('app · CFB_APP_WEB')).toBeInTheDocument()
    expect(within(items[1]!).getByText('38 ms · 4 rows · 0 calls')).toBeInTheDocument()
    expect(within(items[1]!).getByText('odds')).toBeInTheDocument()
    expect(backend.count('GET /api/inspector')).toBeLessThanOrEqual(2)
  })

  it('shows the exact SQL of the selected statement and copies it', async () => {
    const { user, store } = await openEntry('q-0002')
    expect(store.getState().inspector.entryId).toBe('q-0002')
    expect(screen.getByText(FAN_OUT_SQL)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Copy SQL' }))
    expect(await navigator.clipboard.readText()).toBe(FAN_OUT_SQL)
    expect(await screen.findByText('Copied')).toBeInTheDocument()
  })

  it('opens the entry the store names', async () => {
    renderInspector(backendWith(), { inspector: { open: true, entryId: 'q-0001' } })
    expect(await screen.findByText(/FROM "CFB_API"\."PLAYOFF_ODDS"/)).toBeInTheDocument()
  })

  it('closes the entry on a second click', async () => {
    const { user, store } = await openEntry('q-0002')
    await user.click(screen.getByRole('button', { name: /q-0002/ }))
    expect(store.getState().inspector.entryId).toBeNull()
    expect(screen.queryByText(FAN_OUT_SQL)).toBeNull()
  })

  it('lists one request line per slice, each as the HTTP request it became', async () => {
    await openEntry('q-0002')
    const lines = screen.getByRole('list', { name: 'Requests of this statement' })
    const items = within(lines).getAllByRole('listitem')
    expect(items).toHaveLength(3)
    expect(within(items[0]!).getByText('LIVE · 200 · 1 attempt · 400 ms · 20 rows')).toBeInTheDocument()
    expect(within(items[0]!).getByText(HTTP_2022)).toBeInTheDocument()
  })

  it('draws the waterfall of the statement\'s requests, with overlapping bars', async () => {
    await openEntry('q-0002')
    const figure = screen.getByRole('figure', { name: 'Requests' })
    const bars = [...figure.querySelectorAll('rect.waterfall-bar')]
    expect(bars).toHaveLength(3)
    const first = bars[0]!
    expect(Number(bars[1]!.getAttribute('x'))).toBeLessThan(Number(first.getAttribute('x')) + Number(first.getAttribute('width')))
  })

  it('shows no request lines and no waterfall for a statement that made none', async () => {
    await openEntry('q-0001')
    expect(screen.queryByRole('list', { name: 'Requests of this statement' })).toBeNull()
    expect(screen.queryByRole('figure', { name: 'Requests' })).toBeNull()
  })

  it('shows a statement\'s error', async () => {
    const log = fixture<InspectorResponse>('inspector.json')
    log.entries[1]!.error = 'permission denied'
    log.entries[1]!.row_count = null
    renderInspector(backendWith({ 'GET /api/inspector': () => log }))
    expect(await screen.findByText('permission denied')).toBeInTheDocument()
  })

  it('explains a statement on demand as its principal, and shows the planned HTTP requests', async () => {
    const backend = backendWith({ 'POST /api/inspector/q-0002/explain': () => fixture('explain.fan-out.json') })
    const rendered = renderInspector(backend)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /q-0002/ }))
    expect(backend.count('POST /api/inspector/q-0002/explain')).toBe(0)
    await user.click(screen.getByRole('button', { name: 'Show plan · 0 calls' }))
    expect(backend.requests('POST /api/inspector/q-0002/explain')).toHaveLength(1)
    const plan = await screen.findByRole('group', { name: 'Plan' })
    expect(within(plan).getByText('GAMES_TEAMS')).toBeInTheDocument()
    expect(within(plan).getByText('budget 10')).toBeInTheDocument()
    expect(within(plan).getByText('3 planned requests')).toBeInTheDocument()
    expect(within(plan).getByText(HTTP_2022)).toBeInTheDocument()
    expect(within(plan).getAllByText(/^GET https:\/\/api\.collegefootballdata\.com\//)).toHaveLength(3)
    expect(within(plan).getByText('up to 3 live calls')).toBeInTheDocument()
    expect(rendered.container.textContent).not.toMatch(/key=|apikey|authorization/i)
  })

  it('reports no virtual table and 0 calls for a statement over serving views', async () => {
    const backend = backendWith({ 'POST /api/inspector/q-0001/explain': () => fixture('explain.no-virtual-table.json') })
    const user = userEvent.setup()
    renderInspector(backend)
    await user.click(await screen.findByRole('button', { name: /q-0001/ }))
    await user.click(screen.getByRole('button', { name: 'Show plan · 0 calls' }))
    const plan = await screen.findByRole('group', { name: 'Plan' })
    expect(within(plan).getByText('no virtual table: 0 calls')).toBeInTheDocument()
    expect(within(plan).queryByText('budget 10')).toBeNull()
  })

  it('shows a failed plan as a notice', async () => {
    const backend = backendWith({
      'POST /api/inspector/q-0001/explain': () => new Reply(404, { error: 'not_found', message: 'that statement is no longer in the log' }),
    })
    const user = userEvent.setup()
    renderInspector(backend)
    await user.click(await screen.findByRole('button', { name: /q-0001/ }))
    await user.click(screen.getByRole('button', { name: 'Show plan · 0 calls' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('that statement is no longer in the log')
  })

  it('loads the log again on request', async () => {
    const backend = backendWith()
    const user = userEvent.setup()
    renderInspector(backend)
    await screen.findByRole('list', { name: 'Query log' })
    const before = backend.count('GET /api/inspector')
    await user.click(screen.getByRole('button', { name: 'Reload log' }))
    await waitFor(() => expect(backend.count('GET /api/inspector')).toBe(before + 1))
  })

  it('loads the log again when the page changes', async () => {
    const backend = backendWith()
    const { store } = renderInspector(backend)
    await screen.findByRole('list', { name: 'Query log' })
    const before = backend.count('GET /api/inspector')
    store.actions.setPage('upsets')
    await waitFor(() => expect(backend.count('GET /api/inspector')).toBeGreaterThan(before))
  })

  it('says so when the log is empty', async () => {
    renderInspector(backendWith({ 'GET /api/inspector': () => ({ entries: [] }) }))
    expect(await screen.findByText(/no statements yet/i)).toBeInTheDocument()
  })

  it('shows a failure to load the log', async () => {
    renderInspector(backendWith({ 'GET /api/inspector': () => new Reply(503, { error: 'busy', message: 'all sessions are busy' }) }))
    expect(await screen.findByRole('alert')).toHaveTextContent('all sessions are busy')
  })
})

async function openConsole(backend: FakeBackend) {
  const user = userEvent.setup()
  renderInspector(backend)
  await user.click(await screen.findByRole('tab', { name: 'Console' }))
  return user
}

function queryCard(title: string) {
  return screen.getByRole('group', { name: title })
}

describe('Inspector console', () => {
  it('loads the four fixed queries only when the console is opened, labeled with the reader principal', async () => {
    const backend = backendWith()
    const user = userEvent.setup()
    renderInspector(backend)
    await screen.findByRole('tab', { name: 'Console' })
    expect(backend.count('GET /api/console')).toBe(0)
    await user.click(screen.getByRole('tab', { name: 'Console' }))
    expect(await screen.findByText('reader · CFB_CONSOLE_WEB')).toBeInTheDocument()
    for (const title of ['Fan-out', 'Contradiction', 'Stored history', 'Over budget']) {
      expect(queryCard(title)).toBeInTheDocument()
    }
    expect(screen.getByText(FAN_OUT_SQL)).toBeInTheDocument()
    expect(screen.getByText(/head-to-head series since 1897/)).toBeInTheDocument()
  })

  it('accepts no free text', async () => {
    await openConsole(backendWith())
    await screen.findByRole('group', { name: 'Fan-out' })
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('explains a query at 0 calls and lists its planned requests', async () => {
    const backend = backendWith({ 'POST /api/console/fan-out/explain': () => fixture('explain.fan-out.json') })
    const user = await openConsole(backend)
    const card = await screen.findByRole('group', { name: 'Fan-out' })
    expect(within(card).getByRole('button', { name: /^run/i })).toBeDisabled()
    await user.click(within(card).getByRole('button', { name: 'Explain · 0 calls' }))
    expect(backend.count('POST /api/console/fan-out/explain')).toBe(1)
    expect(await within(card).findByText(HTTP_2022)).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Run · up to 3 live calls' })).toBeEnabled()
    expect(backend.count('POST /api/console/fan-out/run')).toBe(0)
  })

  it('runs a live query with max_calls from the plan, after a confirmation, and shows its requests', async () => {
    const backend = backendWith({
      'POST /api/console/fan-out/explain': () => fixture('explain.fan-out.json'),
      'POST /api/console/fan-out/run': () => fixture('console-run.json'),
    })
    const user = await openConsole(backend)
    const card = await screen.findByRole('group', { name: 'Fan-out' })
    await user.click(within(card).getByRole('button', { name: 'Explain · 0 calls' }))
    await user.click(await within(card).findByRole('button', { name: 'Run · up to 3 live calls' }))
    const confirm = within(card).getByRole('group', { name: 'Confirm: Run' })
    expect(confirm).toHaveTextContent('up to 3 live calls, plus retries')
    expect(backend.count('POST /api/console/fan-out/run')).toBe(0)
    const logLoads = backend.count('GET /api/inspector')
    await user.click(within(confirm).getByRole('button', { name: 'Confirm' }))
    expect(backend.requests('POST /api/console/fan-out/run')).toHaveLength(1)
    expect(backend.requests('POST /api/console/fan-out/run')[0]?.body).toEqual({ max_calls: 3 } satisfies ConsoleRunRequest)
    expect(await within(card).findByText('3 live calls spent')).toBeInTheDocument()
    expect(within(card).getAllByRole('listitem').length).toBeGreaterThanOrEqual(3)
    const bars = card.querySelectorAll('rect.waterfall-bar')
    expect(bars).toHaveLength(3)
    await waitFor(() => expect(backend.count('GET /api/config')).toBeGreaterThanOrEqual(3))
    await waitFor(() => expect(backend.count('GET /api/inspector')).toBeGreaterThan(logLoads))
  })

  it('runs a query that costs nothing without asking, with max_calls 0', async () => {
    const run: ConsoleRunResponse = {
      ...fixture<ConsoleRunResponse>('console-run.json'),
      id: 'contradiction',
      columns: [],
      rows: [],
      row_count: 0,
      calls_spent: 0,
      requests: [],
    }
    const explain = {
      ...fixture<ExplainResponse>('explain.fan-out.json'),
      spend: { source: 'NONE' as const, live_calls: 0, fetch_mode: null },
      manifests: [{ table: 'GAMES_TEAMS', fetch_mode: 'LIVE', read_through: true, budget: 10, planned_requests: 0, slice_keys: [], sources: [] }],
    }
    const backend = backendWith({
      'POST /api/console/contradiction/explain': () => explain,
      'POST /api/console/contradiction/run': () => run,
    })
    const user = await openConsole(backend)
    const card = await screen.findByRole('group', { name: 'Contradiction' })
    await user.click(within(card).getByRole('button', { name: 'Explain · 0 calls' }))
    expect(await within(card).findByText('0 planned requests')).toBeInTheDocument()
    await user.click(within(card).getByRole('button', { name: 'Run · no virtual table: 0 calls' }))
    expect(within(card).queryByRole('group', { name: 'Confirm: Run' })).toBeNull()
    expect(backend.requests('POST /api/console/contradiction/run')[0]?.body).toEqual({ max_calls: 0 })
    expect(await within(card).findByText('live · 0 calls')).toBeInTheDocument()
    expect(within(card).queryByRole('figure', { name: 'Requests' })).toBeNull()
  })

  it('shows the stored query\'s rows and its stored requests at 0 calls', async () => {
    const explain: ExplainResponse = {
      ...fixture<ExplainResponse>('explain.fan-out.json'),
      spend: { source: 'STORED', live_calls: 0, fetch_mode: null },
      manifests: [{ table: 'GAMES', fetch_mode: null, read_through: true, budget: null, planned_requests: 0, slice_keys: [], sources: ['STORED'] }],
    }
    const backend = backendWith({
      'POST /api/console/stored/explain': () => explain,
      'POST /api/console/stored/run': () => fixture('console-run.stored.json'),
    })
    const user = await openConsole(backend)
    const card = await screen.findByRole('group', { name: 'Stored history' })
    await user.click(within(card).getByRole('button', { name: 'Explain · 0 calls' }))
    expect(await within(card).findByText('no budget')).toBeInTheDocument()
    await user.click(within(card).getByRole('button', { name: 'Run · stored · 0 calls' }))
    await within(card).findByRole('table', { name: 'Result' })
    expect(within(card).getByRole('status')).toHaveTextContent('stored · 0 calls')
    const table = within(card).getByRole('table', { name: 'Result' })
    expect(within(table).getByText('/games?seasonType=regular&year=1997')).toBeInTheDocument()
  })

  it('shows the virtual schema\'s refusal for the over-budget query and offers no run', async () => {
    const backend = backendWith({ 'POST /api/console/over-budget/explain': () => fixture('explain.refused.json') })
    const user = await openConsole(backend)
    const card = await screen.findByRole('group', { name: 'Over budget' })
    await user.click(within(card).getByRole('button', { name: 'Explain · 0 calls' }))
    expect(await within(card).findByText('15 slices exceed the budget of 10 per query')).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: /^run/i })).toBeDisabled()
    expect(backend.count('POST /api/console/over-budget/run')).toBe(0)
  })

  it('shows a refused explain (an error answer) as a notice', async () => {
    const backend = backendWith({
      'POST /api/console/over-budget/explain': () =>
        new Reply(409, { error: 'max_calls_exceeded', message: '15 requests are planned against a budget of 10' }),
    })
    const user = await openConsole(backend)
    const card = await screen.findByRole('group', { name: 'Over budget' })
    await user.click(within(card).getByRole('button', { name: 'Explain · 0 calls' }))
    expect(await within(card).findByRole('alert')).toHaveTextContent('15 requests are planned against a budget of 10')
  })

  it('shows a busy refusal and a charged failure as notices', async () => {
    const backend = backendWith({
      'POST /api/console/fan-out/explain': () => fixture('explain.fan-out.json'),
      'POST /api/console/fan-out/run': () => new Reply(409, fixture('error.live-busy.json')),
    })
    const user = await openConsole(backend)
    const card = await screen.findByRole('group', { name: 'Fan-out' })
    await user.click(within(card).getByRole('button', { name: 'Explain · 0 calls' }))
    await user.click(await within(card).findByRole('button', { name: 'Run · up to 3 live calls' }))
    await user.click(within(card).getByRole('button', { name: 'Confirm' }))
    expect(await within(card).findByRole('alert')).toHaveTextContent('another process holds the ledger lock')
    backend.on('POST /api/console/fan-out/run', () => new Reply(502, { ...fixture<object>('error.cfbd-failed.json'), calls_charged: 3 }))
    await user.click(within(card).getByRole('button', { name: 'Run · up to 3 live calls' }))
    await user.click(within(card).getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(within(card).getByRole('alert')).toHaveTextContent('3 calls were charged'))
  })

  it('warns when the fetch log could not be written', async () => {
    const backend = backendWith({
      'POST /api/console/fan-out/explain': () => fixture('explain.fan-out.json'),
      'POST /api/console/fan-out/run': () => ({ ...fixture<ConsoleRunResponse>('console-run.json'), fetch_log_ok: false }),
    })
    const user = await openConsole(backend)
    const card = await screen.findByRole('group', { name: 'Fan-out' })
    await user.click(within(card).getByRole('button', { name: 'Explain · 0 calls' }))
    await user.click(await within(card).findByRole('button', { name: 'Run · up to 3 live calls' }))
    await user.click(within(card).getByRole('button', { name: 'Confirm' }))
    expect(await within(card).findByText(/fetch log could not be written/i)).toBeInTheDocument()
  })

  it('is absent without the operator path, while the query log stays', async () => {
    const config = fixture<ConfigResponse>('config.json')
    config.console = { enabled: false, reason: 'the console needs the operator path' }
    const backend = backendWith({ 'GET /api/config': () => config })
    renderInspector(backend)
    expect(await screen.findByRole('list', { name: 'Query log' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('the console needs the operator path')).toBeInTheDocument())
    expect(screen.queryByRole('tab', { name: 'Console' })).toBeNull()
    expect(backend.count('GET /api/console')).toBe(0)
    expect(screen.queryByRole('group', { name: 'Fan-out' })).toBeNull()
  })
})

describe('Inspector refresh requests', () => {
  it('lists the run\'s requests next to the per-step calls, as they arrive', async () => {
    const backend = backendWith({ 'GET /api/timeline': () => fixture<TimelineResponse>('timeline.json') })
    const user = userEvent.setup()
    renderInspector(backend)
    await user.click(await screen.findByRole('tab', { name: 'Refresh requests' }))
    const list = await screen.findByRole('list', { name: 'Requests of the run' })
    const [item] = within(list).getAllByRole('listitem')
    expect(within(item!).getByText('REPLAY · 200 · 1 attempt · 83 ms · 62 rows')).toBeInTheDocument()
    expect(within(item!).getByText('GET https://api.collegefootballdata.com/games?seasonType=regular&week=10&year=2026')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /run 41/i })).toBeInTheDocument()
    const calls = screen.getByRole('list', { name: 'Calls per step' })
    expect(within(calls).getByText('FETCH: 0 calls')).toBeInTheDocument()
    expect(screen.getByText(/replayed rows carry recorded timings/i)).toBeInTheDocument()
  })

  it('says so when there is no refresh run', async () => {
    const user = userEvent.setup()
    renderInspector(backendWith())
    await user.click(await screen.findByRole('tab', { name: 'Refresh requests' }))
    expect(await screen.findByText(/no refresh run yet/i)).toBeInTheDocument()
  })

  it('does not poll for itself', async () => {
    const backend = backendWith()
    const user = userEvent.setup()
    renderInspector(backend)
    await user.click(await screen.findByRole('tab', { name: 'Refresh requests' }))
    await screen.findByText(/no refresh run yet/i)
    expect(backend.count('GET /api/timeline')).toBeLessThanOrEqual(2)
  })
})
