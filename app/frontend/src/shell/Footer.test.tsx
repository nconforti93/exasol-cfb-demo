import { act, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ConfigResponse } from '../api/types.ts'
import { fixture } from '../test/fixtures/index.ts'
import { renderInApp } from '../test/render.tsx'
import { Footer } from './Footer.tsx'

const config = fixture<ConfigResponse>('config.json')

describe('Footer', () => {
  it('credits CFBD with a link and says the data is not official', () => {
    renderInApp(<Footer />)
    const link = screen.getByRole('link', { name: 'CollegeFootballData.com' })
    expect(link).toHaveAttribute('href', 'https://collegefootballdata.com')
    expect(screen.getByText(/Data provided by/)).toBeInTheDocument()
    expect(screen.getByText(/not official NCAA or CFP data/)).toBeInTheDocument()
  })

  it('names the deployment and the fetch mode', () => {
    renderInApp(<Footer />, { initial: { config } })
    expect(screen.getByText(/Deployment: local/)).toBeInTheDocument()
    expect(screen.getByText(/Fetch mode: replay/)).toBeInTheDocument()
  })

  it('follows the configuration when it changes', () => {
    const { store } = renderInApp(<Footer />, { initial: { config } })
    act(() => store.actions.setConfig({ ...config, target: 'aws', fetch_mode: 'LIVE' }))
    expect(screen.getByText(/Deployment: aws/)).toBeInTheDocument()
    expect(screen.getByText(/Fetch mode: live/)).toBeInTheDocument()
  })

  it('still carries the credit before the configuration has loaded', () => {
    renderInApp(<Footer />)
    expect(screen.getByText(/Deployment: unknown/)).toBeInTheDocument()
  })
})
