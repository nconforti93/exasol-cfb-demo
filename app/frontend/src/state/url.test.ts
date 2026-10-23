import { describe, expect, it } from 'vitest'

import { createAppStore } from './store.ts'
import { buildPath, parseLocation, pathFor, syncStoreFromLocation } from './url.ts'

describe('parseLocation', () => {
  it.each([
    ['/', 'home'],
    ['/team/102', 'team'],
    ['/league', 'week'],
    ['/league/week', 'week'],
    ['/league/odds', 'odds'],
    ['/league/predictions', 'predictions'],
    ['/league/upsets', 'upsets'],
    ['/league/stats', 'stats'],
    ['/league/nonsense', 'week'],
    ['/head-to-head', 'head-to-head'],
    ['/odds', 'odds'],
    ['/predictions', 'predictions'],
    ['/upsets', 'upsets'],
    ['/nonsense', 'home'],
    ['/team', 'home'],
    ['/team/abc', 'home'],
    ['/league/odds/', 'odds'],
  ] as const)('maps %s to the %s page', (pathname, page) => {
    expect(parseLocation(pathname, '').page).toBe(page)
  })

  it('reads the team page\'s team from the path, and the map filter from ?conf=', () => {
    expect(parseLocation('/team/102', '?season=2026&week=8')).toMatchObject({ page: 'team', focusTeam: 102 })
    expect(parseLocation('/', '?conf=Atlantic').mapConference).toBe('Atlantic')
    expect(parseLocation('/', '?conf=').mapConference).toBeNull()
    expect(parseLocation('/league/odds', '?conf=Atlantic').mapConference).toBeNull()
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
  it('builds the root path for the map while following, and carries the conference', () => {
    const store = createAppStore()
    expect(buildPath(store.getState())).toBe('/')
    store.actions.setMapConference('Big Ten')
    expect(buildPath(store.getState())).toBe('/?conf=Big+Ten')
  })

  it('builds /team/<id> and /league/<tab> paths, which are what an old path is replaced by', () => {
    const store = createAppStore()
    store.actions.navigate({ page: 'team', team: 102 })
    store.actions.choosePoint(2026, 8)
    expect(buildPath(store.getState())).toBe('/team/102?season=2026&week=8')
    for (const tab of ['week', 'predictions', 'upsets', 'stats'] as const) {
      store.actions.setPage(tab)
      expect(buildPath(store.getState())).toBe(`/league/${tab}?season=2026&week=8`)
    }
  })

  it('keeps an explicit point and the focus team on the odds page', () => {
    const store = createAppStore()
    store.actions.setPage('odds')
    store.actions.choosePoint(2026, 8)
    store.actions.setFocusTeam(102)
    expect(buildPath(store.getState())).toBe('/league/odds?season=2026&week=8&team=102')
  })

  it('writes the address of a destination without changing the state', () => {
    const store = createAppStore()
    store.actions.choosePoint(2026, 8)
    expect(pathFor(store.getState(), { page: 'team', team: 7 })).toBe('/team/7?season=2026&week=8')
    expect(store.getState().page).toBe('home')
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
    expect(buildPath(store.getState())).toBe('/league/upsets?season=2026&week=3')
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

  it('sets the team page\'s team, the map filter and the old paths\' pages', () => {
    const store = createAppStore()
    syncStoreFromLocation(store, '/team/102', '')
    expect(store.getState()).toMatchObject({ page: 'team', focusTeam: 102 })
    syncStoreFromLocation(store, '/', '?conf=Atlantic')
    expect(store.getState()).toMatchObject({ page: 'home', mapConference: 'Atlantic' })
    syncStoreFromLocation(store, '/upsets', '')
    expect(buildPath(store.getState())).toBe('/league/upsets')
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
