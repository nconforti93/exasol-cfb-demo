import { useCallback, useState } from 'react'

import type { GuideResponse, TeamGame, TeamOutlookResponse, WhatIfGamesResponse, WhatIfOddsResponse } from '../api/types.ts'
import { CONDITION_DEBOUNCE_MS } from '../odds/RootingGuide.tsx'
import { useSecondaryRead } from '../odds/useSecondaryRead.ts'
import { WhatIfPanel } from '../odds/WhatIfPanel.tsx'
import { orderGames, type WhatIfOffer } from '../odds/whatIfOffer.ts'
import { WarningIcon } from '../shell/icons.tsx'
import { useTeamDirectory } from '../shell/TeamDirectory.tsx'
import { usePoint } from '../state/pageData.tsx'
import { useAppActions, useAppState } from '../state/react.tsx'
import { Card, Chip } from '../ui/index.ts'
import { OddsCard } from './OddsCard.tsx'
import { RivalriesCard } from './RivalriesCard.tsx'
import { teamLookup } from './teamModel.ts'
import { ThisWeekCard } from './ThisWeekCard.tsx'
import { WatchCard } from './WatchCard.tsx'

export interface OutlookProps {
  teamId: number
  school: string
  accent: string
  games: TeamGame[]
}

function teamOffer(
  outlook: TeamOutlookResponse,
  pointKey: { season: number; week_seq: number },
  games: { status: string; data: WhatIfGamesResponse | null; message: string | null },
  teamId: number,
): WhatIfOffer {
  const unavailable = (message: string): WhatIfOffer => ({ offered: false, message })
  if (outlook.run_id === null) return unavailable(outlook.watch.reason ?? 'no what-if run has been published')
  if (games.status === 'error') return unavailable(games.message ?? 'the what-if games could not be loaded')
  const run = games.data?.run ?? null
  if (games.data === null || run === null) return unavailable(games.status === 'loading' ? 'loading the what-if games…' : 'no what-if run has been published')
  if (run.season !== pointKey.season || run.as_of_week_seq !== pointKey.week_seq) return unavailable(`what-ifs use the run published at ${run.as_of_label}`)
  const guided = new Set([...(outlook.watch.guide?.coming_week ?? []), ...(outlook.watch.guide?.top ?? [])].map((row) => row.game_id))
  const relevant = games.data.games.filter((game) => guided.has(game.game_id) || game.home_team_id === teamId || game.away_team_id === teamId)
  return { offered: true, run, games: orderGames(relevant), maxConditions: games.data.max_conditions }
}

/** The panels that need a published run: odds, this week's pick, games to watch with what-ifs, and rivalries. Each says why when empty. */
export function Outlook({ teamId, school, accent, games }: OutlookProps) {
  const point = usePoint()
  const actions = useAppActions()
  const conditions = useAppState((state) => state.conditions)
  const reloadToken = useAppState((state) => state.reloadToken)
  const [attempt, setAttempt] = useState(0)
  const [runChangedNotice, setRunChangedNotice] = useState(false)

  const handleRunChanged = useCallback(() => {
    actions.clearConditions()
    setRunChangedNotice(true)
    setAttempt((count) => count + 1)
  }, [actions])

  const directory = useTeamDirectory()
  const loaded = useSecondaryRead<TeamOutlookResponse>(
    `outlook|${teamId}|${point.season}|${point.week_seq}|${reloadToken}|${attempt}`,
    0,
    (api, signal) => api.teamOutlook(teamId, { season: point.season, week: point.week_seq }, { signal }),
  )
  const outlook = loaded.data
  const runId = outlook?.run_id ?? null

  const whatifGames = useSecondaryRead<WhatIfGamesResponse>(
    runId === null ? null : `team-whatif-games|${runId}|${attempt}`,
    0,
    (api, signal) => api.whatifGames({ signal }),
  )
  const offer = outlook === null ? null : teamOffer(outlook, point, whatifGames, teamId)
  const offered = offer?.offered === true
  const conditional = offered && conditions.length > 0

  const conditionalOdds = useSecondaryRead<WhatIfOddsResponse>(
    conditional && runId !== null ? `team-whatif|${runId}|${JSON.stringify(conditions)}` : null,
    CONDITION_DEBOUNCE_MS,
    (api, signal) => api.whatifOdds({ run_id: runId ?? 0, conditions }, { signal }),
    handleRunChanged,
  )
  const conditionalGuide = useSecondaryRead<GuideResponse>(
    conditional && runId !== null ? `team-guide|${runId}|${teamId}|${JSON.stringify(conditions)}` : null,
    CONDITION_DEBOUNCE_MS,
    (api, signal) => api.whatifGuide({ run_id: runId ?? 0, team_id: teamId, conditions }, { signal }),
    handleRunChanged,
  )

  if (loaded.status === 'loading') {
    return (
      <section className="team-outlook" aria-label="Outlook">
        <p role="status">Loading the outlook…</p>
      </section>
    )
  }
  if (outlook === null || offer === null) {
    return (
      <section className="team-outlook" aria-label="Outlook">
        <p className="banner banner-warning" role="alert">
          <WarningIcon />
          <span className="banner-text">Could not load the outlook: {loaded.message}</span>
          <button type="button" className="shell-button" onClick={() => setAttempt((count) => count + 1)}>
            Retry
          </button>
        </p>
      </section>
    )
  }

  const teams = teamLookup(games, directory)
  const { queries } = outlook
  const runLabel = outlook.run_id === null ? null : `Run ${outlook.run_id} · ${outlook.point.label}`
  const watchAvailable = outlook.watch.available && outlook.watch.guide !== null
  return (
    <section className="team-outlook" aria-labelledby="team-outlook-title">
      <div className="section-head">
        <h3 id="team-outlook-title" className="section-title">
          Outlook
        </h3>
        <Chip>{runLabel ?? 'No published run'}</Chip>
      </div>
      <div className="grid">
        <OddsCard teamId={teamId} odds={outlook.odds} accent={accent} runLabel={runLabel ?? ''} sqlEntry={queries['odds'] ?? null} conditional={conditionalOdds} />
        <ThisWeekCard teamId={teamId} school={school} panel={outlook.this_week} sqlEntry={queries['this_week'] ?? null} />
        <WatchCard panel={outlook.watch} conditional={conditionalGuide} teams={teams} sqlEntry={queries['watch'] ?? null} span={8} />
        {watchAvailable && (
          <Card className="team-whatif" span={4}>
            <WhatIfPanel
              offer={offer}
              conditions={conditions}
              result={conditionalOdds}
              runChangedNotice={runChangedNotice}
              onSet={(gameId, winner) => {
                setRunChangedNotice(false)
                return actions.setCondition(gameId, winner)
              }}
              onClear={actions.clearConditions}
            />
          </Card>
        )}
        <RivalriesCard teamId={teamId} panel={outlook.rivalries} teams={teams} sqlEntry={queries['rivalries'] ?? null} />
      </div>
    </section>
  )
}
