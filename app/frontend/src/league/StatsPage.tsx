import type { ConferenceRow, MoverRow, StreakRow, TravelRow } from '../api/types.ts'
import { InlineBar } from '../charts/InlineBar.tsx'
import { formatInteger } from '../format/format.ts'
import { formatRating, formatSigned } from '../format/gameFormat.ts'
import { usePageData } from '../state/pageData.tsx'
import { AppLink, Card, ChangeBadge, Note, PageHeader, StatTile, TeamMark } from '../ui/index.ts'
import './league.css'

const DIVERGING_WIDTH = 96
const DIVERGING_HEIGHT = 12

function TeamLink({ team }: { team: TravelRow['team'] }) {
  return (
    <AppLink to={{ page: 'team', team: team.team_id }}>
      <span className="stats-name">{team.school}</span>
    </AppLink>
  )
}

function RoadWarriors({ rows, sqlEntry }: { rows: TravelRow[]; sqlEntry: string | null }) {
  const longest = rows.reduce<TravelRow | null>((best, row) => (best === null || row.longest_trip_km > best.longest_trip_km ? row : best), null)
  const maxKm = Math.max(1, ...rows.map((row) => row.km))
  return (
    <Card title="Road warriors" eyebrow="Kilometres travelled" span={6} sqlEntry={sqlEntry} footer="Great-circle kilometres from each team's home venue to its away and neutral games. Source: CFB_API.TEAM_TRAVEL.">
      {rows.length === 0 ? (
        <Note>No travel is stored for this season yet.</Note>
      ) : (
        <>
          {longest !== null && (
            <StatTile label="Longest single trip" value={<span className="num">{formatInteger(longest.longest_trip_km)} km</span>} detail={`${longest.team.school} to ${longest.longest_trip_to}`} />
          )}
          <ol className="stats-list">
            {rows.map((row, index) => (
              <li key={row.team.team_id} className="stats-row">
                <span className="stats-place num">{index + 1}</span>
                <TeamMark team={row.team} size={32} />
                <div className="stats-bar">
                  <div>
                    <p>
                      <TeamLink team={row.team} />
                    </p>
                    <p className="game-meta">
                      {row.trips} trips · longest {formatInteger(row.longest_trip_km)} km to {row.longest_trip_to}
                    </p>
                  </div>
                </div>
                <div className="stats-bar">
                  <InlineBar value={row.km} max={maxKm} label={`${formatInteger(row.km)} kilometres`} width={96} />
                  <span className="stats-value num">{formatInteger(row.km)} km</span>
                </div>
              </li>
            ))}
          </ol>
        </>
      )}
    </Card>
  )
}

