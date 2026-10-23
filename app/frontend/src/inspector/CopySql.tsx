import { useState } from 'react'

const COPIED_MS = 2000

/** Copies the statement text exactly as logged, so the presenter can paste it into a SQL client. */
export function CopySql({ sql }: { sql: string }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')

  async function copy() {
    try {
      await navigator.clipboard.writeText(sql)
      setState('copied')
    } catch {
      setState('failed')
    }
    setTimeout(() => setState('idle'), COPIED_MS)
  }

  return (
    <span className="inspector-copy">
      <button type="button" className="shell-button shell-button-small" onClick={() => void copy()}>
        Copy SQL
      </button>
      {state === 'copied' && <span role="status">Copied</span>}
      {state === 'failed' && <span role="status">Could not copy: select the text instead</span>}
    </span>
  )
}
