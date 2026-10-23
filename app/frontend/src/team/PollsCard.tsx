import type { PollPoint } from '../api/types.ts'
import { ChartFrame } from '../charts/ChartFrame.tsx'
import { SeriesChart, type Series } from '../charts/SeriesChart.tsx'
import { Card } from '../ui/index.ts'

const RANK_TICKS = [1, 5, 10, 15, 20, 25]
const WORST_RANK_SHOWN = 25
const MAX_X_TICKS = 6

function lastRank(polls: PollPoint[], pick: (poll: PollPoint) => number | null): number | null {
  for (let index = polls.length - 1; index >= 0; index--) {
    const rank = pick(polls[index] as PollPoint)
    if (rank !== null) return rank
  }
  return null
}

function seriesOf(polls: PollPoint[], id: string, name: string, shape: Series['shape'], tone: Series['tone'], pick: (poll: PollPoint) => number | null): Series | null {
  if (!polls.some((poll) => pick(poll) !== null)) return null
  const last = lastRank(polls, pick)
  return {
    id,
    name,
    shape,
    tone,
    points: polls.map((poll) => ({ x: poll.week_seq, y: pick(poll) })),
    ...(last === null ? {} : { endLabel: `${name} #${last}` }),
  }
}

export interface PollsCardProps {
  polls: PollPoint[]
  accent: string
  sqlEntry: string | null
}

/** AP and CFP ranks by week of the season: rank 1 at the top, a gap where the team was unranked. */
export function PollsCard({ polls, accent, sqlEntry }: PollsCardProps) {
  const series = [
    seriesOf(polls, 'ap', 'AP', 'circle', 'accent', (poll) => poll.ap_rank),
    seriesOf(polls, 'cfp', 'CFP', 'diamond', 'neutral', (poll) => poll.cfp_rank),
  ].filter((one): one is Series => one !== null)
  const worst = Math.max(WORST_RANK_SHOWN, ...polls.flatMap((poll) => [poll.ap_rank ?? 0, poll.cfp_rank ?? 0]))
  const step = Math.max(1, Math.ceil(polls.length / MAX_X_TICKS))
  return (
    <Card title="AP and CFP ranks" eyebrow="By week" sqlEntry={sqlEntry}>
      {series.length === 0 ? (
        <p className="note">Not ranked in the AP or CFP polls at any point this season.</p>
      ) : (
        <ChartFrame
          title="Rank by week (1 is best)"
          table={{
            columns: ['Week', 'AP', 'CFP'],
            rows: polls.map((poll) => [poll.label, poll.ap_rank ?? '–', poll.cfp_rank ?? '–']),
          }}
        >
          <SeriesChart
            label="AP and CFP rank by week"
            series={series}
            yDomain={[1, worst]}
            yTicks={RANK_TICKS.filter((tick) => tick <= worst)}
            formatY={(value) => `#${value}`}
            xTicks={polls.filter((_, index) => index % step === 0).map((poll) => ({ x: poll.week_seq, label: `W${poll.week_seq}` }))}
            invert
            accent={accent}
            height={190}
          />
        </ChartFrame>
      )}
    </Card>
  )
}
