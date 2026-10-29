import { formatConfirmation } from '../format/format.ts'

export interface ConfirmSpendProps {
  /** The most live calls the plan can make, as the user saw it; the server refuses more. */
  calls: number
  action: string
  onConfirm(): void
  onCancel(): void
}

/** The confirmation of a live action: it names "up to N live calls, plus retries" before anything is sent. */
export function ConfirmSpend({ calls, action, onConfirm, onCancel }: ConfirmSpendProps) {
  return (
    <div className="spend-confirm" role="group" aria-label={`Confirm: ${action}`}>
      <p className="spend-confirm-text">
        {action} may spend {formatConfirmation(calls)}.
      </p>
      <button type="button" className="spend-button" onClick={onConfirm}>
        Confirm
      </button>
      <button type="button" className="spend-button spend-button-quiet" onClick={onCancel}>
        Cancel
      </button>
    </div>
  )
}
