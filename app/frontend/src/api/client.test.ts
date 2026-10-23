import { describe, expect, it, vi } from 'vitest'

import { ApiError, createApiClient, isAbortError, isLiveBusy, isOutage } from './client.ts'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function clientWith(...responses: Array<Response | Error>) {
  const queue = [...responses]
  const fetchMock = vi.fn<typeof fetch>(() => {
    const next = queue.shift()
    if (next === undefined) throw new Error('unexpected fetch')
    return next instanceof Error ? Promise.reject(next) : Promise.resolve(next)
  })
  return { client: createApiClient({ fetch: fetchMock }), fetchMock }
}

function lastCall(fetchMock: ReturnType<typeof clientWith>['fetchMock']) {
  const [url, init] = fetchMock.mock.calls.at(-1) ?? []
  return { url: String(url), init: (init ?? {}) as RequestInit & { headers: Record<string, string> } }
}

describe('GET', () => {
  it('requests a same-origin path with the query, skipping empty values', async () => {
    const { client, fetchMock } = clientWith(jsonResponse(200, { ok: true }))
    const body = await client.get<{ ok: boolean }>('/api/odds', { season: 2026, week: 9, team: undefined, x: null })
    expect(body).toEqual({ ok: true })
    const { url, init } = lastCall(fetchMock)
    expect(url).toBe('/api/odds?season=2026&week=9')
    expect(init.method).toBe('GET')
    expect(init.credentials).toBe('same-origin')
  })

  it('adds no action header and no body to a GET', async () => {
    const { client, fetchMock } = clientWith(jsonResponse(200, {}))
    await client.get('/api/config')
    const { init } = lastCall(fetchMock)
    expect(init.headers['X-CFB-Action']).toBeUndefined()
    expect(init.body).toBeUndefined()
  })

  it('encodes query values', async () => {
    const { client, fetchMock } = clientWith(jsonResponse(200, {}))
    await client.get('/api/matchup', { team: 'a b&c' })
    expect(lastCall(fetchMock).url).toBe('/api/matchup?team=a+b%26c')
  })
})

