import { describe, expect, it, vi } from 'vitest'

import type { ApiClient, PageLoad } from './client.ts'
import { createApi } from './endpoints.ts'

function fakeClient() {
  const get = vi.fn(() => Promise.resolve({}) as Promise<never>)
  const post = vi.fn(() => Promise.resolve({}) as Promise<never>)
  const pageLoad: PageLoad = { signal: new AbortController().signal, get, post, abort: vi.fn() }
  const client: ApiClient = { get, post, beginPageLoad: () => pageLoad }
  return { api: createApi(client), get, post }
}

describe('read endpoints', () => {
  it('map to the plan.md HTTP API paths and queries', async () => {
    const { api, get } = fakeClient()
    await api.config()
    await api.points(2026)
    await api.teams()
    await api.odds({ season: 2026, week: 9 })
    await api.whatifGames()
    await api.guide({ season: 2026, week: 9, team: 102 })
    await api.predictions({ season: 2026, week: 9 })
    await api.scorecard({ season: 2026, week: 9, population: 'FBS_VS_FBS' })
    await api.upsets({ season: 2026, week: 9 })
    await api.matchup({ team: 101, opponent: 102 })
    await api.previewPlan({ team: 101, opponent: 102 })
    await api.quota({ fresh: true })
    await api.timeline({ run: 41 })
    await api.action('a-1')
    await api.inspector()
    await api.console()
    await api.health()
    expect(get.mock.calls).toEqual([
      ['/api/config', undefined, undefined],
      ['/api/points', { season: 2026 }, undefined],
      ['/api/teams', undefined, undefined],
      ['/api/odds', { season: 2026, week: 9 }, undefined],
      ['/api/whatif/games', undefined, undefined],
      ['/api/guide', { season: 2026, week: 9, team: 102 }, undefined],
      ['/api/predictions', { season: 2026, week: 9 }, undefined],
      ['/api/scorecard', { season: 2026, week: 9, population: 'FBS_VS_FBS' }, undefined],
      ['/api/upsets', { season: 2026, week: 9 }, undefined],
      ['/api/matchup', { team: 101, opponent: 102 }, undefined],
      ['/api/preview/plan', { team: 101, opponent: 102 }, undefined],
      ['/api/quota', { fresh: true }, undefined],
      ['/api/timeline', { run: 41 }, undefined],
      ['/api/actions/a-1', undefined, undefined],
      ['/api/inspector', undefined, undefined],
      ['/api/console', undefined, undefined],
      ['/api/health', undefined, undefined],
    ])
  })

  it('passes an abort signal through', async () => {
    const { api, get } = fakeClient()
    const signal = new AbortController().signal
    await api.odds({ season: 2026, week: 9 }, { signal })
    expect(get).toHaveBeenCalledWith('/api/odds', { season: 2026, week: 9 }, { signal })
  })
})

describe('action endpoints', () => {
  it('post to the plan.md paths with the bodies of the contract', async () => {
    const { api, post } = fakeClient()
    await api.whatifOdds({ run_id: 7, conditions: [{ game_id: 9003, winner: 'away' }] })
    await api.whatifGuide({ run_id: 7, team_id: 102, conditions: [] })
    await api.preview({ team: 101, opponent: 102, max_calls: 1, force: false })
    await api.refreshPlan({ season: 2026, week: 10, season_type: 'regular' })
    await api.refreshRun({ season: 2026, week: 10, season_type: 'regular', max_calls: 0 })
    await api.inspectorExplain('q-0001')
    await api.consoleExplain('fan-out')
    await api.consoleRun('fan-out', { max_calls: 3 })
    expect(post.mock.calls).toEqual([
      ['/api/whatif/odds', { run_id: 7, conditions: [{ game_id: 9003, winner: 'away' }] }, undefined],
      ['/api/whatif/guide', { run_id: 7, team_id: 102, conditions: [] }, undefined],
      ['/api/preview', { team: 101, opponent: 102, max_calls: 1, force: false }, undefined],
      ['/api/refresh/plan', { season: 2026, week: 10, season_type: 'regular' }, undefined],
      ['/api/refresh/run', { season: 2026, week: 10, season_type: 'regular', max_calls: 0 }, undefined],
      ['/api/inspector/q-0001/explain', {}, undefined],
      ['/api/console/fan-out/explain', {}, undefined],
      ['/api/console/fan-out/run', { max_calls: 3 }, undefined],
    ])
  })

  it('encodes ids in paths', async () => {
    const { api, get, post } = fakeClient()
    await api.action('a/../x')
    await api.inspectorExplain('q 1')
    expect(get).toHaveBeenCalledWith('/api/actions/a%2F..%2Fx', undefined, undefined)
    expect(post).toHaveBeenCalledWith('/api/inspector/q%201/explain', {}, undefined)
  })
})
