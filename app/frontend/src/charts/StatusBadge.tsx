import type { RunStatus, StepStatus } from '../api/types.ts'
import { BanIcon, CheckIcon, ClockIcon, CrossIcon, DashIcon } from './icons.tsx'

export type BadgeStatus = StepStatus | RunStatus

const CLASS_OF: Record<BadgeStatus, string> = {
  DONE: 'status-done',
  RUNNING: 'status-running',
  FAILED: 'status-failed',
  PENDING: 'status-pending',
  SKIPPED: 'status-skipped',
  ABANDONED: 'status-abandoned',
}

function iconOf(status: BadgeStatus) {
  switch (status) {
    case 'DONE':
      return <CheckIcon />
    case 'RUNNING':
      return <ClockIcon />
    case 'FAILED':
      return <CrossIcon />
    case 'PENDING':
      return <DashIcon />
    case 'SKIPPED':
    case 'ABANDONED':
      return <BanIcon />
  }
}

/** A state shown as an icon and a word (the status palette), never by color alone. */
export function StatusBadge({ status }: { status: BadgeStatus }) {
  return (
    <span className={`status ${CLASS_OF[status]}`}>
      {iconOf(status)}
      <span className="status-word">{status.toLowerCase()}</span>
    </span>
  )
}
