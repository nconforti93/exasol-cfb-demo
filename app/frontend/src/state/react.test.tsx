import { act, render, screen } from '@testing-library/react'
import { StrictMode } from 'react'
import { describe, expect, it } from 'vitest'

import { AppStoreProvider, useAppActions, useAppState } from './react.tsx'
import { createAppStore } from './store.ts'

function Probe() {
  const page = useAppState((state) => state.page)
  const pair = useAppState((state) => state.pair)
  const { setPair } = useAppActions()
  return (
    <button onClick={() => setPair(101, 102)}>
      {page}:{pair.team ?? '-'}:{pair.opponent ?? '-'}
    </button>
  )
}

describe('store bindings', () => {
  it('re-render a component when its slice changes, in StrictMode', () => {
    const store = createAppStore()
    render(
      <StrictMode>
        <AppStoreProvider store={store}>
          <Probe />
        </AppStoreProvider>
      </StrictMode>,
    )
    expect(screen.getByRole('button').textContent).toBe('odds:-:-')
    act(() => store.actions.setPage('upsets'))
    expect(screen.getByRole('button').textContent).toBe('upsets:-:-')
    act(() => screen.getByRole('button').click())
    expect(screen.getByRole('button').textContent).toBe('upsets:101:102')
  })

  it('refuse to render without a provider', () => {
    expect(() => render(<Probe />)).toThrow(/AppStoreProvider/)
  })
})
