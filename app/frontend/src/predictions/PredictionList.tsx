import type { Grade, PredictionRow } from '../api/types.ts'
import { CheckIcon, CrossIcon, DashIcon } from '../charts/icons.tsx'
import { formatPercent } from '../format/format.ts'
import { formatKickoff } from '../format/utcTime.ts'
import { InfoIcon } from '../shell/icons.tsx'
import { TeamMarks } from '../shell/TeamDirectory.tsx'

const GRADE_WORDS: Record<Grade, string> = {
  CORRECT: 'correct',
  WRONG: 'wrong',
  TIE: 'not graded',
  NO_PICK: 'no pick',
}

function matchup(row: PredictionRow): string {
  return `${row.away_school} at ${row.home_school}`
}

function Pick({ row }: { row: PredictionRow }) {
  if (row.pick_school === null) return <span className="pred-muted">no pick</span>
  return (
    <span>
      <strong>{row.pick_school}</strong> <span className="num">{formatPercent(row.pick_win_prob)}</span>
    </span>
  )
}

function GradeIcon({ grade }: { grade: Grade | null }) {
  if (grade === 'CORRECT') return <CheckIcon />
  if (grade === 'WRONG') return <CrossIcon />
  return <DashIcon />
}

function GradeBadge({ grade }: { grade: Grade | null }) {
  const word = grade === null ? 'not graded' : GRADE_WORDS[grade]
  return (
    <span className={`grade grade-${(grade ?? 'none').toLowerCase()}`} title={grade === 'TIE' ? 'A tie is not graded' : undefined}>
      <GradeIcon grade={grade} /> <span>{word}</span>
    </span>
  )
}

function FreezeCell({ row }: { row: PredictionRow }) {
  return (
    <>
      <span>frozen at {row.freeze_label}</span>
      {row.freeze_kind === 'RECONSTRUCTED' ? (
        <span className="badge-reconstructed" title="Made after kickoff from the data as of that point (leak-free)">
          <InfoIcon /> <span>reconstructed</span>
        </span>
      ) : null}
      {row.is_rescheduled ? <span className="pred-muted"> · rescheduled game</span> : null}
    </>
  )
}

/** This week's games with the predictions frozen for them: score, pick, win probability and when it was frozen. */
export function ThisWeekList({ rows }: { rows: PredictionRow[] }) {
  return (
    <div className="pred-scroll">
      <table className="prediction-table" aria-label="Predictions for this week">
        <thead>
          <tr>
            <th scope="col">Kickoff</th>
            <th scope="col">Game</th>
            <th scope="col">Predicted score</th>
            <th scope="col">Pick</th>
            <th scope="col">Frozen</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.game_id} data-game={row.game_id}>
              <td>{formatKickoff(row.start_at)}</td>
              <th scope="row">
                <TeamMarks teamIds={[row.away_team_id, row.home_team_id]} /><span>{matchup(row)}</span>
              </th>
              <td className="num">
                {row.home_school} {row.pred_home_points.toFixed(1)} – {row.away_school} {row.pred_away_points.toFixed(1)}
              </td>
              <td>
                <Pick row={row} />
              </td>
              <td>
                <FreezeCell row={row} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function result(row: PredictionRow): string {
  if (row.home_points === null || row.away_points === null) return 'no result'
  return `${row.home_school} ${row.home_points} – ${row.away_school} ${row.away_points}`
}

/** Last week's games with their results, the pick the model had frozen, and whether it was right. */
export function LastWeekList({ rows }: { rows: PredictionRow[] }) {
  return (
    <div className="pred-scroll">
      <table className="prediction-table" aria-label="Last week's games, graded">
        <thead>
          <tr>
            <th scope="col">Game</th>
            <th scope="col">Result</th>
            <th scope="col">Pick</th>
            <th scope="col">Grade</th>
            <th scope="col">Frozen</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.game_id} data-game={row.game_id}>
              <th scope="row">
                <TeamMarks teamIds={[row.away_team_id, row.home_team_id]} /><span>{matchup(row)}</span>
              </th>
              <td className="num">{result(row)}</td>
              <td>
                <Pick row={row} />
              </td>
              <td>
                <GradeBadge grade={row.grade} />
              </td>
              <td>
                <FreezeCell row={row} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
