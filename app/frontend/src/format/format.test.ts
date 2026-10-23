import { describe, expect, it } from 'vitest'

import {
  formatCalls,
  formatConfirmation,
  formatDeltaPoints,
  formatHttpRequest,
  formatInteger,
  formatPercent,
  formatPercentExact,
  formatRank,
  formatRankWithSource,
  formatRecord,
  formatRunBanner,
  formatSeasonsInSeconds,
  formatSpendLabel,
} from './format.ts'

describe('formatPercent', () => {
  it('shows one decimal', () => {
    expect(formatPercent(0.1234)).toBe('12.3 %')
    expect(formatPercent(1)).toBe('100.0 %')
    expect(formatPercent(0)).toBe('0.0 %')
  })

  it('shows "< 0.1 %" for a value above 0 and below 0.05 %', () => {
    expect(formatPercent(0.0004)).toBe('< 0.1 %')
    expect(formatPercent(0.000001)).toBe('< 0.1 %')
  })

  it('rounds 0.05 % up to 0.1 %', () => {
    expect(formatPercent(0.0005)).toBe('0.1 %')
  })

  it('shows a dash for a missing value', () => {
    expect(formatPercent(null)).toBe('–')
  })
})

describe('formatPercentExact', () => {
  it('gives the exact value for a hover title', () => {
    expect(formatPercentExact(0.000312)).toBe('0.0312 %')
    expect(formatPercentExact(0.5)).toBe('50 %')
    expect(formatPercentExact(null)).toBe('')
  })
})

describe('formatDeltaPoints', () => {
  it('reads a fraction as signed percentage points', () => {
    expect(formatDeltaPoints(0.023)).toBe('+2.3')
    expect(formatDeltaPoints(-0.011)).toBe('−1.1')
    expect(formatDeltaPoints(0)).toBe('0.0')
    expect(formatDeltaPoints(null)).toBe('')
  })
})

describe('formatRank', () => {
  it('reads a shared rank as T-n and a single rank as n', () => {
    expect(formatRank(37, true)).toBe('T-37')
    expect(formatRank(38, false)).toBe('38')
  })

  it('has no label without a rank', () => {
    expect(formatRank(null, false)).toBe('')
  })

  it('puts the probability source beside the rank', () => {
    expect(formatRankWithSource(37, true, 'LINE')).toBe('T-37 (line)')
    expect(formatRankWithSource(38, false, 'ELO')).toBe('38 (Elo)')
  })
})

describe('formatRecord', () => {
  it('joins wins and losses with an en dash and shows ties only when present', () => {
    expect(formatRecord(41, 9, 0)).toBe('41–9')
    expect(formatRecord(2, 2, 1)).toBe('2–2–1')
  })
})

describe('formatSeasonsInSeconds', () => {
  it('reads "N seasons in S s"', () => {
    expect(formatSeasonsInSeconds(100000, 18.43)).toBe('100,000 seasons in 18.4 s')
    expect(formatSeasonsInSeconds(2000, 3)).toBe('2,000 seasons in 3.0 s')
  })

  it('uses the singular for one season', () => {
    expect(formatSeasonsInSeconds(1, 0.2)).toBe('1 season in 0.2 s')
  })
})

describe('formatRunBanner', () => {
  it('adds the machine sizing', () => {
    expect(formatRunBanner({ n_sims: 100000, seconds_total: 18.4, nodes: 1, instances: 8 })).toBe(
      '100,000 seasons in 18.4 s on 1 node × 8 instances',
    )
    expect(formatRunBanner({ n_sims: 2000, seconds_total: 3, nodes: 2, instances: 1 })).toBe(
      '2,000 seasons in 3.0 s on 2 nodes × 1 instance',
    )
  })

  it('leaves the sizing out when it is unknown', () => {
    expect(formatRunBanner({ n_sims: 4000, seconds_total: 5, nodes: null, instances: null })).toBe(
      '4,000 seasons in 5.0 s',
    )
  })
})

describe('formatInteger', () => {
  it('groups thousands', () => {
    expect(formatInteger(1234567)).toBe('1,234,567')
  })
})

describe('formatCalls', () => {
  it('counts calls', () => {
    expect(formatCalls(0)).toBe('0 calls')
    expect(formatCalls(1)).toBe('1 call')
    expect(formatCalls(3)).toBe('3 calls')
  })
})

describe('formatSpendLabel', () => {
  it('labels a single live call', () => {
    expect(formatSpendLabel({ source: 'LIVE', live_calls: 1 })).toBe('1 live call')
  })

  it('labels several live calls as an upper bound', () => {
    expect(formatSpendLabel({ source: 'LIVE', live_calls: 3 })).toBe('up to 3 live calls')
  })

  it('labels the plans that cost nothing', () => {
    expect(formatSpendLabel({ source: 'REPLAY', live_calls: 0 })).toBe('replay · 0 calls')
    expect(formatSpendLabel({ source: 'STORED', live_calls: 0 })).toBe('stored · 0 calls')
    expect(formatSpendLabel({ source: 'NONE', live_calls: 0 })).toBe('no virtual table: 0 calls')
    expect(formatSpendLabel({ source: 'CACHED', live_calls: 0 })).toBe('cached · 0 calls')
  })

  it('never shows a live plan with 0 calls as live', () => {
    expect(formatSpendLabel({ source: 'LIVE', live_calls: 0 })).toBe('live · 0 calls')
  })
})

describe('formatConfirmation', () => {
  it('names the calls and the retries', () => {
    expect(formatConfirmation(4)).toBe('up to 4 live calls, plus retries')
    expect(formatConfirmation(1)).toBe('up to 1 live call, plus retries')
  })
})

describe('formatHttpRequest', () => {
  const base = 'https://api.collegefootballdata.com'

  it('turns a slice key into the request line', () => {
    expect(formatHttpRequest('/games/teams?seasonType=both&team=Michigan&year=2022')).toBe(
      `GET ${base}/games/teams?seasonType=both&team=Michigan&year=2022`,
    )
  })

  it('accepts a key without a leading slash', () => {
    expect(formatHttpRequest('games/123/preview')).toBe(`GET ${base}/games/123/preview`)
  })

  it('never carries a key or an authorization header', () => {
    const line = formatHttpRequest('/games?year=2024&key=SECRET&apiKey=SECRET&Authorization=Bearer%20SECRET')
    expect(line).toBe(`GET ${base}/games?year=2024`)
    expect(line.toLowerCase()).not.toContain('secret')
    expect(line.toLowerCase()).not.toContain('authorization')
  })
})
