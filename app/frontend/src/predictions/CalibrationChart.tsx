import type { CalibrationBucket } from '../api/types.ts'
import { DotPlot, type DotPoint } from '../charts/DotPlot.tsx'
import { formatPercent } from '../format/format.ts'

const TITLE = 'Calibration: claimed win probability against actual win share'

/** One dot per probability bucket on a diagonal: a model that means what it says sits on the line. */
export function CalibrationChart({ buckets }: { buckets: CalibrationBucket[] }) {
  if (buckets.length === 0) return <p>No graded games to calibrate.</p>
  const points: DotPoint[] = buckets.map((bucket) => ({
    id: `bucket-${bucket.bucket}`,
    x: bucket.mean_pick_win_prob,
    y: bucket.pick_win_share,
    label: `${bucket.games_graded} games: claimed ${formatPercent(bucket.mean_pick_win_prob)}, won ${formatPercent(bucket.pick_win_share)}`,
  }))
  return (
    <DotPlot
      title={TITLE}
      points={points}
      xLabel="Claimed win probability"
      yLabel="Actual win share"
      table={{
        columns: ['Bucket', 'Games graded', 'Claimed win probability', 'Actual win share'],
        rows: buckets.map((bucket) => [
          bucket.bucket,
          bucket.games_graded,
          formatPercent(bucket.mean_pick_win_prob),
          formatPercent(bucket.pick_win_share),
        ]),
      }}
    />
  )
}
