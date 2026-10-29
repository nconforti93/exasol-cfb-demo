import { formatPercent } from '../format/format.ts'
import { WarningIcon } from '../shell/icons.tsx'
import type { UpsetAlert as UpsetAlertData } from './weekModel.ts'

/** The icon and word for a ranked team with a real chance to lose, never color alone. */
export function UpsetAlertTag({ alert }: { alert: UpsetAlertData }) {
  return (
    <span className="upset-tag" title={`${alert.at.team.school} is ${formatPercent(alert.at.winProb)} to win`}>
      <WarningIcon />
      <strong>Upset alert</strong>
    </span>
  )
}
