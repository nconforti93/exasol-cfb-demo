import { fixture } from './fixtures/index.ts'

export interface FakeRequest {
  method: string
  path: string
  query: URLSearchParams
  body: unknown
  headers: Headers
  signal: AbortSignal | undefined
}

/** A handler's way of answering with a non-200 status. */
export class Reply {
  readonly status: number
  readonly body: unknown
  constructor(status: number, body: unknown) {
    this.status = status
    this.body = body
  }
}

/** A handler that throws this makes `fetch` reject, like a network failure. */
export class NetworkFailure extends Error {}

export type Handler = (request: FakeRequest) => unknown

const DEFAULTS: Record<string, string> = {
  'GET /api/config': 'config.json',
  'GET /api/points': 'points.json',
  'GET /api/odds': 'odds.json',
  'GET /api/timeline': 'timeline.idle.json',
  'GET /api/quota': 'quota.json',
  'GET /api/teams': 'teams.json',
}

/** The routes of the HTTP API answered from fixtures; `on` replaces one route, `calls` records every request. */
export class FakeBackend {
  readonly calls: FakeRequest[] = []
  private readonly handlers = new Map<string, Handler>()

  constructor(overrides: Record<string, Handler> = {}) {
    for (const [route, file] of Object.entries(DEFAULTS)) this.handlers.set(route, () => fixture(file))
    for (const [route, handler] of Object.entries(overrides)) this.handlers.set(route, handler)
  }

  on(route: string, handler: Handler): this {
    this.handlers.set(route, handler)
    return this
  }

  count(route: string): number {
    return this.requests(route).length
  }

  requests(route: string): FakeRequest[] {
    const [method, path] = route.split(' ')
    return this.calls.filter((call) => call.method === method && call.path === path)
  }

  readonly fetch: typeof fetch = async (input, init) => {
    const url = new URL(String(input), 'http://app.test')
    const method = init?.method ?? 'GET'
    const request: FakeRequest = {
      method,
      path: url.pathname,
      query: url.searchParams,
      body: typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined,
      headers: new Headers(init?.headers),
      signal: init?.signal ?? undefined,
    }
    this.calls.push(request)
    const signal = init?.signal
    if (signal?.aborted) throw new DOMException('aborted', 'AbortError')
    const handler = this.handlers.get(`${method} ${url.pathname}`)
    if (handler === undefined) {
      return json(404, { error: 'not_found', message: `no route ${method} ${url.pathname}` })
    }
    try {
      const answer = await handler(request)
      if (signal?.aborted) throw new DOMException('aborted', 'AbortError')
      return answer instanceof Reply ? json(answer.status, answer.body) : json(200, answer)
    } catch (failure) {
      if (failure instanceof NetworkFailure) throw new TypeError('network down', { cause: failure })
      throw failure
    }
  }
}

/** A minimal `Response`: what the API client reads (`ok`, `status`, `json()`), without a body stream. */
function json(status: number, body: unknown): Response {
  const text = JSON.stringify(body)
  return { ok: status >= 200 && status < 300, status, json: () => Promise.resolve(JSON.parse(text) as unknown) } as Response
}

export function outageReply(): Reply {
  return new Reply(503, fixture('error.database-unreachable.json'))
}
