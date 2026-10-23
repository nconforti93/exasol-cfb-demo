import type { AppState, AppStore, ChosenPoint, Destination, Pair, PageId } from './store.ts'
import { applyDestination, DEFAULT_PAGE, isLeagueTab, LEAGUE_TABS } from './store.ts'

export interface ParsedLocation {
  page: PageId
  chosen: ChosenPoint
  focusTeam: number | null
  mapConference: string | null
  pair: Pair
}

const LEAGUE_DEFAULT_TAB: PageId = 'week'
const OLD_LEAGUE_PATHS: readonly PageId[] = ['odds', 'predictions', 'upsets']

function integer(value: string | null, minimum: number): number | null {
  if (value === null || !/^\d+$/.test(value)) return null
  const parsed = Number(value)
  return Number.isSafeInteger(parsed) && parsed >= minimum ? parsed : null
}

interface PagePath {
  page: PageId
  teamId: number | null
}

/** The page a path names: `/`, `/team/<id>`, `/league/<tab>`, `/head-to-head` and the old `/odds`, `/predictions`, `/upsets`. */
function pageOf(pathname: string): PagePath {
  const [first, second, ...rest] = pathname.replace(/^\/+|\/+$/g, '').split('/')
  if (first === 'team' && rest.length === 0) {
    const teamId = integer(second ?? null, 0)
    return teamId === null ? { page: DEFAULT_PAGE, teamId: null } : { page: 'team', teamId }
  }
  if (first === 'league') {
    const tab = LEAGUE_TABS.find((candidate) => candidate === second)
    return { page: tab ?? LEAGUE_DEFAULT_TAB, teamId: null }
  }
  if (first === 'head-to-head') return { page: 'head-to-head', teamId: null }
  const old = OLD_LEAGUE_PATHS.find((candidate) => candidate === first)
  return { page: old ?? DEFAULT_PAGE, teamId: null }
}

/** The page, point, focus team, map filter and pair a link names. Anything unusable falls back to the defaults. */
export function parseLocation(pathname: string, search: string): ParsedLocation {
  const params = new URLSearchParams(search)
  const { page, teamId } = pageOf(pathname)
  const season = integer(params.get('season'), 0)
  const week = integer(params.get('week'), 0)
  const chosen: ChosenPoint =
    season !== null && week !== null ? { mode: 'explicit', season, week_seq: week } : { mode: 'follow' }
  const team = integer(params.get('team'), 0)
  const conference = (params.get('conf') ?? '').trim()
  const noPair: Pair = { team: null, opponent: null }
  if (page === 'head-to-head') {
    return { page, chosen, focusTeam: null, mapConference: null, pair: { team, opponent: integer(params.get('opponent'), 0) } }
  }
  if (page === 'team') return { page, chosen, focusTeam: teamId, mapConference: null, pair: noPair }
  if (page === 'home') {
    return { page, chosen, focusTeam: null, mapConference: conference === '' ? null : conference, pair: noPair }
  }
  return { page, chosen, focusTeam: page === 'odds' ? team : null, mapConference: null, pair: noPair }
}

function pathOfPage(state: AppState): string {
  if (state.page === 'home') return '/'
  if (state.page === 'team') return state.focusTeam === null ? '/' : `/team/${state.focusTeam}`
  if (isLeagueTab(state.page)) return `/league/${state.page}`
  return `/${state.page}`
}

/** The path and query that reproduce the state's page, point and team choices. */
export function buildPath(state: AppState): string {
  const params = new URLSearchParams()
  if (state.chosen.mode === 'explicit') {
    params.set('season', String(state.chosen.season))
    params.set('week', String(state.chosen.week_seq))
  }
  if (state.page === 'odds' && state.focusTeam !== null) params.set('team', String(state.focusTeam))
  if (state.page === 'home' && state.mapConference !== null) params.set('conf', state.mapConference)
  if (state.page === 'head-to-head') {
    if (state.pair.team !== null) params.set('team', String(state.pair.team))
    if (state.pair.opponent !== null) params.set('opponent', String(state.pair.opponent))
  }
  const query = params.toString()
  const path = pathOfPage(state)
  return query === '' ? path : `${path}?${query}`
}

/** The address of a destination, keeping the chosen point: what a link's `href` holds. */
export function pathFor(state: AppState, destination: Destination): string {
  return buildPath(applyDestination(state, destination))
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
  if (parsed.page === 'odds' || parsed.page === 'team') actions.setFocusTeam(parsed.focusTeam)
  if (parsed.page === 'home') actions.setMapConference(parsed.mapConference)
  if (parsed.page === 'head-to-head') actions.setPair(parsed.pair.team, parsed.pair.opponent)
}
