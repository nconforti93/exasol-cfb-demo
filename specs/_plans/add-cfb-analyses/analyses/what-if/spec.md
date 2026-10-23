# Feature: What-if: playoff odds given chosen results

"What if Brookvale wins at Ashford?" The stored seasons of the published run already contain the answer: keep the seasons in which that happened and count again. Every toggle on the odds page is such a filter, an interactive query over up to a million simulated seasons, and the page always shows how many seasons are behind each number, because every toggle shrinks that sample.

## Background

- **The what-if publication** is `CFB_PRED.WHATIF_PUBLICATION`: at most one row, the latest publication of the newest published point at or before the current point (`CFB_MODEL.CURRENT_AS_OF`) whose run is still `DONE` (`analyses/published-odds`). Columns: `PUBLICATION_ID`, `RUN_ID`, the point (`AS_OF_SEASON`, `AS_OF_WEEK`, `AS_OF_SEASON_TYPE`, `AS_OF_WEEK_SEQ`, `AS_OF_LABEL`), `N_SIMS` and `PUBLISHED_AT`.
  - It is the only definition of "the what-if run". The generator `WHATIF_VIEWS`, the publication's regeneration check, `WHATIF_GAMES`, `CFB_API.CURRENT_POINT` and `MATCHUP_NEXT` (`analyses/head-to-head`) all read it.
  - After a restore to an earlier week it falls back to the newest publication at or before the restored point. After a dropped run it falls back to the newest publication whose run is still `DONE`.
  - What-ifs are answered for that run only.
