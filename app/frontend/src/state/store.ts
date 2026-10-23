import type { ConfigResponse, CurrentPoint, FollowKey, FollowState, WhatIfCondition } from '../api/types.ts'

export const MAX_CONDITIONS = 16

export const PAGES = ['odds', 'predictions', 'upsets', 'head-to-head'] as const
export type PageId = (typeof PAGES)[number]

export type ChosenPoint = { mode: 'follow' } | { mode: 'explicit'; season: number; week_seq: number }

export interface PointId {
  season: number
  week_seq: number
}

/** The head-to-head pair; the chat's app-state tools read and set it (P7-a). */
export interface Pair {
  team: number | null
  opponent: number | null
}

export interface InspectorState {
  open: boolean
  entryId: string | null
}

export interface FollowStatus {
  /** The key of the latest reading. */
  key: FollowKey | null
  /** The key the pages loaded with. */
  loadedKey: FollowKey | null
  hold: boolean
  stuck: boolean
  /** A newer current point offered to a browser that chose a point itself. */
  offeredKey: FollowKey | null
}

export interface AppState {
  config: ConfigResponse | null
  current: CurrentPoint | null
  chosen: ChosenPoint
  page: PageId
  focusTeam: number | null
  pair: Pair
  conditions: WhatIfCondition[]
  inspector: InspectorState
  follow: FollowStatus
  /** Changes whenever every page must load again: a new follow key, a retry. */
  reloadToken: number
  outage: boolean
}

export type FollowNotice = 'holding' | 'stuck' | 'offer'

export interface ChatContext {
  point: PointId | null
  page: PageId
  focus_team: number | null
  pair: Pair
  conditions: WhatIfCondition[]
}

export interface AppActions {
  setConfig(config: ConfigResponse): void
  setCurrent(current: CurrentPoint): void
  setPage(page: PageId): void
  choosePoint(season: number, weekSeq: number): void
  followCurrent(): void
  applyFollow(follow: FollowState): void
  setFocusTeam(teamId: number | null): void
  setPair(team: number | null, opponent: number | null): void
  swapPair(): void
  /** Sets, replaces or (with null) removes a condition. Returns false when the 16-condition limit refuses it. */
  setCondition(gameId: number, winner: WhatIfCondition['winner'] | null): boolean
  clearConditions(): void
  setInspector(patch: Partial<InspectorState>): void
  setOutage(outage: boolean): void
  reload(): void
}

export interface Store<S> {
  getState(): S
  subscribe(listener: () => void): () => void
}

export interface AppStore extends Store<AppState> {
  actions: AppActions
}

export function followKeyOf(point: CurrentPoint): FollowKey {
  return {
    as_of_season: point.as_of_season,
    as_of_week_seq: point.as_of_week_seq,
    publication_id: point.publication_id,
    whatif_run_id: point.whatif_run_id,
    whatif_views_current: point.whatif_views_current,
  }
}

export function sameFollowKey(a: FollowKey | null, b: FollowKey | null): boolean {
  if (a === null || b === null) return a === b
  return (
    a.as_of_season === b.as_of_season &&
    a.as_of_week_seq === b.as_of_week_seq &&
    a.publication_id === b.publication_id &&
    a.whatif_run_id === b.whatif_run_id &&
    a.whatif_views_current === b.whatif_views_current
  )
}

/** The point every page loads: the explicit choice, else the key the pages last loaded with. */
export function effectivePoint(state: AppState): PointId | null {
  if (state.chosen.mode === 'explicit') return { season: state.chosen.season, week_seq: state.chosen.week_seq }
  const key = state.follow.loadedKey
  return key === null ? null : { season: key.as_of_season, week_seq: key.as_of_week_seq }
}

export function selectFollowNotice(state: AppState): FollowNotice | null {
  if (state.follow.stuck) return 'stuck'
  if (state.follow.hold) return 'holding'
  const offered = state.follow.offeredKey
  if (state.chosen.mode === 'explicit' && offered !== null) {
    const shown = state.chosen
    const alreadyShown = offered.as_of_season === shown.season && offered.as_of_week_seq === shown.week_seq
    return alreadyShown ? null : 'offer'
  }
  return null
}

