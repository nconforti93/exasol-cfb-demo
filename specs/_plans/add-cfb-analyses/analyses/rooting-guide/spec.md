# Feature: Rooting guide

Which games should a bubble team's fans watch this Saturday, and for whom should they cheer? The rooting guide ranks the remaining games by how much each outcome swings the team's playoff odds. Without toggles it is a lookup into counts the simulation already stored, for every remaining game of the season. With what-if toggles it is recomputed over the matching seasons for the coming week's games, which keeps it interactive at a million seasons.

## Background

- **Measures** of game g for team T over a set of seasons, from four counts: `N_HOME` and `N_AWAY` (seasons in which the home or the away team won g), `N_HOME_IN_CFP` and `N_AWAY_IN_CFP` (of those, the seasons in which T reached the playoff):
  - `P_IF_HOME_WIN` = `N_HOME_IN_CFP` / `N_HOME` and `P_IF_AWAY_WIN` = `N_AWAY_IN_CFP` / `N_AWAY` (`DECIMAL(7,6)`, NULL when the branch is empty);
  - `LEVERAGE` = `P_IF_HOME_WIN` − `P_IF_AWAY_WIN`, the mission's "swing in a team's odds between a game's two outcomes" (NULL when a branch is empty);
  - `ROOT_FOR_TEAM_ID`: the home team when `LEVERAGE` > 0, the away team when it is < 0, NULL otherwise;
  - `LOW_SAMPLE`: TRUE when the smaller branch has fewer than `MIN_BRANCH_SAMPLE` seasons (100, `CFB_PRED.ANALYSIS_SETTINGS`);
  - `IS_OWN_GAME`: T plays g.
- **Unconditional guide:** `CFB_PRED.ROOTING_GUIDE` (served as `CFB_API.ROOTING_GUIDE`): one row per `DONE` simulation run, team with at least one playoff season in the run, and remaining game of the run.
  - The counts are Phase 4's precomputed `CFB_SIM.LEVERAGE` (`sim-engine/sim-runs`), so a page reads them without scanning the seasons (S4: 0.08 s at 1,000,000 seasons on AWS).
  - It adds the measures, the game's week and teams, `IS_NEXT_WEEK` (the run's smallest remaining week sequence), `GUIDE_RANK` (by |`LEVERAGE`| descending, then kickoff, then game id, within run and team) and `NEXT_WEEK_RANK` (the same order within the next week's games; NULL for later weeks).
  - It covers every remaining game of the season and every `DONE` run that is kept: published runs, forced re-sims (`analyses/forced-resim`) and the runs of earlier weeks.
  - A team without any playoff season in the run has no rows: its odds are 0 whatever happens.
- **Conditional guide:** `rooting_guide_sql(team_id, conditions, games)` of `analyses/whatif.py` (`analyses/what-if`) counts over the seasons of the what-if run that match every condition, from the generated what-if views:
  - one row per game of `games`, minus the games a condition fixes, with `GAME_ID`, `EFFECTIVE_SAMPLE`, the four counts and the measures above; `N_HOME` + `N_AWAY` = `EFFECTIVE_SAMPLE`;
  - `games` is the coming week's games: the `WHATIF_GAMES` rows with `IS_NEXT_WEEK`.
- **Why the coming week only.** S4 measured the conditional guide over all 400 remaining games at 2.2 s p95 (k = 3, 1,000,000 seasons on AWS), against the 1 s target; the cost sits in counting each game's column over the matching seasons. The cost should follow the number of game columns counted, because Exasol stores tables by column (not stated in the Exasol skills; the probe measures it). Restricting the guide to the coming week (about 60 games at the demo's points) should then cut the work about sevenfold. The design target is < 1 s p95 at k ≤ 3 on AWS, measured by probe 1.1(a) before anything is built on it and again on real data in task 7.2. The fallbacks are in `plan.md`.
- A full-season guide under chosen results is the unconditional guide of a forced re-sim with those results (`analyses/forced-resim`).
- The examples run in the integration-test namespace over the sim league with a published run of 4,000 seasons in 8 batches of 500 at 2026 week 9, for team Brookvale. "Brookvale wins at Ashford" is the condition (week-12 game, FALSE). The test computes the expected counts on the host (`tests/analyses/oracle.py`).

## Scenarios

### Scenario: The unconditional guide reads the precomputed counts

* *GIVEN* the published run
* *WHEN* `CFB_API.ROOTING_GUIDE` is queried for the run and Brookvale
* *THEN* it MUST hold one row per remaining game, with the four counts equal to `CFB_SIM.LEVERAGE` for Brookvale and that game
* *AND* `P_IF_HOME_WIN`, `P_IF_AWAY_WIN`, `LEVERAGE` and `ROOT_FOR_TEAM_ID` MUST follow from those counts as defined
* *AND* the row with `GUIDE_RANK` 1 MUST have the largest |`LEVERAGE`|

### Scenario: The guide marks the coming week's games and the team's own games

* *GIVEN* the published run
* *WHEN* `CFB_API.ROOTING_GUIDE` is queried for the run and Brookvale
* *THEN* `IS_NEXT_WEEK` MUST be TRUE exactly for the week-10 games, and `NEXT_WEEK_RANK` MUST number only those, from 1
* *AND* `IS_OWN_GAME` MUST be TRUE exactly for Brookvale's games, the week-12 game at Ashford among them

### Scenario: With what-if conditions the guide counts only the matching seasons

* *GIVEN* the published run
* *WHEN* the test app role runs the SQL of `rooting_guide_sql` for Brookvale, the condition "Brookvale wins at Ashford" and the week-10 games
* *THEN* it MUST return one row per week-10 game, with `EFFECTIVE_SAMPLE` and the four counts equal to the oracle's over the matching seasons
* *AND* `N_HOME` + `N_AWAY` MUST equal `EFFECTIVE_SAMPLE` in every row
* *AND* `LOW_SAMPLE` MUST be TRUE exactly where the smaller branch has fewer than 100 seasons

### Scenario: Without conditions the computed guide equals the precomputed one

* *GIVEN* the published run
* *WHEN* the SQL of `rooting_guide_sql` runs for Brookvale without conditions and for the week-10 games
* *THEN* every row's counts MUST equal those of `CFB_API.ROOTING_GUIDE` for the run, Brookvale and that game

### Scenario: A game fixed by a condition leaves the conditional guide

* *GIVEN* the published run and one week-10 game G
* *WHEN* the SQL of `rooting_guide_sql` runs for Brookvale with the condition "the home team wins G" and the week-10 games
* *THEN* it MUST NOT return a row for G
* *AND* it MUST return a row for every other week-10 game
