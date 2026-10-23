import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { OutageBanner } from './OutageBanner.tsx'

describe('OutageBanner', () => {
  it('is an alert that names the server message and offers a retry', async () => {
    const onRetry = vi.fn()
    render(<OutageBanner message="Exasol unreachable at 127.0.0.1:8563 (target local)" onRetry={onRetry} />)
    expect(screen.getByRole('alert')).toHaveTextContent('The database is unreachable')
    expect(screen.getByRole('alert')).toHaveTextContent('Exasol unreachable at 127.0.0.1:8563 (target local)')
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('works without a message', () => {
    render(<OutageBanner message={null} onRetry={() => {}} />)
    expect(screen.getByRole('alert')).toHaveTextContent('The database is unreachable')
  })

  it('marks the state with an icon and a word', () => {
    render(<OutageBanner message={null} onRetry={() => {}} />)
    expect(screen.getByRole('alert').querySelector('svg')).not.toBeNull()
  })
})
