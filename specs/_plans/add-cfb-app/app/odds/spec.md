# Feature: Playoff odds page

The demo's hook and the odds part of beat 4. "Your team, its playoff odds": every eligible team's chance to reach the playoff and to win the title, from the published simulation run. Arrows show the movement since the last published week, and a banner says how many seasons were simulated in how many seconds. What-if toggles keep the simulated seasons in which chosen results happened, and the page always says how many seasons remain. The rooting guide tells a bubble team's fans which games to watch and whom to cheer for.

## Background

- **The odds table** is `CFB_API.PLAYOFF_ODDS` at the point, ordered by `ODDS_RANK`.
  - Each row shows the school, the conference, `P_IN_CFP`, `P_BYE` and `P_CHAMPION`, with the other rounds on demand, plus `EXPECTED_WINS` and `MEAN_SEED`.
  - Probabilities are shown in percent with one decimal. A value that is above 0 but below 0.05 % reads "< 0.1 %", with the exact value on hover.
  - **Movement** since the previous published point of the season (`PREV_AS_OF_LABEL`):
    - `UP` and `DOWN` show an arrow icon, the word, and the signed change in percentage points (`DELTA_P_IN_CFP` is a fraction, so 0.023 reads "+2.3");
    - `SAME` shows nothing;
    - `NEW` shows a "new" badge.
  - The threshold comes from `ANALYSIS_SETTINGS.MOVE_THRESHOLD` and is stated under the table ("arrows from 0.7 percentage points").
- **The run banner** reads `CFB_API.PUBLISHED_RUNS` for the point: `N_SIMS`, `SECONDS_TOTAL`, `SIMS_PER_SECOND`, `NODES`, `INSTANCES`, `RUN_ORIGIN` and `PUBLISHED_AT`. It reads, for example, "100,000 seasons in 18.4 s on 1 node × 8 instances".
- **A focus team** can be set from the table or from the URL (`?team=<id>`). Its row is highlighted, and its rooting guide opens.
- **What-if toggles.**
  - They are offered only at the point of the what-if run, and only while `CURRENT_POINT.WHATIF_VIEWS_CURRENT` is TRUE. The point is compared as season and week sequence: the week sequence from `CURRENT_POINT.WHATIF_AS_OF_WEEK_SEQ` and the season from `CFB_API.WHATIF_RUN`, because `CURRENT_POINT` names no what-if season and a week sequence alone repeats across seasons. Elsewhere the page says why: either "what-ifs use the run published at <label>", or "the what-if views are being regenerated".
  - The games come from `CFB_API.WHATIF_GAMES`: the coming week (`IS_NEXT_WEEK`) first, then the later weeks.
  - Each game can be set to a home win, an away win or not set, and shows its `HOME_WIN_PROB`. At most 16 games can be set.
- **Conditional odds.** The backend builds them with `what_if_odds_sql(WHATIF_RUN_ID, conditions, games)` from `analyses/whatif.py` and runs them on the read path (`analyses/what-if`). The page shows:
  - `EFFECTIVE_SAMPLE` as "1,234 of 100,000 seasons";
  - `LOW_SAMPLE` as a visible flag on the affected numbers;
  - the base value and the change for each team;
  - the method: "conditioning: the simulated seasons in which these results happened".

  The library's validation errors are answered with status 400, naming the condition, before any statement runs.
- **When the run changes.** An empty result, or an unknown-column error, means the what-if run changed since the page loaded. The backend answers with status 409 `whatif_run_changed`, and the page reloads `WHATIF_RUN` and `WHATIF_GAMES`, clears the toggles, and says why.
- **The rooting guide** of the focus team:
  - **Without toggles** it reads `CFB_API.ROOTING_GUIDE` for the run of the point's publication. It shows the season's top 10 games by `GUIDE_RANK`, and the coming week's games by `NEXT_WEEK_RANK`, each with `ROOT_FOR_TEAM_ID`, `P_IF_HOME_WIN`, `P_IF_AWAY_WIN`, `LEVERAGE` and `LOW_SAMPLE`.
  - **With toggles** it shows `rooting_guide_sql` over the coming week's games, minus the games a toggle sets, labeled "conditional on your what-if".
  - A run that is no longer kept has no guide rows, and the page says so.
