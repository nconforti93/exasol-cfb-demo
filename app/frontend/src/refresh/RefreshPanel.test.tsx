import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type {
  ActionResponse,
  ConfigResponse,
  RefreshPlanResponse,
  RefreshRunRequest,
  TimelineResponse,
} from '../api/types.ts'
import { ShellProviders } from '../shell/ShellData.tsx'
import { FakeBackend, Reply } from '../test/backend.ts'
import { fixture } from '../test/fixtures/index.ts'
import { renderInApp } from '../test/render.tsx'
import { RefreshPanel } from './RefreshPanel.tsx'

const SLOW = { timeout: 4000 }

function configWith(patch: (config: ConfigResponse) => void): FakeBackend {
  const config = fixture<ConfigResponse>('config.json')
  patch(config)
  return new FakeBackend({ 'GET /api/config': () => config })
}

function livePlanBackend(backend: FakeBackend, plan = fixture<RefreshPlanResponse>('refresh-plan.live.json')) {
  return backend.on('POST /api/refresh/plan', () => plan)
}

function renderPanel(backend = new FakeBackend()) {
  return renderInApp(
    <ShellProviders>
      <RefreshPanel />
    </ShellProviders>,
    { backend },
  )
}

async function plan(backend: FakeBackend) {
  const user = userEvent.setup()
  renderPanel(backend)
  await user.click(await screen.findByRole('button', { name: /plan refresh/i }))
  return user
}

function planBackend(file: string) {
  return new FakeBackend({ 'POST /api/refresh/plan': () => fixture(file) })
}

