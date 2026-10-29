# Feature: Simulation engine contract

The playoff odds come from simulating the rest of a season many times, inside the database, in plain Python. One engine call plays one batch of seasons: it samples the remaining games and the conference championship games, ranks the teams, selects and seeds the playoff field, and plays the bracket. Batches run in parallel on every core. The same inputs and seed always give the same seasons, however the batches are scheduled, so every run can be repeated and checked. A team's strength drifts away from its rating as the weeks pass, and that drift is shared by all of its remaining games, so a simulated season can run hot or cold for a team, as real seasons do.

## Background

- **Call shape** (D19, `specs/architecture.md` § Engine contract): `CFB_INFRA.SIM_ENGINE`, a Python 3 `SET` script with explicit `EMITS`, is called once per batch over the run's model rows cross-joined with the batch ids, `GROUP BY` batch id.
  - Every group receives every model row of the run. Row order inside a group is not guaranteed, so the engine sorts by index.
  - The run columns `RUN_ID`, `RUN_SEED`, `SIMS_PER_BATCH`, `BATCH_ID` and `EMIT_MODE` (`FULL` or `SUMMARY`) are constant within a call.
  - Concurrency is `nrOfCores` instances per node. It is measured per run, not assumed (`sim-engine/sim-runs`).
- **Model rows** (`ROW_KIND`). Columns that do not apply to a kind are NULL. Real values are fixed-scale `DECIMAL`, never `DOUBLE`, so the host and the database see bit-identical values.
  - `TEAM`, one per team, with `TEAM_IDX` from 0 to T − 1:
    - `TEAM_ID`, `CONFERENCE` (NULL for independents and non-FBS teams) and `TIER` (`P4`, `G6`, `IND` or `NON_FBS`);
    - `HAS_CCG`: the team's conference holds a championship game this season;
    - `ELIGIBLE`: FBS teams are eligible for the playoff; non-FBS opponents are not;
    - `RATING` and `SOS` (`DECIMAL(6,2)`);
    - `WINS`, `LOSSES`, `CONF_WINS` and `CONF_LOSSES` so far, regular season only, without conference championship games;
    - `CONF_PD` (conference point differential so far) and `CAPPED_PD` (point differential so far, each game capped at `margin_cap`).
  - `GAME`, by `GAME_ROLE`:
    - `REMAINING`: a regular-season game up to the season's selection week (`sim-engine/sim-runs`), with `GAME_IDX` from 0 to G − 1 (the position in the outcome vector), `HOME_IDX`, `AWAY_IDX`, `NEUTRAL`, `CONF_GAME`, `MU` (`DECIMAL(7,2)`, home minus away), `SIGMA` (`DECIMAL(5,2)`, the margin's total uncertainty), `TEAM_SIGMA` (`DECIMAL(5,2)`, the scale of the shared team offsets at the game's horizon), `TOTAL` (carried, unused) and `FIXED_HOME_WIN` (NULL means drawn);
    - `PLAYED_CONF`: a completed regular-season conference game, with `HOME_IDX`, `AWAY_IDX` and `MARGIN`, used only for head-to-head;
    - `CCG`: a conference championship game whose teams are known, with `HOME_IDX`, `AWAY_IDX`, `MU`, `SIGMA` and `TEAM_SIGMA`, plus `MARGIN` once it is played.
  - `PARAM`: `PARAM_KEY`, `PARAM_VALUE` (`DECIMAL(12,4)`). Every key is required and the engine has no defaults:
    - `hfa`, `game_sigma` (the per-game noise of every simulated game), `post_team_sigma` (the team-offset scale of the simulated championship and bracket games), `ot_margin`, `margin_cap`;
    - the resume weights `w_win_pct`, `w_sos`, `w_pd`, `w_rating`;
    - the selection rules `auto_bid_rule`, `auto_bids`, `g6_bid`, `bye_rule`, `guarantee_team_idx`, `guarantee_rank` and `min_conference_teams` (`sim-engine/selection-rules`).
    - `fail_batch` is the only optional key: a test hook that makes the given batch fail. Only the integration-test namespace sets it.
- **Validation.** The engine rejects an invalid model and never repairs it. The error names the rule and the offending rows. It rejects:
  - an unknown row kind, game role or parameter, or a missing parameter;
  - run columns that are not constant;
  - indices that are out of range or not dense;
  - a game whose home team is its away team, with `SIGMA` ≤ 0 or `TEAM_SIGMA` < 0, or whose `SIGMA` differs from √(`game_sigma`² + 2 × `TEAM_SIGMA`²) by more than 0.02;
  - `game_sigma` ≤ 0 or `post_team_sigma` < 0;
  - a conference game between teams of different conferences;
  - a conference pair that meets more than once in `PLAYED_CONF` and `REMAINING` games together;
  - a `CCG` row for a conference without `HAS_CCG`, two `CCG` rows for one conference, or a `CCG` between teams of different conferences;
  - fewer than 12 eligible teams;
  - a selection-rule code outside its values.
- **Randomness** (S3 finding M2: every draw has a fixed shape, so a change to one game never shifts the draws of another).
  - Each batch has its own seed sequence: `SeedSequence([RUN_SEED, BATCH_ID])`. It spawns two children, each feeding a `Generator(Philox(child))`:
    - the season stream draws `standard_normal((S, G))`, then `random((S, G))`, then the team offsets `standard_normal((S, T))`;
    - the postseason stream draws `standard_normal((S, C + 11))` and then `random((S, C + 11))`.
  - S is `SIMS_PER_BATCH`, G the number of remaining games and T the number of teams.
  - C is the number of distinct non-NULL `CONFERENCE` values of the `TEAM` rows, in sorted name order. It counts every conference, also one below `min_conference_teams` or without a championship game, so a conference crossing the size threshold never shifts the bracket columns. Postseason columns 0 to C − 1 belong to the conferences, columns C to C + 10 to the bracket: 4 first-round games, 4 quarterfinals, 2 semifinals and the final.
  - `SIM_ID` = `BATCH_ID × SIMS_PER_BATCH + k`, with k from 0 to S − 1.
- **Margins** (home minus away, whole points). Each simulated season gives each team t one offset d_t, the team-offset draw of that season.
  - A remaining game's margin is `rint(MU + TEAM_SIGMA × (d_home − d_away) + game_sigma × z)`, rounding half to even, with z the game's season normal. The offsets of both teams have variance 1, so the margin's marginal standard deviation is √(`game_sigma`² + 2 × `TEAM_SIGMA`²) = `SIGMA`, and the game's marginal win probability is the score model's `HOME_WIN_PROB` (`sim-engine/score-model`). A team's games are correlated through its offset.
  - A margin of 0 goes to overtime: `+ot_margin` if the game's uniform is below 0.5, else `−ot_margin`. No game ends tied.
  - **Fixed result:** a game with `FIXED_HOME_WIN` set keeps its winner. Its margin is drawn from the normal distribution of `MU` and `SIGMA` restricted to the winner's side, through the inverse normal CDF of the game's uniform mapped into that side. The inverse CDF's argument is clamped to [1e−12, 1 − 1e−12], and the margin is at least 1 point on the winner's side. It ignores the team offsets and uses no other draw, so forcing a result changes only its own cell.
  - A **known championship game** (a `CCG` row that is not played) uses its own `MU`, `SIGMA` and `TEAM_SIGMA`, the conference's postseason column and the formula above. A played one keeps its `MARGIN`.
  - **Simulated championship games and bracket games** use the postseason stream: margin = `rint(m + post_team_sigma × (d_first − d_second) + game_sigma × z_post)`, where m is the rating difference (plus `hfa` for the better seed's home field in the first round), and the first-listed team is the better standings team or seed. A margin of 0 goes to the first-listed team if the column's uniform is below 0.5.
- **Per simulated season:**
  1. the regular season up to the selection week, with records, conference records and point differentials;
  2. conference standings with the simplified tiebreakers;
  3. the conference championship games and the champions;
  4. the resume ranking;
  5. selection and seeding;
  6. the 12-team bracket.

  Steps 2 to 6 follow `sim-engine/selection-rules`. Wins in the outputs count regular-season games up to the selection week only.
- **Outputs per batch** (`EMIT_MODE` `FULL`). One stage row shape, emitted as a DataFrame (D19):
  - one row per playoff team per simulated season: `SIM_ID`, `TEAM_ID`, `SEED` (1–12), `BYE` (seeds 1–4), `REACHED_ROUND` (1 first round, 2 quarterfinal, 3 semifinal, 4 final) and `CHAMPION`. That is 12 rows per season; teams outside the playoff have no row (D20, sparse);
  - one outcome row per simulated season: `SIM_ID` and `OUTCOMES`, one character per remaining game in `GAME_IDX` order, `1` for a home win and `0` for an away win. With no remaining game (G = 0) the string is empty, which Exasol stores as NULL;
  - tallies over the batch's seasons: regular-season wins per team (`TEAM_ID`, `WINS`, `N`), and conference titles per team (`TEAM_ID`, `N`);
  - one batch summary: the batch id and its number of seasons; a `CHECKSUM` (SHA-256 over the sim ids, the outcomes and the playoff rows); the `MODEL_DIGEST` (SHA-256 over the parsed model, including team and conference names, every array with its type, and the parameters); the `ENGINE_VERSION` embedded in the script; the numpy and Python versions; seconds per stage; the VM id and node id; and the peak memory.

  `EMIT_MODE` `SUMMARY` emits only the batch summary.
- **Reproducibility key:** `RUN_SEED`, `SIMS_PER_BATCH`, the number of seasons, the numpy and Python versions, `MODEL_DIGEST` and `ENGINE_VERSION`. A different batch size is a different run, not a regrouping.
- The examples use crafted models on the host, plus the sim league in the integration-test namespace, as stated per scenario.

## Scenarios

### Scenario: A batch is determined by its seed, its id and its size

* *GIVEN* a crafted model with 24 teams in 4 conferences and 60 remaining games
* *WHEN* batch 3 of run seed 41 is simulated twice with 500 seasons per batch, then once with 1,000
* *THEN* both 500-season results MUST be identical in every output and in their checksum
* *AND* their sim ids MUST run from 1,500 to 1,999
* *AND* batch 4, run seed 42 and the 1,000-season batch MUST each give different outcomes

### Scenario: A run does not depend on how its batches are scheduled

* *GIVEN* the sim league deployed in the integration-test namespace and a run of 4,000 seasons in 8 batches at 2026 week 9
* *WHEN* the engine is called again on the run's stored inputs, once for all 8 batches and once for batches 5 and 2 alone, in that order
* *THEN* every batch MUST report the same checksum and model digest as in the run

### Scenario: The database and the host compute the same batches

* *GIVEN* the run of the previous scenario
* *WHEN* the host engine simulates the same batches from the run's stored inputs, read back from the database
* *THEN* each host checksum MUST equal the checksum the database run recorded for that batch
* *AND* the host model digest MUST equal the run's model digest

### Scenario: Changing one game leaves every other draw unchanged

* *GIVEN* the crafted model, and a copy in which only the predicted margin of game 0 changes, from 0.4 to 0.6
* *WHEN* batch 0 of run seed 7 is simulated for both
* *THEN* the season normals and uniforms of every game, the team offsets and both postseason blocks MUST be identical
* *AND* every outcome of the games other than game 0 MUST be identical in every season

### Scenario: Shared team strength widens the spread of season win totals

* *GIVEN* a crafted model in which team 0 hosts 8 remaining games against 8 different opponents, each with `MU` 0, `SIGMA` 18.03 and `TEAM_SIGMA` 7.07, and `game_sigma` 15.00
* *AND* a copy of it with `TEAM_SIGMA` 0 in every game and `game_sigma` 18.03
* *WHEN* one batch of 20,000 seasons of each is simulated
* *THEN* each of the 8 games MUST be a home win in a share of seasons within 0.02 of 0.5 in both models
* *AND* the variance of team 0's wins in these 8 games MUST be within 0.15 of 2.00 in the copy, and at least 3.0 with the team offsets

### Scenario: A tied margin goes to overtime

* *GIVEN* a remaining game with a predicted margin of 0, an uncertainty of 0.1 and no team-offset scale, `game_sigma` 0.1 and `ot_margin` 3
* *WHEN* 1,000 seasons are simulated
* *THEN* every simulated margin of that game MUST be +3 or −3, never 0
* *AND* the home team MUST win exactly in the seasons in which that game's uniform is below 0.5

### Scenario: A fixed result decides the game on the winner's side

* *GIVEN* a remaining game with a predicted home margin of 10 and an uncertainty of 14, fixed as an away win
* *WHEN* 2,000 seasons are simulated
* *THEN* the away team MUST win that game in every season
* *AND* every margin MUST be −1 or lower
* *AND* the draws of every other game and the team offsets MUST be identical to the same batch without the fixed result

### Scenario: An invalid model is rejected, not repaired

* *GIVEN* a crafted model in which one remaining game has an uncertainty of 0, and one conference pair meets twice
* *WHEN* the engine runs a batch of it
* *THEN* the call MUST fail with an error that names both rules and the offending games
* *AND* it MUST NOT emit any row

### Scenario: Every simulated season produces a complete playoff

* *GIVEN* the crafted model with the 2026 selection rules
* *WHEN* 500 seasons are simulated
* *THEN* each season MUST have exactly 12 playoff rows with seeds 1 to 12 once each, byes exactly for seeds 1 to 4, and only eligible teams
* *AND* each season MUST have exactly one champion with `REACHED_ROUND` 4, and 12, 8, 4 and 2 teams that reached rounds 1 or more, 2 or more, 3 or more, and 4
* *AND* the wins tallies of each team MUST add up to 500 seasons
