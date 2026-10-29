import type { GuideResponse, GuideRow, ResolvedPoint, WhatIfCondition } from '../api/types.ts'
import { CheckIcon } from '../charts/icons.tsx'
import { formatPercent } from '../format/format.ts'
import { WarningIcon } from '../shell/icons.tsx'
import type { WhatIfOffer } from './whatIfOffer.ts'
import { useSecondaryRead } from './useSecondaryRead.ts'

export const CONDITION_DEBOUNCE_MS = 250

interface RootingGuideProps {
  teamId: number
  school: string
  point: ResolvedPoint
  conditions: WhatIfCondition[]
  offer: WhatIfOffer
  onRunChanged(): void
  onClose(): void
}

function leverage(value: number): string {
  return `${(value * 100).toFixed(1)} pp`
}

function GuideTable({ title, rows, rankOf }: { title: string; rows: GuideRow[]; rankOf: (row: GuideRow) => number | null }) {
  if (rows.length === 0) return null
  return (
    <div className="guide-block">
      <h4>{title}</h4>
      <div className="odds-scroll">
        <table className="guide-table" aria-label={title}>
          <thead>
            <tr>
              <th scope="col">Rank</th>
              <th scope="col">Game</th>
              <th scope="col">Root for</th>
              <th scope="col">If the home team wins</th>
              <th scope="col">If the away team wins</th>
              <th scope="col">Leverage</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.game_id}>
                <td className="num">{rankOf(row) ?? '–'}</td>
                <td>
                  {row.away_school} at {row.home_school}
                  <span className="odds-muted"> · week {row.week_seq}</span>
                </td>
                <td>
                  <span className="guide-root">
                    {row.root_for_school === null ? 'Even' : <><CheckIcon /> <strong>{row.root_for_school}</strong></>}
                  </span>
                </td>
                <td className="num">{row.p_if_home_win === null ? '–' : formatPercent(row.p_if_home_win)}</td>
                <td className="num">{row.p_if_away_win === null ? '–' : formatPercent(row.p_if_away_win)}</td>
                <td className="num">
                  {row.leverage === null ? '–' : leverage(row.leverage)}
                  {row.low_sample ? (
                    <span className="flag-low">
                      {' '}
                      <WarningIcon /> <span>low sample</span>
                    </span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function GuideBody({ guide }: { guide: GuideResponse }) {
  const empty = guide.top.length === 0 && guide.coming_week.length === 0
  return (
    <>
      {guide.conditional ? <p className="guide-conditional">Conditional on your what-if: the set games are left out.</p> : null}
      {empty ? <p>{guide.message ?? 'There are no rooting guide rows for this team at this point.'}</p> : null}
      {guide.message !== null && !empty ? <p className="odds-note">{guide.message}</p> : null}
      {guide.conditional ? null : <GuideTable title="Games that matter most this season" rows={guide.top} rankOf={(row) => row.guide_rank} />}
      <GuideTable title="Coming week" rows={guide.coming_week} rankOf={(row) => row.next_week_rank} />
    </>
  )
}

/** Which games to watch and whom to cheer for, for the focus team. With what-if results set, it is conditional on them. */
export function RootingGuide({ teamId, school, point, conditions, offer, onRunChanged, onClose }: RootingGuideProps) {
  const conditional = offer.offered && conditions.length > 0
  const runId = offer.offered ? offer.run.run_id : null
  const key = conditional
    ? `guide|what-if|${runId}|${teamId}|${JSON.stringify(conditions)}`
    : `guide|${point.season}|${point.week_seq}|${teamId}`
  const guide = useSecondaryRead<GuideResponse>(
    key,
    conditional ? CONDITION_DEBOUNCE_MS : 0,
    (api, signal) =>
      conditional && runId !== null
        ? api.whatifGuide({ run_id: runId, team_id: teamId, conditions }, { signal })
        : api.guide({ season: point.season, week: point.week_seq, team: teamId }, { signal }),
    onRunChanged,
  )
  return (
    <section className="guide" aria-labelledby="guide-title">
      <div className="guide-head">
        <h3 id="guide-title">Rooting guide: {school}</h3>
        <button type="button" className="shell-button shell-button-small" onClick={onClose}>
          Close guide
        </button>
      </div>
      {guide.status === 'loading' ? <p role="status">Loading the rooting guide…</p> : null}
      {guide.status === 'error' ? (
        <p className="banner banner-warning" role="alert">
          <WarningIcon />
          <span>{guide.message}</span>
        </p>
      ) : null}
      {guide.status === 'ready' && guide.data !== null ? <GuideBody guide={guide.data} /> : null}
    </section>
  )
}
