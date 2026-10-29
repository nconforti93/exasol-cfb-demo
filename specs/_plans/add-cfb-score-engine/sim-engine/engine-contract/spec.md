# Feature: Simulation engine contract

The playoff odds come from simulating the rest of a season many times, inside the database, in plain Python. One engine call plays one batch of seasons: it samples the remaining games, crowns the conference champions, ranks the teams, selects and seeds the 12-team field and plays the bracket. Batches run in parallel on every core. The same inputs and seed always give the same seasons, however the batches are scheduled. A team's strength can drift away from its rating, and that drift is shared by all of its remaining games, so a simulated season can run hot or cold for a team, as real seasons do. The engine also tallies, per batch, everything the odds, the what-if filter and the rooting guide need, so nothing has to be recomputed from the stored seasons afterwards.

## Background

- **Call shape** (D19): `CFB_INFRA.SIM_ENGINE`, a Python 3 `SET` script with explicit `EMITS` (the columns of `CFB_SIM.SIM_STAGE`), is called once per batch over the run's model rows cross-joined with the batch ids, `GROUP BY` batch id. Its body is `engine/core.py` and `engine/udf.py`, and its comment carries `ENGINE_VERSION`, the first 16 hex digits of the SHA-256 of the body.
  - Every group receives every model row of the run, in no guaranteed order; the engine sorts by index.
  - The run columns `RUN_ID`, `RUN_SEED`, `SIMS_PER_BATCH` and `BATCH_ID` are constant within a call.
  - Concurrency is `nrOfCores` instances per node.
- **Model rows** (`ROW_KIND`; columns that do not apply to a kind are NULL; real values are fixed-scale `DECIMAL`, never `DOUBLE`):
  - `TEAM`, one per team, with `TEAM_IDX` 0 to T − 1: `TEAM_ID`, `CONFERENCE` (NULL for independents and non-FBS teams), `TIER` (`P4`, `G6`, `IND` or `NON_FBS`), `HAS_CCG`, `IS_ELIGIBLE`, `RATING`, `SOS`, `WINS`, `LOSSES`, `CONF_WINS`, `CONF_LOSSES`, `CONF_PD` and `CAPPED_PD` so far.
  - `GAME` with `GAME_ROLE` `REMAINING`: `GAME_IDX` 0 to G − 1 (the position in the outcome string), `HOME_IDX`, `AWAY_IDX`, `CONF_GAME`, `MU`, `TEAM_SIGMA`.
  - `GAME` with `GAME_ROLE` `PLAYED_CONF`: a completed conference game, `HOME_IDX`, `AWAY_IDX` and `MARGIN`, for head-to-head only.
  - `PARAM`: `PARAM_KEY`, `PARAM_VALUE`. Required keys: `hfa`, `game_sigma`, `post_team_sigma`, `ot_margin`, `margin_cap`, `w_win_pct`, `w_sos`, `w_pd`, `w_rating`, `auto_bid_rule`, `auto_bids`, `g6_bid`, `guarantee_team_idx`, `guarantee_rank`, `min_conference_teams`. The only optional key is `fail_batch`, a test hook that makes that batch fail; only the integration-test namespace sets it.
- **Validation:** the engine rejects an invalid model and never repairs it; the error names the rule and the offending rows. It rejects an unknown row kind, role or key, a missing key, run columns that are not constant, indices out of range or not dense, a game whose home team is its away team, `game_sigma` ≤ 0, a negative `TEAM_SIGMA` or `post_team_sigma`, a conference game between teams of different conferences, fewer than 12 eligible teams, and a rule code outside its values. G = 0 (no remaining game) is valid.
- **Randomness** (fixed-shape draws, so a change to one game never shifts the draws of another):
  - Each batch has the seed sequence `SeedSequence([RUN_SEED, BATCH_ID])`, which spawns two children, each feeding a `Generator(Philox(child))`. The season stream draws `standard_normal((S, G))`, then `random((S, G))`, then the team offsets `standard_normal((S, T))`. The postseason stream draws `standard_normal((S, C + 11))`, then `random((S, C + 11))`.
  - S is `SIMS_PER_BATCH`; C is the number of distinct non-NULL conferences of the `TEAM` rows, in sorted name order. Postseason columns 0 to C − 1 are the conferences' championship games, C to C + 10 the 11 bracket games.
  - `SIM_ID` = `BATCH_ID` × `SIMS_PER_BATCH` + k, with k from 0 to S − 1.
- **Margins** (home minus away, whole points). Each simulated season gives each team t one offset d_t.
  - A remaining game: `rint(MU + TEAM_SIGMA × (d_home − d_away) + game_sigma × z)`, rounding half to even. Its marginal standard deviation is the score model's `SIGMA`, so its marginal win probability is the score model's `HOME_WIN_PROB` (`sim-engine/score-model`).
  - A margin of 0 goes to overtime: `+ot_margin` if the game's uniform is below 0.5, else `−ot_margin`.
  - A simulated championship or bracket game: `rint(m + post_team_sigma × (d_first − d_second) + game_sigma × z_post)`, where m is the rating difference of the first-listed team (the better standings team or seed) over the second, plus `hfa` for the better seed's home game in the first round. A margin of 0 goes to the first-listed team if the column's uniform is below 0.5.
