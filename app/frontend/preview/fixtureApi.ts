import { existsSync, readFileSync } from 'node:fs'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'test', 'fixtures')

/** [method, path pattern, fixture file]. First match wins; a query string never changes the pick. */
export const ROUTES: ReadonlyArray<readonly [string, RegExp, string]> = [
  ['GET', /^\/api\/config$/, 'config.json'],
  ['GET', /^\/api\/health$/, ''], // { ok: true }
  ['GET', /^\/api\/points$/, 'points.json'],
  ['GET', /^\/api\/teams$/, 'teams.json'],
  ['GET', /^\/api\/teams\/map$/, 'teams-map.json'],
  ['GET', /^\/api\/team\/\d+$/, 'team.json'],
  ['GET', /^\/api\/team\/102\/outlook$/, 'team-outlook.json'],
  ['GET', /^\/api\/team\/\d+\/outlook$/, 'team-outlook.empty.json'],
  ['GET', /^\/api\/league\/week$/, 'league-week.json'],
  ['GET', /^\/api\/league\/stats$/, 'league-stats.json'],
  ['GET', /^\/api\/odds$/, 'odds.json'],
  ['GET', /^\/api\/whatif\/games$/, 'whatif-games.json'],
  ['POST', /^\/api\/whatif\/odds$/, 'whatif-odds.json'],
  ['GET', /^\/api\/guide$/, 'guide.json'],
  ['POST', /^\/api\/whatif\/guide$/, 'guide.conditional.json'],
  ['GET', /^\/api\/predictions$/, 'predictions.json'],
  ['GET', /^\/api\/scorecard$/, 'scorecard.json'],
  ['GET', /^\/api\/upsets$/, 'upsets.json'],
  ['GET', /^\/api\/matchup$/, 'matchup.json'],
  ['GET', /^\/api\/preview\/plan$/, 'preview-plan.json'],
  ['POST', /^\/api\/preview$/, 'preview.json'],
  ['GET', /^\/api\/quota$/, 'quota.json'],
  ['GET', /^\/api\/timeline$/, 'timeline.done.json'],
  ['POST', /^\/api\/refresh\/plan$/, 'refresh-plan.json'],
  ['POST', /^\/api\/refresh\/run$/, 'refresh-run.json'],
  ['GET', /^\/api\/actions\/[^/]+$/, 'action.done.json'],
  ['GET', /^\/api\/inspector$/, 'inspector.json'],
  ['POST', /^\/api\/inspector\/[^/]+\/explain$/, 'explain.fan-out.json'],
  ['GET', /^\/api\/console$/, 'console.json'],
  ['POST', /^\/api\/console\/(?:contradiction|stored)\/explain$/, 'explain.no-virtual-table.json'],
  ['POST', /^\/api\/console\/over-budget\/explain$/, 'explain.refused.json'],
  ['POST', /^\/api\/console\/[^/]+\/explain$/, 'explain.fan-out.json'],
  ['POST', /^\/api\/console\/stored\/run$/, 'console-run.stored.json'],
  ['POST', /^\/api\/console\/[^/]+\/run$/, 'console-run.json'],
]

export function fixtureFor(method: string, path: string): string | null {
  const hit = ROUTES.find(([m, pattern]) => m === method && pattern.test(path))
  return hit === undefined ? null : hit[2]
}

const SAMPLE_TEAM_ID = 102
const LOGO_PATH = /^\/api\/logos\/([^/]+)\.png$/

function readFixture(name: string): unknown {
  return JSON.parse(readFileSync(join(FIXTURES, name), 'utf8'))
}

interface SampleTeam {
  team_id: number
  school: string
  [field: string]: unknown
}

/** The logo cache directory, as the backend resolves it: `APP_LOGO_DIR`, else `CFB_LOGO_DIR`, else `~/.cfb-demo/logos`. */
export function logoDirectory(env: NodeJS.ProcessEnv = process.env): string {
  return env['APP_LOGO_DIR'] ?? env['CFB_LOGO_DIR'] ?? join(homedir(), '.cfb-demo', 'logos')
}

/** The logo file for a team id, the dark one when asked and present, else the light one; null when there is none. */
export function logoFile(directory: string, teamId: string, variant: string | null): string | null {
  if (!/^\d+$/.test(teamId)) return null
  const light = join(directory, `${teamId}.png`)
  const dark = join(directory, `${teamId}-dark.png`)
  if (variant === 'dark' && existsSync(dark)) return dark
  return existsSync(light) ? light : null
}

/** The real-data snapshot directory (`make app-preview REAL=1`, written by `make app-fixtures`); null for sample data. */
export function snapshotDirectory(env: NodeJS.ProcessEnv = process.env): string | null {
  const directory = env['CFB_FIXTURES_DIR']
  return directory === undefined || directory === '' ? null : directory
}

