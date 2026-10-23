import type { ReactNode } from 'react'

import { InfoIcon } from '../shell/icons.tsx'

/** A quiet line that says why a panel is empty, in words; never an error. */
export function Note({ children }: { children: ReactNode }) {
  return (
    <p className="note" role="status">
      <InfoIcon />
      <span>{children}</span>
    </p>
  )
}
