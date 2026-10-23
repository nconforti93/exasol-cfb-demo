import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ConfigResponse } from '../api/types.ts'
import type { AppState } from '../state/store.ts'
import { fixture } from '../test/fixtures/index.ts'
import { renderInApp } from '../test/render.tsx'
import { ChatSlot, type ChatPanelProps } from './ChatSlot.tsx'

const disabled = fixture<ConfigResponse>('config.json')
const enabled = fixture<ConfigResponse>('config.chat-enabled.json')

const seen: ChatPanelProps[] = []

function Panel(props: ChatPanelProps) {
  seen.push(props)
  return (
    <div>
      <p data-testid="context">{JSON.stringify(props.context)}</p>
      <button onClick={props.actions.clearConditions}>clear conditions</button>
    </div>
  )
}

function stateAt2026Week9(): Partial<AppState> {
  return {
    config: enabled,
    chosen: { mode: 'explicit', season: 2026, week_seq: 9 },
    page: 'odds',
    focusTeam: 101,
    conditions: [{ game_id: 5001, winner: 'home' }],
  }
}

describe('ChatSlot', () => {
  it('renders no chat region with this phase\'s configuration', () => {
    const panel = vi.fn(() => null)
    const { container } = renderInApp(<ChatSlot panel={panel} />, { initial: { config: disabled } })
    expect(container).toBeEmptyDOMElement()
    expect(panel).not.toHaveBeenCalled()
  })

  it('renders nothing before the configuration has loaded', () => {
    const { container } = renderInApp(<ChatSlot panel={Panel} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('hands the point, the page, the focus team and the conditions to the panel', () => {
    renderInApp(<ChatSlot panel={Panel} />, { initial: stateAt2026Week9() })
    expect(screen.getByRole('complementary', { name: 'Chat' })).toBeInTheDocument()
    const context = JSON.parse(screen.getByTestId('context').textContent ?? '{}') as Record<string, unknown>
    expect(context).toMatchObject({
      point: { season: 2026, week_seq: 9 },
      page: 'odds',
      focus_team: 101,
      conditions: [{ game_id: 5001, winner: 'home' }],
    })
  })

  it('passes the chat configuration to the panel', () => {
    seen.length = 0
    renderInApp(<ChatSlot panel={Panel} />, { initial: stateAt2026Week9() })
    expect(seen.at(-1)?.chat.model).toBe('test-model')
  })

  it('lets the panel drive the store through its actions', async () => {
    const { store } = renderInApp(<ChatSlot panel={Panel} />, { initial: stateAt2026Week9() })
    await userEvent.click(screen.getByRole('button', { name: 'clear conditions' }))
    expect(store.getState().conditions).toEqual([])
    expect(JSON.parse(screen.getByTestId('context').textContent ?? '{}')).toMatchObject({ conditions: [] })
  })

  it('updates the panel when the app state changes', () => {
    const { store } = renderInApp(<ChatSlot panel={Panel} />, { initial: stateAt2026Week9() })
    act(() => store.actions.setPage('upsets'))
    expect(JSON.parse(screen.getByTestId('context').textContent ?? '{}')).toMatchObject({ page: 'upsets' })
  })

  it('reserves an empty region when the chat is enabled and no panel is installed', () => {
    renderInApp(<ChatSlot />, { initial: stateAt2026Week9() })
    expect(screen.getByRole('complementary', { name: 'Chat' })).toBeEmptyDOMElement()
  })
})
