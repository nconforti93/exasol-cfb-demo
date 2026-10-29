import { useState } from 'react'

import type { ConditionWinner, WhatIfCondition, WhatIfGame, WhatIfOddsResponse } from '../api/types.ts'
import { formatInteger, formatPercent } from '../format/format.ts'
import { formatKickoff } from '../format/utcTime.ts'
import { InfoIcon, WarningIcon } from '../shell/icons.tsx'
import type { WhatIfOffer } from './whatIfOffer.ts'
import type { SecondaryRead } from './useSecondaryRead.ts'

interface WhatIfPanelProps {
  offer: WhatIfOffer
  conditions: WhatIfCondition[]
  result: SecondaryRead<WhatIfOddsResponse>
  /** Set when the published run changed under the toggles and the page reloaded them. */
  runChangedNotice: boolean
  /** Sets, replaces or (null) removes a result; false when the limit refuses it. */
  onSet(gameId: number, winner: ConditionWinner | null): boolean
  onClear(): void
}

function winnerOf(conditions: WhatIfCondition[], gameId: number): ConditionWinner | null {
  return conditions.find((condition) => condition.game_id === gameId)?.winner ?? null
}

function groupLabel(game: WhatIfGame): string {
  return game.is_next_week ? `Coming week (week ${game.week_seq})` : `Week ${game.week_seq}`
}

function groupGames(games: WhatIfGame[]): Array<{ label: string; games: WhatIfGame[] }> {
  const groups: Array<{ label: string; games: WhatIfGame[] }> = []
  for (const game of games) {
    const label = groupLabel(game)
    const last = groups[groups.length - 1]
    if (last?.label === label) last.games.push(game)
    else groups.push({ label, games: [game] })
  }
  return groups
}

interface GameToggleProps {
  game: WhatIfGame
  winner: ConditionWinner | null
  onSet(gameId: number, winner: ConditionWinner | null): void
}

function GameToggle({ game, winner, onSet }: GameToggleProps) {
  const choices: Array<{ value: ConditionWinner; label: string }> = [
    { value: 'home', label: `${game.home_school} wins` },
    { value: 'away', label: `${game.away_school} wins` },
  ]
  return (
    <li className="whatif-game">
      <div role="group" aria-label={`${game.away_school} at ${game.home_school}`}>
        <p className="whatif-matchup">
          <strong>{game.away_school}</strong> at <strong>{game.home_school}</strong>
          <span className="whatif-meta">
            {' '}
            · {formatKickoff(game.start_at)} · home team wins {formatPercent(game.home_win_prob)}
          </span>
        </p>
        <div className="whatif-choices">
          {choices.map((choice) => (
            <button
              key={choice.value}
              type="button"
              className="shell-button shell-button-small"
              aria-pressed={winner === choice.value}
              onClick={() => onSet(game.game_id, winner === choice.value ? null : choice.value)}
            >
              {choice.label}
            </button>
          ))}
          <button type="button" className="shell-button shell-button-small" disabled={winner === null} onClick={() => onSet(game.game_id, null)}>
            Not set
          </button>
        </div>
      </div>
    </li>
  )
}

function Result({ result }: { result: SecondaryRead<WhatIfOddsResponse> }) {
  if (result.status === 'idle') return null
  if (result.status === 'loading') {
    return (
      <p className="whatif-result" role="status">
        Recomputing the odds…
      </p>
    )
  }
  if (result.status === 'error' || result.data === null) {
    return (
      <p className="banner banner-warning" role="alert">
        <WarningIcon />
        <span>{result.message ?? 'The what-if could not be computed.'}</span>
      </p>
    )
  }
  const data = result.data
  return (
    <div className="whatif-result" role="status">
      <p className="whatif-sample">
        <strong>
          {formatInteger(data.effective_sample)} of {formatInteger(data.n_sims)} seasons
        </strong>{' '}
        match these results.
      </p>
      {data.effective_sample === 0 ? <p>No simulated season matches: loosen the results.</p> : null}
      {data.low_sample ? (
        <p className="flag-low">
          <WarningIcon /> <span>low sample: few seasons match, so read the numbers with care</span>
        </p>
      ) : null}
      <p className="odds-note">Method: {data.method}.</p>
    </div>
  )
}

/** Set results for remaining games and see the odds in the simulated seasons where they happened. */
export function WhatIfPanel({ offer, conditions, result, runChangedNotice, onSet, onClear }: WhatIfPanelProps) {
  const [limitHit, setLimitHit] = useState(false)

  if (!offer.offered) {
    return (
      <section className="whatif" aria-labelledby="whatif-title">
        <h3 id="whatif-title">What if…</h3>
        <p className="whatif-unavailable">
          <InfoIcon /> <span>{offer.message}</span>
        </p>
      </section>
    )
  }

  const set = (gameId: number, winner: ConditionWinner | null) => {
    setLimitHit(!onSet(gameId, winner))
  }

  return (
    <section className="whatif" aria-labelledby="whatif-title">
      <h3 id="whatif-title">What if…</h3>
      <p className="odds-note">
        Set results of remaining games. The table then shows the odds in the simulated seasons where they happened. Up to{' '}
        {offer.maxConditions} games.
      </p>
      {runChangedNotice ? (
        <p className="banner banner-info" role="status">
          <InfoIcon />
          <span>The published run changed, so the games were reloaded and your results cleared.</span>
        </p>
      ) : null}
      <div className="whatif-bar">
        <span className="num">
          {conditions.length} of {offer.maxConditions} games set
        </span>
        <button type="button" className="shell-button shell-button-small" disabled={conditions.length === 0} onClick={onClear}>
          Clear all
        </button>
      </div>
      {limitHit ? (
        <p className="banner banner-warning" role="alert">
          <WarningIcon />
          <span>At most {offer.maxConditions} games can be set. Clear one first.</span>
        </p>
      ) : null}
      <Result result={result} />
      {offer.games.length === 0 ? <p>No remaining games to set.</p> : null}
      {groupGames(offer.games).map((group) => (
        <div key={group.label} className="whatif-group">
          <h4>{group.label}</h4>
          <ul className="whatif-list">
            {group.games.map((game) => (
              <GameToggle key={game.game_id} game={game} winner={winnerOf(conditions, game.game_id)} onSet={set} />
            ))}
          </ul>
        </div>
      ))}
    </section>
  )
}
