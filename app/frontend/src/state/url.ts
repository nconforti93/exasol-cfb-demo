import type { AppState, AppStore, ChosenPoint, Pair, PageId } from './store.ts'
import { PAGES } from './store.ts'

export interface ParsedLocation {
  page: PageId
  chosen: ChosenPoint
  focusTeam: number | null
  pair: Pair
}

function integer(value: string | null, minimum: number): number | null {
  if (value === null || !/^\d+$/.test(value)) return null
  const parsed = Number(value)
  return Number.isSafeInteger(parsed) && parsed >= minimum ? parsed : null
}

function pageOf(pathname: string): PageId {
  const segment = pathname.replace(/^\/+|\/+$/g, '')
  return PAGES.find((page) => page === segment) ?? 'odds'
}

/** The page, point, focus team and pair a link names. Anything unusable falls back to the defaults. */
export function parseLocation(pathname: string, search: string): ParsedLocation {
  const params = new URLSearchParams(search)
  const page = pageOf(pathname)
  const season = integer(params.get('season'), 0)
  const week = integer(params.get('week'), 0)
  const chosen: ChosenPoint =
    season !== null && week !== null ? { mode: 'explicit', season, week_seq: week } : { mode: 'follow' }
  const team = integer(params.get('team'), 0)
  if (page === 'head-to-head') {
    return { page, chosen, focusTeam: null, pair: { team, opponent: integer(params.get('opponent'), 0) } }
  }
  return { page, chosen, focusTeam: page === 'odds' ? team : null, pair: { team: null, opponent: null } }
}

/** The path and query that reproduce the state's page, point and team choices. */
export function buildPath(state: AppState): string {
  const params = new URLSearchParams()
  if (state.chosen.mode === 'explicit') {
    params.set('season', String(state.chosen.season))
    params.set('week', String(state.chosen.week_seq))
  }
  if (state.page === 'odds' && state.focusTeam !== null) params.set('team', String(state.focusTeam))
  if (state.page === 'head-to-head') {
    if (state.pair.team !== null) params.set('team', String(state.pair.team))
    if (state.pair.opponent !== null) params.set('opponent', String(state.pair.opponent))
  }
  const query = params.toString()
  return query === '' ? `/${state.page}` : `/${state.page}?${query}`
}

/** Applies a location to the store; unchanged parts leave the state untouched. */
export function syncStoreFromLocation(store: AppStore, pathname: string, search: string): void {
  const parsed = parseLocation(pathname, search)
  const { actions } = store
  const state = store.getState()
  actions.setPage(parsed.page)
  if (parsed.chosen.mode === 'explicit') {
    const same =
      state.chosen.mode === 'explicit' &&
      state.chosen.season === parsed.chosen.season &&
      state.chosen.week_seq === parsed.chosen.week_seq
    if (!same) actions.choosePoint(parsed.chosen.season, parsed.chosen.week_seq)
  } else if (state.chosen.mode === 'explicit') {
    actions.followCurrent()
  }
  if (parsed.page === 'odds') actions.setFocusTeam(parsed.focusTeam)
  if (parsed.page === 'head-to-head') actions.setPair(parsed.pair.team, parsed.pair.opponent)
}
