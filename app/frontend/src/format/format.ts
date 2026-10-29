const CFBD_ORIGIN = 'https://api.collegefootballdata.com'
const SECRET_PARAMS = new Set(['key', 'apikey', 'api_key', 'authorization', 'access_token', 'token'])
const MINUS = '−'
const EN_DASH = '–'
const INTEGER_FORMAT = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })

export type SpendSource = 'LIVE' | 'REPLAY' | 'STORED' | 'NONE' | 'CACHED'

export interface SpendLabelInput {
  source: SpendSource
  live_calls: number
}

export interface RunBannerInput {
  n_sims: number
  seconds_total: number
  nodes: number | null
  instances: number | null
}

export function formatInteger(value: number): string {
  return INTEGER_FORMAT.format(value)
}

export function formatPercent(probability: number | null): string {
  if (probability === null) return EN_DASH
  const percent = probability * 100
  if (probability > 0 && percent < 0.05) return '< 0.1 %'
  return `${percent.toFixed(1)} %`
}

export function formatPercentExact(probability: number | null): string {
  if (probability === null) return ''
  return `${Number((probability * 100).toPrecision(6))} %`
}

export function formatDeltaPoints(fraction: number | null): string {
  if (fraction === null) return ''
  const points = (fraction * 100).toFixed(1)
  const value = Number(points)
  if (value === 0) return '0.0'
  return value > 0 ? `+${points}` : `${MINUS}${Math.abs(value).toFixed(1)}`
}

export function formatRank(rank: number | null, tied: boolean): string {
  if (rank === null) return ''
  return tied ? `T-${rank}` : String(rank)
}

/** A poll rank as "#6". */
export function formatPollRank(rank: number): string {
  return `#${rank}`
}

export function formatProbSource(source: string): string {
  return source.toUpperCase() === 'ELO' ? 'Elo' : source.toLowerCase()
}

export function formatRankWithSource(rank: number | null, tied: boolean, source: string): string {
  const label = formatRank(rank, tied)
  return label === '' ? '' : `${label} (${formatProbSource(source)})`
}

export function formatRecord(wins: number, losses: number, ties: number): string {
  const record = `${wins}${EN_DASH}${losses}`
  return ties > 0 ? `${record}${EN_DASH}${ties}` : record
}

function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return count === 1 ? singular : pluralForm
}

export function formatSeasonsInSeconds(seasons: number, seconds: number): string {
  return `${formatInteger(seasons)} ${plural(seasons, 'season')} in ${seconds.toFixed(1)} s`
}

export function formatRunBanner(run: RunBannerInput): string {
  const base = formatSeasonsInSeconds(run.n_sims, run.seconds_total)
  if (run.nodes === null || run.instances === null) return base
  return `${base} on ${run.nodes} ${plural(run.nodes, 'node')} × ${run.instances} ${plural(run.instances, 'instance')}`
}

export function formatCalls(calls: number): string {
  return `${calls} ${plural(calls, 'call')}`
}

export function formatSpendLabel(plan: SpendLabelInput): string {
  switch (plan.source) {
    case 'LIVE':
      if (plan.live_calls === 0) return 'live · 0 calls'
      return plan.live_calls === 1 ? '1 live call' : `up to ${plan.live_calls} live calls`
    case 'REPLAY':
      return 'replay · 0 calls'
    case 'STORED':
      return 'stored · 0 calls'
    case 'CACHED':
      return 'cached · 0 calls'
    case 'NONE':
      return 'no virtual table: 0 calls'
  }
}

export function formatConfirmation(calls: number): string {
  return `up to ${calls} live ${plural(calls, 'call')}, plus retries`
}

export function formatHttpRequest(sliceKey: string): string {
  const url = new URL(sliceKey.replace(/^\/?/, '/'), CFBD_ORIGIN)
  for (const name of [...url.searchParams.keys()]) {
    if (SECRET_PARAMS.has(name.toLowerCase())) url.searchParams.delete(name)
  }
  return `GET ${url.origin}${url.pathname}${url.search}`
}