const SNAPSHOT_FILES: ReadonlyArray<readonly [RegExp, (match: RegExpExecArray) => string]> = [
  [/^\/api\/points$/, () => 'points.json'],
  [/^\/api\/teams$/, () => 'teams.json'],
  [/^\/api\/teams\/map$/, () => 'teams-map.json'],
  [/^\/api\/team\/(\d+)$/, (match) => `team/${match[1]}.json`],
  [/^\/api\/team\/(\d+)\/outlook$/, (match) => `team/${match[1]}.outlook.json`],
  [/^\/api\/league\/week$/, () => 'league-week.json'],
  [/^\/api\/league\/stats$/, () => 'league-stats.json'],
  [/^\/api\/inspector$/, () => 'inspector.json'],
]

/** The snapshot file (relative to its directory) that answers a GET, or null for a path the snapshot does not cover. */
export function snapshotFileFor(method: string, path: string): string | null {
  if (method !== 'GET') return null
  for (const [pattern, file] of SNAPSHOT_FILES) {
    const match = pattern.exec(path)
    if (match !== null) return file(match)
  }
  return null
}

/** The sample team page re-headed for another team of the map fixture; null for an id the map does not know. */
export function teamResponseFor(teamId: number): unknown {
  const sample = readFixture('team.json') as { team: SampleTeam; games: Array<{ opponent: SampleTeam }> }
  if (teamId === SAMPLE_TEAM_ID) return sample
  const map = readFixture('teams-map.json') as { teams: Array<SampleTeam> }
  const found = map.teams.find((candidate) => candidate.team_id === teamId)
  if (found === undefined) return null
  const identity = Object.entries(found).filter(([field]) => field in sample.team)
  return {
    ...sample,
    team: { ...sample.team, ...Object.fromEntries(identity) },
    games: sample.games.filter((game) => game.opponent.team_id !== teamId),
  }
}

function send(res: ServerResponse, status: number, body: unknown): void {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json')
  res.setHeader('Cache-Control', 'no-store')
  res.end(JSON.stringify(body))
}

/** Connect middleware serving `/api/*` from the JSON fixtures. Preview only: no database, no CFBD, no writes. */
export function fixtureApi() {
  const polled = new Map<string, number>()
  return (req: IncomingMessage, res: ServerResponse, next: () => void): void => {
    const url = new URL(req.url ?? '/', 'http://localhost')
    if (!url.pathname.startsWith('/api/')) return next()
    const method = req.method ?? 'GET'
    const logo = LOGO_PATH.exec(url.pathname)
    if (logo !== null && method === 'GET') {
      const id = logo[1] ?? ''
      if (!/^\d+$/.test(id)) return send(res, 400, { error: 'invalid_argument', message: 'the team id must be an integer' })
      const file = logoFile(logoDirectory(), id, url.searchParams.get('variant'))
      if (file === null) return send(res, 404, { error: 'not_found', message: 'no logo for this team' })
      res.statusCode = 200
      res.setHeader('Content-Type', 'image/png')
      res.setHeader('Cache-Control', 'no-store')
      res.end(readFileSync(file))
      return
    }
    const snapshot = snapshotDirectory()
    const snapshotFile = snapshot === null ? null : snapshotFileFor(method, url.pathname)
    if (snapshot !== null && snapshotFile !== null) {
      const path = join(snapshot, snapshotFile)
      if (!existsSync(path)) return send(res, 404, { error: 'not_found', message: `the snapshot has no ${snapshotFile}` })
      return send(res, 200, JSON.parse(readFileSync(path, 'utf8')))
    }
    const team = /^\/api\/team\/(\d+)$/.exec(url.pathname)
    if (team !== null && method === 'GET') {
      const body = teamResponseFor(Number(team[1]))
      if (body === null) return send(res, 404, { error: 'not_found', message: `No team with id ${team[1]}` })
      return send(res, 200, body)
    }
    const file = fixtureFor(method, url.pathname)
    if (file === null) return send(res, 404, { error: 'not_found', message: `preview has no fixture for ${method} ${url.pathname}` })
    if (file === '') return send(res, 200, { ok: true })
    // An action reads RUNNING on its first poll and DONE after, so the progress UI is seen once.
    const action = /^\/api\/actions\/([^/]+)$/.exec(url.pathname)
    let name = file
    if (action !== null) {
      const id = action[1] ?? ''
      const n = (polled.get(id) ?? 0) + 1
      polled.set(id, n)
      if (n === 1) name = 'action.running.json'
    }
    // Drain a POST body so the connection stays clean.
    req.resume()
    send(res, 200, readFixture(name))
  }
}
