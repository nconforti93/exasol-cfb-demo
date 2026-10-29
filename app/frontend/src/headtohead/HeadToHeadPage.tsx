import { useState } from 'react'

import type { MatchupResponse, Team } from '../api/types.ts'
import { WarningIcon } from '../shell/icons.tsx'
import { useAppActions, useAppState } from '../state/react.tsx'
import { usePageData, usePoint } from '../state/pageData.tsx'
import { NextMeeting } from './NextMeeting.tsx'
import { PreviewCard } from './PreviewCard.tsx'
import { MeetingsTable, SeriesSummary } from './SeriesSummary.tsx'
import { TeamPickers } from './TeamPicker.tsx'
import './headtohead.css'

interface PageContent {
  teams: Team[]
  matchup: MatchupResponse | null
}

const NO_TEAMS: readonly Team[] = []

function Matchup({ matchup, asOfLabel }: { matchup: MatchupResponse; asOfLabel: string | null }) {
  const { team, opponent, series, next } = matchup
  const canPreview = series !== null || next !== null
  return (
    <>
      {series === null ? (
        <p className="h2h-card">{`${team.school} and ${opponent.school} have never met.`}</p>
      ) : (
        <>
          <SeriesSummary series={series} />
          <MeetingsTable series={series} games={matchup.games} />
        </>
      )}
      {next !== null && <NextMeeting next={next} team={team} opponent={opponent} asOfLabel={asOfLabel} />}
      {series !== null && next === null && (
        <p className="h2h-muted">{`${team.school} and ${opponent.school} do not meet again this season.`}</p>
      )}
      {canPreview && <PreviewCard key={`${team.team_id}-${opponent.team_id}`} team={team} opponent={opponent} />}
    </>
  )
}

/** Head-to-head: two teams, their series since 1869, the next meeting, and the preview fetched on click. */
export function HeadToHeadPage() {
  const actions = useAppActions()
  const pair = useAppState((state) => state.pair)
  const current = useAppState((state) => state.current)
  const point = usePoint()
  const [teams, setTeams] = useState<readonly Team[]>(NO_TEAMS)
  const { team, opponent } = pair
  const complete = team !== null && opponent !== null && team !== opponent

  const page = usePageData<PageContent>(
    async ({ api, options }) => {
      const [teamList, matchup] = await Promise.all([
        api.teams(options),
        complete ? api.matchup({ team, opponent }, options) : Promise.resolve(null),
      ])
      return { teams: teamList.teams, matchup }
    },
    [team, opponent],
  )

  if (page.data !== null && page.data.teams !== teams) setTeams(page.data.teams)

  const behindCurrent =
    current !== null && (point.season !== current.as_of_season || point.week_seq !== current.as_of_week_seq)
  const asOfLabel = behindCurrent && page.data?.matchup != null ? page.data.matchup.next_as_of_label : null

  return (
    <section aria-label="Head-to-head" className="h2h-page">
      <h2>Head-to-head</h2>
      <TeamPickers
        teams={teams}
        team={team}
        opponent={opponent}
        onChange={actions.setPair}
        onSwap={actions.swapPair}
      />
      {!complete && page.status !== 'error' && (
        <p className="h2h-muted">Pick two teams to see every meeting since 1869 and the next one.</p>
      )}
      {page.status === 'loading' && complete && <p className="h2h-muted">Loading the series…</p>}
      {page.status === 'error' && (
        <p className="banner banner-warning" role="alert">
          <WarningIcon />
          <span className="banner-text">Could not load the head-to-head: {page.error?.message}</span>
          <button type="button" className="shell-button" onClick={page.retry}>
            Try again
          </button>
        </p>
      )}
      {page.status === 'ready' && page.data?.matchup != null && (
        <Matchup matchup={page.data.matchup} asOfLabel={asOfLabel} />
      )}
    </section>
  )
}
