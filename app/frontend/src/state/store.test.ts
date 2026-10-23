import { describe, expect, it, vi } from 'vitest'

import type { CurrentPoint, FollowKey, FollowState } from '../api/types.ts'
import { fixture } from '../test/fixtures/index.ts'
import {
  createAppStore,
  effectivePoint,
  followKeyOf,
  MAX_CONDITIONS,
  selectChatContext,
  selectFollowNotice,
} from './store.ts'

const current = fixture<{ current: CurrentPoint }>('points.json').current

function follow(key: Partial<FollowKey>, hold = false, stuck = false): FollowState {
  return { key: { ...followKeyOf(current), ...key }, hold, stuck }
}

describe('subscribe', () => {
  it('notifies on a change and not after unsubscribe', () => {
    const store = createAppStore()
    const listener = vi.fn()
    const unsubscribe = store.subscribe(listener)
    store.actions.setPage('upsets')
    expect(listener).toHaveBeenCalledTimes(1)
    unsubscribe()
    store.actions.setPage('odds')
    expect(listener).toHaveBeenCalledTimes(1)
  })

  it('keeps the same state object when an action changes nothing', () => {
    const store = createAppStore()
    const before = store.getState()
    store.actions.setPage('odds')
    store.actions.setFocusTeam(null)
    expect(store.getState()).toBe(before)
  })
})

describe('page, focus team and head-to-head pair', () => {
  it('starts on the odds page without a focus team or pair', () => {
    const state = createAppStore().getState()
    expect(state.page).toBe('odds')
    expect(state.focusTeam).toBeNull()
    expect(state.pair).toEqual({ team: null, opponent: null })
  })

  it('sets the pair with an action (P7-a)', () => {
    const store = createAppStore()
    store.actions.setPair(101, 102)
    expect(store.getState().pair).toEqual({ team: 101, opponent: 102 })
    store.actions.setPair(null, null)
    expect(store.getState().pair).toEqual({ team: null, opponent: null })
  })

  it('swaps the pair', () => {
    const store = createAppStore()
    store.actions.setPair(101, 102)
    store.actions.swapPair()
    expect(store.getState().pair).toEqual({ team: 102, opponent: 101 })
  })

  it('keeps the focus team across pages', () => {
    const store = createAppStore()
    store.actions.setFocusTeam(102)
    store.actions.setPage('predictions')
    expect(store.getState().focusTeam).toBe(102)
  })
})

describe('what-if conditions', () => {
  it('sets, replaces and removes a condition', () => {
    const store = createAppStore()
    expect(store.actions.setCondition(9003, 'away')).toBe(true)
    expect(store.getState().conditions).toEqual([{ game_id: 9003, winner: 'away' }])
    store.actions.setCondition(9003, 'home')
    expect(store.getState().conditions).toEqual([{ game_id: 9003, winner: 'home' }])
    store.actions.setCondition(9003, null)
    expect(store.getState().conditions).toEqual([])
  })

  it('refuses a condition beyond the library limit of 16', () => {
    const store = createAppStore()
    for (let id = 1; id <= MAX_CONDITIONS; id++) expect(store.actions.setCondition(id, 'home')).toBe(true)
    expect(store.actions.setCondition(999, 'home')).toBe(false)
    expect(store.getState().conditions).toHaveLength(MAX_CONDITIONS)
    expect(store.actions.setCondition(3, 'away')).toBe(true)
  })

  it('clears every condition', () => {
    const store = createAppStore()
    store.actions.setCondition(1, 'home')
    store.actions.setCondition(2, 'away')
    store.actions.clearConditions()
    expect(store.getState().conditions).toEqual([])
  })

  it('clears the conditions when the point changes, since they belong to one run', () => {
    const store = createAppStore()
    store.actions.setCondition(1, 'home')
    store.actions.choosePoint(2026, 8)
    expect(store.getState().conditions).toEqual([])
  })
})

describe('inspector', () => {
  it('opens, selects an entry and closes', () => {
    const store = createAppStore()
    store.actions.setInspector({ open: true, entryId: 'q-0002' })
    expect(store.getState().inspector).toEqual({ open: true, entryId: 'q-0002' })
    store.actions.setInspector({ open: false })
    expect(store.getState().inspector).toEqual({ open: false, entryId: 'q-0002' })
  })
})

