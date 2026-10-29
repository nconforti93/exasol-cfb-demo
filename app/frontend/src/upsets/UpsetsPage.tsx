import type { UpsetRow } from '../api/types.ts'
import { PointNotice } from '../shell/PointNotice.tsx'
import { WarningIcon } from '../shell/icons.tsx'
import { usePageData } from '../state/pageData.tsx'
import { UpsetFeature } from './UpsetFeature.tsx'
import { UpsetTable } from './UpsetTable.tsx'
import './upsets.css'

function byWeekRank(rows: readonly UpsetRow[]): UpsetRow[] {
  const rank = (row: UpsetRow) => row.week_rank ?? Number.MAX_SAFE_INTEGER
  return [...rows].sort((a, b) => rank(a) - rank(b))
}

/** Upsets: the upset of the week ranked since 1978, the week's others, and the biggest upsets of all time and of the line era. */
export function UpsetsPage() {
  const page = usePageData(({ api, point, options }) => api.upsets({ season: point.season, week: point.week_seq }, options))

  if (page.status === 'loading') {
    return (
      <section aria-label="Upsets" className="upsets-page">
        <h2>Upsets</h2>
        <p className="shell-loading">Loading the upsets…</p>
      </section>
    )
  }
  if (page.status === 'error' || page.data === null) {
    return (
      <section aria-label="Upsets" className="upsets-page">
        <h2>Upsets</h2>
        <p className="banner banner-warning" role="alert">
          <WarningIcon />
          <span className="banner-text">Could not load the upsets: {page.error?.message}</span>
          <button type="button" className="shell-button" onClick={page.retry}>
            Try again
          </button>
        </p>
      </section>
    )
  }

  const { all_time: allTime, line_era: lineEra, all_time_from_season: from, point, notice } = page.data
  const [featured, ...others] = byWeekRank(page.data.week)
  return (
    <section aria-label="Upsets" className="upsets-page">
      <h2>Upsets</h2>
      <PointNotice notice={notice} />
      {featured === undefined ? (
        <p className="upset-empty" role="status">
          {`The week had no upset (${point.label}).`}
        </p>
      ) : (
        <UpsetFeature row={featured} />
      )}
      {others.length > 0 && (
        <section aria-label="The week's other upsets">
          <h3>The week&apos;s other upsets</h3>
          <UpsetTable kind="week" rows={others} caption={`Upsets of ${point.label}, by rank`} allTimeFrom={from} />
        </section>
      )}
      <div className="upset-lists">
        <section className="upset-list" aria-label="All-time upsets">
          <h3>{`Biggest upsets since ${from}`}</h3>
          {allTime.length === 0 ? (
            <p className="upset-note">No upsets are ranked yet.</p>
          ) : (
            <UpsetTable kind="all-time" rows={allTime} caption={`Top ${allTime.length} since ${from}`} allTimeFrom={from} />
          )}
        </section>
        <section className="upset-list" aria-label="Betting-line era upsets">
          <h3>Betting-line era</h3>
          {lineEra.length === 0 ? (
            <p className="upset-note">No upsets are ranked yet.</p>
          ) : (
            <UpsetTable
              kind="line-era"
              rows={lineEra}
              caption={`Top ${lineEra.length} with a betting line`}
              allTimeFrom={from}
            />
          )}
        </section>
      </div>
    </section>
  )
}
