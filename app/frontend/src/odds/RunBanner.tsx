import type { RunBanner as RunBannerData } from '../api/types.ts'
import { formatInteger, formatRunBanner } from '../format/format.ts'
import { formatUtcStamp } from '../format/utcTime.ts'

/** How big the published simulation was and how fast it ran: "100,000 seasons in 18.4 s on 1 node × 8 instances". */
export function RunBanner({ run, pointLabel }: { run: RunBannerData | null; pointLabel: string }) {
  if (run === null) {
    return (
      <p className="banner banner-warning" role="status">
        No published run at {pointLabel}.
      </p>
    )
  }
  const speed = run.sims_per_second === null ? null : `${formatInteger(run.sims_per_second)} seasons per second`
  const published = formatUtcStamp(run.published_at)
  return (
    <section className="odds-run" aria-label="Published run">
      <p className="odds-run-main">{formatRunBanner(run)}</p>
      <p className="odds-run-detail">
        {[speed, `${run.run_origin.toLowerCase()} run`, published === '' ? null : `published ${published}`]
          .filter((part) => part !== null)
          .join(' · ')}
      </p>
    </section>
  )
}
