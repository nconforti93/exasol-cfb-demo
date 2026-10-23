import type { ExplainResponse, PlanManifest } from '../api/types.ts'
import { formatHttpRequest, formatSpendLabel } from '../format/format.ts'
import { WarningIcon } from '../shell/icons.tsx'

function httpLine(sliceKey: string): string {
  try {
    return formatHttpRequest(sliceKey)
  } catch {
    return sliceKey
  }
}

function Manifest({ manifest }: { manifest: PlanManifest }) {
  const planned = manifest.planned_requests === 1 ? '1 planned request' : `${manifest.planned_requests} planned requests`
  return (
    <div className="inspector-manifest">
      <h5 className="inspector-manifest-table">{manifest.table}</h5>
      <ul className="inspector-facts">
        <li>{manifest.fetch_mode === null ? 'no fetch' : `fetch ${manifest.fetch_mode.toLowerCase()}`}</li>
        <li>{manifest.read_through ? 'read-through' : 'no read-through'}</li>
        <li>{manifest.budget === null ? 'no budget' : `budget ${manifest.budget}`}</li>
        <li>{planned}</li>
        <li>{manifest.sources.length === 0 ? 'no sources' : `sources ${manifest.sources.join(', ')}`}</li>
      </ul>
      {manifest.slice_keys.length > 0 && (
        <ul className="inspector-requests" aria-label={`Planned requests of ${manifest.table}`}>
          {manifest.slice_keys.map((key) => (
            <li key={key}>
              <code className="inspector-http">{httpLine(key)}</code>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** The `EXPLAIN VIRTUAL` plan: each pushdown's manifest and the HTTP requests its slice keys become, at 0 calls. */
export function PlanView({ explain }: { explain: ExplainResponse }) {
  return (
    <div className="inspector-plan" role="group" aria-label="Plan">
      {explain.no_virtual_table ? (
        <p className="num">{formatSpendLabel({ source: 'NONE', live_calls: 0 })}</p>
      ) : (
        <>
          {explain.manifests.map((manifest, index) => (
            <Manifest key={`${manifest.table}-${index}`} manifest={manifest} />
          ))}
          {explain.refused === null && <p className="num inspector-plan-spend">{formatSpendLabel(explain.spend)}</p>}
        </>
      )}
      {explain.refused !== null && (
        <p className="inspector-refused">
          <WarningIcon />
          <span>{explain.refused}</span>
        </p>
      )}
    </div>
  )
}