export function selectChatContext(state: AppState): ChatContext {
  return {
    point: effectivePoint(state),
    page: state.page,
    focus_team: state.focusTeam,
    pair: state.pair,
    conditions: state.conditions,
  }
}

export function initialState(): AppState {
  return {
    config: null,
    current: null,
    chosen: { mode: 'follow' },
    page: 'odds',
    focusTeam: null,
    pair: { team: null, opponent: null },
    conditions: [],
    inspector: { open: false, entryId: null },
    follow: { key: null, loadedKey: null, hold: false, stuck: false, offeredKey: null },
    reloadToken: 0,
    outage: false,
  }
}

function followReading(state: AppState, reading: FollowState): AppState {
  const status: FollowStatus = { ...state.follow, key: reading.key, hold: reading.hold, stuck: reading.stuck }
  if (status.loadedKey === null) {
    return { ...state, follow: { ...status, loadedKey: reading.key } }
  }
  const changed = !sameFollowKey(reading.key, status.loadedKey)
  if (reading.hold || !changed) return { ...state, follow: status }
  if (state.chosen.mode === 'follow') {
    return {
      ...state,
      follow: { ...status, loadedKey: reading.key, offeredKey: null },
      conditions: [],
      reloadToken: state.reloadToken + 1,
    }
  }
  return { ...state, follow: { ...status, loadedKey: reading.key, offeredKey: reading.key } }
}

export function createAppStore(initial: Partial<AppState> = {}): AppStore {
  let state: AppState = { ...initialState(), ...initial }
  const listeners = new Set<() => void>()

  function update(next: AppState): void {
    if (next === state) return
    state = next
    for (const listener of [...listeners]) listener()
  }

  const actions: AppActions = {
    setConfig: (config) => update({ ...state, config }),
    setCurrent(current) {
      const key = followKeyOf(current)
      const follow = state.follow.loadedKey === null ? { ...state.follow, key, loadedKey: key } : state.follow
      update({ ...state, current, follow })
    },
    setPage: (page) => update(state.page === page ? state : { ...state, page }),
    choosePoint: (season, weekSeq) =>
      update({
        ...state,
        chosen: { mode: 'explicit', season, week_seq: weekSeq },
        conditions: [],
        follow: { ...state.follow, offeredKey: null },
      }),
    followCurrent() {
      const latest = state.follow.key
      update({
        ...state,
        chosen: { mode: 'follow' },
        conditions: [],
        follow: { ...state.follow, loadedKey: latest ?? state.follow.loadedKey, offeredKey: null },
        reloadToken: state.reloadToken + 1,
      })
    },
    applyFollow: (reading) => update(followReading(state, reading)),
    setFocusTeam: (teamId) => update(state.focusTeam === teamId ? state : { ...state, focusTeam: teamId }),
    setPair(team, opponent) {
      if (state.pair.team === team && state.pair.opponent === opponent) return
      update({ ...state, pair: { team, opponent } })
    },
    swapPair: () => update({ ...state, pair: { team: state.pair.opponent, opponent: state.pair.team } }),
    setCondition(gameId, winner) {
      const others = state.conditions.filter((c) => c.game_id !== gameId)
      if (winner === null) {
        update({ ...state, conditions: others })
        return true
      }
      const exists = others.length !== state.conditions.length
      if (!exists && state.conditions.length >= MAX_CONDITIONS) return false
      const next = exists
        ? state.conditions.map((c) => (c.game_id === gameId ? { game_id: gameId, winner } : c))
        : [...others, { game_id: gameId, winner }]
      update({ ...state, conditions: next })
      return true
    },
    clearConditions: () => update(state.conditions.length === 0 ? state : { ...state, conditions: [] }),
    setInspector: (patch) => update({ ...state, inspector: { ...state.inspector, ...patch } }),
    setOutage: (outage) => update(state.outage === outage ? state : { ...state, outage }),
    reload: () => update({ ...state, reloadToken: state.reloadToken + 1 }),
  }

  return {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    actions,
  }
}
