import { useAppActions, useAppState } from '../state/react.tsx'
import { selectFollowNotice, type FollowNotice as Kind } from '../state/store.ts'
import { describePoint } from './AsOfPicker.tsx'
import { InfoIcon, WarningIcon } from './icons.tsx'

const HOLDING_TEXT = 'refresh running: pages update when it publishes'
const STUCK_TEXT = 'this refresh looks stuck: no step has changed for 10 minutes, so the pages no longer wait for it'

function useOfferedLabel(): string | null {
  const offered = useAppState((state) => state.follow.offeredKey)
  const current = useAppState((state) => state.current)
  if (offered === null) return null
  const matches = current?.as_of_season === offered.as_of_season && current.as_of_week_seq === offered.as_of_week_seq
  return matches && current !== null ? current.as_of_label : describePoint({ season: offered.as_of_season, week_seq: offered.as_of_week_seq })
}

/** The follow-mode notice: a refresh is holding the reloads, a run looks stuck, or a newer point is offered. */
export function FollowNoticeBar() {
  const kind: Kind | null = useAppState(selectFollowNotice)
  const actions = useAppActions()
  const offeredLabel = useOfferedLabel()
  if (kind === null) return null
  if (kind === 'holding') {
    return (
      <p className="banner banner-info" role="status">
        <InfoIcon />
        <span>{HOLDING_TEXT}</span>
      </p>
    )
  }
  if (kind === 'stuck') {
    return (
      <p className="banner banner-warning" role="status">
        <WarningIcon />
        <span>{STUCK_TEXT}</span>
      </p>
    )
  }
  return (
    <p className="banner banner-info" role="status">
      <InfoIcon />
      <span>{offeredLabel} is now the current point.</span>
      <button type="button" className="shell-button" onClick={actions.followCurrent}>
        Show current point
      </button>
    </p>
  )
}
