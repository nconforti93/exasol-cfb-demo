import { useState } from 'react'

import type { Population, PredictionsResponse, ScorecardResponse } from '../api/types.ts'
import { PointNotice } from '../shell/PointNotice.tsx'
import { usePageData } from '../state/pageData.tsx'
import { CalibrationChart } from './CalibrationChart.tsx'
import { LastWeekList, ThisWeekList } from './PredictionList.tsx'
import './predictions.css'
import { ScorecardHeadline } from './ScorecardHeadline.tsx'

interface PredictionsPageData {
  predictions: PredictionsResponse
  scorecard: ScorecardResponse
}

const POPULATIONS: Array<{ id: Population; label: string }> = [
  { id: 'FBS_INVOLVED', label: 'Every game with an FBS team' },
  { id: 'FBS_VS_FBS', label: 'FBS against FBS' },
]

function PopulationSwitch({ value, onChange }: { value: Population; onChange(population: Population): void }) {
  return (
    <div className="population" role="group" aria-label="Games counted in the scorecard">
      {POPULATIONS.map((population) => (
        <button
          key={population.id}
          type="button"
          className="shell-button shell-button-small"
          aria-pressed={value === population.id}
          onClick={() => onChange(population.id)}
        >
          {population.label}
        </button>
      ))}
    </div>
  )
}

function notices(data: PredictionsPageData) {
  const first = data.predictions.notice
  const second = data.scorecard.notice
  if (first !== null && second !== null && first.message === second.message) return [first]
  return [first, second]
}

function Content({ data }: { data: PredictionsPageData }) {
  const { predictions, scorecard } = data
  const label = predictions.point.label
  return (
    <>
      {notices(data).map((notice, index) => (
        <PointNotice key={index} notice={notice} />
      ))}
      <ScorecardHeadline heading={`Scorecard at ${scorecard.point.label}`} rows={scorecard.week} variant="week" pointLabel={label} />
      <section className="predictions-section" aria-labelledby="last-week-title">
        <h3 id="last-week-title">Last week, graded</h3>
        {predictions.has_grades && predictions.last_week.length > 0 ? (
          <LastWeekList rows={predictions.last_week} />
        ) : (
          <p>No games were graded at {label}.</p>
        )}
      </section>
      <section className="predictions-section" aria-labelledby="this-week-title">
        <h3 id="this-week-title">This week&apos;s predictions</h3>
        {predictions.has_predictions && predictions.this_week.length > 0 ? (
          <ThisWeekList rows={predictions.this_week} />
        ) : (
          <p>No predictions are frozen for the week after {label}.</p>
        )}
      </section>
      <ScorecardHeadline heading="Season to date" rows={scorecard.season} variant="season" pointLabel={label} />
      <section className="predictions-section" aria-labelledby="calibration-title">
        <h3 id="calibration-title">Calibration</h3>
        <p className="pred-note">A dot on the dashed diagonal means the model&apos;s claimed probability matched how often those picks won.</p>
        <CalibrationChart buckets={scorecard.calibration} />
      </section>
    </>
  )
}

/** The scorecard of the model's frozen predictions, last week's grades, and this week's picks. */
export function PredictionsPage() {
  const [population, setPopulation] = useState<Population>('FBS_INVOLVED')
  const page = usePageData<PredictionsPageData>(
    async ({ api, point, options }) => {
      const at = { season: point.season, week: point.week_seq }
      const [predictions, scorecard] = await Promise.all([
        api.predictions(at, options),
        api.scorecard({ ...at, population }, options),
      ])
      return { predictions, scorecard }
    },
    [population],
  )
  return (
    <section className="predictions-page" aria-labelledby="predictions-title" aria-busy={page.status === 'loading'}>
      <div className="predictions-head">
        <h2 id="predictions-title">Predictions</h2>
        <PopulationSwitch value={population} onChange={setPopulation} />
      </div>
      {page.status === 'loading' ? <p role="status">Loading the scorecard…</p> : null}
      {page.status === 'error' ? (
        <div className="banner banner-warning" role="alert">
          <span className="banner-text">Could not load the predictions: {page.error?.message}</span>
          <button type="button" className="shell-button" onClick={page.retry}>
            Retry
          </button>
        </div>
      ) : null}
      {page.data === null ? null : <Content data={page.data} />}
    </section>
  )
}
