import { describe, expect, it } from 'vitest'

import { createAppStore } from './store.ts'
import { buildPath, parseLocation, syncStoreFromLocation } from './url.ts'

describe('parseLocation', () => {
  it.each([
    ['/', 'odds'],
    ['/odds', 'odds'],
    ['/predictions', 'predictions'],
    ['/upsets', 'upsets'],
    ['/head-to-head', 'head-to-head'],
    ['/nonsense', 'odds'],
    ['/predictions/', 'predictions'],
  ] as const)('maps %s to the %s page', (pathname, page) => {
    expect(parseLocation(pathname, '').page).toBe(page)
  })

  it('reads an explicit point from season and week sequence', () => {
    expect(parseLocation('/odds', '?season=2026&week=8').chosen).toEqual({
      mode: 'explicit',
      season: 2026,
      week_seq: 8,
    })
  })

  it('follows the current point without both parameters or with bad ones', () => {
    expect(parseLocation('/odds', '').chosen).toEqual({ mode: 'follow' })
    expect(parseLocation('/odds', '?season=2026').chosen).toEqual({ mode: 'follow' })
    expect(parseLocation('/odds', '?season=x&week=y').chosen).toEqual({ mode: 'follow' })
    expect(parseLocation('/odds', '?season=2026&week=-1').chosen).toEqual({ mode: 'follow' })
  })

  it('reads the focus team on the odds page', () => {
    const parsed = parseLocation('/odds', '?team=102')
    expect(parsed.focusTeam).toBe(102)
    expect(parsed.pair).toEqual({ team: null, opponent: null })
  })

  it('reads the pair on the head-to-head page', () => {
    const parsed = parseLocation('/head-to-head', '?team=101&opponent=102')
    expect(parsed.pair).toEqual({ team: 101, opponent: 102 })
    expect(parsed.focusTeam).toBeNull()
  })

  it('ignores a team that is not an integer', () => {
    expect(parseLocation('/odds', '?team=abc').focusTeam).toBeNull()
  })
})

describe('buildPath', () => {
  it('builds the path of a page with no parameters while following', () => {
    const store = createAppStore()
    expect(buildPath(store.getState())).toBe('/odds')
  })

  it('keeps an explicit point and the focus team on the odds page', () => {
    const store = createAppStore()
    store.actions.choosePoint(2026, 8)
    store.actions.setFocusTeam(102)
    expect(buildPath(store.getState())).toBe('/odds?season=2026&week=8&team=102')
  })

  it('keeps the pair on the head-to-head page, not the focus team', () => {
    const store = createAppStore()
    store.actions.setPage('head-to-head')
    store.actions.setFocusTeam(102)
    store.actions.setPair(101, 102)
    expect(buildPath(store.getState())).toBe('/head-to-head?team=101&opponent=102')
  })

  it('carries only the point on the other pages', () => {
    const store = createAppStore()
    store.actions.setPage('upsets')
    store.actions.setFocusTeam(102)
    store.actions.choosePoint(2026, 3)
    expect(buildPath(store.getState())).toBe('/upsets?season=2026&week=3')
  })
})

describe('syncStoreFromLocation', () => {
  it('round trips a link into the store', () => {
    const store = createAppStore()
    syncStoreFromLocation(store, '/head-to-head', '?season=2026&week=8&team=101&opponent=102')
    expect(store.getState().page).toBe('head-to-head')
    expect(store.getState().chosen).toEqual({ mode: 'explicit', season: 2026, week_seq: 8 })
    expect(store.getState().pair).toEqual({ team: 101, opponent: 102 })
    expect(buildPath(store.getState())).toBe('/head-to-head?season=2026&week=8&team=101&opponent=102')
  })

  it('does not rewrite an unchanged explicit point, so nothing reloads', () => {
    const store = createAppStore()
    syncStoreFromLocation(store, '/odds', '?season=2026&week=8')
    const before = store.getState()
    syncStoreFromLocation(store, '/odds', '?season=2026&week=8')
    expect(store.getState()).toBe(before)
  })

  it('returns to following when the link has no point', () => {
    const store = createAppStore()
    store.actions.choosePoint(2026, 8)
    syncStoreFromLocation(store, '/odds', '')
    expect(store.getState().chosen).toEqual({ mode: 'follow' })
  })
})