describe('RefreshPanel', () => {
  it('starts collapsed, with a summary, and sends no plan or run on load', async () => {
    const backend = new FakeBackend()
    renderPanel(backend)
    expect(screen.getByRole('region', { name: 'Refresh' })).toBeInTheDocument()
    expect(await screen.findByText(/no refresh run yet/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show details' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByRole('button', { name: /plan refresh/i })).toBeEnabled()
    expect(screen.queryByRole('spinbutton', { name: 'Week' })).toBeNull()
    expect(backend.count('POST /api/refresh/plan')).toBe(0)
    expect(backend.count('POST /api/refresh/run')).toBe(0)
  })

  it('does not poll the timeline itself', async () => {
    const backend = new FakeBackend()
    renderPanel(backend)
    await screen.findByText(/no refresh run yet/i)
    await userEvent.setup().click(screen.getByRole('button', { name: 'Show details' }))
    expect(backend.count('GET /api/timeline')).toBeLessThanOrEqual(2)
  })

  it('plans the coming week at 0 calls and names what it would do', async () => {
    const backend = planBackend('refresh-plan.json')
    await plan(backend)
    expect(await screen.findByText('replay · 0 calls')).toBeInTheDocument()
    expect(backend.requests('POST /api/refresh/plan')).toHaveLength(1)
    expect(backend.requests('POST /api/refresh/plan')[0]?.body).toEqual({ season: 2026, week: 10, season_type: 'regular' })
    expect(screen.getByText('week 10 is complete: the point will move to week 10')).toBeInTheDocument()
    expect(screen.getByText('62 games in storage: 62 completed, 0 not kicked off')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /run refresh · replay · 0 calls/i })).toBeEnabled()
    expect(backend.count('POST /api/refresh/run')).toBe(0)
  })

  it('shows the live plan: the estimate, the retries, the quota, the ledger room and the week', async () => {
    const backend = planBackend('refresh-plan.live.json')
    await plan(backend)
    expect(await screen.findByText('up to 4 live calls, plus retries')).toBeInTheDocument()
    expect(screen.getByText('GAMES: 1 call')).toBeInTheDocument()
    expect(screen.getByText('GAMES_TEAMS: 3 calls')).toBeInTheDocument()
    expect(screen.getByText(/640 calls left/)).toBeInTheDocument()
    expect(screen.getByText(/600 spendable for a refresh/)).toBeInTheDocument()
    expect(screen.getByText('ledger app: 20 of 20 calls free')).toBeInTheDocument()
    expect(screen.getByText('week 10 is not complete: 3 games have not kicked off; the point stays at week 9')).toBeInTheDocument()
  })

  it('lets the presenter pick another week, and a changed target drops the old plan', async () => {
    const backend = planBackend('refresh-plan.json')
    const user = await plan(backend)
    await screen.findByText('replay · 0 calls')
    const week = screen.getByRole('spinbutton', { name: 'Week' })
    await user.clear(week)
    await user.type(week, '11')
    expect(screen.queryByText('replay · 0 calls')).toBeNull()
    await user.click(screen.getByRole('button', { name: /plan refresh/i }))
    await waitFor(() => expect(backend.requests('POST /api/refresh/plan')).toHaveLength(2))
    expect(backend.requests('POST /api/refresh/plan')[1]?.body).toEqual({ season: 2026, week: 11, season_type: 'regular' })
  })

  it('refuses a week outside 1 to 99 before any request', async () => {
    const backend = planBackend('refresh-plan.json')
    const user = await plan(backend)
    await screen.findByText('replay · 0 calls')
    const week = screen.getByRole('spinbutton', { name: 'Week' })
    await user.clear(week)
    await user.type(week, '100')
    expect(screen.getByRole('button', { name: /plan refresh/i })).toBeDisabled()
  })

  it('shows why a plan cannot run and offers no run', async () => {
    await plan(planBackend('refresh-plan.refused.json'))
    expect(await screen.findByText('a refresh is already running (run 40)')).toBeInTheDocument()
    expect(screen.getByText(/run 40 \(REFRESH_WEEK\) is running/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /run refresh/i })).toBeDisabled()
  })

  it('runs a replay refresh at once, follows the action to its end and states the point', async () => {
    let polls = 0
    const backend = planBackend('refresh-plan.json')
      .on('POST /api/refresh/run', () => fixture('refresh-run.json'))
      .on('GET /api/actions/a-0001', () => {
        polls += 1
        return fixture(polls === 1 ? 'action.running.json' : 'action.done.json')
      })
    const user = await plan(backend)
    await user.click(await screen.findByRole('button', { name: /run refresh · replay · 0 calls/i }))
    expect(backend.requests('POST /api/refresh/run')).toHaveLength(1)
    expect(backend.requests('POST /api/refresh/run')[0]?.body).toEqual({
      season: 2026,
      week: 10,
      season_type: 'regular',
      max_calls: 0,
    } satisfies RefreshRunRequest)
    expect(backend.requests('POST /api/refresh/run')[0]?.headers.get('X-CFB-Action')).toBe('1')
    expect(await screen.findByText(/running as run 41/i)).toBeInTheDocument()
    expect(await screen.findByText('the point moved to week 10', {}, SLOW)).toBeInTheDocument()
    expect(screen.getByText('2026, after week 9 → 2026, after week 10')).toBeInTheDocument()
    expect(screen.getByText('0 calls spent')).toBeInTheDocument()
    await waitFor(() => expect(backend.count('GET /api/config')).toBeGreaterThanOrEqual(3))
  })

  it('asks to confirm a live refresh, naming the calls, and sends nothing until confirmed', async () => {
    const backend = livePlanBackend(new FakeBackend()).on('POST /api/refresh/run', () => fixture('refresh-run.json'))
    backend.on('GET /api/actions/a-0001', () => fixture('action.done.json'))
    const user = await plan(backend)
    await user.click(await screen.findByRole('button', { name: /run refresh/i }))
    const confirm = screen.getByRole('group', { name: 'Confirm: Run refresh' })
    expect(within(confirm).getByText(/up to 4 live calls, plus retries/)).toBeInTheDocument()
    expect(backend.count('POST /api/refresh/run')).toBe(0)
    await user.click(within(confirm).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('group', { name: 'Confirm: Run refresh' })).toBeNull()
    expect(backend.count('POST /api/refresh/run')).toBe(0)
    await user.click(screen.getByRole('button', { name: /run refresh/i }))
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(backend.requests('POST /api/refresh/run')).toHaveLength(1)
    expect((backend.requests('POST /api/refresh/run')[0]?.body as RefreshRunRequest).max_calls).toBe(4)
  })

  it('shows a lock refusal as a notice that names the run and offers to follow it', async () => {
    const backend = planBackend('refresh-plan.json').on(
      'POST /api/refresh/run',
      () => new Reply(409, fixture('error.refresh-locked.json')),
    )
    const user = await plan(backend)
    await user.click(await screen.findByRole('button', { name: /run refresh/i }))
    const notice = await screen.findByRole('alert')
    expect(notice).toHaveTextContent('run 40 holds the loader lock')
    expect(screen.getByText(/the refresh was not started/i)).toBeInTheDocument()
    const polled = backend.count('GET /api/timeline')
    await user.click(within(notice).getByRole('button', { name: /follow the running refresh/i }))
    await waitFor(() => expect(backend.count('GET /api/timeline')).toBeGreaterThan(polled))
    expect(backend.count('GET /api/actions/a-0001')).toBe(0)
  })

  it.each([
    ['live_busy', 'error.live-busy.json'],
    ['max_calls_exceeded', 'error.max-calls.json'],
  ])('shows a %s refusal as a notice, not an error page', async (_code, file) => {
    const backend = planBackend('refresh-plan.json').on('POST /api/refresh/run', () => new Reply(409, fixture(file)))
    const user = await plan(backend)
    await user.click(await screen.findByRole('button', { name: /run refresh/i }))
    const notice = await screen.findByRole('alert')
    expect(notice).toHaveTextContent(/nothing was spent/i)
    expect(within(notice).queryByRole('button', { name: /follow/i })).toBeNull()
  })

  it('shows a failed run with the first line of its error', async () => {
    const backend = planBackend('refresh-plan.json')
      .on('POST /api/refresh/run', () => fixture('refresh-run.json'))
      .on('GET /api/actions/a-0001', () => fixture<ActionResponse>('action.failed.json'))
    const user = await plan(backend)
    await user.click(await screen.findByRole('button', { name: /run refresh/i }))
    expect(await screen.findByText('step PUBLISH failed: simulated failure', {}, SLOW)).toBeInTheDocument()
    expect(screen.getByText('the point stayed at week 9')).toBeInTheDocument()
  })

  it('has no refresh button without the operator path, and says why', async () => {
    const backend = configWith((config) => {
      config.operator = { enabled: false, reason: 'the operator path is off (APP_OPERATOR=off)' }
    })
    renderPanel(backend)
    expect(await screen.findByText('the operator path is off (APP_OPERATOR=off)')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /plan refresh|run refresh/i })).toBeNull()
  })

  it('still shows the timeline of the newest run without the operator path', async () => {
    const backend = configWith((config) => {
      config.operator = { enabled: false, reason: 'off' }
    }).on('GET /api/timeline', () => fixture('timeline.done.json'))
    renderPanel(backend)
    expect(await screen.findByRole('figure', { name: 'Refresh timeline' })).toBeInTheDocument()
    expect(screen.getByText('PUBLISH')).toBeInTheDocument()
  })

  it('disables the live refresh in the development server and says what to run instead', async () => {
    const backend = configWith((config) => {
      config.dev = true
      config.fetch_mode = 'LIVE'
    })
    livePlanBackend(backend, { ...fixture<RefreshPlanResponse>('refresh-plan.live.json'), dev_blocks_live: true, can_run: false, reasons: [] })
    await plan(backend)
    expect((await screen.findAllByText(/live refresh needs `make app-serve`/)).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /run refresh/i })).toBeDisabled()
  })

  it('shows a replay refresh as runnable in the development server', async () => {
    const backend = configWith((config) => {
      config.dev = true
    })
    backend.on('POST /api/refresh/plan', () => fixture('refresh-plan.json'))
    await plan(backend)
    expect(await screen.findByRole('button', { name: /run refresh · replay · 0 calls/i })).toBeEnabled()
    expect(screen.queryByText(/make app-serve/)).toBeNull()
  })

  it('follows a run started elsewhere: it opens, shows the steps and names no action', async () => {
    const backend = new FakeBackend({ 'GET /api/timeline': () => fixture<TimelineResponse>('timeline.json') })
    renderPanel(backend)
    expect(await screen.findByRole('figure', { name: 'Refresh timeline' })).toBeInTheDocument()
    expect(screen.getByText('WAIT')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Hide details' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText(/run 41 is running/i)).toBeInTheDocument()
    expect(screen.queryByText(/running as run/i)).toBeNull()
    expect(backend.count('GET /api/actions/a-0001')).toBe(0)
  })

  it('summarizes the newest finished run while collapsed', async () => {
    const backend = new FakeBackend({ 'GET /api/timeline': () => fixture('timeline.done.json') })
    renderPanel(backend)
    expect(await screen.findByText(/run 41 done/i)).toBeInTheDocument()
    expect(screen.queryByRole('figure', { name: 'Refresh timeline' })).toBeNull()
  })
})
