// @vitest-environment node
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import { createApi } from '../src/api/endpoints.ts'
import { fixtureFor, logoFile, teamResponseFor } from './fixtureApi.ts'

describe('preview fixture API', () => {
  it('maps every endpoint the frontend calls to an existing fixture', async () => {
    const calls: Array<[string, string]> = []
    const client = {
      get: (path: string) => (calls.push(['GET', path]), Promise.resolve({})),
      post: (path: string) => (calls.push(['POST', path]), Promise.resolve({})),
      beginPageLoad: () => {
        throw new Error('unused')
      },
    }
    const api = createApi(client as never)
    const pt = { season: 2026, week: 9 }
    await Promise.all([
      api.config(), api.health(), api.points(), api.teams(), api.odds(pt), api.whatifGames(),
      api.whatifOdds({} as never), api.guide({ ...pt, team: 1 }), api.whatifGuide({} as never),
      api.predictions(pt), api.scorecard(pt), api.upsets(pt), api.matchup({ team: 1, opponent: 2 }),
      api.previewPlan({ team: 1, opponent: 2 }), api.preview({} as never), api.quota(), api.timeline(),
      api.refreshPlan({} as never), api.refreshRun({} as never), api.action('a-1'), api.inspector(),
      api.inspectorExplain('e1'), api.console(), api.consoleExplain('fan-out'), api.consoleRun('stored', {} as never),
      api.teamsMap(pt), api.team(102, pt), api.teamOutlook(102, pt), api.teamOutlook(103, pt), api.leagueWeek(pt),
      api.leagueStats(pt),
    ])
    expect(calls.length).toBeGreaterThan(20)
    for (const [method, path] of calls) {
      const file = fixtureFor(method, path)
      expect(file, `${method} ${path}`).not.toBeNull()
      if (file !== '') readFileSync(new URL(`../src/test/fixtures/${file}`, import.meta.url))
    }
  })
})

describe('preview team and logo routes', () => {
  it('re-heads the sample team page for another team of the map, and refuses an unknown id', () => {
    const page = teamResponseFor(103) as { team: { team_id: number; school: string; has_logo: boolean }; games: unknown[] }
    expect(page.team).toMatchObject({ team_id: 103, school: 'Carlton' })
    expect(page.games.length).toBeGreaterThan(5)
    expect(teamResponseFor(999999)).toBeNull()
  })

  it('serves the dark logo when asked and present, else the light one, and nothing for a bad or missing id', () => {
    const dir = mkdtempSync(join(tmpdir(), 'cfb-logos-'))
    writeFileSync(join(dir, '102.png'), 'light')
    writeFileSync(join(dir, '103.png'), 'light')
    writeFileSync(join(dir, '103-dark.png'), 'dark')
    expect(logoFile(dir, '102', 'dark')).toBe(join(dir, '102.png'))
    expect(logoFile(dir, '103', 'dark')).toBe(join(dir, '103-dark.png'))
    expect(logoFile(dir, '103', null)).toBe(join(dir, '103.png'))
    expect(logoFile(dir, '101', null)).toBeNull()
    expect(logoFile(dir, '../102', null)).toBeNull()
  })
})
