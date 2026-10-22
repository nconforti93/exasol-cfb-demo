# Plan: add-cfb-score-engine

## Summary

Roadmap Phase 4 ("Score model + engine"): a calibrated score model, the 2026 playoff selection rules, and the vectorized Python season simulator inside Exasol with a run registry, plus a backtest of the 2024 and 2025 playoff fields. Demo beat: "100k seasons in N seconds, in parallel on every core, in plain Python inside the database", with the sim count as the scale dial (laptop 100k, AWS 1M).

Revised on 2026-10-22 to resolve the independent review (`review.md`, § Review resolution).

- **Score model** (`CFB_SIM`, SQL views). A linear margin model on the previous season's SP+, the current Elo and home field, with normal margins whose uncertainty grows with the weeks until the game.
  - It is fitted on 2014–2025 by `make calibrate`, which writes reviewed seed files and reports MAE, Brier score and calibration next to the betting line, separately for the early, mid and late season.
  - The gate's tolerances are at least three standard errors wide, so a good model does not fail by chance.
  - A `HOLDOUT_2023` fit keeps the backtest honest.
  - Elo-based win probability covers every game back to 1890, and the upset magnitude follows from it.
- **Selection rules.** Rule sets for 2024, 2025 and 2026, configured from a reviewed table:
  - 2026 (from the January 2026 CFP decision): the 4 power-conference champions, the best-ranked Group of Six team (the Pac-12 included), Notre Dame if top 12, 7 at-large; straight seeding with top-4 byes;
  - simplified tiebreakers;
  - a resume formula as the committee, with weights fitted to the committee's final rankings;
  - each season's selection week: the week of its power-conference championship games. Later regular-season games, such as Army–Navy, are not simulated.
- **Engine** (`engine/`, from the S3 prototype; D19, D20 binding).
  - One Python `SET` UDF with explicit `EMITS`, seeded per batch, with a fixed-shape draw layout (common random numbers).
  - Per simulated season, each team gets a strength offset shared by all its remaining games, so win totals and playoff odds are as spread as reality. Single-game probabilities are unchanged.
  - It runs once per batch and feeds every output from that single pass:
    - sparse `SIM_TEAM`;
    - a per-run outcome table (layout C, or layout A if the group-A probe says so);
    - per-run `LEVERAGE`;
    - win and title tallies.
- **Runs.** `CFB_INFRA.SIMULATE` snapshots its inputs, joins the loader's run lock, cleans up runs that were killed, and records the reproducibility key, the machine sizing and the seconds of every step. `make sim-verify` re-proves any run from its stored inputs. `CFB_INFRA.SIMULATE_FROM` re-simulates a stored run with forced results and the same draws.
- **Refresh step** 300 `SIMULATE` after the model step. **Backtest** `make backtest` against the actual 2024/2025 fields, with a calibration check over every team.

Everything runs at zero CFBD calls. Tests run on a synthetic "sim league" in the `TEST_` namespace. The decisive layout probe runs early on synthetic data, locally and on AWS. Live verification runs on AWS after Phase 2's backfill, read only, and on local after a restore.

## Design

### Context

The engine design is settled by the finished spikes, which this plan treats as binding (orchestrator interview, item 1):

- **S3 → D19** (`docs/spikes/s3-python-engine-throughput.md`, `spikes/s3/results/`): Python engine GO.
  - Compute-only 100k sims: 7.06 s local (8 cores, 10k/batch; 7.11 s at 5k/batch) and 1.55 s on AWS (3 × 4, 1k/batch). These figures include the fixed cost per statement (local 3.2 s, AWS 0.8 s).
  - Dense full mode: 55.4 s local [40–76 s] and 12.4 s on AWS. Emission is 75 % of the wall; `SIM_TEAM` emission alone is 38.3 s locally.
  - `sims_per_batch` 1k–10k, at least 2 batches per slot.
  - DataFrame emit; explicit `EMITS`; one engine pass per batch.
  - Seeding per `(run_seed, batch_id)`, with `sims_per_batch` in the key.
  - Fixed-scale `DECIMAL` inputs.
  - Review finding M2: the prototype's draw order breaks common random numbers and must change.
- **S4 → D20** (`docs/spikes/s4-sim-storage.md`, `spikes/s4/results/`): `SIM_PATH` layout C (A is the near-tie fallback). `SIM_TEAM` is sparse, in-CFP rows only.
  - What-if p95 at 1M on AWS: 0.52 s at k = 1, 0.21 s at k = 3.
  - A per-run `LEVERAGE` built after the load (0.078 s lookup, 4.75 s build at 1M) and a `TEAMS` roster.
  - No per-sim margins.
  - Loads at 1M on AWS: C 30.6 s, A 7.2 s, sparse derivation 0.41 s (12M rows by `INSERT … SELECT`, n = 1).
- **`specs/architecture.md` § Engine contract:**
  - tagged `MODEL_ROWS` input;
  - the reproducibility key;
  - fixed results with a conditional margin;
  - the output shape.
- **`CLAUDE.md` § Environment:** builtin SLC Python 3.12.3 with numpy 1.26.4 (host venvs pinned to it); `nrOfCores` concurrency, auto-sized at each Personal start; `exa.meta.vm_id` because `getpid()` is 1; Exasol's string-to-DOUBLE parse is 1 ulp off.
- **Phase 2 loader probe** (`../exasol-cfb-demo-p2/docs/spikes/s5-loader-probe.md`, 1.1i/1.1j, the run lock recipe):
  - liveness comes from `EXA_ALL_SESSIONS`, readable by non-DBA users;
  - a running script is never `IDLE`, while a host session is idle between statements;
  - after `KILL STATEMENT` every later statement of the script fails with `R0003`, so a killed run cannot log its own end;
  - a takeover `UPDATE … ABANDONED` does not wait on the dead run.

