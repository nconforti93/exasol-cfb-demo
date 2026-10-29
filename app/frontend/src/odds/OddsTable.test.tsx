import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import type { OddsResponse, OddsRow, WhatIfOddsResponse } from '../api/types.ts'
import { fixture } from '../test/fixtures/index.ts'
import { OddsTable } from './OddsTable.tsx'

const odds = fixture<OddsResponse>('odds.json')
const IDLE = { status: 'idle', data: null, message: null } as const

function renderTable(rows: OddsRow[] = odds.rows, extra: Partial<Parameters<typeof OddsTable>[0]> = {}) {
  const onFocus = vi.fn()
  const view = render(
    <StrictMode>
      <OddsTable rows={rows} threshold={odds.move_threshold} focusTeam={null} onFocus={onFocus} conditional={IDLE} {...extra} />
    </StrictMode>,
  )
  return { onFocus, ...view }
}

function rowOf(school: string): HTMLElement {
  return screen.getByRole('row', { name: new RegExp(school) })
}

describe('OddsTable', () => {
  it('shows the rows in rank order with school, conference and the main probabilities', () => {
    renderTable()
    const rows = screen.getAllByRole('row').slice(1)
    expect(rows.map((row) => within(row).getAllByRole('cell')[0]?.textContent)).toEqual(['1', '2', '3', '4'])
    const brookvale = rowOf('Brookvale')
    expect(brookvale).toHaveTextContent('Atlantic')
    expect(brookvale).toHaveTextContent('81.2 %')
    expect(brookvale).toHaveTextContent('27.1 %')
    expect(brookvale).toHaveTextContent('13.5 %')
    expect(brookvale).toHaveTextContent('9.9')
  })

  it('shows movement with an icon and a word, and nothing for SAME', () => {
    renderTable()
    const up = within(rowOf('Brookvale')).getByRole('img', { name: 'up +2.3 percentage points' })
    expect(up.querySelector('svg')).not.toBeNull()
    expect(up).toHaveTextContent('up')
    expect(up).toHaveTextContent('+2.3')
    const down = within(rowOf('Ashford')).getByRole('img', { name: 'down −1.1 percentage points' })
    expect(down).toHaveTextContent('down')
    expect(down).toHaveTextContent('−1.1')
    expect(within(rowOf('Carlton')).queryByRole('img', { name: /percentage points/ })).toBeNull()
    expect(within(rowOf('Dunmore')).getByText('new')).toBeInTheDocument()
  })

  it('shows an arrow only from the move threshold, whatever the row says', () => {
    const rows = odds.rows.map((row) => (row.school === 'Carlton' ? { ...row, move: 'UP' as const, delta_p_in_cfp: 0.0069 } : row))
    renderTable(rows)
    expect(within(rowOf('Carlton')).queryByRole('img', { name: /percentage points/ })).toBeNull()
    const exact = odds.rows.map((row) => (row.school === 'Carlton' ? { ...row, move: 'UP' as const, delta_p_in_cfp: 0.007 } : row))
    renderTable(exact)
    expect(screen.getAllByRole('img', { name: 'up +0.7 percentage points' })).toHaveLength(1)
  })

  it('states the arrow threshold in percentage points and the point it compares with', () => {
    renderTable()
    expect(screen.getByText(/arrows from 0\.7 percentage points/i)).toHaveTextContent('since 2026, after week 8')
  })

  it('reads a value above 0 and below 0.05 percent as "< 0.1 %" with the exact value on hover', () => {
    renderTable()
    const cell = within(rowOf('Dunmore')).getAllByText('< 0.1 %')[0]
    expect(cell).toHaveAttribute('title', '0.04 %')
  })

  it('shows a bar for the playoff probability with its value as the name', () => {
    renderTable()
    expect(within(rowOf('Brookvale')).getByRole('img', { name: 'Brookvale: 81.2 % to make the playoff' })).toBeInTheDocument()
  })

  it('adds the other rounds on demand', async () => {
    renderTable()
    expect(screen.queryByRole('columnheader', { name: 'Semifinal' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Show every round' }))
    expect(screen.getByRole('columnheader', { name: 'Semifinal' })).toBeInTheDocument()
    expect(rowOf('Brookvale')).toHaveTextContent('36.6 %')
    await userEvent.click(screen.getByRole('button', { name: 'Hide the other rounds' }))
    expect(screen.queryByRole('columnheader', { name: 'Semifinal' })).toBeNull()
  })

  it('marks the focus team and sets it from a click on the school', async () => {
    const { onFocus, rerender } = renderTable()
    await userEvent.click(within(rowOf('Ashford')).getByRole('button', { name: 'Ashford' }))
    expect(onFocus).toHaveBeenCalledWith(101)
    rerender(<OddsTable rows={odds.rows} threshold={0.007} focusTeam={101} onFocus={onFocus} conditional={IDLE} />)
    expect(rowOf('Ashford')).toHaveAttribute('aria-current', 'true')
    expect(rowOf('Brookvale')).not.toHaveAttribute('aria-current')
    await userEvent.click(within(rowOf('Ashford')).getByRole('button', { name: 'Ashford' }))
    expect(onFocus).toHaveBeenLastCalledWith(null)
  })

  it('shows the conditional odds beside the base odds, with the change and the low-sample flag', () => {
    const whatIf = fixture<WhatIfOddsResponse>('whatif-odds.json')
    renderTable(odds.rows, { conditional: { status: 'ready', data: whatIf, message: null } })
    const brookvale = rowOf('Brookvale')
    expect(brookvale).toHaveTextContent('81.2 %')
    expect(brookvale).toHaveTextContent('91.0 %')
    expect(within(brookvale).getByRole('img', { name: 'up +9.8 percentage points' })).toBeInTheDocument()
    expect(within(brookvale).getByText('low sample')).toBeInTheDocument()
    expect(within(rowOf('Ashford')).getByRole('img', { name: 'down −7.1 percentage points' })).toBeInTheDocument()
    expect(within(rowOf('Dunmore')).queryByText('low sample')).toBeNull()
  })

  it('names the table by what it lists', () => {
    renderTable()
    expect(screen.getByRole('table', { name: 'Playoff odds by team' })).toBeInTheDocument()
  })
})