- **Generated views in `CFB_API`.** Three views are rewritten for the what-if publication's run: by a publication that becomes the what-if publication, in its transaction, and by `make analyses` (`analyses/deploy`), which the restore recipe runs after a restore. They carry the run id as a literal, so the run's partition of `CFB_SIM.SIM_TEAM` is pruned:
  - `CFB_API.WHATIF_RUN`: one row: `RUN_ID`, `RUN_STATUS`, `PUBLICATION_ID`, the point (`AS_OF_SEASON`, `AS_OF_WEEK_SEQ`, `AS_OF_LABEL`), `N_SIMS` and `NEXT_WEEK_SEQ` (the smallest week sequence of the run's remaining games).
  - `CFB_API.WHATIF_OUTCOMES`: one row per simulated season of the run: `RUN_ID`, `SIM_ID`, and one `BOOLEAN` column per remaining game, named `G<game id>`, TRUE for a home win, in the run's game-index order.
    - Each game column's comment names the week, both teams and the game id.
    - Every name and label enters the DDL text through one escaping rule, `'` doubled, so a school such as `Hawai'i` is quoted correctly.
    - The column names, their order and their comments equal those that the host reference renderer `analyses/whatif_ddl.py` produces from the same rows.
    - The view reads the run's outcome table (`SIM_RUN.PATH_TABLE`, `sim-engine/sim-runs`). Phase 4 stores one layout for both targets (its probe 1.1c); the view renames layout-C columns, or decodes a layout-A vector with `SUBSTR`, and consumers see the same columns either way.
  - `CFB_API.WHATIF_PLAYOFF`: one row per simulated season and playoff team of the run (the run's `SIM_TEAM` rows): `SIM_ID`, `TEAM_ID`, `SEED`, `BYE`, `REACHED_ROUND`, `CHAMPION`.

  Before the first publication, and on a fresh deploy, the three views exist without rows, and `WHATIF_OUTCOMES` has only `RUN_ID` and `SIM_ID`.
- **When the views lag.** The generated views name the run of their last regeneration. When `WHATIF_PUBLICATION` has moved on without one (a restore, a dropped run), `CFB_API.CURRENT_POINT.WHATIF_VIEWS_CURRENT` is FALSE and `WHATIF_GAMES` has no rows, so no toggle is offered on stale columns. `make analyses` or the next publication brings them back.
- `CFB_API.WHATIF_GAMES`: one row per remaining game of the what-if publication's run, while the generated `WHATIF_RUN` names the same run (otherwise none): `RUN_ID`, `GAME_ID`, `OUTCOME_COLUMN` (`G<game id>`), `WEEK`, `WEEK_SEQ`, `IS_NEXT_WEEK` (`WEEK_SEQ` = `NEXT_WEEK_SEQ`), `START_AT`, both teams' ids and schools, `IS_NEUTRAL_SITE`, `IS_CONF_GAME`, `MU`, `SIGMA`, `HOME_WIN_PROB` (`CFB_SIM.MARGIN_WIN_PROB(MU, SIGMA)`) and `SIM_HOME_WIN_SHARE` (the share of the run's seasons with a home win).
- `CFB_API.ANALYSIS_SETTINGS`: one row per setting of `CFB_PRED.ANALYSIS_SETTINGS`: `MIN_EFFECTIVE_SAMPLE` 1,000 (a probability's standard error is then at most 1.6 points), `MIN_BRANCH_SAMPLE` 100 (`analyses/rooting-guide`), `MOVE_THRESHOLD` 0.0070 (`analyses/published-odds`) and `ALL_TIME_FROM_SEASON` 1978 (`analyses/upsets`).
- **Conditions** are a list of (game id, home team wins TRUE or FALSE) on games of `WHATIF_GAMES`. Exasol has no parameterized views, so the host library `analyses/whatif.py` builds the query for a run and a list of conditions:
  - `what_if_odds_sql(run_id, conditions, games)`, given the run id and the game ids of `WHATIF_GAMES`;
  - `rooting_guide_sql(run_id, team_id, conditions, games)` (`analyses/rooting-guide`).

  The SQL reads only `CFB_API` views, so the app and the chat roles (`api/roles`) can run it. Before building any SQL, the library refuses, with a message naming the condition or argument: a game that is not in `games`, a game listed twice, more than 16 conditions, and a game, team or run id that is not an integer.
- **Every query names its run.** It reads only the `WHATIF_OUTCOMES` rows with that `RUN_ID`, joins `WHATIF_RUN` on it, and returns `RUN_ID` on every row. A query built for an earlier run therefore returns no rows when the games are the same, and fails with an unknown-column error when they are not. Either way the page reloads `WHATIF_RUN` and `WHATIF_GAMES` (page contract, `api/serving-contract`); it never shows one run's numbers under another run's labels.
- **Conditional odds** (`what_if_odds_sql`): one row per eligible team of the what-if run's publication (`CFB_API.PLAYOFF_ODDS` of `WHATIF_RUN.PUBLICATION_ID`):
  - `RUN_ID`, `TEAM_ID`, `SCHOOL`, `EFFECTIVE_SAMPLE` (the seasons that match every condition), and the counts `N_IN_CFP`, `N_BYE`, `N_QUARTERFINAL`, `N_SEMIFINAL`, `N_FINAL`, `N_CHAMPION` over those seasons;
  - the probabilities `P_IN_CFP` … `P_CHAMPION` (count / `EFFECTIVE_SAMPLE`, `DECIMAL(7,6)` like every probability this plan derives from simulated seasons, so one season in a million shows; NULL when the sample is 0) and `MEAN_SEED` over the team's playoff seasons among them;
  - the publication's unconditional `BASE_P_IN_CFP` and `BASE_P_CHAMPION`, and `DELTA_P_IN_CFP`;
  - `LOW_SAMPLE`: TRUE when `EFFECTIVE_SAMPLE` is below `MIN_EFFECTIVE_SAMPLE`.

  Conference titles and win totals are stored as tallies over all seasons (`sim-engine/sim-runs`), so a what-if does not change them.
- **Conditioning is not forcing.** The kept seasons are those in which the chosen results happened, and in those seasons the winners also tended to be stronger (each team's strength offset is shared by all its games, `sim-engine/engine-contract`). A conditional answer therefore carries what the result says about the teams. A forced re-sim (`analyses/forced-resim`) sets the results without that inference. Pages and the chat name the method with every number.
- Performance targets: `plan.md` § Requirements (a what-if over all teams below 1 s at p95 with 1,000,000 seasons on AWS).
- The examples run in the integration-test namespace over the sim league with a published run of 4,000 seasons in 8 batches of 500 at 2026 week 9. The league's Mountain West has the school `Hawai'i` (Phase 4's sim league). "Brookvale wins at Ashford" is the condition (week-12 game, FALSE). The test computes the expected counts on the host from the run's outcome table and `SIM_TEAM` rows (`tests/analyses/oracle.py`).

## Scenarios

### Scenario: The what-if views expose every simulated season of the published run

* *GIVEN* the published run
* *WHEN* `CFB_API.WHATIF_RUN`, `WHATIF_GAMES` and `WHATIF_OUTCOMES` are queried
* *THEN* `WHATIF_RUN` MUST name the run with 4,000 seasons and `NEXT_WEEK_SEQ` 10
* *AND* `WHATIF_OUTCOMES` MUST hold 4,000 rows and one column per remaining game of the run's `SIM_RUN_GAME`, named `G<game id>`, each equal, season by season, to the run's outcome column for that game
* *AND* the comment of the column of the week-12 game Ashford against Brookvale MUST name the week, both teams and the game id, and `WHATIF_GAMES` MUST give that game `HOME_WIN_PROB` 0.8289
* *AND* `IS_NEXT_WEEK` MUST be TRUE exactly for the week-10 games

### Scenario: School names with an apostrophe are quoted in the generated views

* *GIVEN* the published run, in which `Hawai'i` has remaining games
* *WHEN* the column comments of `CFB_API.WHATIF_OUTCOMES` are read from `EXA_ALL_COLUMNS`, and its view comment from `EXA_ALL_VIEWS`
* *THEN* the comment of each of `Hawai'i`'s game columns MUST contain `Hawai'i` with a single apostrophe
* *AND* the column names, their order, their comments and the view comment MUST equal those that `analyses/whatif_ddl.py` renders from the run's `SIM_RUN_GAME` rows and its publication

### Scenario: Conditional odds count only the matching seasons

* *GIVEN* the published run
* *WHEN* the test app role runs the SQL of `what_if_odds_sql` for the run and "Brookvale wins at Ashford"
* *THEN* `EFFECTIVE_SAMPLE` MUST equal the number of seasons in which that game's column is FALSE
* *AND* every team's counts and probabilities MUST equal the oracle's, every row MUST carry the run's `RUN_ID`, and the `N_IN_CFP` values MUST add up to 12 × `EFFECTIVE_SAMPLE`
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

### Scenario: A what-if built for an earlier run returns no rows

* *GIVEN* the published run R1 and the SQL that `what_if_odds_sql` builds for R1 and "Brookvale wins at Ashford"
* *WHEN* a manual run R2 at 2026 week 9 is published, and the same SQL runs again
* *THEN* it MUST return no rows and no error
* *AND* the SQL built for R2 MUST return one row per eligible team, each with `RUN_ID` R2

### Scenario: Invalid conditions are refused before any SQL is built

* *GIVEN* the game ids of a what-if run
* *WHEN* `what_if_odds_sql` is called with a game that is not among them, with one game listed twice, with 17 conditions, with a game id that is not an integer, or with a run id that is not an integer
* *THEN* each call MUST raise an error that names the offending condition or argument
* *AND* no SQL MUST be returned

### Scenario: Before the first publication the what-if views are empty

* *GIVEN* a freshly deployed namespace with simulation runs but no publication
* *WHEN* `CFB_API.WHATIF_RUN` and `CFB_API.WHATIF_OUTCOMES` are queried, and the SQL of `what_if_odds_sql` runs without conditions for any run id
* *THEN* all three MUST return no rows without an error
* *AND* `WHATIF_OUTCOMES` MUST have exactly the columns `RUN_ID` and `SIM_ID`

### Scenario: After a restore the what-if follows the restored point

* *GIVEN* manual runs at 2026 weeks 8 and 9, published in that order, and the stored data then restored with Phase 2's `make restore` to a snapshot taken when week 8 was the current point
* *WHEN* `CFB_PRED.WHATIF_PUBLICATION`, `CFB_PRED.POINT_PUBLICATION`, `CFB_API.CURRENT_POINT` and `CFB_API.WHATIF_GAMES` are queried, then the owner runs `make analyses ARGS="--prefix TEST_"`, and they are queried again
* *THEN* before the deploy, `WHATIF_PUBLICATION` MUST name the week-8 publication, `POINT_PUBLICATION` MUST have `IS_AFTER_CURRENT` TRUE for week 9 only, `CURRENT_POINT` MUST have `WHATIF_VIEWS_CURRENT` FALSE, and `WHATIF_GAMES` MUST have no rows
* *AND* after the deploy, `CFB_API.WHATIF_RUN` MUST name the week-8 run, `WHATIF_VIEWS_CURRENT` MUST be TRUE, and `WHATIF_GAMES` MUST list that run's remaining games
* *AND* `CFB_INFRA.PUBLISH_RUN` of the week-9 run MUST then be refused with a message that names the current point
