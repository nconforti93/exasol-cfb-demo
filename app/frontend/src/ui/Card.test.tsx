import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { renderInApp } from '../test/render.tsx'
import { AppLink } from './AppLink.tsx'
import { Card } from './Card.tsx'

describe('Card', () => {
  it('has a titled region, a footer, and an SQL button that opens the inspector at its entry', async () => {
    const { store } = renderInApp(
      <Card title="Road warriors" eyebrow="Stats" sqlEntry="q-0007" footer="0 calls · 38 ms">
        body
      </Card>,
    )
    const card = screen.getByRole('region', { name: 'Road warriors' })
    expect(within(card).getByText('0 calls · 38 ms')).toBeInTheDocument()
    await userEvent.click(within(card).getByRole('button', { name: 'SQL' }))
    expect(store.getState().inspector).toEqual({ open: true, entryId: 'q-0007' })
  })

  it('disables the SQL button when the response named no query, and has none when not asked for', () => {
    const { unmount } = renderInApp(<Card title="No query" sqlEntry={null} />)
    expect(screen.getByRole('button', { name: 'SQL' })).toBeDisabled()
    unmount()
    renderInApp(<Card title="Plain">body</Card>)
    expect(screen.queryByRole('button', { name: 'SQL' })).toBeNull()
  })
})

describe('AppLink', () => {
  it('links to the destination with the chosen point and navigates without a reload', async () => {
    const { store } = renderInApp(<AppLink to={{ page: 'team', team: 102 }}>Brookvale</AppLink>, {
      initial: { chosen: { mode: 'explicit', season: 2026, week_seq: 8 } },
    })
    const link = screen.getByRole('link', { name: 'Brookvale' })
    expect(link).toHaveAttribute('href', '/team/102?season=2026&week=8')
    await userEvent.click(link)
    expect(store.getState()).toMatchObject({ page: 'team', focusTeam: 102 })
  })
})
