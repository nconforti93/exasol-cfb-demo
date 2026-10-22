# Feature: What-if: playoff odds given chosen results

"What if Brookvale wins at Ashford?" The stored seasons of the published run already contain the answer: keep the seasons in which that happened and count again. Every toggle on the odds page is such a filter, an interactive query over up to a million simulated seasons, and the page always shows how many seasons are behind each number, because every toggle shrinks that sample.

## Background

- **The what-if run** is the run of the latest publication of the newest published point (`analyses/published-odds`). What-ifs are answered for that run only.
- **Generated views in `CFB_API`.** Every publication of the newest point, and `make analyses` (`analyses/deploy`), rewrites three views in the publication's transaction. They carry the run id as a literal, so the run's partition of `CFB_SIM.SIM_TEAM` is pruned:
  - `CFB_API.WHATIF_RUN`: one row: `RUN_ID`, `RUN_STATUS`, `PUBLICATION_ID`, the point (`AS_OF_SEASON`, `AS_OF_WEEK_SEQ`, `AS_OF_LABEL`), `N_SIMS` and `NEXT_WEEK_SEQ` (the smallest week sequence of the run's remaining games).
  - `CFB_API.WHATIF_OUTCOMES`: one row per simulated season of the run: `RUN_ID`, `SIM_ID`, and one `BOOLEAN` column per remaining game, named `G<game id>`, TRUE for a home win. Each game column's comment names the week, both teams and the game id. The view reads the run's outcome table (`SIM_RUN.PATH_TABLE`, `sim-engine/sim-runs`). Phase 4 stores one layout for both targets (its probe 1.1c); the view renames layout-C columns, or decodes a layout-A vector with `SUBSTR`, and consumers see the same columns either way.
  - `CFB_API.WHATIF_PLAYOFF`: one row per simulated season and playoff team of the run (the run's `SIM_TEAM` rows): `SIM_ID`, `TEAM_ID`, `SEED`, `BYE`, `REACHED_ROUND`, `CHAMPION`.

  Before the first publication, the three views exist without rows, and `WHATIF_OUTCOMES` has only `RUN_ID` and `SIM_ID`.
- `CFB_API.WHATIF_GAMES`: one row per remaining game of the what-if run: `GAME_ID`, `OUTCOME_COLUMN` (`G<game id>`), `WEEK`, `WEEK_SEQ`, `IS_NEXT_WEEK` (`WEEK_SEQ` = `NEXT_WEEK_SEQ`), `START_AT`, both teams' ids and schools, `IS_NEUTRAL_SITE`, `IS_CONF_GAME`, `MU`, `SIGMA`, `HOME_WIN_PROB` (`CFB_SIM.MARGIN_WIN_PROB(MU, SIGMA)`) and `SIM_HOME_WIN_SHARE` (the share of the run's seasons with a home win).
- `CFB_API.WHATIF_SETTINGS`: one row per setting of `CFB_PRED.ANALYSIS_SETTINGS`: `MIN_EFFECTIVE_SAMPLE` 1,000 (a probability's standard error is then at most 1.6 points), `MIN_BRANCH_SAMPLE` 100 (`analyses/rooting-guide`) and `MOVE_THRESHOLD` 0.0050.
- **Conditions** are a list of (game id, home team wins TRUE or FALSE) on games of `WHATIF_GAMES`. Exasol has no parameterized views, so the host library `analyses/whatif.py` builds the query for a list of conditions:
  - `what_if_odds_sql(conditions, games)`, given the game ids of `WHATIF_GAMES`;
  - `rooting_guide_sql(team_id, conditions, games)` (`analyses/rooting-guide`).

  The SQL reads only `CFB_API` views, so the app and the chat roles (`api/roles`) can run it. Before building any SQL, the library refuses, with a message naming the condition: a game that is not in `games`, a game listed twice, more than 16 conditions, and an id that is not an integer.
- **Conditional odds** (`what_if_odds_sql`): one row per eligible team of the what-if run's publication (`CFB_API.PLAYOFF_ODDS` of `WHATIF_RUN.PUBLICATION_ID`):
  - `TEAM_ID`, `SCHOOL`, `EFFECTIVE_SAMPLE` (the seasons that match every condition), and the counts `N_IN_CFP`, `N_BYE`, `N_QUARTERFINAL`, `N_SEMIFINAL`, `N_FINAL`, `N_CHAMPION` over those seasons;
  - the probabilities `P_IN_CFP` … `P_CHAMPION` (count / `EFFECTIVE_SAMPLE`, `DECIMAL(7,6)` like every probability this plan derives from simulated seasons, so one season in a million shows; NULL when the sample is 0) and `MEAN_SEED` over the team's playoff seasons among them;
  - the publication's unconditional `BASE_P_IN_CFP` and `BASE_P_CHAMPION`, and `DELTA_P_IN_CFP`;
  - `LOW_SAMPLE`: TRUE when `EFFECTIVE_SAMPLE` is below `MIN_EFFECTIVE_SAMPLE`.

  Conference titles and win totals are stored as tallies over all seasons (`sim-engine/sim-runs`), so a what-if does not change them.
- **Conditioning is not forcing.** The kept seasons are those in which the chosen results happened, and in those seasons the winners also tended to be stronger (each team's strength offset is shared by all its games, `sim-engine/engine-contract`). A conditional answer therefore carries what the result says about the teams. A forced re-sim (`analyses/forced-resim`) sets the results without that inference. Pages and the chat name the method with every number.
- Performance targets: `plan.md` § Requirements (a what-if over all teams below 1 s at p95 with 1,000,000 seasons on AWS).
- The examples run in the integration-test namespace over the sim league with a published run of 4,000 seasons in 8 batches of 500 at 2026 week 9. "Brookvale wins at Ashford" is the condition (week-12 game, FALSE). The test computes the expected counts on the host from the run's outcome table and `SIM_TEAM` rows (`tests/analyses/oracle.py`).

## Scenarios

### Scenario: The what-if views expose every simulated season of the published run

* *GIVEN* the published run
* *WHEN* `CFB_API.WHATIF_RUN`, `WHATIF_GAMES` and `WHATIF_OUTCOMES` are queried
* *THEN* `WHATIF_RUN` MUST name the run with 4,000 seasons and `NEXT_WEEK_SEQ` 10
* *AND* `WHATIF_OUTCOMES` MUST hold 4,000 rows and one column per remaining game of the run's `SIM_RUN_GAME`, named `G<game id>`, each equal, season by season, to the run's outcome column for that game
* *AND* the comment of the column of the week-12 game Ashford against Brookvale MUST name the week, both teams and the game id, and `WHATIF_GAMES` MUST give that game `HOME_WIN_PROB` 0.8289
* *AND* `IS_NEXT_WEEK` MUST be TRUE exactly for the week-10 games

### Scenario: Conditional odds count only the matching seasons

* *GIVEN* the published run
* *WHEN* the test app role runs the SQL of `what_if_odds_sql` for "Brookvale wins at Ashford"
* *THEN* `EFFECTIVE_SAMPLE` MUST equal the number of seasons in which that game's column is FALSE
* *AND* every team's counts and probabilities MUST equal the oracle's, and the `N_IN_CFP` values MUST add up to 12 × `EFFECTIVE_SAMPLE`
* *AND* Brookvale's `P_IN_CFP` MUST be higher than its `BASE_P_IN_CFP`
* *AND* the owner MUST get the same rows

### Scenario: A small effective sample is flagged

* *GIVEN* the published run, in which Brookvale wins at Ashford in fewer than 1,000 of the 4,000 seasons
* *WHEN* the SQL of `what_if_odds_sql` runs for "Brookvale wins at Ashford", and for no condition
* *THEN* with the condition every row MUST have `LOW_SAMPLE` TRUE
* *AND* without a condition every row MUST have `EFFECTIVE_SAMPLE` 4,000, `LOW_SAMPLE` FALSE, `N_IN_CFP` equal to the team's `SIM_TEAM` rows in the run, and `P_IN_CFP` equal to `BASE_P_IN_CFP` once rounded to the base's scale

### Scenario: Conditions that no season matches give zero counts

* *GIVEN* the published run and a set of conditions that the oracle confirms no season matches
* *WHEN* the SQL of `what_if_odds_sql` runs for them
* *THEN* it MUST return one row per eligible team with `EFFECTIVE_SAMPLE` 0, every count 0, every probability NULL and `LOW_SAMPLE` TRUE
* *AND* it MUST NOT raise an error

### Scenario: Invalid conditions are refused before any SQL is built

* *GIVEN* the game ids of a what-if run
* *WHEN* `what_if_odds_sql` is called with a game that is not among them, with one game listed twice, with 17 conditions, or with a game id that is not an integer
* *THEN* each call MUST raise an error that names the offending condition
* *AND* no SQL MUST be returned

### Scenario: Before the first publication the what-if views are empty

* *GIVEN* a freshly deployed namespace with simulation runs but no publication
* *WHEN* `CFB_API.WHATIF_RUN` and `CFB_API.WHATIF_OUTCOMES` are queried, and the SQL of `what_if_odds_sql` runs without conditions
* *THEN* all three MUST return no rows without an error
* *AND* `WHATIF_OUTCOMES` MUST have exactly the columns `RUN_ID` and `SIM_ID`
