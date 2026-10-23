import { act, fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { Team } from '../api/types.ts'
import { fixture } from '../test/fixtures/index.ts'
import { FakeBackend } from '../test/backend.ts'
import { renderInApp } from '../test/render.tsx'
import { TeamMark } from './TeamMark.tsx'

const [ashford, brookvale] = fixture<{ teams: Team[] }>('teams.json').teams as [Team, Team]

afterEach(() => {
  document.documentElement.removeAttribute('data-theme')
})

function render(team: Team, size?: 24 | 32 | 48 | 96 | 112) {
  const backend = new FakeBackend()
  const result = renderInApp(<TeamMark team={team} labelled {...(size === undefined ? {} : { size })} />, { backend })
  return { ...result, backend }
}

describe('TeamMark', () => {
  it('draws the logo from the app, and a badge with the abbreviation when the team has none', () => {
    expect(brookvale.has_logo).toBe(true)
    expect(ashford.has_logo).toBe(false)
    const withLogo = render(brookvale, 48)
    expect(screen.getByRole('img', { name: 'Brookvale' })).toHaveAttribute('src', '/api/logos/102.png')
    withLogo.unmount()
    render(ashford, 48)
    expect(screen.getByRole('img', { name: 'Ashford' })).toHaveTextContent('ASH')
    expect(document.querySelector('img')).toBeNull()
  })

  it('asks for the dark logo in the dark theme', () => {
    document.documentElement.setAttribute('data-theme', 'dark')
    render(brookvale)
    expect(screen.getByRole('img', { name: 'Brookvale' })).toHaveAttribute('src', '/api/logos/102.png?variant=dark')
  })

  it('switches to the badge when the image fails, and follows a theme switch', async () => {
    render(brookvale)
    fireEvent.error(screen.getByRole('img', { name: 'Brookvale' }))
    expect(screen.getByRole('img', { name: 'Brookvale' })).toHaveTextContent('BRK')
    await act(async () => document.documentElement.setAttribute('data-theme', 'dark'))
    expect(screen.getByRole('img', { name: 'Brookvale' })).toHaveAttribute('src', '/api/logos/102.png?variant=dark')
  })

  it('writes the first four letters of the school for a team without an abbreviation, and hides itself when not labelled', () => {
    const { container } = renderInApp(
      <TeamMark team={{ ...ashford, abbreviation: null, school: 'Carlton' }} size={96} />,
    )
    expect(container).toHaveTextContent('CARL')
    expect(container.querySelector('[aria-hidden="true"]')).not.toBeNull()
  })
})
