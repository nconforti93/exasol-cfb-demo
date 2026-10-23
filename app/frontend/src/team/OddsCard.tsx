import type { OutlookOdds, WhatIfOddsRow } from '../api/types.ts'
import { ChartFrame } from '../charts/ChartFrame.tsx'
import { SeriesChart } from '../charts/SeriesChart.tsx'
import type { SecondaryRead } from '../odds/useSecondaryRead.ts'
import type { WhatIfOddsResponse } from '../api/types.ts'
import { formatDeltaPoints, formatInteger, formatPercent } from '../format/format.ts'
import { Card, Chip, StatTile } from '../ui/index.ts'
import { EmptyPanel } from './EmptyPanel.tsx'

const PERCENT_TICKS = [0, 0.25, 0.5, 0.75, 1]
const MAX_X_TICKS = 8

function Conditional({ result, teamId }: { result: SecondaryRead<WhatIfOddsResponse>; teamId: number }) {
  if (result.status === 'idle') return null
  if (result.status === 'loading') return <p className="note" role="status">Recomputing with your what-ifs…</p>
  const row: WhatIfOddsRow | undefined = result.data?.rows.find((candidate) => candidate.team_id === teamId)
  if (result.data === null || row === undefined) return null
  return (
    <p className="odds-whatif-line" role="status">
      <Chip>What-if</Chip>
      <span>
        With your results, the chance to make the playoff is <strong className="num">{formatPercent(row.p_in_cfp)}</strong> ({formatDeltaPoints(row.delta_p_in_cfp)} points) in{' '}
        <span className="num">{formatInteger(result.data.effective_sample)}</span> of <span className="num">{formatInteger(result.data.n_sims)}</span> seasons.
      </span>
    </p>
  )
}

export interface OddsCardProps {
  teamId: number
  odds: OutlookOdds
  accent: string
  runLabel: string
  sqlEntry: string | null
  conditional: SecondaryRead<WhatIfOddsResponse>
}

/** The team's chance to make the playoff, get a bye and win it all, how it moved, and its path over the season's published points. */
export function OddsCard({ teamId, odds, accent, runLabel, sqlEntry, conditional }: OddsCardProps) {
  if (!odds.available || odds.current === null) {
    return <EmptyPanel title="Playoff odds" eyebrow="Outlook" span={8} reason={odds.reason} fallback="Odds appear after the first simulation run." />
  }
  const { current, trajectory } = odds
  const step = Math.max(1, Math.ceil(trajectory.length / MAX_X_TICKS))
  return (
    <Card title="Playoff odds" eyebrow="Outlook" span={8} sqlEntry={sqlEntry} footer={runLabel}>
      <div className="odds-tiles">
        <StatTile
          label="Make the playoff"
          value={<span className="num">{formatPercent(current.p_in_cfp)}</span>}
          delta={odds.move === null ? undefined : { move: odds.move, delta: odds.delta_p_in_cfp }}
          detail={odds.move === 'SAME' ? 'No change since the last run' : undefined}
        />
        <StatTile label="First-round bye" value={<span className="num">{formatPercent(current.p_bye)}</span>} />
        <StatTile label="National champion" value={<span className="num">{formatPercent(current.p_champion)}</span>} />
      </div>
      <Conditional result={conditional} teamId={teamId} />
      <ChartFrame
        title="Chance to make the playoff, by published run"
        table={{
          columns: ['Point', 'Playoff', 'Bye', 'Champion'],
          rows: trajectory.map((point) => [point.label, formatPercent(point.p_in_cfp), formatPercent(point.p_bye), formatPercent(point.p_champion)]),
        }}
      >
        <SeriesChart
          label="Playoff chance by week"
          series={[
            {
              id: 'p-in-cfp',
              name: 'Playoff',
              shape: 'circle',
              tone: 'accent',
              points: trajectory.map((point) => ({ x: point.week_seq, y: point.p_in_cfp })),
              endLabel: formatPercent(current.p_in_cfp),
            },
          ]}
          yDomain={[0, 1]}
          yTicks={PERCENT_TICKS}
          formatY={(value) => `${Math.round(value * 100)} %`}
          xTicks={trajectory.filter((_, index) => index % step === 0).map((point) => ({ x: point.week_seq, label: `W${point.week_seq}` }))}
          accent={accent}
        />
      </ChartFrame>
    </Card>
  )
}
