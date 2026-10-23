import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { renderInApp } from '../test/render.tsx'
import { RankTag } from './RankTag.tsx'
import { RecordLine } from './RecordLine.tsx'
import { StatTile } from './StatTile.tsx'

describe('small pieces', () => {
  it('writes a rank as #6 and nothing for an unranked team', () => {
    const { container, rerender } = renderInApp(<RankTag rank={6} poll="AP" />)
    expect(screen.getByRole('img', { name: 'AP rank 6' })).toHaveTextContent('#6')
    rerender(<RankTag rank={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('writes a record with its conference record', () => {
    renderInApp(<RecordLine wins={7} losses={1} confWins={4} confLosses={1} conference="Atlantic" />)
    expect(screen.getByText('7–1 · 4–1 Atlantic')).toBeInTheDocument()
  })

  it('shows a KPI with how it moved, as an icon, a word and points', () => {
    renderInApp(<StatTile label="Playoff chance" value="81.2 %" delta={{ move: 'UP', delta: 0.023 }} />)
    expect(screen.getByText('81.2 %')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'up +2.3 percentage points' })).toBeInTheDocument()
  })
})
