import { useState } from 'react'

import type { Move, OddsRow, WhatIfOddsResponse, WhatIfOddsRow } from '../api/types.ts'
import { InlineBar } from '../charts/InlineBar.tsx'
import { MoveBadge } from '../charts/MoveBadge.tsx'
import { formatDeltaPoints, formatPercent, formatPercentExact } from '../format/format.ts'
import { WarningIcon } from '../shell/icons.tsx'
import { TeamLabel } from '../shell/TeamDirectory.tsx'
import type { SecondaryRead } from './useSecondaryRead.ts'

const EPSILON = 1e-9

interface OddsTableProps {
  rows: OddsRow[]
  /** `ANALYSIS_SETTINGS.MOVE_THRESHOLD`, a fraction: arrows start here. */
  threshold: number
  focusTeam: number | null
  onFocus(teamId: number | null): void
  conditional: SecondaryRead<WhatIfOddsResponse>
}

function Probability({ value }: { value: number | null }) {
  return (
    <span className="num" title={formatPercentExact(value)}>
      {formatPercent(value)}
    </span>
  )
}

/** The server's `MOVE`, held to the page contract: an arrow needs a change of at least the threshold. */
function arrowOf(move: Move, delta: number | null, threshold: number): Move {
  if (move !== 'UP' && move !== 'DOWN') return move
  if (delta === null || Math.abs(delta) + EPSILON < threshold) return 'SAME'
  return move
}

function changeOf(delta: number, threshold: number): Move {
  if (Math.abs(delta) + EPSILON < threshold) return 'SAME'
  return delta > 0 ? 'UP' : 'DOWN'
}

function Change({ delta, threshold }: { delta: number; threshold: number }) {
  const move = changeOf(delta, threshold)
  if (move === 'SAME') return <span className="odds-muted num">{formatDeltaPoints(delta)}</span>
  return <MoveBadge move={move} delta={delta} />
}

function firstPreviousLabel(rows: OddsRow[]): string | null {
  return rows.find((row) => row.prev_as_of_label !== null)?.prev_as_of_label ?? null
}

function thresholdNote(threshold: number, previous: string | null): string {
  const points = Number((threshold * 100).toFixed(2))
  const base = `Arrows from ${points} percentage points`
  return previous === null ? `${base}.` : `${base}, since ${previous}.`
}

function PlayoffIfCell({ entry, threshold }: { entry: WhatIfOddsRow | undefined; threshold: number }) {
  if (entry === undefined) return <td className="odds-cond">–</td>
  return (
    <td className="odds-cond">
      <Probability value={entry.p_in_cfp} /> <Change delta={entry.delta_p_in_cfp} threshold={threshold} />
      {entry.low_sample ? (
        <span className="flag-low" title="Few simulated seasons match: read this number with care">
          <WarningIcon /> <span>low sample</span>
        </span>
      ) : null}
    </td>
  )
}

function ChampionIfCell({ entry }: { entry: WhatIfOddsRow | undefined }) {
  return <td className="odds-cond">{entry === undefined ? '–' : <Probability value={entry.p_champion} />}</td>
}

/** Every eligible team's odds at the point, with movement since the previous published week. */
export function OddsTable({ rows, threshold, focusTeam, onFocus, conditional }: OddsTableProps) {
  const [allRounds, setAllRounds] = useState(false)
  const entries = new Map<number, WhatIfOddsRow>(conditional.data?.rows.map((entry) => [entry.team_id, entry]) ?? [])
  const showConditional = conditional.data !== null
  return (
    <div className="odds-table-block">
      <div className="odds-table-tools">
        <p className="odds-note">{thresholdNote(threshold, firstPreviousLabel(rows))}</p>
        <button type="button" className="shell-button shell-button-small" onClick={() => setAllRounds(!allRounds)}>
          {allRounds ? 'Hide the other rounds' : 'Show every round'}
        </button>
      </div>
      <div className="odds-scroll">
        <table className="odds-table" aria-label="Playoff odds by team">
          <thead>
            <tr>
              <th scope="col">Rank</th>
              <th scope="col">Team</th>
              <th scope="col">Conference</th>
              <th scope="col">Makes the playoff</th>
              {showConditional ? <th scope="col">Playoff, if these results happen</th> : null}
              <th scope="col">Since last week</th>
              <th scope="col">First-round bye</th>
              {allRounds ? <th scope="col">Quarterfinal</th> : null}
              {allRounds ? <th scope="col">Semifinal</th> : null}
              {allRounds ? <th scope="col">Final</th> : null}
              <th scope="col">Champion</th>
              {showConditional ? <th scope="col">Champion, if these results happen</th> : null}
              {allRounds ? <th scope="col">Conference title</th> : null}
              <th scope="col">Expected wins</th>
              <th scope="col">Mean seed</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const focused = row.team_id === focusTeam
              return (
                <tr key={row.team_id} className={focused ? 'odds-focus' : undefined} aria-current={focused ? 'true' : undefined}>
                  <td className="num">{row.odds_rank}</td>
                  <th scope="row" className="odds-team">
                    <button
                      type="button"
                      className="odds-team-button"
                      aria-pressed={focused}
                      title={focused ? 'Close the rooting guide' : 'Show the rooting guide'}
                      onClick={() => onFocus(focused ? null : row.team_id)}
                    >
                      <TeamLabel teamId={row.team_id} school={row.school} />
                    </button>
                  </th>
                  <td>{row.conference ?? '–'}</td>
                  <td className="odds-main">
                    <InlineBar
                      value={row.p_in_cfp}
                      label={`${row.school}: ${formatPercent(row.p_in_cfp)} to make the playoff`}
                      width={96}
                    />{' '}
                    <Probability value={row.p_in_cfp} />
                  </td>
                  {showConditional ? <PlayoffIfCell entry={entries.get(row.team_id)} threshold={threshold} /> : null}
                  <td>
                    <MoveBadge move={arrowOf(row.move, row.delta_p_in_cfp, threshold)} delta={row.delta_p_in_cfp} />
                  </td>
                  <td>
                    <Probability value={row.p_bye} />
                  </td>
                  {allRounds ? (
                    <td>
                      <Probability value={row.p_quarterfinal} />
                    </td>
                  ) : null}
                  {allRounds ? (
                    <td>
                      <Probability value={row.p_semifinal} />
                    </td>
                  ) : null}
                  {allRounds ? (
                    <td>
                      <Probability value={row.p_final} />
                    </td>
                  ) : null}
                  <td>
                    <Probability value={row.p_champion} />
                  </td>
                  {showConditional ? <ChampionIfCell entry={entries.get(row.team_id)} /> : null}
                  {allRounds ? (
                    <td>
                      <Probability value={row.p_conf_champion} />
                    </td>
                  ) : null}
                  <td className="num">{row.expected_wins.toFixed(1)}</td>
                  <td className="num">{row.mean_seed === null ? '–' : row.mean_seed.toFixed(1)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
