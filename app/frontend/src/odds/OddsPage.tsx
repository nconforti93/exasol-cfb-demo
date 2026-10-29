import { useCallback, useState } from 'react'

import type { ConditionWinner, OddsResponse, WhatIfGamesResponse, WhatIfOddsResponse } from '../api/types.ts'
import { PointNotice } from '../shell/PointNotice.tsx'
import { usePageData } from '../state/pageData.tsx'
import { useAppActions, useAppState } from '../state/react.tsx'
import './odds.css'
import { OddsTable } from './OddsTable.tsx'
import { CONDITION_DEBOUNCE_MS, RootingGuide } from './RootingGuide.tsx'
import { RunBanner } from './RunBanner.tsx'
import { useSecondaryRead } from './useSecondaryRead.ts'
import { WhatIfPanel } from './WhatIfPanel.tsx'
import { resolveWhatIfOffer } from './whatIfOffer.ts'

interface OddsPageData {
  odds: OddsResponse
  games: WhatIfGamesResponse | null
}

/** The odds of every eligible team at the point, the what-if toggles and the focus team's rooting guide. */
export function OddsPage() {
  const page = usePageData<OddsPageData>(async ({ api, point, options }) => {
    const odds = await api.odds({ season: point.season, week: point.week_seq }, options)
    const games = odds.whatif.available ? await api.whatifGames(options) : null
    return { odds, games }
  })
  const actions = useAppActions()
  const conditions = useAppState((state) => state.conditions)
  const focusTeam = useAppState((state) => state.focusTeam)
  const [runChangedNotice, setRunChangedNotice] = useState(false)
  const reload = page.retry

  const data = page.data
  const offer = data === null ? null : resolveWhatIfOffer(data.odds, data.games)
  const runId = offer?.offered === true ? offer.run.run_id : null

  const handleRunChanged = useCallback(() => {
    actions.clearConditions()
    setRunChangedNotice(true)
    reload()
  }, [actions, reload])

  const conditional = useSecondaryRead<WhatIfOddsResponse>(
    runId !== null && conditions.length > 0 ? `whatif|${runId}|${JSON.stringify(conditions)}` : null,
    CONDITION_DEBOUNCE_MS,
    (api, signal) => api.whatifOdds({ run_id: runId ?? 0, conditions }, { signal }),
    handleRunChanged,
  )

  const setCondition = (gameId: number, winner: ConditionWinner | null): boolean => {
    setRunChangedNotice(false)
    return actions.setCondition(gameId, winner)
  }

  if (page.status === 'loading') {
    return (
      <section className="odds-page" aria-label="Odds">
        <h2>Odds</h2>
        <p role="status">Loading the odds…</p>
      </section>
    )
  }
  if (data === null || offer === null) {
    return (
      <section className="odds-page" aria-label="Odds">
        <h2>Odds</h2>
        <div className="banner banner-warning" role="alert">
          <span className="banner-text">Could not load the odds: {page.error?.message}</span>
          <button type="button" className="shell-button" onClick={page.retry}>
            Retry
          </button>
        </div>
      </section>
    )
  }

  const { odds } = data
  const focusRow = odds.rows.find((row) => row.team_id === focusTeam)
  return (
    <section className="odds-page" aria-labelledby="odds-title">
      <h2 id="odds-title">Odds</h2>
      <p className="odds-point">
        Chance to make the playoff and to win the title · {odds.point.label}
      </p>
      <PointNotice notice={odds.notice} />
      <RunBanner run={odds.run} pointLabel={odds.point.label} />
      {odds.rows.length === 0 ? (
        <p className="odds-empty">There are no odds to show at {odds.point.label}.</p>
      ) : (
        <div className="odds-layout">
          <OddsTable
            rows={odds.rows}
            threshold={odds.move_threshold}
            focusTeam={focusTeam}
            onFocus={actions.setFocusTeam}
            conditional={conditional}
          />
          <aside className="odds-side" aria-label="What-if">
            <WhatIfPanel
              offer={offer}
              conditions={conditions}
              result={conditional}
              runChangedNotice={runChangedNotice}
              onSet={setCondition}
              onClear={actions.clearConditions}
            />
          </aside>
        </div>
      )}
      {odds.rows.length === 0 ? null : focusTeam === null ? (
        <p className="odds-note">Pick a team in the table to see which games to watch and whom to cheer for.</p>
      ) : (
        <RootingGuide
          teamId={focusTeam}
          school={focusRow?.school ?? `team ${focusTeam}`}
          point={odds.point}
          conditions={conditions}
          offer={offer}
          onRunChanged={handleRunChanged}
          onClose={() => actions.setFocusTeam(null)}
        />
      )}
    </section>
  )
}
