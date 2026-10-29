import { CodeIcon } from '../charts/icons.tsx'
import { useAppActions } from '../state/react.tsx'

export interface SqlButtonProps {
  /** The query-log entry the response names for this panel; null when it named none. */
  entryId: string | null
}

/** Opens the inspector at the query-log entry of the panel it sits on. */
export function SqlButton({ entryId }: SqlButtonProps) {
  const actions = useAppActions()
  const title = entryId === null ? 'No query was recorded for this panel' : 'Show the SQL behind this panel'
  return (
    <button
      type="button"
      className="sql-button"
      title={title}
      disabled={entryId === null}
      onClick={() => actions.setInspector({ open: true, entryId })}
    >
      <CodeIcon size={16} />
      <span>SQL</span>
    </button>
  )
}
