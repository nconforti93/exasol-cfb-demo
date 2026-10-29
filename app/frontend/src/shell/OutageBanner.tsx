import { WarningIcon } from './icons.tsx'

export interface OutageBannerProps {
  /** The server's message, which names the address and the target. */
  message: string | null
  onRetry(): void
}

/** Replaces the page while the database is unreachable; the server keeps running and recovers by itself. */
export function OutageBanner({ message, onRetry }: OutageBannerProps) {
  return (
    <div className="banner banner-critical" role="alert">
      <WarningIcon />
      <div className="banner-text">
        <strong>The database is unreachable.</strong>
        {message !== null && <p>{message}</p>}
      </div>
      <button type="button" className="shell-button" onClick={onRetry}>
        Retry
      </button>
    </div>
  )
}
