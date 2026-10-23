import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { PredictionsResponse, ScorecardResponse } from '../api/types.ts'
import { FakeBackend, Reply, outageReply, type Handler } from '../test/backend.ts'
import { fixture } from '../test/fixtures/index.ts'
import { OutageProvider } from '../state/outage.tsx'
import { renderInApp } from '../test/render.tsx'
import { PredictionsPage } from './PredictionsPage.tsx'
import { RECONSTRUCTION_LABEL } from './ScorecardHeadline.tsx'


function backendFor(over: Record<string, Handler> = {}): FakeBackend {
  return new FakeBackend({
    'GET /api/predictions': () => fixture('predictions.json'),
    'GET /api/scorecard': () => fixture('scorecard.json'),
    ...over,
  })
}

function renderPredictions(backend = backendFor(), week = 9) {
  return renderInApp(
    <OutageProvider>
      <PredictionsPage />
    </OutageProvider>,
    { backend, initial: { chosen: { mode: 'explicit', season: 2026, week_seq: week } } },
  )
}

async function ready(): Promise<void> {
  await screen.findByRole('table', { name: 'Predictions for this week' })
}

describe('PredictionsPage', () => {
  it('shows the headline row of the scorecard with the line beside it and the season to date', async () => {
    renderPredictions(backendFor(), 10)
    await ready()
    const week = screen.getByRole('region', { name: 'Scorecard at 2026, after week 10' })
    expect(within(week).getByText('41–9', { selector: '.scorecard-record-big' })).toBeInTheDocument()
    expect(week).toHaveTextContent('82.0 % of picks correct')
    expect(week).toHaveTextContent('Brier score 0.151')
    expect(week).toHaveTextContent('off by 10.4 points')
    const line = within(week).getByRole('group', { name: 'The closing line on the same games' })
    expect(line).toHaveTextContent('39–11')
    expect(line).toHaveTextContent('Brier score 0.143')
    expect(line).toHaveTextContent('the model: 0.153')
    expect(screen.getByRole('region', { name: 'Season to date' })).toHaveTextContent('240–80')
    expect(screen.queryByText(RECONSTRUCTION_LABEL)).toBeNull()
  })

  it('labels the week 8 headline as a reconstruction, and not the week 10 headline', async () => {
    renderPredictions(backendFor({ 'GET /api/scorecard': () => fixture('scorecard.reconstructed.json') }), 8)
    await ready()
    expect(screen.getAllByText(RECONSTRUCTION_LABEL).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByRole('region', { name: 'Scorecard at 2026, after week 8' })).toHaveTextContent('38–12')
  })

  it('lists exactly the predictions frozen for the next week, each with pick, score, probability and freeze label', async () => {
    renderPredictions()
    await ready()
    const table = screen.getByRole('table', { name: 'Predictions for this week' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('Dunmore at Carlton')
    expect(rows[0]).toHaveTextContent('Carlton 28.4 – Dunmore 21.7')
    expect(rows[0]).toHaveTextContent('Carlton 55.0 %')
    expect(rows[0]).toHaveTextContent('frozen at 2026, after week 9')
    expect(rows[0]).toHaveTextContent('Sat, Nov 7, 17:00 UTC')
    expect(within(rows[0] as HTMLElement).queryByText('reconstructed')).toBeNull()
    expect(rows[1]).toHaveTextContent('Fairhaven at Eastgate')
    expect(rows[1]).toHaveTextContent('Eastgate 62.0 %')
    expect(table).not.toHaveTextContent('Ashford')
  })

  it('marks reconstructed predictions and notes rescheduled games', async () => {
    renderPredictions()
    await ready()
    const rows = within(screen.getByRole('table', { name: 'Predictions for this week' })).getAllByRole('row').slice(1)
    expect(within(rows[1] as HTMLElement).getByText('reconstructed')).toBeInTheDocument()
    expect(rows[1]).toHaveTextContent('rescheduled game')
    expect(rows[0]).not.toHaveTextContent('rescheduled game')
  })

  it('grades last week\'s games with their results and reads a tie as not graded', async () => {
    const predictions = fixture<PredictionsResponse>('predictions.json')
    const tied = predictions.last_week.find((row) => row.grade === 'WRONG')
    if (tied === undefined) throw new Error('fixture has no WRONG row')
    tied.grade = 'TIE'
    tied.home_points = 20
    tied.away_points = 20
    renderPredictions(backendFor({ 'GET /api/predictions': () => predictions }))
    await ready()
    const table = screen.getByRole('table', { name: "Last week's games, graded" })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(predictions.last_week.length)
    expect(rows[0]).toHaveTextContent('Brookvale at Ashford')
    expect(rows[0]).toHaveTextContent('Ashford 31 – Brookvale 17')
    expect(rows[0]).toHaveTextContent('correct')
    expect(rows[1]).toHaveTextContent('Carlton 20 – Eastgate 20')
    expect(rows[1]).toHaveTextContent('not graded')
    expect(rows[1]).not.toHaveTextContent('wrong')
    expect(rows[2]?.querySelector('.grade-tie')).not.toBeNull()
    expect(rows[3]?.querySelector('.grade-no_pick')).toHaveTextContent('no pick')
  })

  it('shows each grade as an icon and a word', async () => {
    renderPredictions()
    await ready()
    const rows = within(screen.getByRole('table', { name: "Last week's games, graded" })).getAllByRole('row').slice(1)
    expect(rows[0]?.querySelector('.grade-correct svg')).not.toBeNull()
    expect(rows[1]?.querySelector('.grade-wrong svg')).not.toBeNull()
    expect(rows[1]).toHaveTextContent('wrong')
  })

  it('draws the calibration chart and offers its table', async () => {
    const { container } = renderPredictions()
    await ready()
    expect(container.querySelectorAll('circle.dot')).toHaveLength(5)
    await userEvent.click(screen.getByRole('button', { name: 'Table view' }))
    expect(within(screen.getByRole('table', { name: '' })).getAllByRole('row')).toHaveLength(6)
  })

  it('switches the population and shows its rows in the headline, the line, the season and the calibration', async () => {
    const versus = fixture<ScorecardResponse>('scorecard.json')
    versus.population = 'FBS_VS_FBS'
    for (const row of [...versus.week, ...versus.season]) row.population = 'FBS_VS_FBS'
    const headline = versus.week.find((row) => row.is_headline)
    if (headline === undefined) throw new Error('no headline')
    Object.assign(headline, { record_label: '30–6', line_correct: 28, line_wrong: 8 })
    const season = versus.season[0]
    if (season !== undefined) season.record_label = '150–40'
    versus.calibration = versus.calibration.slice(0, 3)
    const backend = backendFor({
      'GET /api/scorecard': (request) => (request.query.get('population') === 'FBS_VS_FBS' ? versus : fixture('scorecard.json')),
    })
    const { container } = renderPredictions(backend, 10)
    await ready()
    expect(screen.getByRole('button', { name: 'Every game with an FBS team' })).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(screen.getByRole('button', { name: 'FBS against FBS' }))
    await waitFor(() => expect(screen.getByText('30–6', { selector: '.scorecard-record-big' })).toBeInTheDocument())
    await ready()
    expect(screen.getByRole('group', { name: 'The closing line on the same games' })).toHaveTextContent('28–8')
    expect(screen.getByRole('region', { name: 'Season to date' })).toHaveTextContent('150–40')
    expect(container.querySelectorAll('circle.dot')).toHaveLength(3)
    expect(backend.requests('GET /api/scorecard').at(-1)?.query.get('population')).toBe('FBS_VS_FBS')
    expect(screen.getByRole('button', { name: 'FBS against FBS' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('asks for the scorecard of the default population at the point, twice in StrictMode with one aborted', async () => {
    const backend = backendFor()
    renderPredictions(backend, 10)
    await ready()
    const scorecards = backend.requests('GET /api/scorecard')
    expect(scorecards).toHaveLength(2)
    expect(scorecards[0]?.signal?.aborted).toBe(true)
    expect(scorecards[1]?.query.get('population')).toBe('FBS_INVOLVED')
    expect(scorecards[1]?.query.get('week')).toBe('10')
    expect(backend.requests('GET /api/predictions')).toHaveLength(2)
  })

  it('says so when a point has no grades and no predictions', async () => {
    const predictions: PredictionsResponse = {
      ...fixture<PredictionsResponse>('predictions.json'),
      this_week: [],
      last_week: [],
      has_predictions: false,
      has_grades: false,
    }
    const scorecard: ScorecardResponse = { ...fixture<ScorecardResponse>('scorecard.json'), week: [], season: [], calibration: [] }
    renderPredictions(backendFor({ 'GET /api/predictions': () => predictions, 'GET /api/scorecard': () => scorecard }))
    expect(await screen.findByText('No games were graded at 2026, after week 9.')).toBeInTheDocument()
    expect(screen.getByText('No predictions are frozen for the week after 2026, after week 9.')).toBeInTheDocument()
    expect(screen.getAllByText('No graded games at 2026, after week 9.').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('No graded games to calibrate.')).toBeInTheDocument()
  })

  it('shows the server notice once when both answers carry it', async () => {
    const notice = { code: 'after_current', message: 'Week 12 is after the current point; showing 2026, after week 9.' }
    const predictions = { ...fixture<PredictionsResponse>('predictions.json'), notice }
    const scorecard = { ...fixture<ScorecardResponse>('scorecard.json'), notice }
    renderPredictions(backendFor({ 'GET /api/predictions': () => predictions, 'GET /api/scorecard': () => scorecard }))
    await ready()
    expect(screen.getAllByText(notice.message)).toHaveLength(1)
  })

  it('shows a load failure with a retry', async () => {
    let failing = true
    const backend = backendFor({
      'GET /api/predictions': () => (failing ? new Reply(502, { error: 'cfbd_failed', message: 'the data source failed' }) : fixture('predictions.json')),
    })
    renderPredictions(backend)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the predictions: the data source failed')
    failing = false
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await ready()
  })

  it('raises the outage state on a database outage', async () => {
    const { store } = renderPredictions(backendFor({ 'GET /api/scorecard': () => outageReply() }))
    await waitFor(() => expect(store.getState().outage).toBe(true))
  })
})
