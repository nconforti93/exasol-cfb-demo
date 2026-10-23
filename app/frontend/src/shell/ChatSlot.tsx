import { useState, useSyncExternalStore, type ComponentType } from 'react'

import type { ChatConfig } from '../api/types.ts'
import { useAppStore } from '../state/react.tsx'
import { selectChatContext, type AppActions, type AppState, type ChatContext } from '../state/store.ts'

/** What Phase 7's chat panel receives: the app state, the store's actions that set it, and the chat configuration. */
export interface ChatPanelProps {
  context: ChatContext
  actions: AppActions
  chat: ChatConfig
}

function memoizedChatContext(): (state: AppState) => ChatContext {
  let last: AppState | null = null
  let context: ChatContext | null = null
  return (state) => {
    if (context === null || last !== state) {
      last = state
      context = selectChatContext(state)
    }
    return context
  }
}

function useChatContext(): ChatContext {
  const store = useAppStore()
  const [select] = useState(memoizedChatContext)
  return useSyncExternalStore(store.subscribe, () => select(store.getState()))
}

function useChatConfig(): ChatConfig | null {
  const store = useAppStore()
  return useSyncExternalStore(store.subscribe, () => store.getState().config?.chat ?? null)
}

function Region({ chat, Panel }: { chat: ChatConfig; Panel: ComponentType<ChatPanelProps> | undefined }) {
  const store = useAppStore()
  const context = useChatContext()
  return (
    <aside className="chat-slot" aria-label="Chat">
      {Panel !== undefined && <Panel context={context} actions={store.actions} chat={chat} />}
    </aside>
  )
}

/** The region reserved for the chat panel: rendered only when `GET /api/config` reports the chat as enabled. */
export function ChatSlot({ panel }: { panel?: ComponentType<ChatPanelProps> }) {
  const chat = useChatConfig()
  if (chat === null || !chat.enabled) return null
  return <Region chat={chat} Panel={panel} />
}