function MoverList({ title, rows }: { title: string; rows: MoverRow[] }) {
  return (
    <div>
      <h4 className="movers-title">{title}</h4>
      <ol className="stats-list">
        {rows.map((row) => (
          <li key={row.team.team_id} className="stats-row">
            <TeamMark team={row.team} size={32} />
            <div className="mover-text">
              <p>
                <TeamLink team={row.team} />
              </p>
              <p className="game-meta num">
                {formatRating(row.sp_before)} to {formatRating(row.sp_now)}
              </p>
              <ChangeBadge direction={row.delta >= 0 ? 'up' : 'down'} text={formatSigned(row.delta)} />
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}

function Movers({ risers, fallers, sqlEntry }: { risers: MoverRow[]; fallers: MoverRow[]; sqlEntry: string | null }) {
  return (
    <Card title="SP+ movers" eyebrow="Against last season" span={6} sqlEntry={sqlEntry} footer="The five largest rises and falls in the SP+ overall rating. Source: CFB_API.SP_RATINGS.">
      {risers.length === 0 && fallers.length === 0 ? (
        <Note>Not enough SP+ ratings are stored to compare two seasons.</Note>
      ) : (
        <div className="movers">
          <MoverList title="Biggest rises" rows={risers} />
          <MoverList title="Biggest falls" rows={fallers} />
        </div>
      )}
    </Card>
  )
}

function Diverging({ value, extent, label }: { value: number; extent: number; label: string }) {
  const half = DIVERGING_WIDTH / 2
  const length = Math.min(half, (Math.abs(value) / Math.max(extent, 0.0001)) * half)
  return (
    <svg className="diverging" role="img" aria-label={label} width={DIVERGING_WIDTH} height={DIVERGING_HEIGHT} viewBox={`0 0 ${DIVERGING_WIDTH} ${DIVERGING_HEIGHT}`}>
      <rect className={value < 0 ? 'diverging-bar diverging-bar-negative' : 'diverging-bar'} x={value < 0 ? half - length : half} y={0} width={length} height={DIVERGING_HEIGHT} rx={3} />
      <line className="diverging-axis" x1={half} x2={half} y1={0} y2={DIVERGING_HEIGHT} />
    </svg>
  )
}

function ConferenceStrength({ rows, sqlEntry }: { rows: ConferenceRow[]; sqlEntry: string | null }) {
  const ordered = [...rows].sort((a, b) => b.mean_sp - a.mean_sp)
  const extent = Math.max(0, ...ordered.map((row) => Math.abs(row.mean_sp)))
  return (
    <Card title="Conference strength" eyebrow="Mean SP+" span={6} sqlEntry={sqlEntry} footer="Mean SP+ overall of each FBS conference, its record against other FBS conferences, and its ranked teams. Source: CFB_API.TEAM_SEASONS, SP_RATINGS, GAMES.">
      {ordered.length === 0 ? (
        <Note>No conference ratings are stored for this season yet.</Note>
      ) : (
        <div className="team-table-scroll">
          <table className="conf-table">
            <caption className="sr-only">Conference strength</caption>
            <thead>
              <tr>
                <th scope="col">Conference</th>
                <th scope="col">Mean SP+</th>
                <th scope="col">Non-conf.</th>
                <th scope="col">Ranked</th>
              </tr>
            </thead>
            <tbody>
              {ordered.map((row) => (
                <tr key={row.conference}>
                  <td>
                    <span className="conf-name">{row.conference}</span>
                    <br />
                    <span className="game-meta">{row.teams} teams</span>
                  </td>
                  <td>
                    <span className="stats-bar">
                      <Diverging value={row.mean_sp} extent={extent} label={`Mean SP+ ${formatRating(row.mean_sp)}`} />
                      <span className="num stats-value">{formatRating(row.mean_sp)}</span>
                    </span>
                  </td>
                  <td className="num">
                    {row.nonconf_wins}–{row.nonconf_losses}
                  </td>
                  <td className="num">{row.ranked_teams}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

function Streaks({ rows, sqlEntry }: { rows: StreakRow[]; sqlEntry: string | null }) {
  const maxLength = Math.max(1, ...rows.map((row) => row.length))
  return (
    <Card title="Hottest streaks" eyebrow="Active win streaks" span={6} sqlEntry={sqlEntry} footer="Consecutive wins across all stored seasons, with the game each streak started in. Source: CFB_API.GAMES.">
      {rows.length === 0 ? (
        <Note>No team has an active win streak.</Note>
      ) : (
        <ol className="stats-list">
          {rows.map((row, index) => (
            <li key={row.team.team_id} className="stats-row">
              <span className="stats-place num">{index + 1}</span>
              <TeamMark team={row.team} size={32} />
              <div>
                <p>
                  <TeamLink team={row.team} />
                </p>
                <p className="game-meta">
                  since {row.since_season}, week {row.since_week}
                </p>
              </div>
              <div className="stats-bar">
                <InlineBar value={row.length} max={maxLength} label={`${row.length} straight wins`} width={96} />
                <span className="stats-value num">{row.length} wins</span>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  )
}

/** Four cheap, striking numbers from the stored history, each with the SQL that produced it one click away. */
export function StatsPage() {
  const page = usePageData(({ api, point, options }) => api.leagueStats({ season: point.season, week: point.week_seq }, options))
  if (page.status === 'loading') return <p role="status">Loading the stats…</p>
  if (page.data === null) {
    return (
      <p className="banner banner-warning" role="alert">
        Could not load the stats: {page.error?.message}
      </p>
    )
  }
  const { road_warriors: travel, risers, fallers, conference_strength: conferences, streaks, queries, point } = page.data
  return (
    <section className="page-stack" aria-labelledby="stats-title">
      <PageHeader id="stats-title" title="Stats" subtitle={`Season to date · ${point.label}`} />
      <div className="grid stats-grid">
        <RoadWarriors rows={travel} sqlEntry={queries['road_warriors'] ?? null} />
        <Movers risers={risers} fallers={fallers} sqlEntry={queries['movers'] ?? null} />
        <ConferenceStrength rows={conferences} sqlEntry={queries['conference_strength'] ?? null} />
        <Streaks rows={streaks} sqlEntry={queries['streaks'] ?? null} />
      </div>
    </section>
  )
}
