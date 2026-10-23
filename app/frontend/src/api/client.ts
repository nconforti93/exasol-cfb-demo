import type { ApiErrorBody, ApiErrorCode } from './types.ts'

export type Query = Record<string, string | number | boolean | null | undefined>

export type ErrorKind =
  | 'bad_request'
  | 'forbidden'
  | 'not_found'
  | 'conflict'
  | 'bad_gateway'
  | 'unavailable'
  | 'server'
  | 'network'
  | 'aborted'

export type ErrorCode = ApiErrorCode | 'unknown'

const KNOWN_CODES: ReadonlySet<string> = new Set<ApiErrorCode>([
  'bad_host',
  'invalid_argument',
  'condition_error',
  'forbidden',
  'operator_disabled',
  'console_disabled',
  'not_found',
  'max_calls_exceeded',
  'ledger_refused',
  'quota_refused',
  'live_busy',
  'live_disabled',
  'reconcile_pending',
  'refresh_locked',
  'refresh_running',
  'whatif_run_changed',
  'cfbd_failed',
  'database_unreachable',
  'busy',
])

const GENERIC_MESSAGES: Record<ErrorKind, string> = {
  bad_request: 'The request was refused.',
  forbidden: 'That action is not allowed.',
  not_found: 'Not found.',
  conflict: 'The request conflicts with the current state.',
  bad_gateway: 'The data source failed.',
  unavailable: 'The service is unavailable.',
  server: 'The server failed.',
  network: 'The server cannot be reached.',
  aborted: 'The request was cancelled.',
}

/** Any failed API call. `status` is 0 for a network failure or an abort. */
export class ApiError extends Error {
  readonly status: number
  readonly kind: ErrorKind
  readonly code: ErrorCode
  readonly callsCharged: number | null

  constructor(init: { status: number; kind: ErrorKind; code: ErrorCode; message: string; callsCharged?: number | null }) {
    super(init.message)
    this.name = 'ApiError'
    this.status = init.status
    this.kind = init.kind
    this.code = init.code
    this.callsCharged = init.callsCharged ?? null
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

export function isAbortError(error: unknown): boolean {
  return isApiError(error) && error.kind === 'aborted'
}

export function isLiveBusy(error: unknown): boolean {
  return isApiError(error) && error.code === 'live_busy'
}

/** The database is unreachable (the outage banner), as opposed to a busy pool. */
export function isOutage(error: unknown): boolean {
  return isApiError(error) && error.status === 503 && error.code === 'database_unreachable'
}

export function isPoolBusy(error: unknown): boolean {
  return isApiError(error) && error.status === 503 && error.code === 'busy'
}

function kindOfStatus(status: number): ErrorKind {
  switch (status) {
    case 400:
      return 'bad_request'
    case 403:
      return 'forbidden'
    case 404:
      return 'not_found'
    case 409:
      return 'conflict'
    case 502:
      return 'bad_gateway'
    case 503:
      return 'unavailable'
    default:
      return 'server'
  }
}

async function errorFromResponse(response: Response): Promise<ApiError> {
  const kind = kindOfStatus(response.status)
  const body = await response.json().then(
    (parsed: unknown) => parsed as Partial<ApiErrorBody> | null,
    () => null,
  )
  const code: ErrorCode = typeof body?.error === 'string' && KNOWN_CODES.has(body.error) ? body.error : 'unknown'
  const message = typeof body?.message === 'string' && body.message !== '' ? body.message : GENERIC_MESSAGES[kind]
  const callsCharged = typeof body?.calls_charged === 'number' ? body.calls_charged : null
  return new ApiError({ status: response.status, kind, code, message, callsCharged })
}

function errorFromFailure(failure: unknown): ApiError {
  if (failure instanceof DOMException && failure.name === 'AbortError') {
    return new ApiError({ status: 0, kind: 'aborted', code: 'unknown', message: GENERIC_MESSAGES.aborted })
  }
  return new ApiError({ status: 0, kind: 'network', code: 'unknown', message: GENERIC_MESSAGES.network })
}

function withQuery(path: string, query?: Query): string {
  if (query === undefined) return path
  const params = new URLSearchParams()
  for (const [name, value] of Object.entries(query)) {
    if (value !== undefined && value !== null) params.set(name, String(value))
  }
  const text = params.toString()
  return text === '' ? path : `${path}?${text}`
}

export interface RequestOptions {
  signal?: AbortSignal
}

/** The requests of one page load, which share one `AbortController`. */
export interface PageLoad {
  readonly signal: AbortSignal
  get<T>(path: string, query?: Query): Promise<T>
  post<T>(path: string, body: unknown): Promise<T>
  abort(): void
}

export interface ApiClient {
  get<T>(path: string, query?: Query, options?: RequestOptions): Promise<T>
  /** Sends `X-CFB-Action: 1` and JSON. Never retried, so a spending action runs at most once. */
  post<T>(path: string, body: unknown, options?: RequestOptions): Promise<T>
  /** Starts a page load and aborts the previous one: call it on a point or page change. */
  beginPageLoad(): PageLoad
}

export interface ApiClientOptions {
  fetch?: typeof fetch
}

export function createApiClient(options: ApiClientOptions = {}): ApiClient {
  const doFetch = options.fetch ?? ((input, init) => fetch(input, init))
  let current: AbortController | null = null

  async function send<T>(method: 'GET' | 'POST', url: string, body: unknown, signal?: AbortSignal): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' }
    const init: RequestInit = { method, headers, credentials: 'same-origin' }
    if (method === 'POST') {
      headers['X-CFB-Action'] = '1'
      headers['Content-Type'] = 'application/json'
      init.body = JSON.stringify(body)
    }
    if (signal !== undefined) init.signal = signal
    let response: Response
    try {
      response = await doFetch(url, init)
    } catch (failure) {
      throw errorFromFailure(failure)
    }
    if (!response.ok) throw await errorFromResponse(response)
    return (await response.json()) as T
  }

  return {
    get: (path, query, requestOptions) => send('GET', withQuery(path, query), undefined, requestOptions?.signal),
    post: (path, body, requestOptions) => send('POST', path, body, requestOptions?.signal),
    beginPageLoad() {
      current?.abort()
      const controller = new AbortController()
      current = controller
      return {
        signal: controller.signal,
        get: (path, query) => send('GET', withQuery(path, query), undefined, controller.signal),
        post: (path, body) => send('POST', path, body, controller.signal),
        abort: () => controller.abort(),
      }
    },
  }
}
