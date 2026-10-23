import { useId, useState } from 'react'

import { formatSpendLabel, type SpendLabelInput } from '../format/format.ts'
import { ConfirmSpend } from './ConfirmSpend.tsx'
import './spend.css'

/** Why a live action cannot run right now; the text replaces the cost label. */
export interface SpendBlock {
  kind: 'disabled' | 'busy'
  text: string
}

export interface SpendButtonProps {
  /** The verb, for example "Fetch preview". */
  action: string
  /** What the 0-call plan says the action can spend: the label on the button. */
  plan: SpendLabelInput
  /** The request is running: the button is disabled and says so. */
  busy: boolean
  /** A guard that refuses now (live actions disabled, a refresh running); shown instead of a cost. */
  block?: SpendBlock | null
  /** Ask "up to N live calls, plus retries" before running. Only a plan with live calls asks. */
  confirm?: boolean
  onSpend(): void
}

/**
 * A control that can spend live calls: its label carries the planned cost, a refusing guard shows its reason
 * instead of a cost, and it is disabled while its request runs. With `confirm` a live plan first asks for a
 * confirmation that names the number of calls.
 */
export function SpendButton({ action, plan, busy, block = null, confirm = false, onSpend }: SpendButtonProps) {
  const reasonId = useId()
  const [asking, setAsking] = useState(false)
  const blocked = block !== null
  const label = formatSpendLabel(plan)
  const needsConfirmation = confirm && plan.live_calls > 0

  function click() {
    if (needsConfirmation) setAsking(true)
    else onSpend()
  }

  return (
    <span className="spend-control">
      <button
        type="button"
        className="spend-button"
        disabled={busy || blocked || asking}
        aria-busy={busy}
        aria-describedby={blocked ? reasonId : undefined}
        onClick={click}
      >
        {blocked ? action : `${action} · ${label}`}
      </button>
      {blocked && (
        <span id={reasonId} className={`spend-reason spend-reason-${block.kind}`}>
          {block.text}
        </span>
      )}
      {asking && (
        <ConfirmSpend
          calls={plan.live_calls}
          action={action}
          onConfirm={() => {
            setAsking(false)
            onSpend()
          }}
          onCancel={() => setAsking(false)}
        />
      )}
    </span>
  )
}
