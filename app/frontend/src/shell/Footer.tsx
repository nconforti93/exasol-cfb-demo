import { useAppState } from '../state/react.tsx'

function modeLabel(mode: string | null): string {
  return mode === null ? 'unknown' : mode.toLowerCase()
}

/** The credit line every page carries, with the deployment and the fetch mode last reported by the server. */
export function Footer() {
  const target = useAppState((state) => state.config?.target ?? null)
  const fetchMode = useAppState((state) => state.config?.fetch_mode ?? null)
  return (
    <footer className="shell-footer">
      <p>
        Data provided by <a href="https://collegefootballdata.com">CollegeFootballData.com</a>. This is not official
        NCAA or CFP data.
      </p>
      <p className="shell-footer-meta">
        Deployment: {target ?? 'unknown'} · Fetch mode: {modeLabel(fetchMode)}
      </p>
    </footer>
  )
}
