import { readFileSync } from 'node:fs'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'test', 'fixtures')

/** [method, path pattern, fixture file]. First match wins; a query string never changes the pick. */
export const ROUTES: ReadonlyArray<readonly [string, RegExp, string]> = [
  ['GET', /^\/api\/config$/, 'config.json'],
  ['GET', /^\/api\/health$/, ''], // { ok: true }
  ['GET', /^\/api\/points$/, 'points.json'],
  ['GET', /^\/api\/teams$/, 'teams.json'],
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
    send(res, 200, JSON.parse(readFileSync(join(FIXTURES, name), 'utf8')))
  }
}