describe('POST', () => {
  it('sends the action header, JSON content type and a JSON body', async () => {
    const { client, fetchMock } = clientWith(jsonResponse(202, { action_id: 'a-1' }))
    const body = await client.post<{ action_id: string }>('/api/refresh/run', { season: 2026 })
    expect(body.action_id).toBe('a-1')
    const { url, init } = lastCall(fetchMock)
    expect(url).toBe('/api/refresh/run')
    expect(init.method).toBe('POST')
    expect(init.headers['X-CFB-Action']).toBe('1')
    expect(init.headers['Content-Type']).toBe('application/json')
    expect(init.body).toBe('{"season":2026}')
  })

  it('never retries a failed POST', async () => {
    const { client, fetchMock } = clientWith(jsonResponse(503, { error: 'busy', message: 'try later' }), jsonResponse(200, {}))
    await expect(client.post('/api/preview', {})).rejects.toBeInstanceOf(ApiError)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('never retries a POST that failed on the network', async () => {
    const { client, fetchMock } = clientWith(new TypeError('Failed to fetch'), jsonResponse(200, {}))
    await expect(client.post('/api/preview', {})).rejects.toMatchObject({ kind: 'network' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('never retries a GET either', async () => {
    const { client, fetchMock } = clientWith(jsonResponse(502, { error: 'cfbd_failed', message: 'x' }), jsonResponse(200, {}))
    await expect(client.get('/api/odds')).rejects.toBeInstanceOf(ApiError)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe('typed errors', () => {
  it.each([
    [400, 'condition_error', 'bad_request'],
    [403, 'forbidden', 'forbidden'],
    [404, 'not_found', 'not_found'],
    [409, 'live_busy', 'conflict'],
    [409, 'max_calls_exceeded', 'conflict'],
    [502, 'cfbd_failed', 'bad_gateway'],
    [503, 'database_unreachable', 'unavailable'],
    [503, 'busy', 'unavailable'],
  ] as const)('maps %i %s to %s', async (status, code, kind) => {
    const { client } = clientWith(jsonResponse(status, { error: code, message: `m-${code}` }))
    const error = await client.get('/api/x').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status, code, kind, message: `m-${code}` })
  })

  it('keeps the charged calls of a failed live request', async () => {
    const { client } = clientWith(jsonResponse(502, { error: 'cfbd_failed', message: 'timeout', calls_charged: 4 }))
    await expect(client.post('/api/preview', {})).rejects.toMatchObject({ callsCharged: 4 })
  })

  it('survives a body that is not JSON', async () => {
    const { client } = clientWith(new Response('<html>Bad gateway</html>', { status: 502 }))
    await expect(client.get('/api/x')).rejects.toMatchObject({ status: 502, code: 'unknown', kind: 'bad_gateway' })
  })

  it('does not echo a non-JSON body as the message', async () => {
    const { client } = clientWith(new Response('driver stack trace ...', { status: 500 }))
    const error = (await client.get('/api/x').catch((e: unknown) => e)) as ApiError
    expect(error.message).not.toContain('driver')
    expect(error.kind).toBe('server')
  })

  it('classifies a code that the client does not know as unknown', async () => {
    const { client } = clientWith(jsonResponse(409, { error: 'brand_new_code', message: 'm' }))
    await expect(client.get('/api/x')).rejects.toMatchObject({ code: 'unknown', kind: 'conflict' })
  })

  it('offers predicates for the cases the pages branch on', async () => {
    const { client } = clientWith(
      jsonResponse(409, { error: 'live_busy', message: 'm' }),
      jsonResponse(503, { error: 'database_unreachable', message: 'm' }),
      jsonResponse(503, { error: 'busy', message: 'm' }),
    )
    const busy = await client.get('/api/x').catch((e: unknown) => e)
    const outage = await client.get('/api/x').catch((e: unknown) => e)
    const pool = await client.get('/api/x').catch((e: unknown) => e)
    expect(isLiveBusy(busy)).toBe(true)
    expect(isOutage(outage)).toBe(true)
    expect(isOutage(pool)).toBe(false)
    expect(isLiveBusy(outage)).toBe(false)
  })
})

describe('page loads', () => {
  function abortingFetch() {
    return vi.fn(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
        }),
    )
  }

  it('aborts the previous page load when a new one begins', async () => {
    const fetchMock = abortingFetch()
    const client = createApiClient({ fetch: fetchMock })
    const first = client.beginPageLoad()
    const pending = first.get('/api/odds')
    const second = client.beginPageLoad()
    const error = await pending.catch((e: unknown) => e)
    expect(first.signal.aborted).toBe(true)
    expect(second.signal.aborted).toBe(false)
    expect(isAbortError(error)).toBe(true)
    expect(error).toMatchObject({ kind: 'aborted' })
  })

  it('shares one signal across the requests of a page load', async () => {
    const fetchMock = abortingFetch()
    const client = createApiClient({ fetch: fetchMock })
    const load = client.beginPageLoad()
    const a = load.get('/api/odds').catch((e: unknown) => e)
    const b = load.get('/api/guide').catch((e: unknown) => e)
    load.abort()
    expect(isAbortError(await a)).toBe(true)
    expect(isAbortError(await b)).toBe(true)
    expect(fetchMock.mock.calls.map((call) => call[1]?.signal)).toEqual([load.signal, load.signal])
  })

  it('lets a page load carry a read POST, such as a what-if, that the load can abort', async () => {
    const fetchMock = abortingFetch()
    const client = createApiClient({ fetch: fetchMock })
    const load = client.beginPageLoad()
    const pending = load.post('/api/whatif/odds', { run_id: 7 }).catch((e: unknown) => e)
    load.abort()
    expect(isAbortError(await pending)).toBe(true)
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit & { headers: Record<string, string> }
    expect(init.headers['X-CFB-Action']).toBe('1')
  })

  it('does not abort a spending POST made through the client when the page changes', async () => {
    const { client } = clientWith(jsonResponse(200, { ok: true }))
    const load = client.beginPageLoad()
    const spending = client.post<{ ok: boolean }>('/api/preview', {})
    client.beginPageLoad()
    expect(load.signal.aborted).toBe(true)
    await expect(spending).resolves.toEqual({ ok: true })
  })
})
