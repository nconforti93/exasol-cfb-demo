import type { PreviewOdds, PreviewResponse, PreviewTeam } from '../api/types.ts'

const DASH = '–'

function number(value: number | null, digits = 1): string {
  return value === null ? DASH : value.toFixed(digits)
}

function moneyline(value: number | null): string {
  if (value === null) return DASH
  return value > 0 ? `+${value}` : String(value)
}

function spread(value: number | null): string {
  if (value === null) return DASH
  return value > 0 ? `+${value}` : String(value)
}

function TeamRow({ team, side }: { team: PreviewTeam; side: 'home' | 'away' }) {
  return (
    <tr>
      <th scope="row">
        {team.school} <span className="preview-side">({side})</span>
      </th>
      <td>{team.record ?? DASH}</td>
      <td className="num">{number(team.sp_rating)}</td>
      <td className="num">{number(team.srs_rating)}</td>
      <td className="num">{number(team.elo, 0)}</td>
      <td className="num">{number(team.fpi)}</td>
      <td className="num">{team.points === null ? DASH : team.points}</td>
    </tr>
  )
}

function Odds({ odds }: { odds: PreviewOdds | null }) {
  if (odds === null) return <p className="preview-odds">No betting line in CFBD&apos;s answer.</p>
  return (
    <dl className="preview-odds">
      <div>
        <dt>Provider</dt>
        <dd>{odds.provider ?? DASH}</dd>
      </div>
      <div>
        <dt>Spread (home)</dt>
        <dd className="num">{spread(odds.spread)}</dd>
      </div>
      <div>
        <dt>Over/under</dt>
        <dd className="num">{number(odds.over_under)}</dd>
      </div>
      <div>
        <dt>Moneyline home / away</dt>
        <dd className="num">{`${moneyline(odds.home_moneyline)} / ${moneyline(odds.away_moneyline)}`}</dd>
      </div>
    </dl>
  )
}

/** CFBD's view of the game: each team's record and ratings, the line, the venue and status, and how it was fetched. */
export function PreviewResult({ preview }: { preview: PreviewResponse }) {
  return (
    <div className="preview-result">
      <table className="preview-teams">
        <caption className="sr-only">Teams and ratings from CFBD</caption>
        <thead>
          <tr>
            <th scope="col">Team</th>
            <th scope="col">Record</th>
            <th scope="col">SP+</th>
            <th scope="col">SRS</th>
            <th scope="col">Elo</th>
            <th scope="col">FPI</th>
            <th scope="col">Points</th>
          </tr>
        </thead>
        <tbody>
          <TeamRow team={preview.home} side="home" />
          <TeamRow team={preview.away} side="away" />
        </tbody>
      </table>
      <Odds odds={preview.odds} />
      <p className="preview-meta">
        {[preview.venue, preview.status].filter((part) => part !== null && part !== '').join(' · ')}
      </p>
      <p className="preview-meta">
        {`Source ${preview.req_source}`}
        {preview.req_ms === null ? '' : ` · ${preview.req_ms} ms`}
      </p>
    </div>
  )
}
