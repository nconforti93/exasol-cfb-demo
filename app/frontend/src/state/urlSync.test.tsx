import { act } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { renderInApp } from '../test/render.tsx'
import { useUrlSync } from './urlSync.ts'

function Sync() {
  useUrlSync()
  return null
}

function go(url: string): void {
  window.history.replaceState(null, '', url)
}

function here(): string {
  return window.location.pathname + window.location.search
}

afterEach(() => {
  go('/')
})

describe('useUrlSync', () => {
  it('canonicalizes the address to the page without adding a history entry', () => {
    go('/')
    const length = window.history.length
    renderInApp(<Sync />)
    expect(here()).toBe('/odds')
    expect(window.history.length).toBe(length)
  })

  it('writes a chosen point to the address as season and week sequence', () => {
    go('/odds')
    const { store } = renderInApp(<Sync />)
    act(() => store.actions.choosePoint(2026, 8))
    expect(here()).toBe('/odds?season=2026&week=8')
  })

  it('writes the page and the focus team, and drops the point when following again', () => {
    go('/odds?season=2026&week=8')
    const { store } = renderInApp(<Sync />, {
      initial: { chosen: { mode: 'explicit', season: 2026, week_seq: 8 } },
    })
    act(() => store.actions.setFocusTeam(101))
    expect(here()).toBe('/odds?season=2026&week=8&team=101')
    act(() => store.actions.setPage('predictions'))
    expect(here()).toBe('/predictions?season=2026&week=8')
    act(() => store.actions.followCurrent())
    expect(here()).toBe('/predictions')
  })

  it('adds one history entry per change and none when nothing changes', () => {
    go('/odds')
    const { store } = renderInApp(<Sync />)
    const length = window.history.length
    act(() => store.actions.setPage('upsets'))
    expect(window.history.length).toBe(length + 1)
    act(() => store.actions.setPage('upsets'))
    act(() => store.actions.reload())
    expect(window.history.length).toBe(length + 1)
  })

  it('moves the store when the user goes back or forward', () => {
    go('/odds')
    const { store } = renderInApp(<Sync />)
    act(() => {
      go('/head-to-head?team=101&opponent=102&season=2026&week=7')
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    const state = store.getState()
    expect(state.page).toBe('head-to-head')
    expect(state.pair).toEqual({ team: 101, opponent: 102 })
    expect(state.chosen).toEqual({ mode: 'explicit', season: 2026, week_seq: 7 })
    expect(here()).toBe('/head-to-head?season=2026&week=7&team=101&opponent=102')
  })

  it('does not push entries while applying a back navigation', () => {
    go('/odds')
    const { store } = renderInApp(<Sync />)
    act(() => store.actions.choosePoint(2026, 8))
    const length = window.history.length
    act(() => {
      go('/odds')
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    expect(store.getState().chosen).toEqual({ mode: 'follow' })
    expect(window.history.length).toBe(length)
    expect(here()).toBe('/odds')
  })

  it('stops listening on unmount', () => {
    go('/odds')
    const { store, unmount } = renderInApp(<Sync />)
    unmount()
    act(() => store.actions.setPage('upsets'))
    expect(here()).toBe('/odds')
    act(() => {
      go('/predictions')
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    expect(store.getState().page).toBe('upsets')
  })
})
