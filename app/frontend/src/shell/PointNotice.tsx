import type { Notice } from '../api/types.ts'
import { InfoIcon } from './icons.tsx'

/** A page's server notice (for example "week 9 is after the current point"), shown as a status line, never as an error. */
export function PointNotice({ notice }: { notice: Notice | null }) {
  if (notice === null) return null
  return (
    <p className="banner banner-info" role="status">
      <InfoIcon />
      <span>{notice.message}</span>
    </p>
  )
}
