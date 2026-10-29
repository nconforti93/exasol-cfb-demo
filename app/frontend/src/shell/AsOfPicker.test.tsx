import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { PointsResponse } from '../api/types.ts'
import { fixture } from '../test/fixtures/index.ts'
import { AsOfPicker } from './AsOfPicker.tsx'

const points = fixture<PointsResponse>('points.json')

function renderPicker(overrides: Partial<Parameters<typeof AsOfPicker>[0]> = {}) {
  const handlers = { onSeason: vi.fn(), onChoose: vi.fn(), onFollow: vi.fn() }
  render(
    <AsOfPicker
      points={points}
      selection={{ season: 2026, week_seq: 9 }}
      following
      {...handlers}
      {...overrides}
    />,
  )
  return handlers
}

describe('AsOfPicker', () => {
  it('shows the current point by its label', () => {
    renderPicker()
    expect(screen.getByRole('combobox', { name: 'Week' })).toHaveDisplayValue(/2026, after week 9/)
  })

  it('offers the points of the season with their labels and never one after the current point', () => {
    renderPicker({
      points: {
        ...points,
        points: [
          ...points.points,
          { ...points.points[0]!, as_of_week: 10, as_of_week_seq: 10, as_of_label: '2026, after week 10', is_current: false, is_after_current: true },
        ],
      },
    })
    const options = screen.getAllByRole('option').map((option) => option.textContent ?? '')
    expect(options.some((text) => text.startsWith('2026, after week 8'))).toBe(true)
    expect(options.some((text) => text.includes('week 10'))).toBe(false)
  })

  it('hints at points that have no published run', () => {
    renderPicker()
    expect(screen.getByRole('option', { name: /after week 3/ })).toHaveTextContent('no published run')
    expect(screen.getByRole('option', { name: /after week 8/ })).not.toHaveTextContent('no published run')
  })

  it('offers the seasons and reports a season change', async () => {
    const { onSeason } = renderPicker()
    const season = screen.getByRole('combobox', { name: 'Season' })
    expect(season).toHaveDisplayValue('2026')
    await userEvent.selectOptions(season, '2025')
    expect(onSeason).toHaveBeenCalledWith(2025)
  })

  it('reports an earlier week as a chosen point', async () => {
    const { onChoose } = renderPicker()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Week' }), screen.getByRole('option', { name: /after week 8/ }))
    expect(onChoose).toHaveBeenCalledWith({ season: 2026, week_seq: 8 })
  })

  it('offers "Follow current" only after an explicit choice', async () => {
    const { onFollow } = renderPicker({ following: false, selection: { season: 2026, week_seq: 8 } })
    await userEvent.click(screen.getByRole('button', { name: 'Follow current' }))
    expect(onFollow).toHaveBeenCalledTimes(1)
  })

  it('has no follow button while following', () => {
    renderPicker()
    expect(screen.queryByRole('button', { name: 'Follow current' })).toBeNull()
  })

  it('describes a selected point that the list does not contain', () => {
    renderPicker({ selection: { season: 2026, week_seq: 103 } })
    expect(screen.getByRole('combobox', { name: 'Week' })).toHaveDisplayValue('2026, postseason week 3')
  })

  it('works before the points have loaded', () => {
    renderPicker({ points: null, selection: null })
    expect(screen.getByRole('combobox', { name: 'Week' })).toBeDisabled()
  })
})
