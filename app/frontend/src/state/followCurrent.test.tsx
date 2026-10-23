import { act, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { FollowKey, PointsResponse, TimelineResponse } from '../api/types.ts'
import { AppShell } from '../shell/AppShell.tsx'
import { FakeBackend, outageReply } from '../test/backend.ts'
import { fixture } from '../test/fixtures/index.ts'
import { probePages } from '../test/probePages.tsx'
import { renderInApp } from '../test/render.tsx'

const WEEK_10: FollowKey = {
  as_of_season: 2026,
  as_of_week_seq: 10,
  publication_id: 13,
  whatif_run_id: 7,
  whatif_views_current: true,
}

let reading: TimelineResponse
let pointsLabelWeek = 9

function timeline(name: string): TimelineResponse {
  return fixture<TimelineResponse>(name)
}

/** The refresh has moved `CURRENT_POINT` to week 10 while the run is still going. */
function running(over: Partial<TimelineResponse['follow']> = {}): TimelineResponse {
  const base = timeline('timeline.json')
  return { ...base, follow: { key: WEEK_10, hold: true, stuck: false, ...over } }
}

function makeBackend(): FakeBackend {
  return new FakeBackend({
    'GET /api/timeline': () => structuredClone(reading),
    'GET /api/points': () => {
      const points = fixture<PointsResponse>('points.json')
      if (pointsLabelWeek === 10) {
        points.current = { ...points.current, as_of_week: 10, as_of_week_seq: 10, as_of_label: '2026, after week 10', publication_id: 13 }
      }
      return points
    },
  })
}

function renderFollowing(backend: FakeBackend, initial = {}) {
  return renderInApp(
    <AppShell pages={probePages} refreshPanel={() => null} inspector={() => null} />,
    { backend, initial },
  )
}

function liveLoads(backend: FakeBackend): number {
  return backend.count('GET /api/odds')
}

async function settle(ms = 1): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  reading = timeline('timeline.idle.json')
  pointsLabelWeek = 9
  window.history.replaceState(null, '', '/odds')
})

afterEach(() => {
  vi.useRealTimers()
  window.history.replaceState(null, '', '/')
})

describe('follow mode', () => {
  it('holds the reload while a refresh runs, says so, and reloads once when it has published', async () => {
    const backend = makeBackend()
    renderFollowing(backend)
    await settle()
    expect(screen.getByTestId('probe')).toHaveTextContent('odds@2026/9:ready')
    const first = liveLoads(backend)

    reading = running()
    await settle(5000)
    expect(screen.getByText('refresh running: pages update when it publishes')).toBeInTheDocument()
    expect(screen.getByTestId('probe')).toHaveTextContent('odds@2026/9:ready')
    await settle(3000)
    expect(liveLoads(backend)).toBe(first)

    reading = timeline('timeline.done.json')
    pointsLabelWeek = 10
    await settle(1000)
    expect(screen.getByTestId('probe')).toHaveTextContent('odds@2026/10:ready')
    expect(screen.queryByText(/refresh running/)).toBeNull()
    expect(liveLoads(backend)).toBe(first + 1)

    await settle(20_000)
    expect(liveLoads(backend)).toBe(first + 1)
  })

  it('does not reload when a run ends without moving the point', async () => {
    const backend = makeBackend()
    renderFollowing(backend)
    await settle()
    const first = liveLoads(backend)
    const sameKey = timeline('timeline.idle.json').follow.key
    reading = running({ key: sameKey })
    await settle(5000)
    expect(screen.getByText(/refresh running/)).toBeInTheDocument()
    reading = timeline('timeline.idle.json')
    await settle(1000)
    expect(screen.queryByText(/refresh running/)).toBeNull()
    expect(liveLoads(backend)).toBe(first)
  })

  it('says a run looks stuck and stops holding', async () => {
    renderFollowing(makeBackend())
    await settle()
    reading = timeline('timeline.stuck.json')
    await settle(5000)
    expect(screen.getByText(/looks stuck/)).toBeInTheDocument()
    expect(screen.queryByText(/pages update when it publishes/)).toBeNull()
  })

  it('keeps a browser that chose a week where it is and offers the new current point', async () => {
    window.history.replaceState(null, '', '/odds?season=2026&week=8')
    const backend = makeBackend()
    const { store } = renderFollowing(backend, { chosen: { mode: 'explicit', season: 2026, week_seq: 8 } })
    await settle()
    expect(screen.getByTestId('probe')).toHaveTextContent('odds@2026/8:ready')
    const first = liveLoads(backend)

    reading = timeline('timeline.done.json')
    pointsLabelWeek = 10
    await settle(5000)
    await settle(1)
    expect(screen.getByTestId('probe')).toHaveTextContent('odds@2026/8:ready')
    expect(liveLoads(backend)).toBe(first)
    expect(screen.getByText(/2026, after week 10 is now the current point/)).toBeInTheDocument()

    await act(async () => {
      screen.getByRole('button', { name: 'Show current point' }).click()
    })
    await settle()
    expect(store.getState().chosen).toEqual({ mode: 'follow' })
    expect(screen.getByTestId('probe')).toHaveTextContent('odds@2026/10:ready')
    expect(window.location.search).toBe('')
  })

  it('does not offer the point the browser already shows', async () => {
    window.history.replaceState(null, '', '/odds?season=2026&week=10')
    renderFollowing(makeBackend(), { chosen: { mode: 'explicit', season: 2026, week_seq: 10 } })
    await settle()
    reading = timeline('timeline.done.json')
    await settle(5000)
    expect(screen.queryByText(/is now the current point/)).toBeNull()
  })
})

describe('the timeline poll', () => {
  it('polls every five seconds while no run is running, once per mount in StrictMode', async () => {
    const backend = makeBackend()
    renderFollowing(backend)
    await settle(0)
    expect(backend.count('GET /api/timeline')).toBe(1)
    await settle(10_000)
    expect(backend.count('GET /api/timeline')).toBe(3)
  })

  it('polls every second while the newest run is running', async () => {
    reading = running()
    const backend = makeBackend()
    renderFollowing(backend)
    await settle(0)
    await settle(3000)
    expect(backend.count('GET /api/timeline')).toBe(4)
  })

  it('goes back to five seconds when the run ends', async () => {
    reading = running()
    const backend = makeBackend()
    renderFollowing(backend)
    await settle(2000)
    reading = timeline('timeline.done.json')
    await settle(1000)
    const atEnd = backend.count('GET /api/timeline')
    await settle(4999)
    expect(backend.count('GET /api/timeline')).toBe(atEnd)
    await settle(1)
    expect(backend.count('GET /api/timeline')).toBe(atEnd + 1)
  })

  it('stops polling when the app unmounts', async () => {
    const backend = makeBackend()
    const { unmount } = renderFollowing(backend)
    await settle(0)
    unmount()
    const before = backend.count('GET /api/timeline')
    await settle(30_000)
    expect(backend.count('GET /api/timeline')).toBe(before)
  })

  it('shows the outage banner when the poll finds the database unreachable', async () => {
    const backend = makeBackend()
    renderFollowing(backend)
    await settle()
    backend.on('GET /api/timeline', () => outageReply())
    await settle(5000)
    expect(screen.getByRole('alert')).toHaveTextContent('Exasol unreachable')
  })
})
