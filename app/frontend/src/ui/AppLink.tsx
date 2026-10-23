import type { MouseEvent, ReactNode } from 'react'

import { useAppActions, useAppState } from '../state/react.tsx'
import type { Destination } from '../state/store.ts'
import { pathFor } from '../state/url.ts'

export interface AppLinkProps {
  to: Destination
  children: ReactNode
  className?: string
  title?: string
  'aria-label'?: string
}

function isPlainClick(event: MouseEvent): boolean {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey
}

/** A link inside the app: a real `href` that keeps the chosen point, and a plain click that moves the store without a reload. */
export function AppLink({ to, children, ...rest }: AppLinkProps) {
  const actions = useAppActions()
  const key = JSON.stringify(to)
  const href = useAppState((state) => pathFor(state, JSON.parse(key) as Destination))
  return (
    <a
      href={href}
      {...rest}
      onClick={(event) => {
        if (!isPlainClick(event)) return
        event.preventDefault()
        actions.navigate(to)
      }}
    >
      {children}
    </a>
  )
}
