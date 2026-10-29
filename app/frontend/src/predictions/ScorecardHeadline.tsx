import type { FreezeKind, ScorecardRow } from '../api/types.ts'
import { formatInteger, formatPercent, formatRecord } from '../format/format.ts'
import { InfoIcon } from '../shell/icons.tsx'

export const RECONSTRUCTION_LABEL = 'reconstructed at the as-of point (leak-free, made after kickoff)'

const FREEZE_WORDS: Record<FreezeKind, string> = {
  IN_ADVANCE: 'in advance',
  RECONSTRUCTED: 'reconstructed',
  ALL: 'all',
}

interface ScorecardHeadlineProps {
  heading: string
  rows: ScorecardRow[]
  /** The week's scorecard shows the line and the freeze-kind detail; the season to date is the compact form. */
  variant: 'week' | 'season'
  pointLabel: string
}

function brier(value: number): string {
  return value.toFixed(3)
}

function headlineOf(rows: ScorecardRow[]): ScorecardRow | null {
  return rows.find((row) => row.is_headline) ?? null
}

function FreezeNote({ kind }: { kind: FreezeKind }) {
  if (kind === 'ALL') {
    return (
      <p className="scorecard-freeze scorecard-reconstructed">
        <InfoIcon /> <span>{RECONSTRUCTION_LABEL}</span>
      </p>
    )
  }
  if (kind === 'IN_ADVANCE') return <p className="scorecard-freeze">Predictions frozen before kickoff.</p>
  return null
}

function LineBeside({ row }: { row: ScorecardRow }) {
  if (row.line_correct === null || row.line_wrong === null) return null
  return (
    <div className="scorecard-line" role="group" aria-label="The closing line on the same games">
      <p className="scorecard-line-record">
        <span className="scorecard-record">{formatRecord(row.line_correct, row.line_wrong, 0)}</span>
        <span className="scorecard-sub">
          {row.line_brier === null ? '' : ` Brier score ${brier(row.line_brier)}`}
          {row.model_brier_on_line_games === null ? '' : ` (the model: ${brier(row.model_brier_on_line_games)} on these games)`}
        </span>
      </p>
      <p className="pred-note">
        closing-line information, which knew more than the model did when it froze its predictions.
      </p>
    </div>
  )
}

function FreezeKinds({ rows }: { rows: ScorecardRow[] }) {
  return (
    <details className="scorecard-detail">
      <summary>The same games by freeze kind</summary>
      <table aria-label="The same games by freeze kind">
        <thead>
          <tr>
            <th scope="col">Freeze kind</th>
            <th scope="col">Record</th>
            <th scope="col">Picks correct</th>
            <th scope="col">Brier score</th>
            <th scope="col">Margin error</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.freeze_kind}>
              <th scope="row">{FREEZE_WORDS[row.freeze_kind]}</th>
              <td className="num">{row.record_label}</td>
              <td className="num">{formatPercent(row.pick_accuracy)}</td>
              <td className="num">{brier(row.brier)}</td>
              <td className="num">{row.mae_margin.toFixed(1)} points</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  )
}

/** The model's record on graded games: the `IS_HEADLINE` row, its freeze label, and the closing line beside it. */
export function ScorecardHeadline({ heading, rows, variant, pointLabel }: ScorecardHeadlineProps) {
  const headline = headlineOf(rows)
  return (
    <section className="scorecard" aria-label={heading}>
      <h3>{heading}</h3>
      {headline === null ? (
        <p>No graded games at {pointLabel}.</p>
      ) : (
        <>
          <div className="scorecard-grid">
            <div className="scorecard-model">
              <p className="scorecard-record scorecard-record-big">{headline.record_label}</p>
              <p className="scorecard-sub">
                {formatPercent(headline.pick_accuracy)} of picks correct · Brier score {brier(headline.brier)} · off by{' '}
                {headline.mae_margin.toFixed(1)} points · {formatInteger(headline.games_graded)} games graded
              </p>
              <FreezeNote kind={headline.freeze_kind} />
            </div>
            {variant === 'week' ? <LineBeside row={headline} /> : null}
          </div>
          {variant === 'week' && rows.length > 1 ? <FreezeKinds rows={rows} /> : null}
        </>
      )}
    </section>
  )
}