- **Per simulated season:** the regular season, conference standings, championship games and champions, the resume ranking, selection and seeding, and the bracket, by `sim-engine/selection-rules`. Wins in the outputs count regular-season games only.
- **Outputs:** one `pandas.DataFrame` per batch with the columns of `SIM_STAGE`, tagged by `ROW_KIND`. Columns that do not apply to a kind carry a sentinel (0, FALSE, or NULL in the text columns), never a numeric NULL:

  | `ROW_KIND` | One row per | Columns |
  |---|---|---|
  | `T` | simulated season and playoff team | `SIM_ID`, `TEAM_ID`, `SEED` 1–12, `BYE` (seeds 1–4), `REACHED_ROUND` (1 first round, 2 quarterfinal, 3 semifinal, 4 final; seeds 1–4 start at 2), `CHAMPION` |
  | `P` | simulated season | `SIM_ID`, `OUTCOMES`: one ASCII character per remaining game in `GAME_IDX` order, `1` = home win, `0` = away win; NULL when G = 0 |
  | `W` | team and regular-season win total | `TEAM_ID`, `WINS`, `N` (seasons of the batch with that total) |
  | `C` | team that won its conference at least once | `TEAM_ID`, `N` |
  | `G` | remaining game | `GAME_IDX`, `N` (seasons with a home win) |
  | `L` | team and remaining game, where the count is above 0 | `TEAM_ID`, `GAME_IDX`, `N` (seasons in which the team made the field and the home team won) |
  | `B` | batch | `INFO`: `vm=<exa.meta.vm_id>;node=<node id>;digest=<model digest>;checksum=<batch checksum>;numpy=<version>;python=<version>;ms=<engine milliseconds>` |

  The `L` counts are one matrix product per batch (in-field indicator by team, transposed, times home-win indicator by game), computed in floating point, which is exact for counts below 2²⁴.

## Scenarios

### Scenario: A batch is determined by its seed, its id and its size

* *GIVEN* a crafted model with 40 teams and 60 remaining games
* *WHEN* `engine/core.py` simulates batch 3 of seed 77 with 500 seasons twice, and batch 4 once
* *THEN* both runs of batch 3 MUST produce identical output rows and the same checksum
* *AND* batch 4 MUST produce different outcome strings
* *AND* the `SIM_ID`s of batch 3 MUST run from 1,500 to 1,999

### Scenario: Shared team strength widens win totals but keeps each game's probability

* *GIVEN* a crafted model in which one team plays 8 remaining home games against 8 different opponents, each with `MU` 0, `TEAM_SIGMA` 7.07 and `game_sigma` 15
* *AND* the same model with `TEAM_SIGMA` 0 and `game_sigma` 18.03, the same marginal uncertainty
* *WHEN* each is simulated for 20,000 seasons
* *THEN* the variance of the team's simulated wins MUST lie between 3.1 and 3.7 with the shared offsets, and between 1.8 and 2.2 without them
* *AND* every game's home-win share MUST lie between 0.48 and 0.52 in both

### Scenario: Every simulated season produces a complete playoff

* *GIVEN* a crafted model with four power conferences, five Group of Six conferences, two independents and non-FBS opponents
* *WHEN* `engine/core.py` simulates 2,000 seasons
* *THEN* every season MUST have exactly 12 `T` rows with 12 distinct eligible teams, seeds 1 to 12 each once, and `BYE` exactly for seeds 1 to 4
* *AND* every season MUST have 12, 8, 4 and 2 teams with `REACHED_ROUND` at least 1, 2, 3 and 4, and exactly one `CHAMPION`, whose `REACHED_ROUND` is 4
* *AND* no non-FBS team MUST ever appear in a `T` row

### Scenario: The tallies agree with the simulated seasons

* *GIVEN* the batch of the previous scenario
* *WHEN* its `W`, `C`, `G` and `L` rows are recounted on the host from its `T` and `P` rows and its per-season wins and champions
* *THEN* each team's `W` counts MUST sum to the number of seasons
* *AND* each `G` count MUST equal the number of `1` characters at that game's position in the `P` rows
* *AND* each `L` count MUST equal the number of seasons whose `T` rows hold the team and whose outcome string has a `1` at that game's position
* *AND* every `P` row MUST have exactly G characters

### Scenario: An invalid model is rejected, not repaired

* *GIVEN* three crafted models: one without the `game_sigma` parameter, one with a game whose `HOME_IDX` is outside the teams, and one with 11 eligible teams
* *WHEN* `engine/core.py` is asked to simulate each
* *THEN* each call MUST raise an error naming its rule (`missing parameter game_sigma`, `index out of range`, `fewer than 12 eligible teams`)
* *AND* no output row MUST be produced

### Scenario: The database computes what the host computes

* *GIVEN* a deployed engine and a stored run of the synthetic league at 2026 week 9 with 2 batches of 200 seasons
* *WHEN* the run's snapshot rows are read back and simulated on the host with `engine/core.py`
* *THEN* the host's `T` rows and outcome strings MUST equal the run's `SIM_TEAM` and `SIM_PATH` rows
* *AND* the host's model digest MUST equal the run's `MODEL_DIGEST`