describe('follow mode', () => {
  function loaded() {
    const store = createAppStore()
    store.actions.setCurrent(current)
    return store
  }

  it('opens at the current point without a reload', () => {
    const store = loaded()
    expect(effectivePoint(store.getState())).toEqual({ season: 2026, week_seq: 9 })
    expect(store.getState().reloadToken).toBe(0)
  })

  it('knows no point before the current point has loaded', () => {
    expect(effectivePoint(createAppStore().getState())).toBeNull()
  })

  it('reloads once when the key changes and no refresh holds', () => {
    const store = loaded()
    store.actions.applyFollow(follow({ as_of_week_seq: 10, publication_id: 13 }))
    expect(store.getState().reloadToken).toBe(1)
    expect(effectivePoint(store.getState())).toEqual({ season: 2026, week_seq: 10 })
    store.actions.applyFollow(follow({ as_of_week_seq: 10, publication_id: 13 }))
    expect(store.getState().reloadToken).toBe(1)
  })

  it('holds every reload while the refresh runs, even when the point already moved', () => {
    const store = loaded()
    store.actions.applyFollow(follow({ as_of_week_seq: 10 }, true))
    expect(store.getState().reloadToken).toBe(0)
    expect(effectivePoint(store.getState())).toEqual({ season: 2026, week_seq: 9 })
    expect(selectFollowNotice(store.getState())).toBe('holding')
  })

  it('reloads once after the hold ends when the key changed', () => {
    const store = loaded()
    store.actions.applyFollow(follow({ as_of_week_seq: 10 }, true))
    store.actions.applyFollow(follow({ as_of_week_seq: 10, publication_id: 13 }))
    expect(store.getState().reloadToken).toBe(1)
    expect(effectivePoint(store.getState())).toEqual({ season: 2026, week_seq: 10 })
    expect(selectFollowNotice(store.getState())).toBeNull()
  })

  it('does not reload after a hold that changed nothing', () => {
    const store = loaded()
    store.actions.applyFollow(follow({}, true))
    store.actions.applyFollow(follow({}))
    expect(store.getState().reloadToken).toBe(0)
    expect(selectFollowNotice(store.getState())).toBeNull()
  })

  it('reloads when only the what-if run changes', () => {
    const store = loaded()
    store.actions.applyFollow(follow({ whatif_run_id: 8 }))
    expect(store.getState().reloadToken).toBe(1)
  })

  it('reports a run that looks stuck', () => {
    const store = loaded()
    store.actions.applyFollow(follow({}, false, true))
    expect(selectFollowNotice(store.getState())).toBe('stuck')
  })

  it('stays at an explicit choice and offers the new current point', () => {
    const store = loaded()
    store.actions.choosePoint(2026, 8)
    store.actions.applyFollow(follow({ as_of_week_seq: 10, publication_id: 13 }))
    expect(store.getState().reloadToken).toBe(0)
    expect(effectivePoint(store.getState())).toEqual({ season: 2026, week_seq: 8 })
    expect(selectFollowNotice(store.getState())).toBe('offer')
    expect(store.getState().follow.offeredKey?.as_of_week_seq).toBe(10)
  })

  it('goes back to following the current point and reloads', () => {
    const store = loaded()
    store.actions.choosePoint(2026, 8)
    store.actions.applyFollow(follow({ as_of_week_seq: 10, publication_id: 13 }))
    const before = store.getState().reloadToken
    store.actions.followCurrent()
    expect(effectivePoint(store.getState())).toEqual({ season: 2026, week_seq: 10 })
    expect(store.getState().reloadToken).toBe(before + 1)
    expect(selectFollowNotice(store.getState())).toBeNull()
  })

  it('shows an explicit choice that equals the key without an offer', () => {
    const store = loaded()
    store.actions.choosePoint(2026, 9)
    expect(selectFollowNotice(store.getState())).toBeNull()
  })
})

describe('outage and config', () => {
  it('records the outage and clears it', () => {
    const store = createAppStore()
    store.actions.setOutage(true)
    expect(store.getState().outage).toBe(true)
    store.actions.setOutage(false)
    expect(store.getState().outage).toBe(false)
  })

  it('bumps the reload token for a retry', () => {
    const store = createAppStore()
    store.actions.reload()
    expect(store.getState().reloadToken).toBe(1)
  })
})

describe('chat context (P7)', () => {
  it('hands over the point, the page, the focus team, the pair and the conditions', () => {
    const store = createAppStore()
    store.actions.setCurrent(current)
    store.actions.setFocusTeam(102)
    store.actions.setPair(101, 102)
    store.actions.setCondition(9003, 'away')
    store.actions.setPage('odds')
    expect(selectChatContext(store.getState())).toEqual({
      point: { season: 2026, week_seq: 9 },
      page: 'odds',
      focus_team: 102,
      pair: { team: 101, opponent: 102 },
      conditions: [{ game_id: 9003, winner: 'away' }],
    })
  })
})
