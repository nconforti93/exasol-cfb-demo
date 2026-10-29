import { render, screen, within } from '@testing-library/react'
import { StrictMode } from 'react'
import { describe, expect, it } from 'vitest'

import type { ScorecardResponse } from '../api/types.ts'
import { fixture } from '../test/fixtures/index.ts'
import { RECONSTRUCTION_LABEL, ScorecardHeadline } from './ScorecardHeadline.tsx'

const inAdvance = fixture<ScorecardResponse>('scorecard.json')
const reconstructed = fixture<ScorecardResponse>('scorecard.reconstructed.json')

function renderHeadline(rows = inAdvance.week, variant: 'week' | 'season' = 'week', label = '2026, after week 10') {
  return render(
    <StrictMode>
      <ScorecardHeadline heading="Last week, graded" rows={rows} variant={variant} pointLabel={label} />
    </StrictMode>,
  )
}

describe('ScorecardHeadline', () => {
  it('shows the marked row: record, accuracy, Brier score and margin error', () => {
    renderHeadline()
    const headline = screen.getByRole('region', { name: 'Last week, graded' })
    expect(within(headline).getByText('41–9', { selector: '.scorecard-record-big' })).toBeInTheDocument()
    expect(headline).toHaveTextContent('82.0 % of picks correct')
    expect(headline).toHaveTextContent('Brier score 0.151')
    expect(headline).toHaveTextContent('off by 10.4 points')
    expect(headline).toHaveTextContent('50 games graded')
  })

  it('takes the IS_HEADLINE row, not the first row or the largest record', () => {
    const rows = [...inAdvance.week].reverse()
    const { container } = renderHeadline(rows)
    expect(container.querySelector('.scorecard-record-big')).toHaveTextContent('41–9')
  })

  it('labels a reconstructed headline in words', () => {
    renderHeadline(reconstructed.week, 'week', '2026, after week 8')
    expect(screen.getByText('38–12')).toBeInTheDocument()
    expect(screen.getByText(RECONSTRUCTION_LABEL)).toBeInTheDocument()
    expect(RECONSTRUCTION_LABEL).toBe('reconstructed at the as-of point (leak-free, made after kickoff)')
  })

  it('carries no reconstruction label when the headline was frozen in advance', () => {
    renderHeadline()
    expect(screen.queryByText(RECONSTRUCTION_LABEL)).toBeNull()
    expect(screen.getByText(/frozen before kickoff/)).toBeInTheDocument()
  })

  it('puts the closing line beside the model on the same games, labeled as better informed', () => {
    renderHeadline()
    const line = screen.getByRole('group', { name: 'The closing line on the same games' })
    expect(line).toHaveTextContent('39–11')
    expect(line).toHaveTextContent('Brier score 0.143')
    expect(line).toHaveTextContent('the model: 0.153')
    expect(line).toHaveTextContent('closing-line information')
    expect(line).toHaveTextContent('knew more than the model did when it froze its predictions')
  })

  it('leaves out the line when the games had none', () => {
    const rows = inAdvance.week.map((row) => ({ ...row, line_correct: null, line_wrong: null, line_brier: null, model_brier_on_line_games: null }))
    renderHeadline(rows)
    expect(screen.queryByRole('group', { name: 'The closing line on the same games' })).toBeNull()
  })

  it('shows the other freeze kinds as detail', () => {
    renderHeadline()
    const detail = screen.getByRole('table', { name: 'The same games by freeze kind' })
    const rows = within(detail).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('in advance')
    expect(rows[0]).toHaveTextContent('41–9')
    expect(rows[1]).toHaveTextContent('all')
    expect(rows[1]).toHaveTextContent('44–12')
  })

  it('shows the season to date as a compact headline with the same label rule', () => {
    renderHeadline(reconstructed.season, 'season', '2026, after week 8')
    const season = screen.getByRole('region', { name: 'Last week, graded' })
    expect(season).toHaveTextContent('200–70')
    expect(screen.getByText(RECONSTRUCTION_LABEL)).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'The closing line on the same games' })).toBeNull()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('says there is no scorecard when no game was graded', () => {
    renderHeadline([])
    expect(screen.getByText('No graded games at 2026, after week 10.')).toBeInTheDocument()
  })
})