- **Not shown in this phase:** the forced re-sims. They serve no demo beat, and `CFB_API.FORCED_RUN_ODDS` stays available to the chat.
- The examples run on the app test stack, with publications at 2026 week 8 (a manual run of 4,000 seasons) and week 9 (the refresh's run of 2,000 seasons). The what-if condition is "Brookvale wins at Ashford", the week-12 game set to an away win. Expected values come from the serving views and the library's SQL, run directly as `CFB_TEST_APP`.

## Scenarios

### Scenario: The odds show the published run with arrows since the last published week

* *GIVEN* the app test stack
* *WHEN* the odds of 2026 week 9 are loaded
* *THEN* the rows MUST equal `TEST_CFB_API.PLAYOFF_ODDS` at week 9, in `ODDS_RANK` order, with `MOVE` and `DELTA_P_IN_CFP` against "2026, after week 8"
* *AND* the run banner MUST state the run's `N_SIMS`, `SECONDS_TOTAL`, `NODES` and `INSTANCES` from `TEST_CFB_API.PUBLISHED_RUNS`

### Scenario: Movement is shown with an icon and a word, never by color alone

* *GIVEN* odds rows with `MOVE` values `UP` (+2.3 percentage points), `DOWN` (−1.1), `SAME` and `NEW`
* *WHEN* the odds table is rendered
* *THEN* the `UP` and `DOWN` rows MUST show an arrow icon, the words "up" or "down" in their accessible names, and "+2.3" or "−1.1"
* *AND* the `SAME` row MUST show no movement, and the `NEW` row MUST show a "new" badge

### Scenario: A toggle recomputes the odds over the matching seasons

* *GIVEN* the what-if run published at 2026 week 9
* *WHEN* the user sets "Brookvale wins at Ashford"
* *THEN* the returned rows MUST equal the result of `what_if_odds_sql` for that run and condition, run directly as `CFB_TEST_APP`
* *AND* the page MUST show the effective sample as "<n> of 2,000 seasons", and the `LOW_SAMPLE` flag on every team when n is below 1,000
* *AND* Brookvale's row MUST show its conditional `P_IN_CFP` above its base value

### Scenario: A what-if for a superseded run is reloaded, not shown

* *GIVEN* a page holding toggles for the week-9 run, after which a new manual run at 2026 week 9 is published
* *WHEN* the user changes a toggle
* *THEN* the backend MUST answer with status 409 `whatif_run_changed` and return no odds
* *AND* the page MUST reload the what-if games of the new run, clear the toggles, and state that the published run changed

### Scenario: Toggles are offered only at the what-if run's point

* *GIVEN* the app test stack
* *WHEN* the odds of 2026 week 8 are loaded, and then loaded again after the stored data is restored to week 8 without `make analyses`, which leaves the what-if views on the week-9 run (`WHATIF_VIEWS_CURRENT` FALSE, harness `snapshot_restore_at(8)`)
* *THEN* at week 8 the page MUST offer no toggles and MUST state "what-ifs use the run published at 2026, after week 9"
* *AND* in the second case the page MUST offer no toggles and MUST state that the what-if views are being regenerated

### Scenario: The rooting guide of a team, without and with toggles

* *GIVEN* Brookvale as the focus team at 2026 week 9
* *WHEN* the guide is loaded without toggles, and then with "Brookvale wins at Ashford" set
* *THEN* the first MUST equal `TEST_CFB_API.ROOTING_GUIDE` for the week-9 run and Brookvale: the top 10 by `GUIDE_RANK` and the coming week by `NEXT_WEEK_RANK`
* *AND* the second MUST equal the result of `rooting_guide_sql` over the coming week's games, MUST NOT contain the set game, and MUST be labeled conditional

### Scenario: Invalid conditions are refused before any query

* *GIVEN* the what-if run's games
* *WHEN* a client posts 17 conditions, and then a condition on a game that is not among the run's games
* *THEN* both requests MUST be refused with status 400 and the library's message naming the offending condition
* *AND* the inspector's query log MUST NOT gain an entry for either request
