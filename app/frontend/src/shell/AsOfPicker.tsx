import type { AsOfPoint, PointsResponse } from '../api/types.ts'
import type { PointId } from '../state/store.ts'

const POSTSEASON_OFFSET = 100

export interface AsOfPickerProps {
  points: PointsResponse | null
  /** The point every page shows. */
  selection: PointId | null
  /** True while no point was chosen: the pages follow the current point. */
  following: boolean
  onSeason(season: number): void
  onChoose(point: PointId): void
  onFollow(): void
}

/** The words for a point the list does not hold (the `AS_OF_LABEL` forms: "2026, after week 9"). */
export function describePoint(point: PointId): string {
  if (point.week_seq === 0) return `${point.season}, preseason`
  if (point.week_seq > POSTSEASON_OFFSET) return `${point.season}, postseason week ${point.week_seq - POSTSEASON_OFFSET}`
  return `${point.season}, after week ${point.week_seq}`
}

function optionText(point: AsOfPoint): string {
  const marks = [point.is_current ? 'current' : null, point.has_publication ? null : 'no published run']
  const hints = marks.filter((mark) => mark !== null)
  return hints.length === 0 ? point.as_of_label : `${point.as_of_label} · ${hints.join(' · ')}`
}

function sameSeason(points: PointsResponse | null, selection: PointId | null): boolean {
  return points !== null && selection !== null && selection.season === points.season
}

const OUTSIDE_LIST = ''

/** Season and week selects over the points the server offers (never one after the current point). */
export function AsOfPicker({ points, selection, following, onSeason, onChoose, onFollow }: AsOfPickerProps) {
  const offered = points?.points.filter((point) => !point.is_after_current) ?? []
  const inList = selection !== null && sameSeason(points, selection)
    ? offered.find((point) => point.as_of_week_seq === selection.week_seq)
    : undefined
  const value = inList === undefined ? OUTSIDE_LIST : String(inList.as_of_week_seq)

  function chooseWeek(weekSeq: number) {
    if (points !== null) onChoose({ season: points.season, week_seq: weekSeq })
  }

  return (
    <div className="picker">
      <label className="picker-field">
        <span className="picker-label">Season</span>
        <select
          value={String(points?.season ?? selection?.season ?? '')}
          disabled={points === null}
          onChange={(event) => onSeason(Number(event.target.value))}
        >
          {points === null && selection !== null && <option value={String(selection.season)}>{selection.season}</option>}
          {points?.seasons.map((season) => (
            <option key={season} value={String(season)}>
              {season}
            </option>
          ))}
        </select>
      </label>
      <label className="picker-field">
        <span className="picker-label">Week</span>
        <select
          value={value}
          disabled={points === null}
          onChange={(event) => chooseWeek(Number(event.target.value))}
        >
          {inList === undefined && selection !== null && <option value={OUTSIDE_LIST}>{describePoint(selection)}</option>}
          {inList === undefined && selection === null && <option value={OUTSIDE_LIST}>Choose a week</option>}
          {offered.map((point) => (
            <option key={point.as_of_week_seq} value={String(point.as_of_week_seq)}>
              {optionText(point)}
            </option>
          ))}
        </select>
      </label>
      {!following && (
        <button type="button" className="shell-button" onClick={onFollow}>
          Follow current
        </button>
      )}
    </div>
  )
}