The inputs come from Phase 3 (`specs/_plans/add-cfb-analytical-model/`, decision [31]):
- `GAME_AS_OF` (neutral site, conference flag, `MARGIN`, `PARTICIPANTS_RULE`, `IS_CONF_CHAMPIONSHIP`, `POSTSEASON_KIND`, `CFP_ROUND`);
- `TEAM_SEASON_AS_OF` (conference, tier, classification, records, conference records, `CONF_POINT_DIFF`, `ELO_AS_OF`, `SP_PRIOR_RATING`);
- `RATING_SP`, `GAME`, `SEASON_WEEK`, `CONFERENCE_CHAMPIONSHIP_GAME`, `RANKING` (the committee's final ranking), and the reference tables `CONFERENCE_CHAMPIONSHIP`, `CONFERENCE_TIER` and `POLL`.

All of it is fixed-scale `DECIMAL`. Phase 3 deliberately leaves indices, μ, σ, totals, ratings, strength of schedule and capped differentials to this phase. Result-dependent games are hidden until their participants are known, which keeps the backtest leak-free by construction.

Phase 2 (`specs/_plans/add-cfb-persistence-refresh/`) provides `REFRESH_WEEK` with registered steps ≥ 100 called as `(run_id, season, week, season_type)`, the run lock in `LOAD_RUN` with stale-row rules, the Lua bundler and the IT harness. Phase 3 provides the SQL-object deployer (`vs/host/sqlobjects.py`), the synthetic league machinery and step 100 `MODEL`.

**2026 CFP rules (web research, 2026-10-22; confirmed by the review):**
- The CFP management committee decided in January 2026 to stay at 12 teams for 2026.
- Automatic bids go to the champions of the SEC, Big Ten, ACC and Big 12, and to one Group of Six team; the Pac-12 counts as a Group of Six conference. Notre Dame is guaranteed a place if it is in the final top 12. Seven places are at-large.
- Seeding has been straight since 2025, with the top 4 getting byes.
- Sources conflict on the G6 bid: On3/Yahoo (2026-01-23) say "highest-ranked G6 champion"; SI citing The Athletic (2026-01-27) and an NCAA explainer (2026-02-03) say "highest-ranked G6 team, champion or not". This plan takes the team rule, configurable, and checks cfp.org before the first committee ranking of 2026 (task 1.11, Q1).
- 2024 and 2025 gave automatic bids to the five best-ranked conference champions. 2024 gave byes to the four best-ranked champions; in 2025, ACC champion Duke was left out behind Tulane and James Madison.

**Selection weekend on real data (checked during the revision, zero calls, from the stored replay recordings in `cache/replay/`):**
- 2024 (`/games?seasonType=both&year=2024`, keyed on the game `ID`):
  - CFBD's regular week 15 holds the nine conference championship games (6–7 December).
  - Week 16 holds one FBS game, Army–Navy on 14 December, after the committee's selection.
  - The committee's selection ranking (`isFinal`) is labeled regular week 16.
  - So `LAST_REGULAR_WEEK_SEQ` is 16, and the selection week is 15.
- 2026 (`/games?seasonType=both&year=2026`):
  - The stored schedule has regular weeks 1–13, then Army–Navy alone in week 15 (12 December), and no week-14 rows: the championship games are not listed until their pairings are set.
  - Army–Navy is flagged as an American Athletic conference game in 2026 (not in 2024).
- 2025 is not on disk; it is checked on the stored data in 6.1.

Forces:
- **Emission dominates the wall time** (S3).
  - Every extra output statement costs a full engine pass.
  - Layout C emitted directly from the UDF costs 4× layout A at 1M (30.6 s vs 7.2 s, S4 AWS).
  - So the shape of the single emission matters more than the engine's speed.
- **Reproducibility is a mission constraint.** "Runs MUST be reproducible from inputs + seed." Inputs change with every refresh, so a run must keep its own inputs.
- **Honest calibration.**
  - Current-season SP+ and lines of historic seasons were fetched after those seasons, so the as-of views hide them at historic points.
  - The model can only use what it will have live: the previous season's SP+ and the current Elo.
  - The closing line is the natural yardstick, but it knows more than the model at the point.
  - The weight of the two features moves through a season, and the demo lives in weeks 8–14.
  - A gate checked on two seasons must tolerate sampling noise, or it fails a good model by chance.
- **Season odds need correlated outcomes.** A rating grows stale over the weeks for all of a team's games at once. Independent per-game noise keeps every game's probability right but makes win totals and playoff odds too sure.
- **A small league, a real league.** Integration tests need a league with four power conferences, championship games, polls and 12-team fields, which Phase 3's league does not have. Live data arrives only after Phase 2's backfill.
- **Runs die mid-demo.** A `KILL STATEMENT`, a client abort or a lost session leaves rows that the killed run cannot clean up itself.
- **Process.**
  - No DB session while planning (spike CSVs and stored replay files only), zero CFBD calls.
  - Shared databases: probes and timings run locally only in "local free" windows.
  - AWS is free for this work (orchestrator, 2026-10-22): deploy and run only, never stop, start or resize it.
  - `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md` are edited only by the docs tasks, after re-reading them.

**Goals**
- `CFB_SIM` with the objects of Key Interfaces: four seeded reference tables, two functions, 17 views and 12 data tables. Scripts `CFB_INFRA.SIM_ENGINE`, `SIMULATE`, `SIMULATE_FROM`, `REFRESH_SIMULATE` and `DROP_SIM_RUN`.
- The score model: ratings, μ, σ by horizon, totals, predicted points and win probability per game at any point, leak-free. Calibrated on 2014–2025, with an out-of-sample gate against the line per week phase.
- Pregame win probability and upset magnitude for every completed game since 1890.
- Rule sets 2024/2025/2026; the selection week per season; a resume formula fitted to final committee rankings; simplified tiebreakers documented.
- The engine per the contract, reusing the S3 prototype:
  - the fixed-shape draw layout with shared team offsets;
  - fixed results;
  - known championship pairings and results;
  - non-FBS opponents.
- `SIMULATE` with input snapshot, checks, registry, step timings, sizing, lock, failure and abandoned-run cleanup, verify, drop and forced re-sims. Outputs per D20.
- Refresh step 300 with per-target defaults: local 100k at 5,000 per batch, AWS 1M at 10,000.
- The backtest of 2024 and 2025 with a selection gate and a pooled calibration check.
- `make sim`, `make simulate`, `make sim-verify`, `make sim-drop`, `make calibrate`, `make backtest`; `make deploy` includes the sim schema.

**Non-Goals**
- What-if queries, the conditional rooting guide, the forced re-sim and what-if pages, week-over-week movement, frozen predictions, the scorecard, upset rankings and `CFB_API` views. These are Phase 5. This plan provides their inputs: `SIM_PATH_R<run>` (named in `SIM_RUN.PATH_TABLE`), `SIM_TEAM`, `LEVERAGE`, `GAME_SCORE_AS_OF`, `GAME_WIN_PROB`, fixed results in the engine and `SIMULATE_FROM`.
- Grants to `CFB_APP` and `CFB_MCP`.
- Per-sim margins and the M0 histogram (D20: optional, off).
- In-simulation rating updates, where a simulated result would change a team's rating for its later games. The contract's rating-update flag stays unimplemented. Per-sim team strength offsets are in scope ([29]).
- Using the current season's SP+ or lines as features.
- Formats other than 12 teams: no 4-team era runs, no 14/16/24-team options. No bracket-only simulation after selection Sunday: points after the selection week are refused.
- Full conference tiebreakers, divisions, CFP committee behavior beyond the resume formula, and transitional-FBS eligibility.
- Automatic pruning of stored runs (retention is documented; `make sim-drop` per run).
- ML frameworks and custom SLCs: runtime code is builtin `PYTHON3` (numpy 1.26.4) and SQL.
- The benchmark harness and `docs/benchmark.md` (Phase 8). The run registry is what they will read.

### Decision

The score model is SQL views over `CFB_MODEL`'s as-of views, applying coefficients from a generated, reviewed seed table that `make calibrate` fits on the host with pinned numpy. The engine is the S3 prototype grown to the full contract, bundled into one `PYTHON3 SET` script with a single explicit `EMITS` stage shape. A Lua script runs the steps. It takes Phase 2's run lock, cleans up runs that were killed, and logs every step:
1. snapshot the run's inputs;
2. check them;
3. create the per-run outcome table;
4. fan out one engine call per batch into a stage table;
5. split the stage into the outputs with plain SQL (the layout-C columns derived from the emitted outcome string);
6. build `LEVERAGE`;
7. finish.

#### Architecture

```
 CFB_MODEL (Phase 3)  GAME_AS_OF · TEAM_SEASON_AS_OF · RATING_SP · GAME · SEASON_WEEK · CONFERENCE_CHAMPIONSHIP(_GAME) · RANKING · AS_OF_POINT
        │
        ▼  CFB_SIM views (sql/sim/, deployed by vs/host/sqlobjects.py)
 SCORE_MODEL_PARAMS ─┐  SELECTION_WEEK (per season: power CCG week, else the December rule)
 (version × phase)   │  TEAM_RATING_AS_OF ─▶ GAME_SCORE_AS_OF ─▶ SCORE_TRAINING_ROWS ─(make calibrate)─▶ CALIBRATION_ROWS
 RESUME_WEIGHTS ─────┤        │                     │                         ─▶ SCORE_MODEL_REPORT · SCORE_CALIBRATION_BUCKETS
 SELECTION_RULES ────┤        ▼                     ▼                            SCORE_SOURCE_REPORT
 SIM_CONFIG ─────────┘  ENGINE_TEAM_AS_OF · ENGINE_GAME_AS_OF · ENGINE_PARAM_AS_OF        GAME_WIN_PROB (1890–, upsets)
        ▲                       │
 make calibrate (host numpy 1.26.4, lock kind DEPLOY): fit ─▶ sql/sim/010_*.sql, 012_*.sql (generated, reviewed) ─▶ deploy ─▶ report, gate
        │
 EXECUTE SCRIPT CFB_INFRA.SIMULATE(2026, 9, 'regular', 100000, 5000, NULL, NULL, NULL)   (or step 300 of REFRESH_WEEK)
   lock (LOAD_RUN kind SIMULATE, script rule; the refresh step uses the refresh's lock)
   reconcile: every other RUNNING SIM_RUN ─▶ ABANDONED, outputs deleted, path table dropped, stage truncated
   1 INPUTS      SIM_RUN_TEAM ◀ ENGINE_TEAM_AS_OF; SIM_RUN_GAME / SIM_RUN_PARAM ◀ ENGINE_GAME/PARAM_AS_OF ⋈ SIM_RUN_TEAM
                 (games up to the selection week only)
   2 CHECKS      SIM_INPUT_CHECKS (ERROR stops)
   3 PATH_TABLE  CREATE TABLE CFB_SIM.SIM_PATH_R<run> (RUN_ID, SIM_ID, G0001 … BOOLEAN, comments per game)
   4 ENGINE      INSERT INTO SIM_STAGE SELECT CFB_INFRA.SIM_ENGINE(run cols, model cols)
                   FROM SIM_RUN_MODEL_ROWS CROSS JOIN (VALUES BETWEEN 0 AND n) AS b(BATCH_ID) GROUP BY BATCH_ID
                   ┌ per batch: SeedSequence([seed, batch]).spawn(2) → season (S×G normals, S×G uniforms, S×T team offsets),
                   │            postseason (S×(C+11) normals, uniforms); season → standings → CCGs → resume →
                   └ selection (rule set) → bracket; ONE DataFrame emit: T rows (12/sim), P rows (outcome string),
                                 W/C tallies, B summary (checksum, model digest, versions, stage seconds, vm/node)
   5 OUTPUTS     SIM_TEAM ◀ T · SIM_PATH_R<run> ◀ SUBSTR(OUTCOMES, i, 1) = '1' · TEAM_WINS ◀ W · CONF_CHAMP ◀ C
                 SIM_RUN_BATCH ◀ B (JSON_VALUE) · check: all batches, one digest, one engine version
   6 LEVERAGE    one pass over SIM_TEAM ⋈ SIM_PATH_R<run> (S4 shape) ─▶ LEVERAGE
   7 FINISH      TRUNCATE SIM_STAGE, SIM_RUN DONE, seconds, sims/s, instances, sizing        (every step row committed)
   any error ─▶ delete the run's outputs, drop its path table, truncate the stage, FAILED, release the lock, re-raise
   a kill    ─▶ rows stay RUNNING until the next lock holder reconciles
 EXECUTE SCRIPT CFB_INFRA.SIMULATE_FROM(base_run, '<game id>=A,…', label)   copy base inputs + FIXED_HOME_WIN, steps 2–7, same seed
        │
        ▼
 SIM_TEAM_ODDS (DONE runs)   BACKTEST_FIELD ─▶ BACKTEST_REPORT, BACKTEST_CALIBRATION ◀── make backtest
                                                               (2024/2025, SELECTION_WEEK_SEQ − k, k = 5, 3, 1, 0)
        │
        ▼  Phase 5: CFB_SIM what-if, CFB_PRED, CFB_API (SQL generated from SIM_RUN.PATH_TABLE and SIM_RUN_GAME.PATH_COLUMN)
```

#### Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| Coefficients as generated, reviewed seeds | `sql/sim/010_score_model_params.sql`, `012_resume_weights.sql` (written by `make calibrate`) | A calibration is a deliberate, diffable act, like `make generate`. Runs are reproducible across databases (local synthetic, local restored, AWS) because the parameters travel in git, not in one database |
| One formula in SQL, fitted on the host | `TEAM_RATING_AS_OF` / `GAME_SCORE_AS_OF` / `ENGINE_GAME_AS_OF` apply; `engine/calibrate.py` fits from the same view's raw columns | No second implementation of the prediction to drift. The fit is pure numpy, unit-tested on generated truth |
| Two params versions × three week phases | `CURRENT` (2014–2025), `HOLDOUT_2023` (2014–2023); `EARLY`/`MID`/`LATE` rows, equal unless the phased fit is chosen | The roadmap's calibration window, plus an honest out-of-sample gate and backtest; phase-specific coefficients without a schema change |
| Gates at least three standard errors wide | calibration targets; backtest calibration check | A gate on two seasons must not fail a good model by chance (M2); the check is simulated in a unit test |
| As-of inputs only | every `CFB_SIM` view reads `*_AS_OF`, the prior season's `RATING_SP`; `GAME` and `RANKING` only for labels, the schedule and completed-game probabilities | Leak-free by construction; the invariance test of Phase 3 extends to predictions |
| Selection week as season metadata | `SELECTION_WEEK` | The committee picks the field after the power championship games; points, skips, refusals, the resume fit and the backtest all key on it |
| Tagged model rows snapshot per run | `SIM_RUN_TEAM` / `SIM_RUN_GAME` / `SIM_RUN_PARAM` → `SIM_RUN_MODEL_ROWS` | The engine reads the snapshot, never the live views. A run stays reproducible and verifiable after any refresh (`make sim-verify`) and can be re-simulated with forced results (`SIMULATE_FROM`) |
| Single engine pass, one explicit stage shape | `SIM_ENGINE … EMITS (<stage>)` → `SIM_STAGE` → SQL splits | Reconciles S3's "explicit EMITS per shape" with "one compute pass": the stage is the one shape. Saves a pass (7 s local, 1.5 s per 100k on AWS) per extra output |
| Outcome string emitted, layout C derived in SQL | `OUTCOMES` → `SIM_PATH_R<run>` | C is the read layout (D20). A is the cheap emission (S4: 7.2 s vs 30.6 s at 1M). Probe 1.1c decides C or A once, for both targets, in group A |
| Per-run outcome table, shared sparse tables partitioned by run | `SIM_PATH_R<run>`; `SIM_TEAM`, `LEVERAGE` `PARTITION BY RUN_ID` | The game set changes every week (C needs per-run DDL); the team-level tables stay one table for Phase 5's cross-run queries, pruned by run |
| Fixed-shape draws, child stream per stage | `SeedSequence([seed, batch]).spawn(2)`; team offsets after the season block | S3 M2: common random numbers for what-if deltas and fixed results; scheduling independence kept |
| Shared team offsets with an unchanged marginal | `TEAM_SIGMA` = `TAU`·√(h − 1)/√2 per game, per-game noise `SIGMA_1` | Correlated outcomes for a team's games (M1) without changing any game's probability |
| Engine has no defaults | every `PARAM` required | The snapshot holds every value the run used; nothing hides in code |
| Lock reuse, two kinds | `LOAD_RUN` kind `SIMULATE` (scripts: idle = stale) and `DEPLOY` (host: stale only when gone); the refresh step under the refresh's lock | One lock for everything that writes; Phase 2's stale-row rules apply unchanged |
| Next lock holder cleans up | reconciliation right after the lock | A killed run cannot log its end (Phase 2 probe 1.1j); the next writer can, safely, because it holds the lock |
| Steps commit their rows; failure cleans up | `SIM_RUN_STEP`, `SIM_RUN` | The timeline shows progress from another session; consumers read only `DONE` runs |
| Declarative checks, thin Lua | `SIM_INPUT_CHECKS` view, `engine/lua/*.lua` | Same split as Phases 2 and 3 |
| Synthetic league through the real flattener | `tests/synthetic/sim_league.py` (reuses Phase 3's league machinery) | Faithful mirror rows, CFP fields and polls without CFBD data |
| Pure engine core, loop oracle | `engine/core.py` vs `tests/engine/loop_ref.py` | S3's proven method: vectorization correctness by equality with plain loops on identical draws |

#### Key Interfaces

**Objects of `CFB_SIM`** (files `sql/sim/NNN_<object>.sql`; data tables `sql/sim/data/<object>.sql`; every comment starts with "One row per <grain>")

| File | Object | Kind | Grain | Reads |
|---|---|---|---|---|
| 010 | `SCORE_MODEL_PARAMS` | reference, generated by `make calibrate` | params version and week phase | seed |
| 011 | `SELECTION_RULES` | reference | rule set and season range | seed |
| 012 | `RESUME_WEIGHTS` | reference, generated by `make calibrate` | params version | seed |
| 013 | `SIM_CONFIG` | reference, rendered from `engine/sim-config.yaml` (target block + `--config`) | setting | yaml |
| 020 | `NORMAL_CDF` | function (A&S 26.2.17, error < 7.5e-8; inlined into the views if probe 1.1h finds the call too slow) | — | — |
| 021 | `MARGIN_WIN_PROB(MU, SIGMA)` | function: ½ (Φ((μ − ½)/σ) + Φ((μ + ½)/σ)) | — | `NORMAL_CDF` |
| 030 | `SELECTION_WEEK` | view | season with regular-season weeks | `CONFERENCE_CHAMPIONSHIP_GAME`, `TEAM_SEASON` (`IS_POWER_CONFERENCE`), `SEASON_WEEK`, `AS_OF_POINT` |
| 100 | `TEAM_RATING_AS_OF` | view | params version, point, team-season | `TEAM_SEASON_AS_OF`, `RATING_SP` (S − 1), `SCORE_MODEL_PARAMS` (the point's phase) |
| 110 | `GAME_SCORE_AS_OF` | view | params version, point, `NEXT`/`REMAINING` regular-season game with teams involving FBS | `GAME_AS_OF`, `GAME` (`INVOLVES_FBS`), `TEAM_RATING_AS_OF`, params |
| 120 | `GAME_WIN_PROB` | view | completed game with both teams | `GAME`, params (`CURRENT`) |
| 130 | `SCORE_TRAINING_ROWS` | view | params version, point, game later completed | `GAME_SCORE_AS_OF`, `TEAM_RATING_AS_OF`, `GAME` (labels, closing line) |
| 200 | `ENGINE_TEAM_AS_OF` | view | params version, point, engine team | `TEAM_SEASON_AS_OF`, `GAME_AS_OF`, `TEAM_RATING_AS_OF`, `CONFERENCE_CHAMPIONSHIP`, `RESUME_WEIGHTS`, `SELECTION_WEEK` |
| 210 | `ENGINE_GAME_AS_OF` | view | params version, point, engine game row (team ids) | `GAME_AS_OF`, `GAME_SCORE_AS_OF`, `SELECTION_WEEK`, params |
| 220 | `ENGINE_PARAM_AS_OF` | view | params version, point, parameter | params, `RESUME_WEIGHTS`, `SELECTION_RULES`, `SIM_CONFIG`, `SELECTION_WEEK`, `AS_OF_POINT` |
| 230 | `SIM_RUN_MODEL_ROWS` | view | run and tagged model row | `SIM_RUN_TEAM`, `SIM_RUN_GAME`, `SIM_RUN_PARAM` |
| 240 | `SIM_INPUT_CHECKS` | view | run and check | `SIM_RUN_*`, `SIM_RUN`, `SELECTION_RULES`, `SELECTION_WEEK`, `GAME_AS_OF` (left-out games) |
| 300 | `SIM_TEAM_ODDS` | view | `DONE` run and eligible roster team | `SIM_RUN`, `SIM_RUN_TEAM`, `SIM_TEAM`, `TEAM_WINS`, `CONF_CHAMP` |
| 400 | `SCORE_MODEL_REPORT` | view | params version, evaluation seasons, horizon bucket, week phase (with `ALL`) | `CALIBRATION_ROWS` |
| 410 | `SCORE_CALIBRATION_BUCKETS` | view | params version, evaluation seasons, horizon bucket, week phase, tenth of probability | `CALIBRATION_ROWS` |
| 420 | `SCORE_SOURCE_REPORT` | view | params version, evaluation seasons, rating-source pair | `CALIBRATION_ROWS` |
| 500 | `BACKTEST_FIELD` | view | season ≥ 2024 and actual playoff team | `GAME` (`POSTSEASON_KIND` `CFP`) |
| 510 | `BACKTEST_REPORT` | view | `DONE` backtest run | `SIM_RUN`, `SIM_TEAM_ODDS`, `BACKTEST_FIELD` |
| 520 | `BACKTEST_CALIBRATION` | view | params version of the backtest runs | `SIM_RUN`, `SIM_TEAM_ODDS`, `TEAM_WINS`, `BACKTEST_FIELD`, `SELECTION_WEEK`, `GAME` (actual wins) |
| data | `SIM_RUN`, `SIM_RUN_STEP`, `SIM_RUN_BATCH`, `SIM_RUN_TEAM`, `SIM_RUN_GAME`, `SIM_RUN_PARAM`, `SIM_STAGE`, `SIM_TEAM`, `LEVERAGE`, `TEAM_WINS`, `CONF_CHAMP`, `CALIBRATION_ROWS` | data tables (created if missing, never replaced; a column difference stops the deploy) | see DDL below | — |
| runtime | `SIM_PATH_R<run id>` | per-run table created by `SIMULATE` | simulated season of the run | — |

`CFB_INFRA`: `SIM_ENGINE` (PYTHON3 `SET`, explicit `EMITS`, body = `engine/core.py` + `engine/udf.py`, `ENGINE_VERSION` = first 16 hex of SHA-256 over the body without its constant line), and the Lua scripts `SIMULATE`, `SIMULATE_FROM`, `REFRESH_SIMULATE`, `DROP_SIM_RUN`. They are bundled by Phase 2's `loader/bundle.py` with `loadlib.lua`, `engine/lua/simlib.lua` and the entry. Allowed references for `sql/sim/`: `{SIM_SCHEMA}.*`, `{MODEL_SCHEMA}.*`.

**Engine input mapping** (fulfills Phase 3 decision [31]: the model provides the data, Phase 4 computes the rest)

| Engine input | Source at point P (season S) | Phase 4 rule |
|---|---|---|
| `TEAM` rows | FBS team-seasons of `TEAM_SEASON_AS_OF` (`CLASSIFICATION` `fbs`) + the non-FBS opponents of `REMAINING` games | `ELIGIBLE` = FBS; `TEAM_IDX` = `ROW_NUMBER() OVER (ORDER BY TEAM_ID) − 1`, assigned in the snapshot |
| `CONFERENCE`, `TIER` | `CONFERENCE_NAME`, `CONFERENCE_TIER`: `P4` (and before 2024 `AQ`, `P5`) → `P4`; `G6` (and before 2024 `NON_AQ`, `G5`) → `G6`; `INDEPENDENT` → `IND`; non-FBS → NULL, `NON_FBS` | The tier never enters the resume. The pre-2024 mapping keeps `ENGINE_TEAM_AS_OF` valid for the resume fit over 2014–2023. The Pac-12 is `G6` from 2024 (Phase 3's NULL-name row) |
| `HAS_CCG` | conference has a `CFB_MODEL.CONFERENCE_CHAMPIONSHIP` row covering S | |
| `RATING` | `TEAM_RATING_AS_OF.RATING` (the phase of P) | |
| `SOS` | mean `RATING` of the opponents of all the team's regular-season `FIXED` games up to the selection week in `GAME_AS_OF` (played and remaining), championship games excluded | `DECIMAL(6,2)` |
| `WINS`, `LOSSES` | `TEAM_SEASON_AS_OF` minus the team's played championship game | the engine adds the championship game uniformly |
| `CONF_WINS`, `CONF_LOSSES`, `CONF_PD` | `CONF_WINS`, `CONF_LOSSES`, `CONF_POINT_DIFF` (already without championship games) | |
| `CAPPED_PD` | Σ `LEAST(GREATEST(team margin, −cap), cap)` over `PLAYED` regular-season games without championship games | cap = `RESUME_WEIGHTS.MARGIN_CAP` (24) |
| `GAME` `REMAINING` | `GAME_SCORE_AS_OF` rows with `POSTSEASON_KIND` NULL, not `IS_CONF_CHAMPIONSHIP`, and `WEEK_SEQ` ≤ `SELECTION_WEEK_SEQ` | `GAME_IDX` by `START_AT`, `GAME_ID`; `CONF_GAME` = flag and same conference (else `CROSS_CONFERENCE_FLAG`), resolved in the snapshot; `TEAM_SIGMA` = `TAU` × √(`HORIZON_WEEKS` − 1) / √2 (`DECIMAL(5,2)`) |
| `GAME` `PLAYED_CONF` | `GAME_AS_OF` `PLAYED`, `IS_CONFERENCE_GAME`, not championship, both teams in one conference | head-to-head only |
| `GAME` `CCG` | `GAME_AS_OF` rows with `IS_CONF_CHAMPIONSHIP` (visible from the week before) and `WEEK_SEQ` ≤ `SELECTION_WEEK_SEQ` | `MU`/`SIGMA` from `GAME_SCORE_AS_OF`, `TEAM_SIGMA` as above; `MARGIN` if `PLAYED` |
| left out | `NOT_PLAYED` games; regular-season games after the selection week; flagged championship games after it | `GAMES_NOT_PLAYED`, `GAMES_AFTER_SELECTION`, `CCG_AFTER_SELECTION` warnings |
| `PARAM` | `hfa` = `HFA`; `game_sigma` = `SIGMA_1`; `post_team_sigma` = `TAU` × √(`SELECTION_WEEK_SEQ` − P) / √2 (the horizon `SELECTION_WEEK_SEQ` − P + 1); `ot_margin` = `OT_MARGIN` (3); `margin_cap`, `w_*` from `RESUME_WEIGHTS`; `auto_bid_rule`, `auto_bids`, `g6_bid`, `bye_rule`, `guarantee_rank`, `min_conference_teams` from `SELECTION_RULES` of S (codes below); `guarantee_team_idx` = roster index of `GUARANTEE_SCHOOL`, else −1, resolved in the snapshot | `fail_batch` only from `SIM_CONFIG.TEST_FAIL_BATCH` |

Rule codes in `PARAM_VALUE`: `auto_bid_rule` 1 `TOP_CHAMPIONS`, 2 `POWER_CHAMPIONS_PLUS_G6`; `g6_bid` 1 `CHAMPION`, 2 `TEAM`; `bye_rule` 1 `STRAIGHT`, 2 `CHAMPIONS`.

**Selection week** (`030_selection_week.sql`, `sim-engine/sim-runs`):
- `SELECTION_WEEK_SEQ` is the `WEEK_SEQ` of the season's last championship game of a power conference, with source `CCG`.
- While none is stored, it is one more than the last regular-season week of `SEASON_WEEK` that ends before the first Saturday of December (UTC), with source `DECEMBER`. The first Saturday is computed with `DAYS_BETWEEN` from a known Saturday, so no NLS-dependent weekday function is involved.
- Power conferences only, because Phase 3's championship detection would take the 2026 Army–Navy game (an American Athletic conference game alone in week 15) for the American Athletic's title game. That is cross-plan finding X1 in the decision log.

**Engine core changes vs `spikes/s3/engine_proto.py`** (task 1.4)

| Prototype | Phase 4 |
|---|---|
| draws: season normals, then `random(n_ties)` (data-dependent), then postseason | `spawn(2)`: season `standard_normal((S, G))` + `random((S, G))` + team offsets `standard_normal((S, T))`; postseason `standard_normal((S, C + 11))` + `random((S, C + 11))`; C = distinct non-NULL conferences of the `TEAM` rows (small and no-CCG conferences included) |
| margin `rint(μ + σ z)` | `rint(MU + TEAM_SIGMA (d_home − d_away) + game_sigma z)`; simulated championship and bracket games with `post_team_sigma`; a known championship game with its own `MU`/`SIGMA`/`TEAM_SIGMA` |
| `PARAM` overrides defaults | every key required; `fail_batch` optional test hook |
| teams P4 / G6 / IND, all eligible | + `NON_FBS` (never ranked or selected), `HAS_CCG`, `TEAM_ID` |
| every conference plays a championship game between its top two | known `CCG` rows fix the pairing (and result when played); no CCG → standings leader; below `min_conference_teams` → no champion |
| selection: 4 power champions + best G6 champion; straight seeding | rule sets: `TOP_CHAMPIONS` (n = `auto_bids`) or `POWER_CHAMPIONS_PLUS_G6` (`g6_bid` `TEAM`/`CHAMPION`) + guarantee; `bye_rule` `STRAIGHT` / `CHAMPIONS` |
| resume: win %, SOS, capped PD | + `w_rating × RATING` |
| no fixed results | `FIXED_HOME_WIN`: winner kept, margin = `rint(μ + σ Φ⁻¹(p))` with p the cell's uniform mapped into the winner's side (`statistics.NormalDist().inv_cdf`, p clamped to [1e−12, 1 − 1e−12]), at least 1 point on that side, team offsets ignored |
| outputs dense `SIM_TEAM` (team index) or path string per mode | one stage shape: `T` playoff rows (`TEAM_ID`), `P` outcome string (NULL when G = 0), `W`/`C` tallies, `B` summary JSON |

```python
# engine/core.py — draw layout (normative)
season_ss, post_ss = np.random.SeedSequence([run_seed, batch_id]).spawn(2)
season = np.random.Generator(np.random.Philox(season_ss))
post = np.random.Generator(np.random.Philox(post_ss))
z, u = season.standard_normal((S, G)), season.random((S, G))
d = season.standard_normal((S, T))                    # team offsets, drawn after z and u (review M1)
z_post, u_post = post.standard_normal((S, C + 11)), post.random((S, C + 11))
margin = np.rint(mu + team_sigma * (d[:, home] - d[:, away]) + game_sigma * z)   # REMAINING, not fixed
```

**Stage and run tables** (`sql/sim/data/*.sql`; DDL names unquoted upper case, every column commented in the files)

```sql
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.SIM_STAGE (RUN_ID DECIMAL(18,0) NOT NULL, BATCH_ID DECIMAL(9,0) NOT NULL,
  ROW_KIND CHAR(1) ASCII NOT NULL,          -- T playoff team, P outcomes, W wins tally, C title tally, B batch summary
  SIM_ID DECIMAL(18,0), TEAM_ID DECIMAL(18,0), SEED DECIMAL(2,0), BYE BOOLEAN, REACHED_ROUND DECIMAL(1,0),
  CHAMPION BOOLEAN, WINS DECIMAL(2,0), N DECIMAL(18,0), OUTCOMES VARCHAR(2000) ASCII, DETAIL VARCHAR(20000) UTF8);
  -- SIM_ENGINE EMITS exactly these columns; NULL vs sentinel values for unused columns per probe 1.1a
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.SIM_TEAM (RUN_ID DECIMAL(18,0) NOT NULL, SIM_ID DECIMAL(18,0) NOT NULL,
  TEAM_ID DECIMAL(18,0) NOT NULL, SEED DECIMAL(2,0) NOT NULL, BYE BOOLEAN NOT NULL, REACHED_ROUND DECIMAL(1,0) NOT NULL,
  CHAMPION BOOLEAN NOT NULL, PARTITION BY RUN_ID);                          -- probe 1.1e
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.LEVERAGE (RUN_ID DECIMAL(18,0) NOT NULL, TEAM_ID DECIMAL(18,0) NOT NULL,
  GAME_IDX DECIMAL(4,0) NOT NULL, N_HOME DECIMAL(18,0) NOT NULL, N_HOME_IN_CFP DECIMAL(18,0) NOT NULL,
  N_AWAY DECIMAL(18,0) NOT NULL, N_AWAY_IN_CFP DECIMAL(18,0) NOT NULL, PARTITION BY RUN_ID);
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.TEAM_WINS (RUN_ID …, TEAM_ID …, WINS DECIMAL(2,0), N DECIMAL(18,0));
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.CONF_CHAMP (RUN_ID …, TEAM_ID …, N DECIMAL(18,0));
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.SIM_RUN (RUN_ID DECIMAL(18,0) IDENTITY NOT NULL, RUN_STATUS VARCHAR(10) UTF8 NOT NULL,
  RUN_ORIGIN VARCHAR(8) UTF8 NOT NULL, LOAD_RUN_ID DECIMAL(18,0), BASE_RUN_ID DECIMAL(18,0), RUN_LABEL VARCHAR(200) UTF8,
  AS_OF_SEASON DECIMAL(4,0) NOT NULL, AS_OF_WEEK DECIMAL(3,0) NOT NULL, AS_OF_SEASON_TYPE VARCHAR(10) UTF8 NOT NULL,
  AS_OF_WEEK_SEQ DECIMAL(3,0) NOT NULL, AS_OF_LABEL VARCHAR(100) UTF8, SELECTION_WEEK_SEQ DECIMAL(3,0),
  RULE_SET VARCHAR(20) UTF8, PARAMS_VERSION VARCHAR(20) UTF8,
  RUN_SEED DECIMAL(18,0) NOT NULL, N_SIMS DECIMAL(18,0) NOT NULL, SIMS_PER_BATCH DECIMAL(9,0) NOT NULL, N_BATCHES DECIMAL(9,0) NOT NULL,
  MODEL_DIGEST VARCHAR(64) ASCII, ENGINE_VERSION VARCHAR(16) ASCII, NUMPY_VERSION VARCHAR(20) ASCII, PYTHON_VERSION VARCHAR(20) ASCII,
  DB_VERSION VARCHAR(40) UTF8, NODES DECIMAL(4,0), DB_RAM_GIB DECIMAL(7,1), INSTANCES DECIMAL(5,0), BATCHES_PER_INSTANCE DECIMAL(7,2),
  PATH_TABLE VARCHAR(128) UTF8, N_TEAMS DECIMAL(4,0), N_GAMES DECIMAL(5,0), SESSION_ID DECIMAL(20,0), STARTED_AT TIMESTAMP NOT NULL,
  ENDED_AT TIMESTAMP, SECONDS_TOTAL DECIMAL(9,3), SECONDS_ENGINE DECIMAL(9,3), SIMS_PER_SECOND DECIMAL(12,1), DETAIL VARCHAR(2000) UTF8);
-- SIM_RUN_STEP (RUN_ID, STEP_NO, STEP_NAME, STEP_STATUS, STARTED_AT, ENDED_AT, SECONDS, ROWS_WRITTEN, DETAIL)
--   STEP_STATUS RUNNING / DONE / FAILED / ABANDONED
-- SIM_RUN_BATCH (RUN_ID, BATCH_ID, N_SIMS, CHECKSUM, MODEL_DIGEST, ENGINE_VERSION, ENGINE_SECONDS, STAGE_SECONDS (JSON), VM_ID, NODE_ID, PEAK_RSS_KB)
-- SIM_RUN_TEAM (RUN_ID, TEAM_IDX, TEAM_ID, SCHOOL, CONFERENCE, TIER, HAS_CCG, ELIGIBLE, RATING, RATING_SOURCE, SOS,
--               WINS, LOSSES, CONF_WINS, CONF_LOSSES, CONF_PD, CAPPED_PD)                                  = D20's TEAMS roster
-- SIM_RUN_GAME (RUN_ID, GAME_ROLE, GAME_IDX, GAME_ID, WEEK_SEQ, START_AT, HOME_IDX, AWAY_IDX, HOME_TEAM_ID, AWAY_TEAM_ID,
--               NEUTRAL, CONF_GAME, MU, SIGMA, TEAM_SIGMA, TOTAL, FIXED_HOME_WIN, MARGIN, PATH_COLUMN)
-- SIM_RUN_PARAM (RUN_ID, PARAM_KEY, PARAM_VALUE DECIMAL(12,4))
-- CALIBRATION_ROWS: the columns of SCORE_TRAINING_ROWS (with WEEK_PHASE and both RATING_SOURCEs)
```

Reserved words (`vs/gen/exasol_reserved_keywords.txt`, 2026.1.0) checked for every planned name: `SOURCE`, `VALUE`, `RESULT`, `STATE` and `POSITION` are reserved and avoided (`RATING_SOURCE`, `PARAM_VALUE`, `PROB_SOURCE`, `RUN_STATUS`, `SEED`, `SELECTION_WEEK_SOURCE`). The task that writes the DDL re-checks the new names (`WEEK_PHASE`, `TEAM_SIGMA`, `BASE_RUN_ID`, `PATH_TABLE`, `TEAM_POINTS`, `WIN_DISPERSION`) against the file.

**Run SQL** (generated by `simlib.lua`; names are deploy-time constants; values bound)

```sql
-- 4 ENGINE: one SET group per batch (the grammar allows a column alias only on a parenthesized from_item)
INSERT INTO "CFB_SIM"."SIM_STAGE"
SELECT "CFB_INFRA"."SIM_ENGINE"(:run, :seed, :spb, b."BATCH_ID", 'FULL', m."ROW_KIND", m."GAME_ROLE", … , m."PARAM_VALUE")
  FROM "CFB_SIM"."SIM_RUN_MODEL_ROWS" m
 CROSS JOIN (VALUES BETWEEN 0 AND :last_batch) AS b("BATCH_ID")      -- bound vs verbatim ::last_batch: probe 1.1d
 WHERE m."RUN_ID" = :run
 GROUP BY b."BATCH_ID";
-- 3 + 5 PATH: per-run DDL (column comment "Week 12: Brookvale at Ashford (game 401…); TRUE = home win"), then
INSERT INTO "CFB_SIM"."SIM_PATH_R42" SELECT "RUN_ID", "SIM_ID", SUBSTR("OUTCOMES", 1, 1) = '1', …, SUBSTR("OUTCOMES", G, 1) = '1'
  FROM "CFB_SIM"."SIM_STAGE" WHERE "RUN_ID" = 42 AND "ROW_KIND" = 'P';       -- C or A decided by probe 1.1c in group A
-- 5 SIM_TEAM, TEAM_WINS (SUM(N) by team and wins), CONF_CHAMP, SIM_RUN_BATCH (JSON_VALUE(DETAIL, '$.checksum' RETURNING …))
-- 6 LEVERAGE: spikes/s4/layouts.py fill_leverage_sql adapted: per team SUM(CASE WHEN p."Gnnnn" THEN 1 ELSE 0 END) over
--   SIM_TEAM ⋈ SIM_PATH_R<run> on SIM_ID (run filter first), unpivoted by game, joined to per-game home-win counts
-- 7 FINISH, failure cleanup and abandoned-run cleanup: the lock makes the run the stage's only writer
TRUNCATE TABLE "CFB_SIM"."SIM_STAGE";
```

**Score model** (formulas in `sim-engine/score-model`; fit in `sim-engine/calibration`)
- `RATING` by source: `SP_ELO`, `ELO`, `SP`, `CLASS_DEFAULT`, with the coefficients of the point's week phase. `MU` = `HFA` × not neutral + `RATING`ₕ − `RATING`ₐ. `SIGMA` = √(`SIGMA_1`² + `TAU`² (k − 1)).
- `TOTAL` from the previous season's SP+ offense and defense ratings, or `TOTAL_DEFAULT`. `HOME_WIN_PROB` = `MARGIN_WIN_PROB(MU, SIGMA)`.
- In the engine, `SIGMA` splits into a per-game noise `SIGMA_1` and shared team offsets of scale `TEAM_SIGMA` = `TAU`·√(k − 1)/√2 per team. The marginal is unchanged: `SIGMA`² = `SIGMA_1`² + 2·`TEAM_SIGMA`².
- `ELO_CENTER` is fitted as the mean Elo of the FBS teams over the version's training points, so the Elo term is centered on the average FBS team like the SP+ prior.
- Fallbacks, in order, when 6.2 misses a gate (each logged):
  1. `SP_ELO_PHASED`: `B_SP` and `B_ELO` fitted per week phase;
  2. F2 (6.2b): add the season-to-date point differential per game from `TEAM_SEASON_AS_OF` (`POINTS_FOR`, `POINTS_AGAINST`, games) as a third rating feature, with its coefficient column.

  `FEATURE_SET` records which one is deployed.
- `SCORE_MODEL_PARAMS` columns: `PARAMS_VERSION`, `WEEK_PHASE`, `PHASE_FROM_WEEK_SEQ`, `PHASE_TO_WEEK_SEQ`, `FEATURE_SET`, `HFA`, `B_SP`, `B_ELO`, `B_ELO_ONLY`, `B_SP_ONLY`, `ELO_CENTER`, `FBS_DEFAULT_RATING`, `NON_FBS_RATING`, `SIGMA_1`, `TAU`, `OT_MARGIN`, `TOTAL_INTERCEPT`, `TOTAL_SLOPE`, `TOTAL_DEFAULT`, `ELO_PROB_SCALE`, `ELO_PROB_HFA`, `FIT_SEASONS`, `FITTED_AT`, `FIT_TARGET`, `DATA_FINGERPRINT`. Phases: `EARLY` 0–4, `MID` 5–9, `LATE` 10–999.
- Until the first calibration (task 6.2), all six rows hold `FEATURE_SET` `UNCALIBRATED` with the S3 defaults (`HFA` 2.5, `B_SP` 1.0, `B_ELO` 0, `B_ELO_ONLY` 0.04, `B_SP_ONLY` 1.0, `ELO_CENTER` 1500, `SIGMA_1` 15, `TAU` 0, `OT_MARGIN` 3, `TOTAL_DEFAULT` 52, `ELO_PROB_SCALE` 400, `ELO_PROB_HFA` 55). With `TAU` 0 the team offsets have no effect. `RESUME_WEIGHTS` holds the S3 weights (100, 2.0, 0.5, `w_rating` 0, `MARGIN_CAP` 24).

**Calibration gate** (`sim-engine/calibration`; `HOLDOUT_2023` on 2024–2025, horizon 1, FBS-vs-FBS)
- In each phase:
  - MAE ≤ line + 1.5 and ≤ 15.0;
  - Brier ≤ line + 0.020 and ≤ 0.190;
  - |bias| ≤ max(1.0, 3 SE);
  - |Spiegelhalter Z| ≤ 3.0.
- Over all phases:
  - every tenth with ≥ 100 games within max(0.05, 3·√(p̄(1 − p̄)/n));
  - totals beat the mean.
- Simulated on gate populations of 300/450/550 games:
  - a perfect model fails this family in 4.3 % of samples;
  - with 2·SE buckets it would fail in 30 %;
  - with a fixed ±1.0 bias gated per phase, in 48 %;
  - a model whose σ is about 20 % too small fails the per-phase Z alone in 69 %.

  The unit test pins these properties.

**Selection rules seed** (`sql/sim/011_selection_rules.sql`): the table of `sim-engine/selection-rules`.
- Columns: `RULE_SET`, `SEASON_FROM`, `SEASON_TO`, `CFP_SIZE`, `N_BYES`, `AUTO_BID_RULE`, `AUTO_BIDS`, `G6_BID`, `BYE_RULE`, `GUARANTEE_SCHOOL`, `GUARANTEE_RANK`, `MIN_CONFERENCE_TEAMS`, `SOURCE_NOTE`.
- `SEASON_TO` is NULL for `CFP12_2026` (open).

**Configuration** (`engine/sim-config.yaml`)

```yaml
local: {N_SIMS: 100000, SIMS_PER_BATCH: 5000}      # 20 batches, 2.5 per slot on an 8-core start: every core busy to the end
aws:   {N_SIMS: 1000000, SIMS_PER_BATCH: 10000}    # the 1M scale dial: 100 batches over 12 slots
```

For 100k on AWS, D19's pick is 1,000 per batch: `make simulate TARGET=aws SIMS=100000 BATCH=1000`. D19 measured 5,000 and 10,000 per batch as equal locally (7.11 s vs 7.06 s compute-only); 10,000 leaves 6 of 8 slots idle in the second wave, which undercuts "every core" on stage.

**Make targets** (`scripts/sim.py`; every target runs `db-check` first and accepts `TARGET` and `ARGS="--prefix …"`)

| Target | Does |
|---|---|
| `make sim [ARGS="--config-block aws --config K=V,…"]` | lint, render, bundle, deploy `CFB_SIM`, the engine and the Lua scripts under the run lock (kind `DEPLOY`); register step 300; report |
| `make deploy` | virtual schema → loader → model → sim |
| `make simulate SEASON= WEEK= [SEASON_TYPE= SIMS= BATCH= SEED= PARAMS= LABEL=]` | `EXECUTE SCRIPT CFB_INFRA.SIMULATE(…)`; print the timeline and the top 25 of `SIM_TEAM_ODDS` |
| `make simulate BASE=<run> FIX="<game id>=H\|A,…" [LABEL=]` | `EXECUTE SCRIPT CFB_INFRA.SIMULATE_FROM(…)`; print the timeline and the top 25 next to the base run's |
| `make sim-verify RUN=` | `SUMMARY`-mode engine over the run's snapshot; compare checksums; writes nothing |
| `make sim-drop RUN= CONFIRM=yes` | `EXECUTE SCRIPT CFB_INFRA.DROP_SIM_RUN(run)` |
| `make calibrate [ARGS="--out DIR --targets k=v,… --feature-set SP_ELO\|SP_ELO_PHASED\|F2 --resume-only"]` | `sim-engine/calibration`, under the run lock (kind `DEPLOY`) |
| `make backtest [SEASONS= SIMS= BATCH=]` | `sim-engine/backtest` |

**Code layout**

```
engine/__init__.py
engine/core.py          pure numpy engine (from spikes/s3/engine_proto.py), host + UDF
engine/udf.py           UDF wrapper: chunked get_dataframe read, parse, simulate, one DataFrame emit
engine/bundle.py        CREATE PYTHON3 SET SCRIPT text, ENGINE_VERSION, size check
engine/calibrate.py     pure fits and gate evaluation + DB orchestration of make calibrate
engine/backtest.py      make backtest orchestration, the pure calibration-check function (host oracle), report writer
engine/deploy.py        make sim (binds vs/host/sqlobjects.py to sql/sim/, data tables, scripts, config)
engine/sim-config.yaml  per-target SIM_CONFIG
engine/lua/simlib.lua, simulate.lua, simulate_from.lua, refresh_simulate.lua, drop_sim_run.lua
scripts/sim.py          CLI for the make targets
sql/sim/*.sql, sql/sim/data/*.sql
tests/engine/{models.py, loop_ref.py}   crafted models, loop oracle
tests/synthetic/sim_league.py
```

**Sim league** (`tests/synthetic/sim_league.py`; the facts the specs use)

| Item | Facts |
|---|---|
| Seasons | 2013–2026. 2013–2025 complete; 2026 weeks 1–9 completed, 10–15 scheduled, the week-10 lines fetched before its cutoff. The 2026 championship games are not scheduled yet, as on CFBD before the pairings are set. Regular season weeks 1–15, championship games in week 15 (the selection week), postseason week 1. A stored calendar covers 2013–2026; 2026 week 15 holds Saturday 5 December |
| Conferences (real CFBD names as reference keys, fictional teams) | SEC, Big Ten, Big 12, ACC with 10 teams each; American Athletic, Mountain West, Sun Belt, Mid-American, Conference USA with 6 each; Pac-12 with 8 until 2023, 2 in 2024–2025 (Dunmore, Orland), 6 from 2026 (tier `G6` from 2024); independents Notre Dame and Rivermouth; FCS Big Sky with Lowell, Pemberton and 2 more |
| Named teams | Ashford, Brookvale (SEC); Granton (Big Ten); Hollis (Big 12); Fillmore (ACC); Ivel (American Athletic); Eastport, Fairhaven (Mountain West); Sable (Sun Belt, FBS from 2026, no 2025 SP+, Elo 1550 at 2026 week 9); Dunmore (Pac-12) |
| Schedules | 12 regular games per FBS team (8 conference games in 10-team conferences, 5 or 7 in the others), one FCS opponent per team, no conference pair twice; Ashford hosts Lowell in 2026 week 11 and Brookvale in week 12 |
| Facts | Pregame and postgame Elo on every FBS game, none for Lowell (simple Elo updates); SP+ for every FBS team every season (Ashford 2025: 20.00 / offense 35.0 / defense 18.0; Brookvale 10.00 / 30.0 / 22.0); Ashford's Elo after 2026 week 9 is 1700, Brookvale's 1600; lines on every FBS-vs-FBS game; AP polls weekly; committee rankings from week 10 and a final committee ranking per season from 2014 (`POLL_CODE` `CFP`, `IS_FINAL_POLL`) |
| Committee and fields | The generator's committee ranks by the resume formula with the test resume weights over the test-parameter ratings at the selection point (no resume gap below 0.5 among the top 30); 2024 and 2025 have 12-team fields selected under `CFP12_2024` and `CFP12_2025`, with first-round, quarterfinal, semifinal and final games and seeds; 2014–2023 have 4-team playoff games |
| Generation | Results drawn from a known truth (μ = 2.5 × home + strength difference, σ = 15) with a fixed seed; Phase 3's machinery (API-shaped bodies → `vs.cfbd_vs.flatten`, slice keys, `LOAD_LOG` registrations, replay export) |
| Variants | `mutate_after(point)` (every fact not visible at the point; Phase 3's list); `with_neutral_week12`; `with_repeated_conference_pair` (two SEC teams meet twice); `with_bad_2025_committee` (three at-large teams ranked below 20 by the resume); `with_changed_late_results`; `with_post_selection_game` (Ivel at Eastport, a non-conference game in week 16 of 2024, played, and of 2026, scheduled) |
| Scale option | `scale="real"`: 136 FBS teams in 10 conferences plus FCS opponents, about 420 remaining games at 2026 week 9, for the local performance smoke (3.4) in the `PERF_` namespace |

Test parameters, loaded by `set_score_params` into both versions and all three phases: `HFA` 2.50, `B_SP` 0.8000, `B_ELO` 0.04000, `B_ELO_ONLY` 0.05000, `B_SP_ONLY` 1.0000, `ELO_CENTER` 1500, `FBS_DEFAULT_RATING` −5.00, `NON_FBS_RATING` −20.00, `SIGMA_1` 15.00, `TAU` 2.00, `OT_MARGIN` 3, `TOTAL_INTERCEPT` 10.0, `TOTAL_SLOPE` 0.5000, `TOTAL_DEFAULT` 52.0, `ELO_PROB_SCALE` 400, `ELO_PROB_HFA` 55. Test resume weights: 100, 2.0, 0.5, 1.0, cap 24. With these, the week-12 game Ashford–Brookvale at week 9 has `SIGMA` 15.26 and `TEAM_SIGMA` 2.00.

**Spike reuse**

| Spike artifact | Phase 4 use |
|---|---|
| `spikes/s3/engine_proto.py` | copied to `engine/core.py`, then changed per the table above (the spike copy stays as the evidence of D19) |
| `spikes/s3/engine_loop_ref.py` + `spikes/s3/tests/test_engine_proto.py` | copied to `tests/engine/loop_ref.py` and `tests/unit/test_engine_core.py`, updated to the Phase 4 rules |
| `spikes/s3/synth_season.py` | crafted-model helpers in `tests/engine/models.py` |
| `spikes/s3/engine_udf.py`, `spikes/common/deploy.py` | the chunked read, the DataFrame emit and the inlining approach of `engine/udf.py` / `engine/bundle.py` (no mode inference: one explicit shape) |
| `spikes/s3/results/numpy_stream.json` | the evidence that host 1.26.4 equals the SLC stream; `tests/unit/test_engine_stream.py` pins the host version and a stream digest |
| `spikes/s4/layouts.py` (`wide_game_names`, `fill_leverage_sql`), the S4 generator | column naming `G0001…`, the `LEVERAGE` SQL, and the synthetic outcome strings of probe 1.1c |
| `spikes/s4/queries.py` (`_sparse_what_if`, Q2) | the what-if smoke of probe 1.1e and task 6.4 (Phase 5 owns the real queries) |
| `spikes/s3/results/*.csv`, `spikes/s4/results/*.csv` | the performance budget below and the verification baselines |

**Performance budget** (estimates from the spike CSVs and the Phase 3 targets; measured in 1.1, 3.4 and 6.4; demo shape about 400 remaining games)

| Component | Local 100k (8 cores, 5k/batch) | AWS 1M (3 × 4, 10k/batch) | Basis |
|---|---|---|---|
| `INPUTS` snapshot (`ENGINE_TEAM_AS_OF` once, games and parameters join the snapshot) | ≈ 3 s | ≈ 2 s | Phase 3 target < 1 s per as-of view and point, about three evaluations |
| `CHECKS` | ≈ 0.5 s | ≈ 0.5 s | one view over the snapshot and the point's games |
| `PATH_TABLE` DDL (≤ 900 commented columns) and 14 step-row commits | ≈ 1.5 s | ≈ 1.5 s | probe 1.1b times the DDL, 1.1i the commits; Phase 2 probe 1.1m: 6–10 ms per small statement locally |
| Engine compute, one pass, fixed costs included | ≈ 7.1 s | ≈ 7–9 s, budgeted 12 s | S3 compute-only: local 7.06 s (10k) / 7.11 s (5k) with the 3.2 s fixed cost inside; AWS 9 waves × 0.66 s per 10k batch + 0.8 s fixed |
| Playoff rows (12 per sim) | ≈ 3.4 s | ≈ 6.5 s | S3 emission 0.35M / 1.84M rows/s |
| Wider stage row (13 columns, two long `VARCHAR`s, vs S3's 8) | ≈ 1.0 s | ≈ 2.0 s | estimate +30 % on the playoff rows; probe 1.1a measures it |
| Outcome strings | ≈ 3.8 s | ≈ 7.2 s | S3 path emission (10.82 − 7.06 s); S4 A load at 1M |
| `OUTPUTS`: second write of the playoff rows into `SIM_TEAM` (1.2M / 12M), tallies, batches | ≈ 0.5 s | ≈ 1.0 s | S4: 12M sparse rows by `INSERT … SELECT` in 0.41 s on AWS; probe 1.1c times the copy |
| Layout-C derivation | gate ≤ 5 s | gate ≤ 10 s | probe 1.1c, both targets, group A |
| `LEVERAGE` | ≈ 1 s | ≈ 4.8 s | S4 AWS 4.75 s at 1M |
| `FINISH`: `TRUNCATE` of the stage (1.3M / 13M rows) | ≈ 0.2 s | ≈ 0.2 s | probe 1.1c times it |
| **Sum** | **≈ 22 s, ≤ 27 s with C at its gate** | **≈ 38 s, ≤ 48 s with C at its gate** | |
| **Target** | **≤ 30 s** | **≤ 60 s** | mission: `REFRESH_WEEK` incl. 100k < 2 min locally |

- The earlier budget counted the local fixed cost (3.2 s) next to the compute figure. S3 measures it inside, so it is no longer added twice.
- The C gates are what is left of each target after the other rows, minus a reserve: 3 s locally, where S3 saw 40–76 s for one cell, and 12 s on AWS.
- Targets are judged on the median of three runs, with min and max reported.
- A preseason run (about 850 remaining games) is recorded, not gated.

**Exasol claims checked against the `exasol-udfs`, `exasol-database` and `exasol-distributed-ml` skills and the spikes**

| Claim | Evidence | Result |
|---|---|---|
| `PYTHON3 SET SCRIPT … EMITS (…)`, `ctx.get_dataframe(num_rows=n)`, `ctx.emit(DataFrame)` (several emits per group allowed) | `exasol-udfs` `create-script-syntax.md`, `udf-python.md`; S3 measured | Confirmed; NULLs in emitted frames: probe 1.1a |
| Explicit `EMITS` beats variadic | S3 remedy 2 (estimate) | Adopted, one shape |
| `GROUP BY batch_id` fans out to `nrOfCores` per node; modules load once per VM | S3 fan-out; `exasol-distributed-ml` `ml-performance.md` (lazy module init) | Confirmed |
| `exa.meta.vm_id`, `memory_limit`; `node_id`, `node_count` | S3; `udf-python.md` lists `meta` attributes | `vm_id` confirmed; `node_id`/`node_count` probe 1.1f |
| Lua `query`/`pquery`, nested `EXECUTE SCRIPT`, output discarded without `WITH OUTPUT`, binds `:v` / `::name` | `exasol-udfs` `lua-execute-scripts.md`; Phase 2 probe 1.1e/1.1m | Confirmed; nested `COMMIT` + DDL: Phase 3 probe 1.1h, re-checked in 1.1i |
| Liveness via `EXA_ALL_SESSIONS` (non-DBA), `IDLE` after a kill, `R0003` on every later statement, takeover without waiting | Phase 2 probe 1.1i/1.1j | Confirmed; a kill during `os.sleep` (the cleanup test's hook): probe 1.1j |
| `CREATE TABLE IF NOT EXISTS`, `PARTITION BY` on `DECIMAL` | `exasol-grammar.md` `create_table_statement_1/2`, `partition_by`; `table-design.md` supported types | Confirmed; latency with 20–40 stored runs: probe 1.1e |
| `TRUNCATE TABLE t` | `exasol-grammar.md` `truncate_statement`, listed under DML | Confirmed as DML; rollback inside the run's transaction and its time: probe 1.1i, 1.1c |
| `VALUES BETWEEN min AND max` as a cross-joined source with a column alias | `exasol-grammar.md` `value_range_table`; `from_item` allows `"(" value_range_table ")" [AS] alias (col)` | Confirmed: `(VALUES BETWEEN 0 AND n) AS b("BATCH_ID")`; a bound upper limit: probe 1.1d |
| `JSON_VALUE(… RETURNING type)` | `exasol-grammar-functions.md` `json_value_1` | Confirmed; probe 1.1g |
| No `ERF` / normal CDF built in; `EXP`, `SQRT`, `ABS` exist; `CREATE FUNCTION … RETURN DOUBLE` | `exasol-grammar-functions.md`; Phase 3's checked `create_function_statement` | `NORMAL_CDF` as a SQL function; accuracy and per-row cost against an inlined polynomial: probe 1.1h |
| `REGR_*`, `CORR` exist | `exasol-grammar-functions.md` | Not used: 2–3 feature OLS and a logistic fit are done on the host |
| Maximum columns per table ≥ 2 + 2,000 | not in the skills | Probe 1.1b, both targets; more than 2,000 remaining games falls back to layout A |
| Reserved words | `vs/gen/exasol_reserved_keywords.txt` | Avoided (above) |
| DDL names unquoted, query identifiers quoted | `exasol-database` rules; Phase 3 lint | Enforced by the shared lint |

### Consequences

| Decision | Alternatives Considered | Rationale |
|----------|------------------------|-----------|
| Linear margin model on SP+ prior + Elo as of the point + home field, normal margins ([2]); `ELO_CENTER` fitted as the mean FBS Elo ([33]) | Market lines as μ; ML models; ratings solved from games in SQL (SRS); a fixed center of 1500 | Uses only what is live at every point, calibrates in seconds, explains in one sentence; teams rated from different sources compare fairly |
| σ grows with the horizon ([3]); the growth is shared per team as per-sim strength offsets ([29]) | Independent per-game noise; a separate rating-update model | Every game keeps its probability, and a team's games move together, so season odds are not overconfident (M1) |
| Week phases in the report and the gate, phased coefficients as the first fallback ([32]) | One pooled fit and gate; separate models per phase | The demo lives late in the season, where a pooled gate can hide drift; three × two coefficients stay explainable |
| Gates at least three standard errors wide, plus an aggregate calibration test ([31]) | Fixed 0.05 buckets; 2·SE buckets | 2·SE buckets fail a perfect model in 30 % of samples, a fixed per-phase bias gate in 48 %; this family fails in 4.3 % |
| Coefficients fitted on the host, committed as generated seeds; `CURRENT` + `HOLDOUT_2023` ([6]) | In-DB fit UDF; fit on every deploy | Reviewable, reproducible across databases; an honest holdout |
| Yardstick = closing line, never a feature ([7]) | No baseline; line as feature | The line knows more than the model at the point; comparing keeps the targets meaningful |
| 2026 rules: 4 power champions + best G6 team + Notre Dame top-12 guarantee; G6 rule configurable ([9]) | "Best G6 champion"; mission glossary wording | Two sources against one; one seed value flips it; cfp.org check by 2026-11-03 |
| Rule sets for 2024 and 2025 too ([10]) | Backtest under 2026 rules | A backtest against real fields needs the rules they were selected under (2025: Duke left out) |
| Resume formula with a rating term, weights fitted to final committee rankings at the selection point ([11], [40]) | Prototype weights; ordinal model | The committee rewards strength; the grid is deterministic and explainable |
| Selection week per season from the power championship games, December rule as fallback ([37]) | `LAST_REGULAR_WEEK_SEQ`; any championship game; a seeded week per season | On real data the last regular week is Army–Navy's; the power CCG weekend is robust to Phase 3's 2026 misdetection |
| `spawn(2)` fixed-shape draws, team offsets after the season block ([13], [29]) | Prototype order; fixed OT block only; a third child stream | S3 M2: common random numbers across what-if deltas, independent of ties and of the game count of the postseason |
| Fixed results in the engine and `SIMULATE_FROM` now ([14], [38]) | Leave to Phase 5 | Phase 5's forced re-sim calls one script and needs no engine change |
| One engine pass, one stage shape, SQL splits ([15]) | One UDF per output (several passes); direct C emission | Saves a compute pass per output; emission is the cost |
| C derived from the outcome string; per-run path table; C or A decided once in group A on both targets ([16], [34]) | Emit C from the UDF; store A only; decide per target | Keeps D20's read layout at A's emission cost, measured before anything depends on it; one layout for Phase 5 |
| Shared sparse tables `PARTITION BY RUN_ID`, engine emits `TEAM_ID` ([17]) | Per-run tables for everything; team index | Phase 5 cross-run queries stay plain SQL; no idx→id join at 12M rows |
| Input snapshot per run + `make sim-verify` ([18]) | Re-read live views | The mission's reproducibility constraint holds after refreshes and is provable |
| Loader run lock, kinds `SIMULATE` and `DEPLOY`; the next lock holder cleans up killed runs ([19], [36]) | A separate sim lock; cleanup only on caught errors | One lock, Phase 2's stale-row rules; a killed run cannot clean up itself |
| Step 300, per-target defaults, seed = season × 1,000 + week sequence, skip after the selection week ([20]) | Step 200; random seeds | Leaves 200 to Phase 5 (Phase 2's examples already use it for `PREDICT`); deterministic weekly runs |
| Runs refused after the selection week and before 2024 ([21], [37]) | Bracket-only mode; 4-team rules | YAGNI for the in-season demo; logged |
| Non-FBS opponents as ineligible teams; records without championship games ([22]) | Drop FCS games; let the model count CCGs | Real schedules include FCS games; the engine adds the championship game uniformly, played or not |
| Local default 5,000 per batch ([39]) | 10,000 (D19's laptop pick) | Equal per D19, and every core works to the end on stage |

## Features

| Feature | Status | Spec |
|---------|--------|------|
| sim-engine/engine-contract | NEW | `sim-engine/engine-contract/spec.md` |
| sim-engine/selection-rules | NEW | `sim-engine/selection-rules/spec.md` |
| sim-engine/score-model | NEW | `sim-engine/score-model/spec.md` |
| sim-engine/calibration | NEW | `sim-engine/calibration/spec.md` |
| sim-engine/win-probability | NEW | `sim-engine/win-probability/spec.md` |
| sim-engine/sim-runs | NEW | `sim-engine/sim-runs/spec.md` |
| sim-engine/refresh-step | NEW | `sim-engine/refresh-step/spec.md` |
| sim-engine/backtest | NEW | `sim-engine/backtest/spec.md` |
| sim-engine/deploy | NEW | `sim-engine/deploy/spec.md` |

The domain is `sim-engine/`, as `CLAUDE.md` plans it ("The engine contract … is a spec in `sim-engine/`"); the code lives in `engine/` (decision [1]).

The Phase 2 and Phase 3 specs are not changed by delta files (neither is recorded yet). This plan uses their contracts:
- `loader/refresh-week`: registered steps and their arguments;
- `loader/run-logs`: the run lock. It adds run kinds `SIMULATE` (script rule) and `DEPLOY` (host rule) through hand-off H1 (task 1.10); recorder note: when `loader/run-logs` is recorded, its kind list gains both;
- `loader/load-plan`: one more `refresh_steps` entry;
- `model/as-of`, `model/games`, `model/ratings-rankings`, `model/teams-conferences`, `model/seasons-eras`: the inputs;
- `model/deploy`: the deployer and lint it reuses.

## Requirements

| Requirement | Details |
|-------------|---------|
| Zero CFBD calls | No `CFB_SIM` object references `CFBD_VS` (lint + dependency test). Tests use the sim league, replay and the mock; the harness guard against `CFBD_API` stays. Probes use synthetic data only. Live verification reads only stored data; `/info` used calls unchanged across group J |
| Shared DB etiquette | Probe 1.1, the performance smoke 3.4 and `make test-it` locally only in "local free" windows, serialized with `flock <scratchpad>/cfb-it.lock`. AWS `default` is free for this work (orchestrator, 2026-10-22): deploy and run only, never stop, start or resize. `PERF_` and `PROBE_` objects dropped after use. No DB session during planning |
| Layering | `CFB_SIM` reads only `CFB_MODEL` and itself; Lua also `CFB_META` (lock) and `CFB_INFRA` (engine). No grants to `CFB_APP`/`CFB_MCP` (Phase 5) |
| Leak-free | Features only from as-of views; labels only from completed games and final rankings; `HOLDOUT_2023` fitted on ≤ 2023. Invariance tests for predictions, training rows and backtest inputs |
| Reproducibility | Registry key per run; per-batch Philox seeding with `spawn(2)`; inputs snapshotted; `make sim-verify` passes for every `DONE` run, forced re-sims included; numpy 1.26.4 in host venv and SLC (stream pinned by a unit test against S3's recorded digest) |
| Engine contract | `specs/architecture.md` § Engine contract as amended by task 5.2; D19 (batch sizes, explicit `EMITS`, DataFrame emit, one pass) and D20 (layout C or the A fallback, sparse `SIM_TEAM`, `LEVERAGE` built after the load, roster, no per-sim margins) |
| Runtime code | Builtin `PYTHON3` SLC (Python 3.12.3, numpy 1.26.4, pandas inside the UDF only), Exasol Lua and SQL. Host: Python 3.12, numpy 1.26.4 (new dev dependency), pyexasol, PyYAML. No ML frameworks, no custom SLC |
| Self-description | 100 % comments on `CFB_SIM` objects and on every column of every `SIM_PATH_R<run>`; grain first; DDL names unquoted upper case, not reserved |
| Performance (targets; measured) | Local 100k run ≤ 30 s at 5k/batch on an 8-core start; AWS 1M ≤ 60 s. Both are judged on the median of 3 runs, min and max reported, at the demo shape (about 400 remaining games), with sizing recorded. `REFRESH_WEEK` with 100k sims < 2 min locally (mission). `SIM_TEAM` what-if smoke (S4 Q1 k = 1 text) within 1.5× of S4's single-run p95 with 20–40 runs stored (probe 1.1e), re-checked with the runs stored in 6.4. A miss is recorded with its step breakdown and a decision; it does not change the design inside the measuring task |
| Calibration quality | Out-of-sample targets of `sim-engine/calibration` per week phase on real data (6.2). A miss triggers `SP_ELO_PHASED`, then F2 (6.2b), each logged |
| Data in git | Seeds hold coefficients, weights and published rules only; docs hold derived numbers; the sim league is synthetic (real conference names and "Notre Dame" only as reference keys) |
| Ownership | `sys` owns the real `CFB_SIM`; `CFB_TEST_OWNER` the `TEST_` and `PERF_` namespaces |

## Dependencies

- **`add-cfbd-vs-foundation`**: complete through group I (harness, mock, replay, `vs/host/target.py`, bundle size limit).
- **`add-cfb-persistence-refresh`** (implemented before this plan's group B): meta renderer with placeholders, `loader/bundle.py`, `loadlib.lua` lock helpers, `refresh_week.lua` step registry, harness helpers (`deploy_plan`, `set_now`, `refresh`, recordings). Its group J (AWS demo backfill + snapshot) before this plan's group F.
- **Hand-off H1 (cross-plan prerequisite; task 1.10).** This plan never edits `loadlib.lua` (single owner).
  - **Owner:** Phase 2 task 4.2, the single owner of `loader/lua/loadlib.lua`, in `../exasol-cfb-demo-p2` (not started as of 2026-10-22, group F of its tasks), plus task 5.2 for the host-side lock rows. The orchestrator relays H1 to that implementer.
  - **H1a (4.2):** run kind `SIMULATE`, stale under the script rule (session gone, own session, or idle). Lock and release functions callable from other bundles (`acquire_lock(kind, args)` returning the lock's run id, `release_lock(run_id, status, detail)`). An integration test for the kind in `tests/it/test_run_logs_it.py`.
  - **H1b (5.2):** run kind `DEPLOY` for host-driven writers, stale only when its session is gone or it is the caller's own session (the snapshot rule), exposed as a host helper that `engine/deploy.py` and `engine/calibrate.py` call.
  - **Recorder note:** `loader/run-logs` lists both kinds. Phase 3's model deploy, whose lock kind is unspecified, should take `DEPLOY` too.
  - If 4.2 or 5.2 is already done when H1 arrives, their owner adds H1 as a follow-up commit in the Phase 2 worktree. Tasks 2.2 and 3.1 of this plan wait for H1.
- **`add-cfb-analytical-model`** (implemented before group B): `CFB_MODEL` views, `vs/host/sqlobjects.py` (extended by 2.1), `tests/synthetic/league.py` machinery, harness `deploy_model`, `hold_run_lock`. Its group K (AWS model deploy and checks) before group F; its Q3 outcome (one postseason week) is consistent with this plan's refusal of postseason points.
  - **Cross-plan finding X1 (Phase 3 owns the fix; relayed by the orchestrator).** On the stored 2026 schedule, Phase 3's championship detection ("C's only conference game in its week, in C's last week with such a game") takes Army–Navy for the American Athletic's 2026 championship game. Army–Navy is flagged as a conference game in 2026 and is alone in week 15. Once the real American Athletic championship game is stored in week 14, that game is not detected.
    - This plan stays correct for the selection week (power conferences only).
    - The flagged game after the selection week is left out with `CCG_AFTER_SELECTION`.
    - The undetected real game would enter as a regular conference game and can stop the run with `CONFERENCE_PAIR_REPEATED` from the 2026 week-13 point on (late November).
  - **X2 (information).** The 2024 selection ranking is labeled regular week 16, so Phase 3's label rule makes it visible one point after the selection week. This plan reads final rankings only as labels, so it is unaffected.
- **`add-engine-spikes`**: done (D19, D20). `spikes/` stays as evidence; the results CSVs are read, not regenerated.
- **Tools:** numpy 1.26.4 joins the dev group (`uv lock`). Nothing else new.
- **User prerequisites:** U1 the "local free" signal (AWS is free for this work as of 2026-10-22); U2 Phase 2's AWS snapshot for the local restore (6.4); U3 the owner commits the calibrated seeds after 6.2.

## Migration

No data migration: `CFB_SIM` is new.

| Current | New |
|---------|-----|
| `make deploy`: VS → loader → model | VS → loader → model → sim |
| `loader/load-plan.yaml` `refresh_steps`: `MODEL` 100 | + `{step_no: 300, name: SIMULATE, script: CFB_INFRA.REFRESH_SIMULATE}` |
| `LOAD_RUN.RUN_KIND`: `BACKFILL`, `REFRESH_WEEK`, `SNAPSHOT`, `RESTORE` | + `SIMULATE` (script rule) and `DEPLOY` (host rule), through hand-off H1 |
| `vs/host/namespace.py` | + `sim_schema`; renderer + `{SIM_SCHEMA}` |
| `vs/host/sqlobjects.py` | + data-table kind, keep rules (data tables, `SIM_PATH_R<n>`), per-schema allowed references |
| `tests/it/harness.py` | `drop_namespace`/`leftovers` cover `{prefix}CFB_SIM` (and its per-run tables); helpers `deploy_sim`, `seed_sim_league`, `set_score_params`, `set_resume_weights`, `set_sim_config`, `hold_deploy_lock` |
| `pyproject.toml` | dev + `numpy==1.26.4`; mypy `files` + `engine`; coverage source + `engine` |
| `Makefile` | targets `sim`, `simulate`, `sim-verify`, `sim-drop`, `calibrate`, `backtest`; `coverage` reports `engine` |

## Implementation Tasks

Tasks are TDD per `/speq:code-guardrails`. Host code writes its unit tests first. Each SQL/Lua task writes its integration test file first (failing), then the code. Lua has no unit tier. The engine core is unit-tested on the host, and equality with the database is an integration test.

1. **Probe, scaffolding, pure code and hand-offs (no DB except 1.1)**
   - [ ] 1.1 Engine platform probe `docs/spikes/s7_engine_probe.py`, findings in `docs/spikes/s7-engine-probe.md`. It runs as sys on PROBE_* objects, at 0 calls, with synthetic data only, on two targets:
     - Personal local in a "local free" window;
     - with `--target aws`, AWS `default` (free for this work: deploy and run only, never stop, start or resize).

     Items (b), (c), (e) and (f) run on both targets in this task; the rest run locally. Timed items take 3 warm-ups and 5 runs with the query cache off in an exclusive window; the median is judged. Each item records its adopted fallback.
     - (a) A `PYTHON3 SET` script with the explicit `SIM_STAGE` `EMITS`, emitting several DataFrames per group:
       - unused columns as NULL (pandas nullable dtypes, `None` in object columns) vs sentinels (0, FALSE, `''`);
       - read back exactly;
       - the cost per million rows of either form, and of the 13-column stage row against S3's 8-column emission (the budget's width row).
     - (b) `CREATE TABLE` with 2 + 2,000 `BOOLEAN` columns and 2,002 column comments, inside a transaction with DML; also 2 + 4,000 columns. Record the limit and the time of a 2 + 900 commented-column `CREATE` (the budget's DDL row).
     - (c) Layout C or A, decided once for both targets.
       - Stage synthetic outcome strings of 400 games (the S4 generator): 100k locally and 1M on AWS. Time `INSERT … SELECT SUBSTR(…) = '1'` into a 400-column table (C), and the copy into `SIM_PATH_VEC (RUN_ID, SIM_ID, OUTCOMES)` (A).
       - Also time 900 games (preseason, for information), a 12-column copy of 1.2M / 12M playoff rows (the second `SIM_TEAM` write) and `TRUNCATE TABLE` of the stage.
       - Compare with S4's C and A loads (local 28.9 s and 16.6 s; AWS 1M 30.6 s and 7.2 s).
       - **Gate:** C if the median derivation at 400 games is ≤ 5 s locally (100k) and ≤ 10 s on AWS (1M). Otherwise layout A on both targets, `PARTITION BY RUN_ID`, logged as a decision.
     - (d) Batch ids from `CROSS JOIN (VALUES BETWEEN 0 AND :last_batch) AS b("BATCH_ID")` inside a `GROUP BY` fan-out, with the upper limit bound (`:last_batch`) and verbatim (`::last_batch`); fallback: a data table `SIM_BATCH_NUMBERS` 0–9999.
     - (e) `PARTITION BY RUN_ID` sparse `SIM_TEAM` with a season's worth of runs stored against a single run:
       - locally 30 runs of 100k (36M rows), on AWS 20 runs of 1M (240M rows);
       - S4's sparse Q1 k = 1 and Q2 texts (`spikes/s4/queries.py`), p95 of 20;
       - fallback if more than 1.5× slower: no partitioning, plus a `DISTRIBUTE BY SIM_ID` retest; logged.
     - (f) `EXA_SYSTEM_EVENTS` columns (version, nodes, DB RAM); `exa.meta.vm_id`, `node_id` and `node_count`; numpy, pandas and Python versions of the builtin SLC.
     - (g) `JSON_VALUE(DETAIL, '$.x' RETURNING DECIMAL(18,0) | DOUBLE | VARCHAR(64))` on a summary row.
     - (h) `NORMAL_CDF` (A&S 26.2.17):
       - accuracy against `statistics.NormalDist` at 1e-7 over −8…8;
       - its per-row cost over 300k synthetic training rows, against the same polynomial inlined in the view's SQL, adopting the inlined form if the function is more than 2× slower;
       - one point of a two-version view filtered on `PARAMS_VERSION` against the unfiltered view (the filter must prune the other version);
       - `CAST(<DOUBLE> AS DECIMAL(5,4))` (reuse Phase 3 1.1i).
     - (i) Lua:
       - `CREATE TABLE`, `INSERT` and `COMMIT` in a nested `EXECUTE SCRIPT` called from a script that committed its step row; an `error()` after it keeps the committed rows (reuse Phase 3 1.1h);
       - the time of 14 small committed `UPDATE`s;
       - `TRUNCATE TABLE` followed by `ROLLBACK` inside a script keeps the rows.
     - (j) `KILL STATEMENT` on a session whose Lua script waits in `os.sleep`. Record whether the script stops, the session is `IDLE`, and the committed `RUNNING` row stays (the premise of the cleanup test's hook). Fallback: the hook ends the script without cleanup.
     - **Gate:** (a) fixes the stage value form, (b) the column limit, (c) C vs A for both targets, (d) the batch source, (e) the partitioning. (f)–(j) negative → their fallbacks.
   - [ ] 1.2 `Namespace.sim_schema` and `{SIM_SCHEMA}` in the placeholder renderer. Unit tests in `tests/unit/test_namespace.py` and `test_meta_ddl.py`.
   - [ ] 1.3 Toolchain:
     - `numpy==1.26.4` in the dev group (`uv lock`); `engine/__init__.py`;
     - mypy and coverage include `engine`; `make coverage` reports `engine/*` with ≥ 90 % (`engine/lua` excluded);
     - `tests/unit/test_engine_stream.py`: the host numpy version is 1.26.4 (the SLC's, `spikes/s3/results/numpy_stream.json`), and a fixed digest of one batch's season stream (normals, uniforms, team offsets) and postseason stream is pinned, so a numpy upgrade on the host fails loudly. Stream equality with the database is proven per target by probe 1.1f and `test_database_equals_host_engine`.
   - [ ] 1.4 Engine core `engine/core.py`, copied from `spikes/s3/engine_proto.py` and changed per Key Interfaces (§ Engine core changes): [expert]
     - input parse;
     - validation, including the `TEAM_SIGMA` consistency rule;
     - the `spawn(2)` draw layout with the team-offset block, and C over every conference;
     - margins with shared team offsets; fixed results with the clamped inverse CDF;
     - known (played and unplayed) `CCG` rows with their own uncertainty, no-CCG conferences, the minimum size;
     - `NON_FBS` teams;
     - rule sets and bye rules;
     - `w_rating`;
     - the stage outputs (an empty outcome string when G = 0) with checksum, model digest and stage seconds.

     Test-first in `tests/unit/test_engine_core.py` (engine-contract scenarios) and `tests/unit/test_selection_rules.py` (selection-rule scenarios), with crafted models in `tests/engine/models.py` (from `spikes/s3/synth_season.py`). Keep the vectorized style: no per-sim Python loops (profile per stage, as S3).
   - [ ] 1.5 Loop reference `tests/engine/loop_ref.py`, ported from `spikes/s3/engine_loop_ref.py` to the Phase 4 rules: plain loops and `Fraction`s, never importing `engine.core`.
     - `tests/unit/test_engine_loop_ref.py::test_vectorized_matches_loop_reference`: 500 seasons of a crafted season, every output field and the checksum equal.
     - The test also asserts that every rule was exercised: two-way tie with and without a meeting, three-way tie, overtime, nonzero team offsets, a fixed result, a known unplayed and a played championship game, a no-CCG conference, a small conference, each rule set, the guarantee in and out, an auto-bid outside the top 12.
   - [ ] 1.6 UDF wrapper and bundle.
     - `engine/udf.py`: chunked `get_dataframe(num_rows=4096)`, parse, `simulate_batch`, one stage frame per batch (value form per 1.1a), `SUMMARY` mode, and the instance identity from `exa.meta`.
     - `engine/bundle.py`: renders `CREATE OR REPLACE PYTHON3 SET SCRIPT {INFRA_SCHEMA}.SIM_ENGINE(<run cols>, <model cols>) EMITS (<stage cols>) AS …`, computes `ENGINE_VERSION`, and fails at 1,990,000 characters.
     - Unit tests `tests/unit/test_engine_udf.py` (fake `ctx`/`exa`) and `tests/unit/test_engine_bundle.py`.
   - [ ] 1.7 Calibration fits and gates, the pure part of `engine/calibrate.py`:
     - `ELO_CENTER` as the mean FBS Elo;
     - OLS per feature set, pooled or per week phase, class defaults, σ/τ weighted least squares, totals;
     - the Elo logistic fit (IRLS, fixed iteration cap and tolerance);
     - the resume grid search;
     - rounding to column scales and seed SQL rendering for both versions and three phases;
     - target evaluation: per-phase relative targets, bias with its standard error, Spiegelhalter Z, the bucket tolerance max(0.05, 3·SE).

     Unit tests `tests/unit/test_calibrate.py` on generated arrays with known truth:
     - `HFA` within 0.3 and `B_SP` within 0.05 at n = 20,000, also per phase with phase-specific truth;
     - `ELO_CENTER` equal to the generated mean;
     - `SIGMA_1` and `TAU` within 5 %;
     - the Elo scale within 5 %;
     - the grid recovers the generating weights exactly;
     - identical output text for identical input;
     - `test_gate_tolerates_noise_and_catches_overconfidence` (calibration scenario): 1,000 seeded perfect populations pass in at least 94 %, and 1,000 populations with σ 20 % too small fail in at least 50 %.

     The pure calibration-check function of the backtest (`engine/backtest.py`) is unit-tested in `tests/unit/test_backtest.py` on hand-computed cases: Brier score, Z, and the dispersion with its clustered standard error.
   - [ ] 1.8 Sim league `tests/synthetic/sim_league.py` per Key Interfaces, reusing Phase 3's league machinery. Its committee and its 2024/2025 field selection are its own plain-Python implementation, not `engine.core`.

     Unit tests `tests/unit/test_sim_league.py`:
     - deterministic output;
     - conference sizes per season;
     - no conference pair twice (base league);
     - one championship game per seeded conference and season through 2025, none stored for 2026;
     - the calendar, with 5 December 2026 in week 15;
     - 12-team fields with valid bracket games for 2024/2025;
     - the final committee ranking per season;
     - the named facts of Key Interfaces;
     - the `with_post_selection_game` variant;
     - mirror rows typed per catalog;
     - the `scale="real"` option's counts.
   - [ ] 1.9 Seeds and configuration:
     - `sql/sim/011_selection_rules.sql`;
     - the initial generated-format `010_score_model_params.sql` (two versions × three phases) and `012_resume_weights.sql` (`UNCALIBRATED`, both versions);
     - `engine/sim-config.yaml` (local 100,000 / 5,000; AWS 1,000,000 / 10,000).

     Unit tests `tests/unit/test_sim_seeds.py`:
     - rule sets cover 2024 onward without overlap;
     - codes valid;
     - `GUARANTEE_SCHOOL` set only for 2026;
     - the params table has exactly the three phases of `CURRENT` and of `HOLDOUT_2023`, with contiguous phase ranges and equal shared columns;
     - the resume table has one row per version;
     - config blocks valid (multiple, 100–20,000).
   - [ ] 1.10 Hand-off H1 (cross-plan prerequisite; owner: Phase 2 tasks 4.2 and 5.2 in `../exasol-cfb-demo-p2`, relayed by the orchestrator). Deliver the H1 text of Dependencies to the owner before Phase 2's 4.2 starts.
     - **Acceptance:** Phase 2's `tests/it/test_run_logs_it.py` covers a stale `SIMULATE` row (idle session) and a live idle `DEPLOY` row (not stale); `acquire_lock`/`release_lock` and the host `DEPLOY` helper are importable from this plan's bundle and host code.
     - Record the outcome here and in the decision log. No edit of Phase 2 files from this plan.
   - [ ] 1.11 Check Q1 and Q2 on cfp.org (format and selection protocol) before the first committee ranking of 2026 (about 2026-11-03), with zero CFBD calls. Record the source in `SELECTION_RULES.SOURCE_NOTE`; a different reading changes `G6_BID` (one value) or the guarantee semantics, logged.

2. **Schema and deploy**
   - [ ] 2.1 Extend `vs/host/sqlobjects.py` (owned by Phase 3's 1.3/2.1; no parallel edits): a data-table kind (`CREATE TABLE IF NOT EXISTS`, column comparison against `EXA_ALL_COLUMNS`, a difference stops the deploy), keep rules (registered data tables, `SIM_PATH_R[0-9]+`), per-schema allowed references. Extend `tests/unit/test_sqlobjects.py` and `test_model_deploy.py`.
   - [ ] 2.2 `engine/deploy.py`, `scripts/sim.py deploy` and `make sim`:
     - called from `scripts/deploy.py` after the model;
     - the run lock as kind `DEPLOY` (H1b);
     - config block and overrides, with the `TEST_*` hooks removed outside `TEST_`;
     - the engine script (bundle) and the Lua scripts (sim entries in `loader/bundle.py`);
     - the `refresh_steps` entry;
     - the report.

     Unit tests `tests/unit/test_sim_deploy.py` (fake connection) and `tests/cli/test_sim_cli.py`. Harness helpers per Migration. Integration test file first: `tests/it/test_sim_deploy_it.py`.
   - [ ] 2.3 Data tables `sql/sim/data/*.sql` per Key Interfaces, every column commented (covered by 2.2's tests).
   - [ ] 2.4 Functions and score views `020`, `021`, `100`, `110`, `120`, `130`, with the week-phase join and the `NORMAL_CDF` form chosen by 1.1h. Integration tests first:
     - `tests/it/test_score_model_it.py`;
     - `tests/it/test_win_probability_it.py`;
     - the invariance scenarios in `tests/it/test_sim_variants_it.py`.
   - [ ] 2.5 Selection week and engine input views `030`, `200`, `210`, `220`, `230`, `240` and `300_sim_team_odds.sql`. Integration tests first in `tests/it/test_sim_inputs_it.py`: the selection-week and Pac-12 scenarios, plus supporting tests for the mapping table:
     - records without the championship game;
     - capped differential;
     - SOS up to the selection week;
     - head-to-head rows;
     - known and played championship rows;
     - `TEAM_SIGMA` and `post_team_sigma`;
     - the pre-2024 tier mapping;
     - games and championship games after the selection week left out, with their checks;
     - the cross-conference flag;
     - non-FBS opponents;
     - guarantee index;
     - rule codes;
     - the index order.

3. **Runs**
   - [ ] 3.1 Lua `engine/lua/simlib.lua`, `simulate.lua`, `simulate_from.lua` and `drop_sim_run.lua`, bundled with `loadlib.lua`: [expert]
     - the lock as run kind `SIMULATE` through H1a's functions, then the abandoned-run cleanup;
     - the seven steps with committed step rows, the snapshot that evaluates `ENGINE_TEAM_AS_OF` once;
     - generated outcome-table DDL with per-game comments (or the A table per 1.1c);
     - the fan-out, the split and the `LEVERAGE` SQL;
     - the digest, completeness and instance checks;
     - sizing from `EXA_SYSTEM_EVENTS`;
     - `TRUNCATE` of the stage in the finish, failure and cleanup paths;
     - failure cleanup that never leaves a `RUNNING` row or a partial output;
     - `SIMULATE_FROM` (copy of the base inputs with fixed results, same seed and batch size);
     - `DROP_SIM_RUN`;
     - the test hooks `TEST_FAIL_BATCH` and `TEST_SLEEP_BEFORE_STEP` / `TEST_SLEEP_SECONDS`.

     `pquery` everywhere, `ROLLBACK` before logging a failure, and `R0003` treated as "cannot log" (Phase 2 recipe). Integration tests first: `tests/it/test_sim_runs_it.py` and `tests/it/test_sim_engine_it.py`.
   - [ ] 3.2 `engine/lua/refresh_simulate.lua` (step 300, the abandoned-run cleanup at its start, the skip after the selection week, `LOAD_RUN_ID`), integration tests first in `tests/it/test_sim_refresh_step_it.py`.
   - [ ] 3.3 Host wrappers `scripts/sim.py simulate | verify | drop` and their make targets, including `make simulate BASE= FIX=`; verify runs the `SUMMARY` mode. CLI tests `tests/cli/test_sim_cli.py`; verify and drop scenarios in `tests/it/test_sim_runs_it.py`.
   - [ ] 3.4 Local performance smoke, in a "local free" window: seed `sim_league(scale="real")` into `PERF_`, deploy, then 3 × `make simulate SIMS=100000 BATCH=5000 ARGS="--prefix PERF_"` at 2026 week 9.
     - Record sizing (`EXA_SYSTEM_EVENTS`, `podman logs` lines), step seconds against every budget row, and the median, min and max against the 30 s target.
     - Also one preseason-point run (recorded, not gated) and one 100k `REFRESH_WEEK` with replay (< 2 min).
     - Drop `PERF_`. Write the numbers to `docs/engine-verification.md` (draft).

4. **Calibration and backtest**
   - [ ] 4.1 `make calibrate`: the DB part of `engine/calibrate.py` and `scripts/sim.py calibrate`:
     - the run lock as kind `DEPLOY`;
     - steps 1–5 of `sim-engine/calibration`, with `--feature-set` and `--resume-only`;
     - the resume fit at the selection point against the final committee ranking (`RANKING`, `CFP`, `IS_FINAL_POLL`);
     - the views `400_score_model_report.sql`, `410_score_calibration_buckets.sql` and `420_score_source_report.sql`;
     - the docs writer and the gate.

     Integration tests first: `tests/it/test_calibrate_it.py`.
   - [ ] 4.2 `make backtest`: `engine/backtest.py` and `scripts/sim.py backtest`.
     - The points `SELECTION_WEEK_SEQ − k`.
     - The views `500_backtest_field.sql`, `510_backtest_report.sql` and `520_backtest_calibration.sql`.
     - The printed calibration review, the docs writer and the gate.

     Integration tests first: `tests/it/test_backtest_it.py`.

5. **Documentation** (re-read each file first; another agent edits `docs/roadmap.md` now)
   - [ ] 5.1 `docs/user-guide.md`, section "Score model and simulations":
     - the score model in one paragraph, its parameters and week phases, and how to read `SCORE_MODEL_REPORT` and `SCORE_SOURCE_REPORT`;
     - the selection rules, the selection week and their simplifications;
     - `make simulate` (and `BASE=`/`FIX=`) / `sim-verify` / `sim-drop` / `calibrate` / `backtest`;
     - the registry columns, reproducibility, sizing (`nrOfCores`, batch sizes per target);
     - the outputs, `SIM_TEAM_ODDS`, and the team offsets in one sentence;
     - the refresh step; killed runs and their cleanup;
     - retention: weekly refresh runs are kept for the season (Phase 5 movement reads them); manual, forced and superseded backtest runs are dropped with `make sim-drop`; the storage per 1M run;
     - the zero-call guarantee.
   - [ ] 5.2 `specs/architecture.md`:
     - § Engine contract: the `spawn(2)` draw layout with the team-offset block replacing "season normals, overtime uniforms, …"; game roles `REMAINING`/`PLAYED_CONF`/`CCG`; `TEAM_SIGMA`, `game_sigma`, `post_team_sigma`; `NON_FBS` teams; the required parameters and rule codes; the fixed-result method; the one-shape stage output; `TEAM_ID` in `SIM_TEAM`; `SIM_PATH_R<run>` (named in `SIM_RUN.PATH_TABLE`) and the roster `SIM_RUN_TEAM` (D20's `TEAMS`); `SIMULATE_FROM`; the rating-update flag not implemented;
     - § Score model: the as-of inputs, horizon σ and its split, week phases, totals, host calibration with generated seeds, the two params versions, the selection week;
     - the schema table: `CFB_SIM` contents and `CFB_INFRA.SIM_ENGINE`/`SIMULATE`/`SIMULATE_FROM`/`REFRESH_SIMULATE`/`DROP_SIM_RUN`; the run kinds `SIMULATE` and `DEPLOY`;
     - design question "CFP rules: G6 auto-bid wording" → resolved by [9] with the result of 1.11.
   - [ ] 5.3 `docs/roadmap.md`:
     - decisions marked `Promotes to ADR: yes`, numbered after the last D-number present;
     - Phase 4 bullets: the 2026 rule set as decided, `HOLDOUT_2023`, the refresh step at 300, the selection week;
     - Phase 5: per-run outcome tables need generated SQL; `SIMULATE_FROM` serves the forced re-sim;
     - mark Phase 4 progress;
     - backlog: in-simulation rating updates, bracket-only postseason sims, 16/24-team formats, automatic run pruning.
   - [ ] 5.4 `CLAUDE.md` dense bullets:
     - `engine/` + `sim-engine/` domain;
     - numpy 1.26.4 in the main dev venv;
     - `make sim`/`simulate`/`sim-verify`/`sim-drop`/`calibrate`/`backtest`;
     - consumers read only `DONE` runs; the next lock holder cleans up killed runs;
     - calibrated seeds are generated, reviewed and committed;
     - batch-size picks per target (local 5,000, AWS 10,000, AWS 100k at 1,000).

     `specs/mission.md`:
     - § Commands (the new targets);
     - § Project Structure (`engine/`);
     - Glossary "Resume score / auto-bid" with the 2026 rule ("4 power-conference champions + the best-ranked Group of Six team + Notre Dame if top 12; 7 at-large");
     - Capability 6's selection wording.

6. **Live verification** (last; zero CFBD calls; AWS `default` deploy and run only, local in a "local free" window)
   - [ ] 6.1 Gates, deploy and data checks:
     - gates: Phase 2 group J (AWS backfill + snapshot) and Phase 3 group K done; H1 accepted (1.10);
     - record `make quota TARGET=aws` and `SELECT MAX(LOGGED_AT) FROM CFB_META.FETCH_LOG`;
     - `make deploy TARGET=aws`, which uses the `aws` config block;
     - `SELECT * FROM CFB_SIM.SELECTION_WEEK WHERE SEASON BETWEEN 2024 AND 2026`. Expect 2024: 15, `CCG`, last regular week 16; 2025: 15, `CCG` (Q14); 2026: 14, `DECEMBER` until its championship games are stored. Any other result is logged with the stored week rows;
     - check Phase 3's 2026 championship detection for the American Athletic (X1, Q15) and record it.
   - [ ] 6.2 `make calibrate TARGET=aws` on the real 2013–2025 data:
     - review the report per week phase and the targets;
     - on a miss, rerun with `ARGS="--feature-set SP_ELO_PHASED"` and log the decision with both reports;
     - record outcomes for Q3, Q7, Q8, Q10 and Q16 (decision log);
     - the owner commits the generated seeds (U3);
     - redeploy locally after the restore (6.4).
     - [ ] 6.2b (only if a target is still missed) Feature set F2 with its coefficient column, unit- and integration-tested like 1.7/2.4; recalibrate; log the decision with the reports.
   - [ ] 6.3 `make backtest TARGET=aws SIMS=100000 BATCH=1000`:
     - the gate at k = 0, the report and `docs/backtest-2024-2025.md`;
     - the pooled calibration check: a triggered review is logged as a decision on [29] (`TAU`, the offset share);
     - the Q6 outcome. A gate miss is logged with the per-season overlap and leads to the review of [11] (resume features), not to a change inside this task.
   - [ ] 6.4 Demo-scale runs:
     - AWS:
       - 3 × `make simulate TARGET=aws SEASON=2026 WEEK=<current> SIMS=1000000 BATCH=10000` (median judged);
       - 3 × 100k at `BATCH=1000`;
       - one forced re-sim (`BASE=`, `FIX=`) of a 1M run;
       - `make sim-verify` on one run of each origin;
     - S4's Q1 k = 1 / Q2 sparse texts against the production tables with every run stored by then (p95 of 20);
     - local:
       - `make restore TARGET=local NAME=<Phase 2 snapshot> CONFIRM=yes` if needed, then `make deploy`;
       - 3 × 100k at `BATCH=5000`;
       - `REFRESH_WEEK(<current>)` in replay with step 300 (< 2 min);
     - record sizing and step breakdowns.
   - [ ] 6.5 Zero-call proof and write-up:
     - `make quota TARGET=aws` used calls equal 6.1's, and `FETCH_LOG` has no row after 6.1's maximum;
     - `docs/engine-verification.md`, derived numbers only: calibration metrics per phase, backtest metrics and calibration check, timings per step and target with sizing, verify results, versions;
     - the demo beat's N for 100k local and 1M AWS.

## Parallelization

| Parallel Group | Tasks |
|----------------|-------|
| Group A | 1.1, 1.2, 1.3, 1.4, 1.7, 1.8, 1.9, 1.10, 1.11 |
| Group A2 | 1.5, 1.6 |
| Group B | 2.1, 2.3 |
| Group C | 2.2 |
| Group D | 2.4 |
| Group E | 2.5 |
| Group F | 3.1 |
| Group G | 3.2, 3.3, 4.1 |
| Group H | 3.4, 4.2 |
| Group I | 5.1, 5.2, 5.3, 5.4 |
| Group J (sequential, gated) | 6.1 → 6.2 (→ 6.2b) → 6.3 → 6.4 → 6.5 |

Sequential dependencies:
- A → B → C → D → E → F → G → H → I → J. A2 needs 1.4 and runs alongside B; 1.6 must be done before 2.2.
- 1.4 and 1.5 share the rules text of `sim-engine/engine-contract` and `selection-rules`, not code; 1.5's equality test needs 1.4. 1.6 needs 1.4's interface. 1.3 before 1.4's tests run.
- 1.1 needs a "local free" window for its local part. Its AWS part needs only synthetic data and AWS (free for this work), not Phase 2's backfill. 1.1(c) fixes the layout before 1.4's stage outputs and 3.1's SQL depend on it.
- 1.10 is delivered in group A; its acceptance (Phase 2's 4.2 and 5.2) gates 2.2 and 3.1. 1.11 has a calendar deadline (about 2026-11-03) and no code dependency.
- Before group B: Phase 3's groups A–H (deployer, model views, league machinery, harness) and Phase 2's 3.1, 4.1, 4.2 (with H1a), 4.3, 5.2 (with H1b), 6.1, 6.2.
- 2.1 needs Phase 3's `sqlobjects.py`. 2.2 needs 1.2, 1.6, 1.9, 2.1, 2.3, H1b and 1.1(a, b). 2.4 needs 1.1(h), 1.8 and 2.2. 2.5 needs 2.4.
- 3.1 needs 1.1(a, c, d, g, i, j), 2.5, H1a and Phase 2's `loadlib.lua`. 3.2 needs 3.1 and Phase 2's 4.3. 3.3 needs 3.1. 3.4 needs 3.1, 3.2, 1.8 (`scale="real"`) and a "local free" window.
- 4.1 needs 1.7, 2.4 and 2.5 (the resume fit reads `ENGINE_TEAM_AS_OF` and `SELECTION_WEEK`). 4.2 needs 1.7, 3.1 and 3.3; its tests load the test parameters into both versions, so it does not wait for a real calibration.
- Group I needs green A–H; group J needs I, Phase 2's group J, Phase 3's group K, U1–U3.

## Dead Code Removal

| Type | Location | Reason |
|------|----------|--------|
| — | none | This plan adds new code only. `spikes/` stays untouched as the evidence of D19/D20 (its README already marks it throwaway); `engine_proto.py` and `engine_loop_ref.py` are copied, not moved, so the write-ups' references stay valid. Removing `spikes/` is the owner's call after Phase 8 |

## Verification

### Scenario Coverage

| Scenario | Test Type | Test Location | Test Name |
|----------|-----------|---------------|-----------|
| engine-contract: A batch is determined by its seed, its id and its size | Unit | `tests/unit/test_engine_core.py` | `test_batch_determined_by_seed_id_and_size` |
| engine-contract: A run does not depend on how its batches are scheduled | Integration | `tests/it/test_sim_engine_it.py` | `test_run_independent_of_batch_scheduling` |
| engine-contract: The database and the host compute the same batches | Integration | `tests/it/test_sim_engine_it.py` | `test_database_equals_host_engine` |
| engine-contract: Changing one game leaves every other draw unchanged | Unit | `tests/unit/test_engine_core.py` | `test_one_game_change_keeps_other_draws` |
| engine-contract: Shared team strength widens the spread of season win totals | Unit | `tests/unit/test_engine_core.py` | `test_team_offsets_widen_win_totals` |
| engine-contract: A tied margin goes to overtime | Unit | `tests/unit/test_engine_core.py` | `test_tied_margin_goes_to_overtime` |
| engine-contract: A fixed result decides the game on the winner's side | Unit | `tests/unit/test_engine_core.py` | `test_fixed_result_on_winner_side` |
| engine-contract: An invalid model is rejected, not repaired | Unit + Integration | `tests/unit/test_engine_core.py`, `tests/it/test_sim_engine_it.py` | `test_invalid_model_rejected`, `test_invalid_model_error_surfaces_from_udf` |
| engine-contract: Every simulated season produces a complete playoff | Unit | `tests/unit/test_engine_core.py` | `test_every_season_complete_playoff` |
| selection-rules: In 2026 the four power champions and the best-ranked Group of Six team get in | Unit | `tests/unit/test_selection_rules.py` | `test_2026_power_champions_and_best_g6_team` |
| selection-rules: A power champion ranked outside the top 12 still gets in | Unit | `tests/unit/test_selection_rules.py` | `test_2026_power_champion_outside_top12_gets_in` |
| selection-rules: The guaranteed team gets in within the top 12 | Unit | `tests/unit/test_selection_rules.py` | `test_2026_guarantee_within_top12` |
| selection-rules: In 2025 the five best-ranked champions get in, whatever their conference | Unit | `tests/unit/test_selection_rules.py` | `test_2025_five_best_champions` |
| selection-rules: In 2024 the four best-ranked champions get the byes | Unit | `tests/unit/test_selection_rules.py` | `test_2024_champion_byes` |
| selection-rules: From 2025 on, seeding is straight by rank | Unit | `tests/unit/test_selection_rules.py` | `test_straight_seeding_from_2025` |
| selection-rules: Ties in the standings use the simplified tiebreakers | Unit | `tests/unit/test_selection_rules.py` | `test_simplified_tiebreakers` |
| selection-rules: A Pac-12 team can take the 2026 Group of Six bid | Unit | `tests/unit/test_selection_rules.py` | `test_2026_pac12_team_takes_g6_bid` |
| selection-rules: In 2026 the Pac-12 is a Group of Six conference | Integration | `tests/it/test_sim_inputs_it.py` | `test_pac12_is_g6_from_2026` |
| selection-rules: A known championship pairing that is not played yet is simulated between those teams | Unit | `tests/unit/test_selection_rules.py` | `test_known_unplayed_ccg_pairing` |
| selection-rules: Championship games use the known pairing, and small conferences crown no champion | Unit | `tests/unit/test_selection_rules.py` | `test_known_ccg_no_ccg_and_small_conference` |
| selection-rules: The bracket pairs the seeds and gives the first round a home field | Unit | `tests/unit/test_selection_rules.py` | `test_bracket_pairing_and_home_field` |
| selection-rules: The seeded rule sets match the published rules | Integration | `tests/it/test_sim_seeds_it.py` | `test_selection_rules_match_published_rules` |
| score-model: A team's rating combines its SP+ prior and its current Elo | Integration | `tests/it/test_score_model_it.py` | `test_rating_combines_sp_prior_and_elo` |
| score-model: Teams without an SP+ prior or an Elo fall back | Integration | `tests/it/test_score_model_it.py` | `test_rating_fallbacks` |
| score-model: Home field counts only off neutral sites | Integration | `tests/it/test_sim_variants_it.py` | `test_home_field_only_off_neutral_sites` |
| score-model: The uncertainty grows with the weeks until the game | Integration | `tests/it/test_score_model_it.py` | `test_sigma_grows_with_horizon` |
| score-model: Predicted points and win probability follow from the margin | Integration | `tests/it/test_score_model_it.py` | `test_points_and_win_probability` |
| score-model: Every game the engine simulates has a prediction | Integration | `tests/it/test_score_model_it.py` | `test_every_simulated_game_has_prediction` |
| score-model: Predictions at a point do not change when later facts change | Integration | `tests/it/test_sim_variants_it.py` | `test_predictions_leak_free` |
| score-model: The params version selects the parameters | Integration | `tests/it/test_score_model_it.py` | `test_params_version_selects_parameters` |
| score-model: The point's week phase selects the coefficients | Integration | `tests/it/test_score_model_it.py` | `test_week_phase_selects_coefficients` |
| score-model: Engine-facing values are fixed-scale decimals | Integration | `tests/it/test_score_model_it.py` | `test_engine_facing_decimals_and_normal_cdf` |
| calibration: Calibration writes the parameters and the report | Integration (CLI + DB) | `tests/it/test_calibrate_it.py` | `test_calibrate_writes_params_and_report` |
| calibration: Per-phase coefficients are fitted on request | Integration (CLI + DB) | `tests/it/test_calibrate_it.py` | `test_phased_feature_set` |
| calibration: Training rows hold only what was known at their point | Integration (CLI + DB) | `tests/it/test_calibrate_it.py` | `test_training_rows_leak_free` |
| calibration: The holdout parameters never see the evaluation seasons | Integration (CLI + DB) | `tests/it/test_calibrate_it.py` | `test_holdout_never_sees_evaluation_seasons` |
| calibration: The report compares the model with the betting line in every phase of the season | Integration (CLI + DB) | `tests/it/test_calibrate_it.py` | `test_report_compares_with_line_per_phase` |
| calibration: A well-calibrated model passes the gate, and an overconfident one does not | Unit | `tests/unit/test_calibrate.py` | `test_gate_tolerates_noise_and_catches_overconfidence` |
| calibration: A missed target fails the command and keeps the report | Integration (CLI + DB) | `tests/it/test_calibrate_it.py` | `test_missed_target_exit_3_keeps_report` |
| calibration: The same data gives the same parameters | Integration (CLI + DB) | `tests/it/test_calibrate_it.py` | `test_same_data_same_seed_files` |
| calibration: The resume weights are fitted to the committee's final rankings | Integration (CLI + DB) | `tests/it/test_calibrate_it.py` | `test_resume_weights_fitted` |
| win-probability: A game since 2013 uses its betting line | Integration | `tests/it/test_win_probability_it.py` | `test_line_probability_since_2013` |
| win-probability: An older game uses its pregame Elo | Integration | `tests/it/test_win_probability_it.py` | `test_elo_probability_for_older_game` |
| win-probability: A game without a line or an Elo has no probability | Integration | `tests/it/test_win_probability_it.py` | `test_no_line_no_elo_no_probability` |
| win-probability: The upset magnitude is what the winner was not expected to get | Integration | `tests/it/test_win_probability_it.py` | `test_upset_magnitude` |
| sim-runs: One call simulates the rest of the season and returns its timeline | Integration | `tests/it/test_sim_runs_it.py` | `test_simulate_returns_timeline` |
| sim-runs: The registry records what makes the run repeatable | Integration | `tests/it/test_sim_runs_it.py` | `test_registry_records_reproducibility_key` |
| sim-runs: The run keeps its inputs when the data changes | Integration | `tests/it/test_sim_runs_it.py` | `test_run_keeps_inputs_when_data_changes` |
| sim-runs: A run can be verified from its stored inputs | Integration (CLI + DB) | `tests/it/test_sim_runs_it.py` | `test_sim_verify_matches` |
| sim-runs: Only the playoff teams of each season are stored | Integration | `tests/it/test_sim_runs_it.py` | `test_only_playoff_teams_stored` |
| sim-runs: Every simulated game's outcome is stored per season | Integration | `tests/it/test_sim_runs_it.py` | `test_outcome_table_per_run` |
| sim-runs: The rooting guide's counts are precomputed | Integration | `tests/it/test_sim_runs_it.py` | `test_leverage_equals_recomputed_counts` |
| sim-runs: Win and title distributions cover every team | Integration | `tests/it/test_sim_runs_it.py` | `test_win_and_title_distributions` |
| sim-runs: The selection week is the week of the power championship games | Integration | `tests/it/test_sim_inputs_it.py` | `test_selection_week` |
| sim-runs: Games after the selection week are not simulated | Integration | `tests/it/test_sim_runs_it.py` | `test_games_after_selection_left_out` |
| sim-runs: A run is refused while another run holds the lock | Integration | `tests/it/test_sim_runs_it.py` | `test_run_refused_while_locked` |
| sim-runs: An idle deploy keeps the lock until its session ends | Integration | `tests/it/test_sim_runs_it.py` | `test_idle_deploy_keeps_lock` |
| sim-runs: A failed run leaves no outputs | Integration | `tests/it/test_sim_runs_it.py` | `test_failed_run_leaves_no_outputs` |
| sim-runs: A killed run is cleaned up by the next run | Integration | `tests/it/test_sim_runs_it.py` | `test_killed_run_cleaned_up_by_next_run` |
| sim-runs: An input error stops the run before the engine | Integration | `tests/it/test_sim_variants_it.py` | `test_input_error_stops_before_engine` |
| sim-runs: Points after the selection week and uneven batches are refused | Integration | `tests/it/test_sim_runs_it.py` | `test_points_after_selection_and_uneven_batches_refused` |
| sim-runs: A forced result re-simulates from the base run's inputs and draws | Integration | `tests/it/test_sim_runs_it.py` | `test_simulate_from_forced_result` |
| sim-runs: A run's outputs can be dropped | Integration (CLI + DB) | `tests/it/test_sim_runs_it.py` | `test_sim_drop` |
| refresh-step: The refresh simulates the season after the model step | Integration | `tests/it/test_sim_refresh_step_it.py` | `test_refresh_simulates_after_model_step` |
| refresh-step: The step follows the deployment's configuration | Integration | `tests/it/test_sim_refresh_step_it.py` | `test_step_follows_configuration` |
| refresh-step: A refresh after the selection week skips the simulation | Integration | `tests/it/test_sim_refresh_step_it.py` | `test_refresh_after_selection_week_skips_simulation` |
| refresh-step: A failed simulation stops the refresh and keeps the fetched data | Integration | `tests/it/test_sim_refresh_step_it.py` | `test_failed_simulation_stops_refresh` |
| backtest: The backtest simulates each season from fixed points under its own rules | Integration (CLI + DB) | `tests/it/test_backtest_it.py` | `test_backtest_runs_per_season_and_point` |
| backtest: The actual field comes from the stored playoff games | Integration | `tests/it/test_backtest_it.py` | `test_actual_field_from_playoff_games` |
| backtest: The report scores the odds against the actual field | Integration | `tests/it/test_backtest_it.py` | `test_report_scores_odds` |
| backtest: The backtest checks the calibration of its odds over every team | Integration (CLI + DB) | `tests/it/test_backtest_it.py` | `test_calibration_check_pooled_over_teams` |
| backtest: On the selection weekend the engine picks the actual field | Integration (CLI + DB) | `tests/it/test_backtest_it.py` | `test_selection_weekend_reproduces_field` |
| backtest: The backtest sees only what was known at each point | Integration (CLI + DB) | `tests/it/test_backtest_it.py` | `test_backtest_inputs_leak_free` |
| backtest: A failed gate fails the command and keeps the report | Integration (CLI + DB) | `tests/it/test_backtest_it.py` | `test_failed_gate_exit_3_keeps_report` |
| deploy: One command installs the simulation schema | Integration (CLI + DB) | `tests/it/test_sim_deploy_it.py` | `test_make_sim_installs_schema` |
| deploy: A redeploy keeps every run | Integration | `tests/it/test_sim_deploy_it.py` | `test_redeploy_keeps_runs` |
| deploy: The full deploy includes the simulation schema | Integration | `tests/it/test_sim_deploy_it.py` | `test_full_deploy_includes_sim` |
| deploy: The simulation schema reads only the model | Integration (CLI + DB) | `tests/cli/test_sim_cli.py`, `tests/it/test_sim_deploy_it.py` | `test_layering_lint_names_reference`, `test_sim_reads_only_model` |
| deploy: A changed data table stops the deploy | Integration | `tests/it/test_sim_deploy_it.py` | `test_changed_data_table_stops_deploy` |
| deploy: A deploy is refused while a run holds the lock | Integration | `tests/it/test_sim_deploy_it.py` | `test_deploy_refused_while_locked` |
| deploy: The configuration follows the target and can be overridden | Integration (CLI + DB) | `tests/it/test_sim_deploy_it.py` | `test_config_block_and_overrides` |

Supporting tests back the integration tests. They do not replace them:
- unit (pure computation, no I/O):
  - the numpy version and stream pin: `tests/unit/test_engine_stream.py`;
  - the vectorized engine against the loop reference: `tests/unit/test_engine_loop_ref.py`;
  - the UDF wrapper with a fake context: `tests/unit/test_engine_udf.py`;
  - the bundle and `ENGINE_VERSION`: `tests/unit/test_engine_bundle.py`;
  - the fits on generated truth: `tests/unit/test_calibrate.py`;
  - the backtest's calibration-check function: `tests/unit/test_backtest.py`;
  - seeds and configuration: `tests/unit/test_sim_seeds.py`;
  - the sim league: `tests/unit/test_sim_league.py`;
  - the deployer extensions: `tests/unit/test_sqlobjects.py`, `test_model_deploy.py`, `test_sim_deploy.py`;
  - namespace and renderer: `test_namespace.py`, `test_meta_ddl.py`;
  - the Lua bundle: `test_lua_bundle.py` (extended);
  - CLI: `tests/cli/test_sim_cli.py`;
- integration: `tests/it/test_sim_inputs_it.py` (the engine input mapping, row by row).

### Manual Testing

All commands run from the repo root against Exasol Personal local (`TARGET=local`) after `make deploy`, unless the row names AWS. None spends a CFBD call.

| Feature | Command | Expected Output |
|---------|---------|-----------------|
| sim-engine/deploy | `make sim`, then again; `make sim TARGET=aws` | Report: 4 reference tables, 2 functions, 17 views, 12 data tables, 5 scripts, comments 100 %; the second run drops nothing and keeps every run; local shows `SIM_CONFIG` 100,000 / 5,000, AWS 1,000,000 / 10,000 |
| sim-engine/score-model | `SELECT G.HOME_TEAM_ID, G.AWAY_TEAM_ID, S.WEEK_PHASE, S.MU, S.SIGMA, S.HOME_POINTS_PRED, S.AWAY_POINTS_PRED, S.HOME_WIN_PROB FROM CFB_SIM.GAME_SCORE_AS_OF S JOIN CFB_MODEL.GAME G ON G.GAME_ID = S.GAME_ID JOIN CFB_MODEL.CURRENT_AS_OF C ON C.AS_OF_SEASON = S.AS_OF_SEASON AND C.AS_OF_WEEK_SEQ = S.AS_OF_WEEK_SEQ WHERE S.PARAMS_VERSION = 'CURRENT' AND S.HORIZON_WEEKS = 1 ORDER BY S.HOME_WIN_PROB DESC LIMIT 10` | Next week's games with plausible margins (\|μ\| < 45), σ ≈ `SIGMA_1`, probabilities between 0 and 1, the current week's phase |
| sim-engine/calibration | `make calibrate TARGET=aws` | Report per version, horizon and week phase, the source-pair report; `HOLDOUT_2023` on 2024–2025 at horizon 1 within the targets in every phase (or a logged `SP_ELO_PHASED`/F2 decision); seeds written; `git diff --stat sql/sim/010_score_model_params.sql sql/sim/012_resume_weights.sql` shows the new coefficients |
| sim-engine/win-probability | `SELECT SEASON, HOME_TEAM_ID, AWAY_TEAM_ID, PROB_SOURCE, UPSET_MAGNITUDE FROM CFB_SIM.GAME_WIN_PROB ORDER BY UPSET_MAGNITUDE DESC NULLS LAST LIMIT 10`; `SELECT PROB_SOURCE, MIN(SEASON), COUNT(*) FROM CFB_SIM.GAME_WIN_PROB GROUP BY 1` | The biggest upsets of all stored history; `ELO` from 1890, `LINE` from 2013, NULL before Elo coverage |
| sim-engine/engine-contract | `make sim-verify RUN=<id>` for a run of each target and a forced run | "20/20 batches match" (or the run's batch count), the run's model digest |
| sim-engine/selection-rules | `SELECT * FROM CFB_SIM.SELECTION_RULES ORDER BY SEASON_FROM`; `SELECT * FROM CFB_SIM.SELECTION_WEEK WHERE SEASON BETWEEN 2024 AND 2026`; `SELECT * FROM CFB_SIM.BACKTEST_REPORT WHERE RUN_LABEL LIKE 'BACKTEST 2025 k=0'` | Three rule sets as published; selection weeks 15 / 15 / 14 (2026 from the December rule until its championship games are stored); the engine's 2025 field vs the actual one (`SELECTED_OVERLAP` ≥ 10) |
| sim-engine/sim-runs | `make simulate SEASON=2026 WEEK=<current>` three times; `SELECT RUN_ID, N_SIMS, SECONDS_TOTAL, SIMS_PER_SECOND, INSTANCES, BATCHES_PER_INSTANCE, DB_RAM_GIB, ENGINE_VERSION FROM CFB_SIM.SIM_RUN ORDER BY RUN_ID DESC LIMIT 3`; `SELECT * FROM CFB_SIM.SIM_TEAM_ODDS WHERE RUN_ID = <id> ORDER BY P_IN_CFP DESC LIMIT 15` | Timeline of 7 steps; 100,000 sims in ≤ 30 s (median of 3) with sizing recorded, 20 batches, 2.5 per instance on an 8-core start; top 15 playoff odds that add up with the rest to 12 |
| sim-engine/sim-runs (AWS dial) | `make simulate TARGET=aws SEASON=2026 WEEK=<current> SIMS=1000000 BATCH=10000` three times | 1,000,000 sims in ≤ 60 s (median of 3); `INSTANCES` 12 |
| sim-engine/sim-runs (forced) | `make simulate BASE=<id> FIX="<game id of a remaining game>=A"` | A `FORCED` run with the base's seed and batch size; the printed odds next to the base run's; the forced game's column all FALSE |
| sim-engine/refresh-step | `make refresh SEASON=2026 WEEK=<current>` in replay mode (`FETCH_MODE=replay`) | Timeline `CHECK`, `FETCH` ×4, `MODEL`, `SIMULATE` all `DONE`, 0 calls; a new `REFRESH` run in `SIM_RUN` |
| sim-engine/backtest | `make backtest TARGET=aws SIMS=100000 BATCH=1000` | 8 runs at the selection week minus 5, 3, 1, 0; report per season and k; gate passed (or logged); the pooled calibration check with its review verdict; `docs/backtest-2024-2025.md` written |

### Checklist

| Step | Command | Expected |
|------|---------|----------|
| Generate | `make generate && git diff --exit-code -- vs/catalog sql/core` | Exit 0 |
| Build | `uv run python -m vs.bundle && uv run python -m loader.bundle && uv run python -m engine.bundle` | Exit 0; `build/sql/` holds the adapter, fetch, loader, model step and `sim_engine.sql` + the four sim Lua scripts, all below 1,990,000 characters |
| Sim lint | `uv run python -m scripts.sim deploy --lint-only` | Exit 0; "lint ok" |
| Test | `make test` | 0 failures (unit + CLI, no DB) |
| Integration | `flock <scratchpad>/cfb-it.lock make test-it` | 0 failures; mock and replay only; 0 requests to `api.collegefootballdata.com` |
| Performance | task 3.4 (local, "local free" window) | Targets met on the median of 3, or a logged decision with the step breakdown |
| Lint | `make lint` | 0 ruff errors, 0 mypy errors (`vs`, `scripts`, `tests`, `loader`, `model`, `engine`) |
| Format | `uv run ruff format --check .` | No changes |
| Coverage | `make coverage` | ≥ 90 % line coverage for `vs/cfbd_vs`, `vs/gen`, `loader`, `model`, `engine` |
| Deploy | `make deploy && make deploy TARGET=aws` | Exit 0 both; `CFB_SIM` comments 100 %; a second run drops nothing |
| Layering | `git grep -n -i -e 'CFBD_VS' -e 'CFB_CORE' -e 'CFB_META' -e '{VS_NAME}' -- sql/sim` | No match |
| Reproducibility | `make sim-verify RUN=<latest>` on local and AWS, and on a forced run | All batches match |
| No stale runs | `SELECT COUNT(*) FROM CFB_SIM.SIM_RUN WHERE RUN_STATUS = 'RUNNING'` after group J | 0 |
| Zero calls | `make quota TARGET=aws` before and after group J | `/info` used calls unchanged |
| No data in git | `git ls-files '*.parquet' '*.cfbd.json'` | Empty |
