import type { OddsResponse, WhatIfGame, WhatIfGamesResponse, WhatIfRun } from '../api/types.ts'

export type WhatIfOffer =
  | { offered: true; run: WhatIfRun; games: WhatIfGame[]; maxConditions: number }
  | { offered: false; message: string }

function fallbackMessage(odds: OddsResponse): string {
  const { reason, run_label: label } = odds.whatif
  switch (reason) {
    case 'run_at_other_point':
      return label === null ? 'what-ifs use the run published at another point' : `what-ifs use the run published at ${label}`
    case 'regenerating':
      return 'the what-if views are being regenerated'
    case 'no_run':
    case null:
      return 'no what-if run has been published'
  }
}

/** Earliest coming-week games first, then the later weeks in order. */
export function orderGames(games: readonly WhatIfGame[]): WhatIfGame[] {
  return [...games].sort(
    (a, b) =>
      Number(b.is_next_week) - Number(a.is_next_week) ||
      a.week_seq - b.week_seq ||
      (a.start_at ?? '').localeCompare(b.start_at ?? '') ||
      a.game_id - b.game_id,
  )
}

/**
 * Toggles are offered only at the what-if run's point, compared as (season, week sequence): the season
 * comes from the run itself, because a week sequence alone repeats across seasons.
 */
export function resolveWhatIfOffer(odds: OddsResponse, games: WhatIfGamesResponse | null): WhatIfOffer {
  if (!odds.whatif.available) return { offered: false, message: odds.whatif.message ?? fallbackMessage(odds) }
  const run = games?.run ?? null
  if (games === null || run === null) return { offered: false, message: 'no what-if run has been published' }
  if (run.season !== odds.point.season || run.as_of_week_seq !== odds.point.week_seq) {
    return { offered: false, message: `what-ifs use the run published at ${run.as_of_label}` }
  }
  return { offered: true, run, games: orderGames(games.games), maxConditions: games.max_conditions }
}
