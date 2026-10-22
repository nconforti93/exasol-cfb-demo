# Plan: add-cfb-score-engine

## Summary

Roadmap Phase 4 ("Score model + engine"): a calibrated score model, the 2026 playoff selection rules, and the vectorized Python season simulator inside Exasol with a run registry, plus a backtest of the 2024 and 2025 playoff fields. Demo beat: "100k seasons in N seconds, in parallel on every core, in plain Python inside the database", with the sim count as the scale dial (laptop 100k, AWS 1M).

- **Score model** (`CFB_SIM`, SQL views). A linear margin model on the previous season's SP+, the current Elo and home field, with normal margins whose uncertainty grows with the weeks until the game.
  - It is fitted on 2014–2025 by `make calibrate`, which writes reviewed seed files and reports MAE, Brier score and calibration next to the betting line.
  - A `HOLDOUT_2023` fit keeps the backtest honest.
  - Elo-based win probability covers every game back to 1890, and the upset magnitude follows from it.
- **Selection rules.** Rule sets for 2024, 2025 and 2026, configured from a reviewed table:
  - 2026 (from the January 2026 CFP decision): the 4 power-conference champions, the best-ranked Group of Six team, Notre Dame if top 12, 7 at-large; straight seeding with top-4 byes;
  - simplified tiebreakers;
  - a resume formula as the committee, with weights fitted to the committee's final rankings.
- **Engine** (`engine/`, from the S3 prototype; D19, D20 binding).
  - One Python `SET` UDF with explicit `EMITS`, seeded per batch, with a fixed-shape draw layout (common random numbers).
  - It runs once per batch and feeds every output from that single pass:
    - sparse `SIM_TEAM`;
    - a per-run layout-C outcome table;
    - per-run `LEVERAGE`;
    - win and title tallies.
- **Runs.** `CFB_INFRA.SIMULATE` snapshots its inputs, joins the loader's run lock, and records the reproducibility key, the machine sizing and the seconds of every step. `make sim-verify` re-proves any run from its stored inputs.
- **Refresh step** 300 `SIMULATE` after the model step. **Backtest** `make backtest` against the actual 2024/2025 fields.

Everything runs at zero CFBD calls. Tests run on a synthetic "sim league" in the `TEST_` namespace. Live verification runs on AWS after Phase 2's backfill, read only, and on local after a restore.

## Design

### Context

The engine design is settled by the finished spikes, which this plan treats as binding (orchestrator interview, item 1):

- **S3 → D19** (`docs/spikes/s3-python-engine-throughput.md`, `spikes/s3/results/`): Python engine GO.
  - Compute-only 100k sims: 7.06 s local (8 cores, 10k/batch) and 1.55 s on AWS (3 × 4, 1k/batch).
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
  - Loads at 1M on AWS: C 30.6 s, A 7.2 s, sparse derivation 0.41 s.
- **`specs/architecture.md` § Engine contract:**
  - tagged `MODEL_ROWS` input;
  - the reproducibility key;
  - fixed results with a conditional margin;
  - the output shape.
- **`CLAUDE.md` § Environment:** builtin SLC Python 3.12.3 with numpy 1.26.4 (host venvs pinned to it); `nrOfCores` concurrency, auto-sized at each Personal start; `exa.meta.vm_id` because `getpid()` is 1; Exasol's string-to-DOUBLE parse is 1 ulp off.

The inputs come from Phase 3 (`specs/_plans/add-cfb-analytical-model/`, decision [31]):
- `GAME_AS_OF` (neutral site, conference flag, `MARGIN`, `PARTICIPANTS_RULE`, `IS_CONF_CHAMPIONSHIP`, `POSTSEASON_KIND`, `CFP_ROUND`);
- `TEAM_SEASON_AS_OF` (conference, tier, classification, records, conference records, `CONF_POINT_DIFF`, `ELO_AS_OF`, `SP_PRIOR_RATING`);
- `RATING_SP`, `GAME`, and the reference table `CONFERENCE_CHAMPIONSHIP`.

All of it is fixed-scale `DECIMAL`. Phase 3 deliberately leaves indices, μ, σ, totals, ratings, strength of schedule and capped differentials to this phase. Result-dependent games are hidden until their participants are known, which keeps the backtest leak-free by construction.

Phase 2 (`specs/_plans/add-cfb-persistence-refresh/`) provides `REFRESH_WEEK` with registered steps ≥ 100 called as `(run_id, season, week, season_type)`, the run lock in `LOAD_RUN` with stale-row rules, the Lua bundler and the IT harness. Phase 3 provides the SQL-object deployer (`vs/host/sqlobjects.py`), the synthetic league machinery and step 100 `MODEL`.

**2026 CFP rules (web research, 2026-10-22):**
- The CFP management committee decided in January 2026 to stay at 12 teams for 2026.
- Automatic bids go to the champions of the SEC, Big Ten, ACC and Big 12, and to one Group of Six team. Notre Dame is guaranteed a place if it is in the final top 12. Seven places are at-large.
- Seeding has been straight since 2025, with the top 4 getting byes.
- Sources conflict on the G6 bid: On3/Yahoo (2026-01-23) say "highest-ranked G6 champion"; SI citing The Athletic (2026-01-27) and an NCAA explainer say "highest-ranked G6 team, champion or not". This plan takes the team rule, configurable, and logs it with a verify step (Q1).
- 2024 and 2025 gave automatic bids to the five best-ranked conference champions. 2024 gave byes to the four best-ranked champions; in 2025, ACC champion Duke was left out behind Tulane and James Madison.

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
- **A small league, a real league.** Integration tests need a league with four power conferences, championship games, polls and 12-team fields, which Phase 3's league does not have. Live data arrives only after Phase 2's backfill.
- **Process.** No DB session while planning (spike CSVs only), zero CFBD calls, shared databases (probes and timings only in "local free" / "AWS free" windows). `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md` are edited only by the docs tasks, after re-reading them.

**Goals**
- `CFB_SIM` with the objects of Key Interfaces: four seeded reference tables, two functions, 14 views and 12 data tables. Scripts `CFB_INFRA.SIM_ENGINE`, `SIMULATE`, `REFRESH_SIMULATE` and `DROP_SIM_RUN`.
- The score model: ratings, μ, σ by horizon, totals, predicted points and win probability per game at any point, leak-free. Calibrated on 2014–2025 with an out-of-sample gate against the line.
- Pregame win probability and upset magnitude for every completed game since 1890.
- Rule sets 2024/2025/2026; a resume formula fitted to final committee rankings; simplified tiebreakers documented.
- The engine per the contract, reusing the S3 prototype:
  - the fixed-shape draw layout;
  - fixed results;
  - known championship pairings and results;
  - non-FBS opponents.
- `SIMULATE` with input snapshot, checks, registry, step timings, sizing, lock, failure cleanup, verify and drop. Outputs per D20.
- Refresh step 300 with per-target defaults: local 100k, AWS 1M.
- The backtest of 2024 and 2025 with a selection gate.
- `make sim`, `make simulate`, `make sim-verify`, `make sim-drop`, `make calibrate`, `make backtest`; `make deploy` includes the sim schema.

**Non-Goals**
- What-if queries, the conditional rooting guide, forced re-sims, week-over-week movement, frozen predictions, the scorecard, upset rankings and `CFB_API` views. These are Phase 5; this plan provides their inputs: `SIM_PATH_R<run>`, `SIM_TEAM`, `LEVERAGE`, `GAME_SCORE_AS_OF`, `GAME_WIN_PROB` and fixed results in the engine.
- Grants to `CFB_APP` and `CFB_MCP`.
- Per-sim margins and the M0 histogram (D20: optional, off).
- In-simulation rating updates, i.e. per-sim team strength offsets (decision [3]; reversal trigger in the log).
- Using the current season's SP+ or lines as features.
- Formats other than 12 teams: no 4-team era runs, no 14/16/24-team options. No bracket-only simulation after selection Sunday: points after the last regular week are refused.
- Full conference tiebreakers, divisions, CFP committee behavior beyond the resume formula, and transitional-FBS eligibility.
- ML frameworks and custom SLCs: runtime code is builtin `PYTHON3` (numpy 1.26.4) and SQL.
- The benchmark harness and `docs/benchmark.md` (Phase 8). The run registry is what they will read.

### Decision

The score model is SQL views over `CFB_MODEL`'s as-of views, applying coefficients from a generated, reviewed seed table that `make calibrate` fits on the host with pinned numpy. The engine is the S3 prototype grown to the full contract, bundled into one `PYTHON3 SET` script with a single explicit `EMITS` stage shape. A Lua script runs the steps; it uses Phase 2's run lock and logs every step:
1. snapshot the run's inputs;
2. check them;
3. create the per-run outcome table;
4. fan out one engine call per batch into a stage table;
5. split the stage into the outputs with plain SQL (the layout-C columns derived from the emitted outcome string);
6. build `LEVERAGE`;
7. finish.

#### Architecture

```
 CFB_MODEL (Phase 3)  GAME_AS_OF · TEAM_SEASON_AS_OF · RATING_SP · GAME · CONFERENCE_CHAMPIONSHIP · AS_OF_POINT
        │
        ▼  CFB_SIM views (sql/sim/, deployed by vs/host/sqlobjects.py)
 SCORE_MODEL_PARAMS ─┐  TEAM_RATING_AS_OF ─▶ GAME_SCORE_AS_OF ─▶ SCORE_TRAINING_ROWS ─(make calibrate)─▶ CALIBRATION_ROWS
 RESUME_WEIGHTS ─────┤        │                     │                                                   ─▶ SCORE_MODEL_REPORT
 SELECTION_RULES ────┤        ▼                     ▼                                                      SCORE_CALIBRATION_BUCKETS
 SIM_CONFIG ─────────┘  ENGINE_TEAM_AS_OF · ENGINE_GAME_AS_OF · ENGINE_PARAM_AS_OF        GAME_WIN_PROB (1890–, upsets)
        ▲                       │
 make calibrate (host numpy 1.26.4): fit ─▶ sql/sim/010_*.sql, 012_*.sql (generated, reviewed) ─▶ deploy ─▶ report, gate
        │
 EXECUTE SCRIPT CFB_INFRA.SIMULATE(2026, 9, 'regular', 100000, 10000, NULL, NULL, NULL)   (or step 300 of REFRESH_WEEK)
   lock (LOAD_RUN kind SIMULATE; the refresh step uses the refresh's lock)
   1 INPUTS      INSERT INTO SIM_RUN_TEAM / SIM_RUN_GAME / SIM_RUN_PARAM SELECT … FROM ENGINE_*_AS_OF   (the snapshot)
   2 CHECKS      SIM_INPUT_CHECKS (ERROR stops)
   3 PATH_TABLE  CREATE TABLE CFB_SIM.SIM_PATH_R<run> (RUN_ID, SIM_ID, G0001 … BOOLEAN, comments per game)
   4 ENGINE      INSERT INTO SIM_STAGE SELECT CFB_INFRA.SIM_ENGINE(run cols, model cols)
                   FROM SIM_RUN_MODEL_ROWS × batch ids GROUP BY batch_id      ── nrOfCores instances per node
                   ┌ per batch: SeedSequence([seed, batch]).spawn(2) → season (S×G normals, S×G uniforms),
                   │            postseason (S×(C+11) normals, uniforms); season → standings → CCGs → resume →
                   └ selection (rule set) → bracket; ONE DataFrame emit: T rows (12/sim), P rows (outcome string),
                                 W/C tallies, B summary (checksum, model digest, versions, stage seconds, vm/node)
   5 OUTPUTS     SIM_TEAM ◀ T · SIM_PATH_R<run> ◀ SUBSTR(OUTCOMES, i, 1) = '1' · TEAM_WINS ◀ W · CONF_CHAMP ◀ C
                 SIM_RUN_BATCH ◀ B (JSON_VALUE) · check: all batches, one digest, one engine version
   6 LEVERAGE    one pass over SIM_TEAM ⋈ SIM_PATH_R<run> (S4 shape) ─▶ LEVERAGE
   7 FINISH      clear SIM_STAGE, SIM_RUN DONE, seconds, sims/s, instances, sizing        (every step row committed)
   any error ─▶ delete the run's outputs, drop its path table, clear the stage, FAILED, release the lock, re-raise
        │
        ▼
 SIM_TEAM_ODDS (DONE runs)   BACKTEST_FIELD ─▶ BACKTEST_REPORT ◀── make backtest (2024/2025, k = 5, 3, 1, 0)
        │
        ▼  Phase 5: CFB_SIM what-if, CFB_PRED, CFB_API
```

#### Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| Coefficients as generated, reviewed seeds | `sql/sim/010_score_model_params.sql`, `012_resume_weights.sql` (written by `make calibrate`) | A calibration is a deliberate, diffable act, like `make generate`. Runs are reproducible across databases (local synthetic, local restored, AWS) because the parameters travel in git, not in one database |
| One formula in SQL, fitted on the host | `TEAM_RATING_AS_OF` / `GAME_SCORE_AS_OF` apply; `engine/calibrate.py` fits from the same view's raw columns | No second implementation of the prediction to drift. The fit is pure numpy, unit-tested on generated truth |
| Two params versions | `CURRENT` (2014–2025), `HOLDOUT_2023` (2014–2023) | The roadmap's calibration window, plus an honest out-of-sample gate and backtest |
| As-of inputs only | every `CFB_SIM` view reads `*_AS_OF`, the prior season's `RATING_SP`, `GAME` only for labels and completed-game probabilities | Leak-free by construction; the invariance test of Phase 3 extends to predictions |
| Tagged model rows snapshot per run | `SIM_RUN_TEAM` / `SIM_RUN_GAME` / `SIM_RUN_PARAM` → `SIM_RUN_MODEL_ROWS` | The engine reads the snapshot, never the live views. A run stays reproducible and verifiable after any refresh (`make sim-verify`) |
| Single engine pass, one explicit stage shape | `SIM_ENGINE … EMITS (<stage>)` → `SIM_STAGE` → SQL splits | Reconciles S3's "explicit EMITS per shape" with "one compute pass": the stage is the one shape. Saves a pass (7 s local, 1.5 s per 100k on AWS) per extra output |
| Outcome string emitted, layout C derived in SQL | `OUTCOMES` → `SIM_PATH_R<run>` | C is the read layout (D20). A is the cheap emission (S4: 7.2 s vs 30.6 s at 1M). Probe 1.1c gates it, with A as the D20 fallback |
| Per-run outcome table, shared sparse tables partitioned by run | `SIM_PATH_R<run>`; `SIM_TEAM`, `LEVERAGE` `PARTITION BY RUN_ID` | The game set changes every week (C needs per-run DDL); the team-level tables stay one table for Phase 5's cross-run queries, pruned by run |
| Fixed-shape draws, child stream per stage | `SeedSequence([seed, batch]).spawn(2)` | S3 M2: common random numbers for what-if deltas and fixed results; scheduling independence kept |
| Engine has no defaults | every `PARAM` required | The snapshot holds every value the run used; nothing hides in code |
| Lock reuse | `LOAD_RUN` kind `SIMULATE`; the refresh step under the refresh's lock; deploy under the lock | One lock for everything that writes; Phase 2's stale-row rules apply unchanged |
| Steps commit their rows; failure cleans up | `SIM_RUN_STEP`, `SIM_RUN` | The timeline shows progress from another session; consumers read only `DONE` runs |
| Declarative checks, thin Lua | `SIM_INPUT_CHECKS` view, `engine/lua/*.lua` | Same split as Phases 2 and 3 |
| Synthetic league through the real flattener | `tests/synthetic/sim_league.py` (reuses Phase 3's league machinery) | Faithful mirror rows, CFP fields and polls without CFBD data |
| Pure engine core, loop oracle | `engine/core.py` vs `tests/engine/loop_ref.py` | S3's proven method: vectorization correctness by equality with plain loops on identical draws |

#### Key Interfaces

**Objects of `CFB_SIM`** (files `sql/sim/NNN_<object>.sql`; data tables `sql/sim/data/<object>.sql`; every comment starts with "One row per <grain>")

| File | Object | Kind | Grain | Reads |
|---|---|---|---|---|
| 010 | `SCORE_MODEL_PARAMS` | reference, generated by `make calibrate` | params version | seed |
| 011 | `SELECTION_RULES` | reference | rule set and season range | seed |
| 012 | `RESUME_WEIGHTS` | reference, generated by `make calibrate` | params version | seed |
| 013 | `SIM_CONFIG` | reference, rendered from `engine/sim-config.yaml` (target block + `--config`) | setting | yaml |
| 020 | `NORMAL_CDF` | function (A&S 26.2.17, error < 7.5e-8) | — | — |
| 021 | `MARGIN_WIN_PROB(MU, SIGMA)` | function: ½ (Φ((μ − ½)/σ) + Φ((μ + ½)/σ)) | — | `NORMAL_CDF` |
| 100 | `TEAM_RATING_AS_OF` | view | params version, point, team-season | `TEAM_SEASON_AS_OF`, `RATING_SP` (S − 1), `SCORE_MODEL_PARAMS` |
| 110 | `GAME_SCORE_AS_OF` | view | params version, point, `NEXT`/`REMAINING` regular-season game with teams involving FBS | `GAME_AS_OF`, `GAME` (`INVOLVES_FBS`), `TEAM_RATING_AS_OF`, params |
| 120 | `GAME_WIN_PROB` | view | completed game with both teams | `GAME`, params (`CURRENT`) |
| 130 | `SCORE_TRAINING_ROWS` | view | params version, point, game later completed | `GAME_SCORE_AS_OF`, `TEAM_RATING_AS_OF`, `GAME` (labels, closing line) |
| 200 | `ENGINE_TEAM_AS_OF` | view | params version, point, engine team | `TEAM_SEASON_AS_OF`, `GAME_AS_OF`, `TEAM_RATING_AS_OF`, `CONFERENCE_CHAMPIONSHIP`, `RESUME_WEIGHTS` |
| 210 | `ENGINE_GAME_AS_OF` | view | params version, point, engine game row | `GAME_AS_OF`, `GAME_SCORE_AS_OF`, `ENGINE_TEAM_AS_OF` |
| 220 | `ENGINE_PARAM_AS_OF` | view | params version, point, parameter | params, `RESUME_WEIGHTS`, `SELECTION_RULES`, `SIM_CONFIG`, `ENGINE_TEAM_AS_OF`, `AS_OF_POINT` |
| 230 | `SIM_RUN_MODEL_ROWS` | view | run and tagged model row | `SIM_RUN_TEAM`, `SIM_RUN_GAME`, `SIM_RUN_PARAM` |
| 240 | `SIM_INPUT_CHECKS` | view | run and check | `SIM_RUN_*`, `SIM_RUN`, `SELECTION_RULES` |
| 300 | `SIM_TEAM_ODDS` | view | `DONE` run and eligible roster team | `SIM_RUN`, `SIM_RUN_TEAM`, `SIM_TEAM`, `TEAM_WINS`, `CONF_CHAMP` |
| 400 | `SCORE_MODEL_REPORT` | view | params version, evaluation seasons, horizon bucket | `CALIBRATION_ROWS` |
| 410 | `SCORE_CALIBRATION_BUCKETS` | view | params version, evaluation seasons, tenth of probability | `CALIBRATION_ROWS` |
| 500 | `BACKTEST_FIELD` | view | season ≥ 2024 and actual playoff team | `GAME` (`POSTSEASON_KIND` `CFP`) |
| 510 | `BACKTEST_REPORT` | view | `DONE` backtest run | `SIM_RUN`, `SIM_TEAM_ODDS`, `BACKTEST_FIELD` |
| data | `SIM_RUN`, `SIM_RUN_STEP`, `SIM_RUN_BATCH`, `SIM_RUN_TEAM`, `SIM_RUN_GAME`, `SIM_RUN_PARAM`, `SIM_STAGE`, `SIM_TEAM`, `LEVERAGE`, `TEAM_WINS`, `CONF_CHAMP`, `CALIBRATION_ROWS` | data tables (created if missing, never replaced; a column difference stops the deploy) | see DDL below | — |
| runtime | `SIM_PATH_R<run id>` | per-run table created by `SIMULATE` | simulated season of the run | — |

`CFB_INFRA`: `SIM_ENGINE` (PYTHON3 `SET`, explicit `EMITS`, body = `engine/core.py` + `engine/udf.py`, `ENGINE_VERSION` = first 16 hex of SHA-256 over the body without its constant line), and the Lua scripts `SIMULATE`, `REFRESH_SIMULATE`, `DROP_SIM_RUN` (bundled by Phase 2's `loader/bundle.py` with `loadlib.lua` + `engine/lua/simlib.lua` + entry). Allowed references for `sql/sim/`: `{SIM_SCHEMA}.*`, `{MODEL_SCHEMA}.*`.

**Engine input mapping** (fulfills Phase 3 decision [31]: the model provides the data, Phase 4 computes the rest)

| Engine input | Source at point P (season S) | Phase 4 rule |
|---|---|---|
| `TEAM` rows | FBS team-seasons of `TEAM_SEASON_AS_OF` (`CLASSIFICATION` `fbs`) + the non-FBS opponents of `REMAINING` games | `ELIGIBLE` = FBS; `TEAM_IDX` = `ROW_NUMBER() OVER (ORDER BY TEAM_ID) − 1` |
| `CONFERENCE`, `TIER` | `CONFERENCE_NAME`, `CONFERENCE_TIER` (`P4`, `G6`, `INDEPENDENT` → `IND`); non-FBS → NULL, `NON_FBS` | |
| `HAS_CCG` | conference has a `CFB_MODEL.CONFERENCE_CHAMPIONSHIP` row covering S | |
| `RATING` | `TEAM_RATING_AS_OF.RATING` | |
| `SOS` | mean `RATING` of the opponents of all the team's regular-season `FIXED` games in `GAME_AS_OF` (played and remaining), championship games excluded | `DECIMAL(6,2)` |
| `WINS`, `LOSSES` | `TEAM_SEASON_AS_OF` minus the team's played championship game | the engine adds the championship game uniformly |
| `CONF_WINS`, `CONF_LOSSES`, `CONF_PD` | `CONF_WINS`, `CONF_LOSSES`, `CONF_POINT_DIFF` (already without championship games) | |
| `CAPPED_PD` | Σ `LEAST(GREATEST(team margin, −cap), cap)` over `PLAYED` regular-season games without championship games | cap = `RESUME_WEIGHTS.MARGIN_CAP` (24) |
| `GAME` `REMAINING` | `GAME_SCORE_AS_OF` rows with `POSTSEASON_KIND` NULL and not `IS_CONF_CHAMPIONSHIP` | `GAME_IDX` by `START_AT`, `GAME_ID`; `CONF_GAME` = flag and same conference (else `CROSS_CONFERENCE_FLAG`) |
| `GAME` `PLAYED_CONF` | `GAME_AS_OF` `PLAYED`, `IS_CONFERENCE_GAME`, not championship, both teams in one conference | head-to-head only |
| `GAME` `CCG` | `GAME_AS_OF` rows with `IS_CONF_CHAMPIONSHIP` (visible from the week before) | `MU`/`SIGMA` from `GAME_SCORE_AS_OF`; `MARGIN` if `PLAYED` |
| `NOT_PLAYED` games | left out | `GAMES_NOT_PLAYED` warning |
| `PARAM` | `hfa` = `HFA`; `post_sigma` = σ at horizon `LAST_REGULAR_WEEK_SEQ − P + 1`; `ot_margin` = `OT_MARGIN` (3); `margin_cap`, `w_*` from `RESUME_WEIGHTS`; `auto_bid_rule`, `auto_bids`, `g6_bid`, `bye_rule`, `guarantee_rank`, `min_conference_teams` from `SELECTION_RULES` of S (codes below); `guarantee_team_idx` = roster index of `GUARANTEE_SCHOOL`, else −1 | `fail_batch` only from `SIM_CONFIG.TEST_FAIL_BATCH` |

Rule codes in `PARAM_VALUE`: `auto_bid_rule` 1 `TOP_CHAMPIONS`, 2 `POWER_CHAMPIONS_PLUS_G6`; `g6_bid` 1 `CHAMPION`, 2 `TEAM`; `bye_rule` 1 `STRAIGHT`, 2 `CHAMPIONS`.

**Engine core changes vs `spikes/s3/engine_proto.py`** (task 1.4)

| Prototype | Phase 4 |
|---|---|
| draws: season normals, then `random(n_ties)` (data-dependent), then postseason | `spawn(2)`: season `standard_normal((S, G))` + `random((S, G))`; postseason `standard_normal((S, C + 11))` + `random((S, C + 11))` |
| `PARAM` overrides defaults | every key required; `fail_batch` optional test hook |
| teams P4 / G6 / IND, all eligible | + `NON_FBS` (never ranked or selected), `HAS_CCG`, `TEAM_ID` |
| every conference plays a championship game between its top two | known `CCG` rows fix the pairing (and result when played); no CCG → standings leader; below `min_conference_teams` → no champion |
| selection: 4 power champions + best G6 champion; straight seeding | rule sets: `TOP_CHAMPIONS` (n = `auto_bids`) or `POWER_CHAMPIONS_PLUS_G6` (`g6_bid` `TEAM`/`CHAMPION`) + guarantee; `bye_rule` `STRAIGHT` / `CHAMPIONS` |
| resume: win %, SOS, capped PD | + `w_rating × RATING` |
| no fixed results | `FIXED_HOME_WIN`: winner kept, margin = `rint(μ + σ Φ⁻¹(p))` with p the cell's uniform mapped into the winner's side (`statistics.NormalDist().inv_cdf`), at least 1 point on that side |
| outputs dense `SIM_TEAM` (team index) or path string per mode | one stage shape: `T` playoff rows (`TEAM_ID`), `P` outcome string, `W`/`C` tallies, `B` summary JSON |

```python
# engine/core.py — draw layout (normative)
season_ss, post_ss = np.random.SeedSequence([run_seed, batch_id]).spawn(2)
season = np.random.Generator(np.random.Philox(season_ss))
post = np.random.Generator(np.random.Philox(post_ss))
z, u = season.standard_normal((S, G)), season.random((S, G))
z_post, u_post = post.standard_normal((S, C + 11)), post.random((S, C + 11))
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
  RUN_ORIGIN VARCHAR(8) UTF8 NOT NULL, LOAD_RUN_ID DECIMAL(18,0), RUN_LABEL VARCHAR(200) UTF8,
  AS_OF_SEASON DECIMAL(4,0) NOT NULL, AS_OF_WEEK DECIMAL(3,0) NOT NULL, AS_OF_SEASON_TYPE VARCHAR(10) UTF8 NOT NULL,
  AS_OF_WEEK_SEQ DECIMAL(3,0) NOT NULL, AS_OF_LABEL VARCHAR(100) UTF8, RULE_SET VARCHAR(20) UTF8, PARAMS_VERSION VARCHAR(20) UTF8,
  RUN_SEED DECIMAL(18,0) NOT NULL, N_SIMS DECIMAL(18,0) NOT NULL, SIMS_PER_BATCH DECIMAL(9,0) NOT NULL, N_BATCHES DECIMAL(9,0) NOT NULL,
  MODEL_DIGEST VARCHAR(64) ASCII, ENGINE_VERSION VARCHAR(16) ASCII, NUMPY_VERSION VARCHAR(20) ASCII, PYTHON_VERSION VARCHAR(20) ASCII,
  DB_VERSION VARCHAR(40) UTF8, NODES DECIMAL(4,0), DB_RAM_GIB DECIMAL(7,1), INSTANCES DECIMAL(5,0), BATCHES_PER_INSTANCE DECIMAL(7,2),
  N_TEAMS DECIMAL(4,0), N_GAMES DECIMAL(5,0), SESSION_ID DECIMAL(20,0), STARTED_AT TIMESTAMP NOT NULL, ENDED_AT TIMESTAMP,
  SECONDS_TOTAL DECIMAL(9,3), SECONDS_ENGINE DECIMAL(9,3), SIMS_PER_SECOND DECIMAL(12,1), DETAIL VARCHAR(2000) UTF8);
-- SIM_RUN_STEP (RUN_ID, STEP_NO, STEP_NAME, STEP_STATUS, STARTED_AT, ENDED_AT, SECONDS, ROWS_WRITTEN, DETAIL)
-- SIM_RUN_BATCH (RUN_ID, BATCH_ID, N_SIMS, CHECKSUM, MODEL_DIGEST, ENGINE_VERSION, ENGINE_SECONDS, STAGE_SECONDS (JSON), VM_ID, NODE_ID, PEAK_RSS_KB)
-- SIM_RUN_TEAM (RUN_ID, TEAM_IDX, TEAM_ID, SCHOOL, CONFERENCE, TIER, HAS_CCG, ELIGIBLE, RATING, RATING_SOURCE, SOS,
--               WINS, LOSSES, CONF_WINS, CONF_LOSSES, CONF_PD, CAPPED_PD)                                  = D20's TEAMS roster
-- SIM_RUN_GAME (RUN_ID, GAME_ROLE, GAME_IDX, GAME_ID, WEEK_SEQ, START_AT, HOME_IDX, AWAY_IDX, HOME_TEAM_ID, AWAY_TEAM_ID,
--               NEUTRAL, CONF_GAME, MU, SIGMA, TOTAL, FIXED_HOME_WIN, MARGIN, PATH_COLUMN)
-- SIM_RUN_PARAM (RUN_ID, PARAM_KEY, PARAM_VALUE DECIMAL(12,4))
-- CALIBRATION_ROWS: the columns of SCORE_TRAINING_ROWS
```

Reserved words (`vs/gen/exasol_reserved_keywords.txt`, 2026.1.0) checked for every planned name: `SOURCE`, `VALUE`, `RESULT`, `STATE` and `POSITION` are reserved and avoided (`RATING_SOURCE`, `PARAM_VALUE`, `PROB_SOURCE`, `RUN_STATUS`, `SEED`).

**Run SQL** (generated by `simlib.lua`; names are deploy-time constants; values bound)

```sql
-- 4 ENGINE: one SET group per batch
INSERT INTO "CFB_SIM"."SIM_STAGE"
SELECT "CFB_INFRA"."SIM_ENGINE"(:run, :seed, :spb, b."BATCH_ID", 'FULL', m."ROW_KIND", m."GAME_ROLE", … , m."PARAM_VALUE")
  FROM "CFB_SIM"."SIM_RUN_MODEL_ROWS" m
 CROSS JOIN (SELECT … FROM VALUES BETWEEN 0 AND :last_batch) b        -- alias form or numbers table: probe 1.1d
 WHERE m."RUN_ID" = :run
 GROUP BY b."BATCH_ID";
-- 3 + 5 PATH: per-run DDL (column comment "Week 12: Brookvale at Ashford (game 401…); TRUE = home win"), then
INSERT INTO "CFB_SIM"."SIM_PATH_R42" SELECT "RUN_ID", "SIM_ID", SUBSTR("OUTCOMES", 1, 1) = '1', …, SUBSTR("OUTCOMES", G, 1) = '1'
  FROM "CFB_SIM"."SIM_STAGE" WHERE "RUN_ID" = 42 AND "ROW_KIND" = 'P';       -- gate: probe 1.1c, else layout A
-- 5 SIM_TEAM, TEAM_WINS (SUM(N) by team and wins), CONF_CHAMP, SIM_RUN_BATCH (JSON_VALUE(DETAIL, '$.checksum' RETURNING …))
-- 6 LEVERAGE: spikes/s4/layouts.py fill_leverage_sql adapted: per team SUM(CASE WHEN p."Gnnnn" THEN 1 ELSE 0 END) over
--   SIM_TEAM ⋈ SIM_PATH_R<run> on SIM_ID (run filter first), unpivoted by game, joined to per-game home-win counts
```

**Score model** (formulas in `sim-engine/score-model`; fit in `sim-engine/calibration`)
- `RATING` by source: `SP_ELO`, `ELO`, `SP`, `CLASS_DEFAULT`. `MU` = `HFA` × not neutral + `RATING`ₕ − `RATING`ₐ. `SIGMA` = √(`SIGMA_1`² + `TAU`² (k − 1)).
- `TOTAL` from the previous season's SP+ offense and defense ratings, or `TOTAL_DEFAULT`. `HOME_WIN_PROB` = `MARGIN_WIN_PROB(MU, SIGMA)`.
- Fallback feature set F2 (only if 6.2 misses a gate): add the season-to-date point differential per game from `TEAM_SEASON_AS_OF` (`POINTS_FOR`, `POINTS_AGAINST`, games) as a third rating feature. `FEATURE_SET` records which one is deployed.
- `SCORE_MODEL_PARAMS` columns: `PARAMS_VERSION`, `FEATURE_SET`, `HFA`, `B_SP`, `B_ELO`, `B_ELO_ONLY`, `B_SP_ONLY`, `ELO_CENTER`, `FBS_DEFAULT_RATING`, `NON_FBS_RATING`, `SIGMA_1`, `TAU`, `OT_MARGIN`, `TOTAL_INTERCEPT`, `TOTAL_SLOPE`, `TOTAL_DEFAULT`, `TOTAL_SIGMA`, `ELO_PROB_SCALE`, `ELO_PROB_HFA`, `FIT_SEASONS`, `FITTED_AT`, `FIT_TARGET`, `DATA_FINGERPRINT`.
- Until the first calibration (task 6.2), both versions hold `FEATURE_SET` `UNCALIBRATED` with the S3 defaults (`HFA` 2.5, `B_SP` 1.0, `B_ELO` 0, `B_ELO_ONLY` 0.04, `B_SP_ONLY` 1.0, `SIGMA_1` 15, `TAU` 0, `OT_MARGIN` 3, `TOTAL_DEFAULT` 52, `ELO_PROB_SCALE` 400, `ELO_PROB_HFA` 55). `RESUME_WEIGHTS` holds the S3 weights (100, 2.0, 0.5, `w_rating` 0, `MARGIN_CAP` 24).

**Selection rules seed** (`sql/sim/011_selection_rules.sql`): the table of `sim-engine/selection-rules`.
- Columns: `RULE_SET`, `SEASON_FROM`, `SEASON_TO`, `CFP_SIZE`, `N_BYES`, `AUTO_BID_RULE`, `AUTO_BIDS`, `G6_BID`, `BYE_RULE`, `GUARANTEE_SCHOOL`, `GUARANTEE_RANK`, `MIN_CONFERENCE_TEAMS`, `SOURCE_NOTE`.
- `SEASON_TO` is NULL for `CFP12_2026` (open).

**Configuration** (`engine/sim-config.yaml`)

```yaml
local: {N_SIMS: 100000, SIMS_PER_BATCH: 10000}     # D19's measured laptop pick (10 batches; 1.25 per slot on an 8-core start)
aws:   {N_SIMS: 1000000, SIMS_PER_BATCH: 10000}    # the 1M scale dial: 100 batches over 12 slots
```

For 100k on AWS, D19's pick is 1,000 per batch: `make simulate TARGET=aws SIMS=100000 BATCH=1000`.

**Make targets** (`scripts/sim.py`; every target runs `db-check` first and accepts `TARGET` and `ARGS="--prefix …"`)

| Target | Does |
|---|---|
| `make sim [ARGS="--config-block aws --config K=V,…"]` | lint, render, bundle, deploy `CFB_SIM`, the engine and the Lua scripts under the run lock; register step 300; report |
| `make deploy` | virtual schema → loader → model → sim |
| `make simulate SEASON= WEEK= [SEASON_TYPE= SIMS= BATCH= SEED= PARAMS= LABEL=]` | `EXECUTE SCRIPT CFB_INFRA.SIMULATE(…)`; print the timeline and the top 25 of `SIM_TEAM_ODDS` |
| `make sim-verify RUN=` | `SUMMARY`-mode engine over the run's snapshot; compare checksums; writes nothing |
| `make sim-drop RUN= CONFIRM=yes` | `EXECUTE SCRIPT CFB_INFRA.DROP_SIM_RUN(run)` |
| `make calibrate [ARGS="--out DIR --targets k=v,…"]` | `sim-engine/calibration` |
| `make backtest [SEASONS= SIMS= BATCH=]` | `sim-engine/backtest` |

**Code layout**

```
engine/__init__.py
engine/core.py          pure numpy engine (from spikes/s3/engine_proto.py), host + UDF
engine/udf.py           UDF wrapper: chunked get_dataframe read, parse, simulate, one DataFrame emit
engine/bundle.py        CREATE PYTHON3 SET SCRIPT text, ENGINE_VERSION, size check
engine/calibrate.py     pure fits + DB orchestration of make calibrate
engine/backtest.py      make backtest orchestration and report writer
engine/deploy.py        make sim (binds vs/host/sqlobjects.py to sql/sim/, data tables, scripts, config)
engine/sim-config.yaml  per-target SIM_CONFIG
engine/lua/simlib.lua, simulate.lua, refresh_simulate.lua, drop_sim_run.lua
scripts/sim.py          CLI for the make targets
sql/sim/*.sql, sql/sim/data/*.sql
tests/engine/{models.py, loop_ref.py}   crafted models, loop oracle
tests/synthetic/sim_league.py
```

**Sim league** (`tests/synthetic/sim_league.py`; the facts the specs use)

| Item | Facts |
|---|---|
| Seasons | 2013–2026. 2013–2025 complete; 2026 weeks 1–9 completed, 10–15 scheduled, the week-10 lines fetched before its cutoff. Regular season weeks 1–15, championship games in week 15, postseason week 1 |
| Conferences (real CFBD names as reference keys, fictional teams) | SEC, Big Ten, Big 12, ACC with 10 teams each; American Athletic, Mountain West, Sun Belt, Mid-American, Conference USA with 6 each; Pac-12 with 8 until 2023, 2 in 2024–2025 (Dunmore, Orland), 6 from 2026; independents Notre Dame and Rivermouth; FCS Big Sky with Lowell, Pemberton and 2 more |
| Named teams | Ashford, Brookvale (SEC); Granton (Big Ten); Hollis (Big 12); Fillmore (ACC); Ivel (American Athletic); Eastport, Fairhaven (Mountain West); Sable (Sun Belt, FBS from 2026, no 2025 SP+, Elo 1550 at 2026 week 9) |
| Schedules | 12 regular games per FBS team (8 conference games in 10-team conferences, 5 or 7 in the others), one FCS opponent per team, no conference pair twice; Ashford hosts Lowell in 2026 week 11 and Brookvale in week 12 |
| Facts | Pregame and postgame Elo on every game (simple Elo updates); SP+ for every FBS team every season (Ashford 2025: 20.00 / offense 35.0 / defense 18.0; Brookvale 10.00 / 30.0 / 22.0); Ashford's Elo after 2026 week 9 is 1700, Brookvale's 1600; lines on every FBS-vs-FBS game; AP polls weekly; committee rankings from week 10 and a final ranking per season from 2014 |
| Committee and fields | The generator's committee ranks by the resume formula with the test resume weights over the test-parameter ratings (no resume gap below 0.5 among the top 30); 2024 and 2025 have 12-team fields selected under `CFP12_2024` and `CFP12_2025`, with first-round, quarterfinal, semifinal and final games and seeds; 2014–2023 have 4-team playoff games |
| Generation | Results drawn from a known truth (μ = 2.5 × home + strength difference, σ = 15) with a fixed seed; Phase 3's machinery (API-shaped bodies → `vs.cfbd_vs.flatten`, slice keys, `LOAD_LOG` registrations, replay export) |
| Variants | `mutate_after(point)` (every fact not visible at the point; Phase 3's list); `with_neutral_week12`; `with_repeated_conference_pair` (two SEC teams meet twice); `with_bad_2025_committee` (three at-large teams ranked below 20 by the resume); `with_changed_late_results` |
| Scale option | `scale="real"`: 136 FBS teams in 10 conferences plus FCS opponents, about 420 remaining games at 2026 week 9, for the local performance smoke (3.4) in the `PERF_` namespace |

Test parameters, loaded by `set_score_params` into both versions: `HFA` 2.50, `B_SP` 0.8000, `B_ELO` 0.04000, `B_ELO_ONLY` 0.05000, `B_SP_ONLY` 1.0000, `ELO_CENTER` 1500, `FBS_DEFAULT_RATING` −5.00, `NON_FBS_RATING` −20.00, `SIGMA_1` 15.00, `TAU` 2.00, `OT_MARGIN` 3, `TOTAL_INTERCEPT` 10.0, `TOTAL_SLOPE` 0.5000, `TOTAL_DEFAULT` 52.0, `ELO_PROB_SCALE` 400, `ELO_PROB_HFA` 55. Test resume weights: 100, 2.0, 0.5, 1.0, cap 24.

**Spike reuse**

| Spike artifact | Phase 4 use |
|---|---|
| `spikes/s3/engine_proto.py` | copied to `engine/core.py`, then changed per the table above (the spike copy stays as the evidence of D19) |
| `spikes/s3/engine_loop_ref.py` + `spikes/s3/tests/test_engine_proto.py` | copied to `tests/engine/loop_ref.py` and `tests/unit/test_engine_core.py`, updated to the Phase 4 rules |
| `spikes/s3/synth_season.py` | crafted-model helpers in `tests/engine/models.py` |
| `spikes/s3/engine_udf.py`, `spikes/common/deploy.py` | the chunked read, the DataFrame emit and the inlining approach of `engine/udf.py` / `engine/bundle.py` (no mode inference: one explicit shape) |
| `spikes/s3/results/numpy_stream.json` | the evidence that host 1.26.4 equals the SLC stream; `tests/unit/test_engine_stream.py` pins the host version and a stream digest |
| `spikes/s4/layouts.py` (`wide_game_names`, `fill_leverage_sql`) | column naming `G0001…` and the `LEVERAGE` SQL |
| `spikes/s4/queries.py` (`_sparse_what_if`, Q2) | the what-if smoke of probe 1.1e and task 6.4 (Phase 5 owns the real queries) |
| `spikes/s3/results/*.csv`, `spikes/s4/results/*.csv` | the performance budget below and the verification baselines |

**Performance budget** (estimates from the spike CSVs; measured in 3.4 and 6.4)

| Component | Local 100k (8 cores, 10k/batch) | AWS 1M (3 × 4, 10k/batch) | Basis |
|---|---|---|---|
| Fixed per statement (VM start, imports, first read) | ≈ 3.2 s | ≈ 0.8 s | S3 fixed costs |
| Engine compute, one pass | ≈ 7.1 s | ≈ 10–16 s | S3 compute-only (local 7.06 s; AWS 1.55 s per 100k, 0.66 s per 10k batch per instance) |
| Playoff rows (12 per sim) | ≈ 3.4 s | ≈ 6.5 s | S3 emission rates (0.35M / 1.84M rows/s) |
| Outcome strings | ≈ 3.8 s | ≈ 7 s | S3 path emission; S4 A load 7.2 s at 1M |
| Splits + C derivation | probe 1.1c | probe 1.1c at 1M (6.1) | — |
| `LEVERAGE` | < 1 s | ≈ 4.8 s | S4 AWS 4.75 s |
| **Target** | **≤ 30 s** | **≤ 60 s** | mission: `REFRESH_WEEK` incl. 100k < 2 min locally |

**Exasol claims checked against the `exasol-udfs`, `exasol-database` and `exasol-distributed-ml` skills and the spikes**

| Claim | Evidence | Result |
|---|---|---|
| `PYTHON3 SET SCRIPT … EMITS (…)`, `ctx.get_dataframe(num_rows=n)`, `ctx.emit(DataFrame)` (several emits per group allowed) | `exasol-udfs` `create-script-syntax.md`, `udf-python.md`; S3 measured | Confirmed; NULLs in emitted frames: probe 1.1a |
| Explicit `EMITS` beats variadic | S3 remedy 2 (estimate) | Adopted, one shape |
| `GROUP BY batch_id` fans out to `nrOfCores` per node; modules load once per VM | S3 fan-out; `exasol-distributed-ml` `ml-performance.md` (lazy module init) | Confirmed |
| `exa.meta.vm_id`, `memory_limit`; `node_id`, `node_count` | S3; `udf-python.md` lists `meta` attributes | `vm_id` confirmed; `node_id`/`node_count` probe 1.1f |
| Lua `query`/`pquery`, nested `EXECUTE SCRIPT`, output discarded without `WITH OUTPUT`, binds `:v` / `::name` | `exasol-udfs` `lua-execute-scripts.md` | Confirmed; nested `COMMIT` + DDL: Phase 3 probe 1.1h, re-checked in 1.1i |
| `CREATE TABLE IF NOT EXISTS`, `PARTITION BY` on `DECIMAL` | `exasol-grammar.md` `create_table_statement_1/2`, `partition_by`; `table-design.md` supported types | Confirmed; multi-run latency probe 1.1e |
| `VALUES BETWEEN min AND max` | `exasol-grammar.md` `value_range_table` | Confirmed; alias form probe 1.1d |
| `JSON_VALUE(… RETURNING type)` | `exasol-grammar-functions.md` `json_value_1` | Confirmed; probe 1.1g |
| No `ERF` / normal CDF built in; `EXP`, `SQRT`, `ABS` exist; `CREATE FUNCTION … RETURN DOUBLE` | `exasol-grammar-functions.md`; Phase 3's checked `create_function_statement` | `NORMAL_CDF` as a SQL function; probe 1.1h |
| `REGR_*`, `CORR` exist | `exasol-grammar-functions.md` | Not used: 2–3 feature OLS and a logistic fit are done on the host |
| Maximum columns per table ≥ 2 + 2,000 | not in the skills | Probe 1.1b; more than 2,000 remaining games falls back to layout A |
| Reserved words | `vs/gen/exasol_reserved_keywords.txt` | Avoided (above) |
| DDL names unquoted, query identifiers quoted | `exasol-database` rules; Phase 3 lint | Enforced by the shared lint |

### Consequences

| Decision | Alternatives Considered | Rationale |
|----------|------------------------|-----------|
| Linear margin model on SP+ prior + Elo as of the point + home field, normal margins ([2]) | Market lines as μ; ML models; ratings solved from games in SQL (SRS) | Uses only what is live at every point, calibrates in seconds, explains in one sentence; SRS-style features are the F2 fallback |
| σ grows with the horizon; no per-sim team offsets ([3]) | One σ; per-sim strength offsets (rating-update flag) | Measurable per-game calibration with two parameters; correlated team strength only if the backtest shows overconfidence |
| Coefficients fitted on the host, committed as generated seeds; `CURRENT` + `HOLDOUT_2023` ([6]) | In-DB fit UDF; fit on every deploy | Reviewable, reproducible across databases; an honest holdout |
| Yardstick = closing line, never a feature ([7]) | No baseline; line as feature | The line knows more than the model at the point; comparing keeps the targets meaningful |
| 2026 rules: 4 power champions + best G6 team + Notre Dame top-12 guarantee; G6 rule configurable ([9]) | "Best G6 champion"; mission glossary wording | Two sources against one; one seed value flips it |
| Rule sets for 2024 and 2025 too ([10]) | Backtest under 2026 rules | A backtest against real fields needs the rules they were selected under (2025: Duke left out) |
| Resume formula with a rating term, weights fitted to committee rankings ([11]) | Prototype weights; ordinal model | The committee rewards strength; the grid is deterministic and explainable |
| `spawn(2)` fixed-shape draws ([13]) | Prototype order; fixed OT block only | S3 M2: common random numbers across what-if deltas, independent of ties and of the game count of the postseason |
| Fixed results in the engine now ([14]) | Leave to Phase 5 | The contract states them; Phase 5's forced re-sim then needs no engine change |
| One engine pass, one stage shape, SQL splits ([15]) | One UDF per output (several passes); direct C emission | Saves a compute pass per output; emission is the cost |
| C derived from the outcome string; per-run path table; A as gated fallback ([16]) | Emit C from the UDF; store A only | Keeps D20's read layout at A's emission cost, measured before it is relied on |
| Shared sparse tables `PARTITION BY RUN_ID`, engine emits `TEAM_ID` ([17]) | Per-run tables for everything; team index | Phase 5 cross-run queries stay plain SQL; no idx→id join at 12M rows |
| Input snapshot per run + `make sim-verify` ([18]) | Re-read live views | The mission's reproducibility constraint holds after refreshes and is provable |
| Loader run lock, kind `SIMULATE` ([19]) | A separate sim lock | One lock, one stale-row rule; backfills and deploys cannot interleave with a run |
| Step 300, per-target defaults, seed = season × 1,000 + week sequence, skip after the regular season ([20]) | Step 200; random seeds | Leaves 200 to Phase 5 (Phase 2's examples already use it for `PREDICT`); deterministic weekly runs |
| Runs refused after the last regular week and before 2024 ([21]) | Bracket-only mode; 4-team rules | YAGNI for the in-season demo; logged |
| Non-FBS opponents as ineligible teams; records without championship games ([22]) | Drop FCS games; let the model count CCGs | Real schedules include FCS games; the engine adds the championship game uniformly, played or not |

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
- `loader/run-logs`: the run lock. It adds run kind `SIMULATE`; recorder note: when `loader/run-logs` is recorded, its kind list gains `SIMULATE`;
- `loader/load-plan`: one more `refresh_steps` entry;
- `model/as-of`, `model/games`, `model/ratings-rankings`: the inputs;
- `model/deploy`: the deployer and lint it reuses.

## Requirements

| Requirement | Details |
|-------------|---------|
| Zero CFBD calls | No `CFB_SIM` object references `CFBD_VS` (lint + dependency test). Tests use the sim league, replay and the mock; the harness guard against `CFBD_API` stays. Live verification reads only stored data; `/info` used calls unchanged across group F |
| Shared DB etiquette | Probe 1.1, the performance smoke 3.4 and `make test-it` only in "local free" windows, serialized with `flock <scratchpad>/cfb-it.lock`. AWS `default`: deploy and run only, after "AWS free"; never stop, start or resize. `PERF_` and `PROBE_` objects dropped after use. No DB session during planning |
| Layering | `CFB_SIM` reads only `CFB_MODEL` and itself; Lua also `CFB_META` (lock) and `CFB_INFRA` (engine). No grants to `CFB_APP`/`CFB_MCP` (Phase 5) |
| Leak-free | Features only from as-of views; labels only from completed games; `HOLDOUT_2023` fitted on ≤ 2023. Invariance tests for predictions, training rows and backtest inputs |
| Reproducibility | Registry key per run; per-batch Philox seeding with `spawn(2)`; inputs snapshotted; `make sim-verify` passes for every `DONE` run; numpy 1.26.4 in host venv and SLC (stream pinned by a unit test against S3's recorded digest) |
| Engine contract | `specs/architecture.md` § Engine contract as amended by task 5.2; D19 (batch sizes, explicit `EMITS`, DataFrame emit, one pass) and D20 (layout C, sparse `SIM_TEAM`, `LEVERAGE` built after the load, roster, no per-sim margins) |
| Runtime code | Builtin `PYTHON3` SLC (Python 3.12.3, numpy 1.26.4, pandas inside the UDF only), Exasol Lua and SQL. Host: Python 3.12, numpy 1.26.4 (new dev dependency), pyexasol, PyYAML. No ML frameworks, no custom SLC |
| Self-description | 100 % comments on `CFB_SIM` objects and on every column of every `SIM_PATH_R<run>`; grain first; DDL names unquoted upper case, not reserved |
| Performance (targets; measured) | Local 100k run ≤ 30 s total at 10k/batch on an 8-core start (min–max of 3 runs reported, sizing recorded); AWS 1M ≤ 60 s; `REFRESH_WEEK` with 100k sims < 2 min locally (mission). `SIM_TEAM` what-if smoke (S4 Q1 k = 1 text) with ≥ 3 runs stored within 1.5× of S4's single-run p95. A miss is recorded with its step breakdown and a decision; it does not change the design inside the measuring task |
| Calibration quality | Out-of-sample targets of `sim-engine/calibration` on real data (6.2); a miss triggers F2 (6.2b) and is logged |
| Data in git | Seeds hold coefficients, weights and published rules only; docs hold derived numbers; the sim league is synthetic (real conference names and "Notre Dame" only as reference keys) |
| Ownership | `sys` owns the real `CFB_SIM`; `CFB_TEST_OWNER` the `TEST_` and `PERF_` namespaces |

## Dependencies

- **`add-cfbd-vs-foundation`**: complete through group I (harness, mock, replay, `vs/host/target.py`, bundle size limit).
- **`add-cfb-persistence-refresh`** (implemented before this plan's group B): meta renderer with placeholders, `loader/bundle.py`, `loadlib.lua` lock helpers (extended here for kind `SIMULATE`; coordinate with its owner), `refresh_week.lua` step registry, harness helpers (`deploy_plan`, `set_now`, `refresh`, recordings). Its group J (AWS demo backfill + snapshot) before this plan's group F.
- **`add-cfb-analytical-model`** (implemented before group B): `CFB_MODEL` views, `vs/host/sqlobjects.py` (extended by 2.1), `tests/synthetic/league.py` machinery, harness `deploy_model`, `hold_run_lock`. Its group K (AWS model deploy and checks) before group F; its Q3 outcome (one postseason week) is consistent with this plan's refusal of postseason points.
- **`add-engine-spikes`**: done (D19, D20). `spikes/` stays as evidence; the results CSVs are read, not regenerated.
- **Tools:** numpy 1.26.4 joins the dev group (`uv lock`). Nothing else new.
- **User prerequisites:** U1 the "local free" / "AWS free" signals; U2 Phase 2's AWS snapshot for the local restore (6.4); U3 the owner commits the calibrated seeds after 6.2.

## Migration

No data migration: `CFB_SIM` is new.

| Current | New |
|---------|-----|
| `make deploy`: VS → loader → model | VS → loader → model → sim |
| `loader/load-plan.yaml` `refresh_steps`: `MODEL` 100 | + `{step_no: 300, name: SIMULATE, script: CFB_INFRA.REFRESH_SIMULATE}` |
| `LOAD_RUN.RUN_KIND`: `BACKFILL`, `REFRESH_WEEK`, `SNAPSHOT`, `RESTORE` | + `SIMULATE` (`loadlib.lua` lock helper) |
| `vs/host/namespace.py` | + `sim_schema`; renderer + `{SIM_SCHEMA}` |
| `vs/host/sqlobjects.py` | + data-table kind, keep rules (data tables, `SIM_PATH_R<n>`), per-schema allowed references |
| `tests/it/harness.py` | `drop_namespace`/`leftovers` cover `{prefix}CFB_SIM` (and its per-run tables); helpers `deploy_sim`, `seed_sim_league`, `set_score_params`, `set_resume_weights`, `set_sim_config` |
| `pyproject.toml` | dev + `numpy==1.26.4`; mypy `files` + `engine`; coverage source + `engine` |
| `Makefile` | targets `sim`, `simulate`, `sim-verify`, `sim-drop`, `calibrate`, `backtest`; `coverage` reports `engine` |

## Implementation Tasks

Tasks are TDD per `/speq:code-guardrails`. Host code writes its unit tests first. Each SQL/Lua task writes its integration test file first (failing), then the code. Lua has no unit tier. The engine core is unit-tested on the host, and equality with the database is an integration test.

1. **Probe, scaffolding and pure code (no DB except 1.1)**
   - [ ] 1.1 Engine platform probe `docs/spikes/s7_engine_probe.py`, findings in `docs/spikes/s7-engine-probe.md`. It runs as sys on local Personal in a "local free" window, on `PROBE_*` objects, at 0 calls; `--target aws` re-runs (b, c, e, f) at 1M in 6.1. Each item records its adopted fallback.
     - (a) A `PYTHON3 SET` script with the explicit `SIM_STAGE` `EMITS`, emitting several DataFrames per group:
       - unused columns as NULL (pandas nullable dtypes, `None` in object columns) vs sentinels (0, FALSE, `''`);
       - read back exactly;
       - the cost per million rows of either form.
     - (b) `CREATE TABLE` with 2 + 2,000 `BOOLEAN` columns and 2,002 column comments, inside a transaction with DML; also 2 + 4,000 columns. Record the limit.
     - (c) Layout-C derivation. Stage 100k synthetic outcome strings of 400 games (`spikes/s4` generator), then time `INSERT … SELECT SUBSTR(…) = '1'` into a 400-column table (3 warm-ups, 5 runs; query cache off; exclusive window). Compare with S4's local C (28.9 s) and A (16.6 s) loads.
       - **Gate:** derivation ≤ 10 s locally → layout C as planned; otherwise layout A (`SIM_PATH_VEC (RUN_ID, SIM_ID, OUTCOMES)`, `PARTITION BY RUN_ID`), logged as a decision.
     - (d) Batch ids from `VALUES BETWEEN 0 AND 7` with a column alias inside a `GROUP BY` fan-out; fallback: a data table `SIM_BATCH_NUMBERS` 0–9999.
     - (e) `PARTITION BY RUN_ID` sparse `SIM_TEAM` with 3 runs of 100k vs a single run: S4's sparse Q1 k = 1 and Q2 texts (`spikes/s4/queries.py`), p95 of 20 runs. Fallback if more than 1.5× slower: no partitioning, plus `DISTRIBUTE BY SIM_ID` retest; logged.
     - (f) `EXA_SYSTEM_EVENTS` columns (version, nodes, DB RAM); `exa.meta.vm_id`, `node_id` and `node_count`; numpy, pandas and Python versions of the builtin SLC.
     - (g) `JSON_VALUE(DETAIL, '$.x' RETURNING DECIMAL(18,0) | DOUBLE | VARCHAR(64))` on a summary row.
     - (h) `NORMAL_CDF` (A&S 26.2.17) as a SQL function against `statistics.NormalDist` at 1e-7 over −8…8; `CAST(<DOUBLE> AS DECIMAL(5,4))` (reuse Phase 3 1.1i).
     - (i) Lua: `CREATE TABLE`, `INSERT` and `COMMIT` in a nested `EXECUTE SCRIPT` called from a script that committed its step row; an `error()` after it keeps the committed rows (reuse Phase 3 1.1h).
     - **Gate:** (a) fixes the stage value form, (b) the column limit, (c) C vs A, (d) the batch source, (e) the partitioning. (f)–(i) negative → their fallbacks.
   - [ ] 1.2 `Namespace.sim_schema` and `{SIM_SCHEMA}` in the placeholder renderer. Unit tests in `tests/unit/test_namespace.py` and `test_meta_ddl.py`.
   - [ ] 1.3 Toolchain:
     - `numpy==1.26.4` in the dev group (`uv lock`); `engine/__init__.py`;
     - mypy and coverage include `engine`; `make coverage` reports `engine/*` with ≥ 90 % (`engine/lua` excluded);
     - `tests/unit/test_engine_stream.py`: the host numpy version is 1.26.4 (the SLC's, `spikes/s3/results/numpy_stream.json`), and a fixed digest of the season and postseason child streams of one batch is pinned, so a numpy upgrade on the host fails loudly. Stream equality with the database is proven per target by probe 1.1f and `test_database_equals_host_engine`.
   - [ ] 1.4 Engine core `engine/core.py`, copied from `spikes/s3/engine_proto.py` and changed per Key Interfaces (§ Engine core changes): [expert]
     - input parse;
     - validation;
     - the `spawn(2)` draw layout;
     - fixed results;
     - known/played `CCG` rows, no-CCG conferences, the minimum size;
     - `NON_FBS` teams;
     - rule sets and bye rules;
     - `w_rating`;
     - the stage outputs with checksum, model digest and stage seconds.

     Test-first in `tests/unit/test_engine_core.py` (engine-contract scenarios) and `tests/unit/test_selection_rules.py` (selection-rule scenarios), with crafted models in `tests/engine/models.py` (from `spikes/s3/synth_season.py`). Keep the vectorized style: no per-sim Python loops (profile per stage, as S3).
   - [ ] 1.5 Loop reference `tests/engine/loop_ref.py`, ported from `spikes/s3/engine_loop_ref.py` to the Phase 4 rules: plain loops and `Fraction`s, never importing `engine.core`.
     - `tests/unit/test_engine_loop_ref.py::test_vectorized_matches_loop_reference`: 500 seasons of a crafted season, every output field and the checksum equal.
     - The test also asserts that every rule was exercised: two-way tie with and without a meeting, three-way tie, overtime, a fixed result, a known and a played championship game, a no-CCG conference, a small conference, each rule set, the guarantee in and out, an auto-bid outside the top 12.
   - [ ] 1.6 UDF wrapper and bundle.
     - `engine/udf.py`: chunked `get_dataframe(num_rows=4096)`, parse, `simulate_batch`, one stage frame per batch (value form per 1.1a), `SUMMARY` mode, and the instance identity from `exa.meta`.
     - `engine/bundle.py`: renders `CREATE OR REPLACE PYTHON3 SET SCRIPT {INFRA_SCHEMA}.SIM_ENGINE(<run cols>, <model cols>) EMITS (<stage cols>) AS …`, computes `ENGINE_VERSION`, and fails at 1,990,000 characters.
     - Unit tests `tests/unit/test_engine_udf.py` (fake `ctx`/`exa`) and `tests/unit/test_engine_bundle.py`.
   - [ ] 1.7 Calibration fits, the pure part of `engine/calibrate.py`:
     - OLS per feature set, class defaults, σ/τ weighted least squares, totals;
     - the Elo logistic fit (IRLS, fixed iteration cap and tolerance);
     - the resume grid search;
     - rounding to column scales and seed SQL rendering for both versions;
     - target evaluation.

     Unit tests `tests/unit/test_calibrate.py` on generated arrays with known truth:
     - `HFA` within 0.3 and `B_SP` within 0.05 at n = 20,000;
     - `SIGMA_1` and `TAU` within 5 %;
     - the Elo scale within 5 %;
     - the grid recovers the generating weights exactly;
     - identical output text for identical input.
   - [ ] 1.8 Sim league `tests/synthetic/sim_league.py` per Key Interfaces, reusing Phase 3's league machinery. Its committee and its 2024/2025 field selection are its own plain-Python implementation, not `engine.core`.

     Unit tests `tests/unit/test_sim_league.py`:
     - deterministic output;
     - conference sizes per season;
     - no conference pair twice (base league);
     - one championship game per seeded conference and season;
     - 12-team fields with valid bracket games for 2024/2025;
     - the named facts of Key Interfaces;
     - mirror rows typed per catalog;
     - the `scale="real"` option's counts.
   - [ ] 1.9 Seeds and configuration:
     - `sql/sim/011_selection_rules.sql`;
     - the initial generated-format `010_score_model_params.sql` and `012_resume_weights.sql` (`UNCALIBRATED`, both versions);
     - `engine/sim-config.yaml`.

     Unit tests `tests/unit/test_sim_seeds.py`:
     - rule sets cover 2024 onward without overlap;
     - codes valid;
     - `GUARANTEE_SCHOOL` set only for 2026;
     - each params table has exactly `CURRENT` and `HOLDOUT_2023`;
     - config blocks valid (multiple, 100–20,000).

2. **Schema and deploy**
   - [ ] 2.1 Extend `vs/host/sqlobjects.py` (owned by Phase 3's 1.3/2.1; no parallel edits): a data-table kind (`CREATE TABLE IF NOT EXISTS`, column comparison against `EXA_ALL_COLUMNS`, a difference stops the deploy), keep rules (registered data tables, `SIM_PATH_R[0-9]+`), per-schema allowed references. Extend `tests/unit/test_sqlobjects.py` and `test_model_deploy.py`.
   - [ ] 2.2 `engine/deploy.py`, `scripts/sim.py deploy` and `make sim`:
     - called from `scripts/deploy.py` after the model;
     - the run lock;
     - config block and overrides, with `TEST_FAIL_BATCH` removed outside `TEST_`;
     - the engine script (bundle) and the Lua scripts (sim entries in `loader/bundle.py`);
     - the `refresh_steps` entry;
     - the report.

     Unit tests `tests/unit/test_sim_deploy.py` (fake connection) and `tests/cli/test_sim_cli.py`. Harness helpers per Migration. Integration test file first: `tests/it/test_sim_deploy_it.py`.
   - [ ] 2.3 Data tables `sql/sim/data/*.sql` per Key Interfaces, every column commented (covered by 2.2's tests).
   - [ ] 2.4 Functions and score views `020`, `021`, `100`, `110`, `120`, `130`. Integration tests first:
     - `tests/it/test_score_model_it.py`;
     - `tests/it/test_win_probability_it.py`;
     - the invariance scenarios in `tests/it/test_sim_variants_it.py`.
   - [ ] 2.5 Engine input views `200`, `210`, `220`, `230`, `240` and `300_sim_team_odds.sql`. Integration tests first in `tests/it/test_sim_inputs_it.py`, which are supporting tests for the mapping table:
     - records without the championship game;
     - capped differential;
     - SOS;
     - head-to-head rows;
     - known and played championship rows;
     - the cross-conference flag;
     - non-FBS opponents;
     - guarantee index;
     - rule codes;
     - the index order.

3. **Runs**
   - [ ] 3.1 Lua `engine/lua/simlib.lua`, `simulate.lua` and `drop_sim_run.lua`, bundled with `loadlib.lua`: [expert]
     - the seven steps with committed step rows;
     - generated outcome-table DDL with per-game comments;
     - the fan-out, the split and the `LEVERAGE` SQL;
     - the digest, completeness and instance checks;
     - sizing from `EXA_SYSTEM_EVENTS`;
     - the lock as run kind `SIMULATE` (extend `loadlib`'s lock helper through its owner);
     - failure cleanup that never leaves a `RUNNING` row or a partial output;
     - `DROP_SIM_RUN`.

     Integration tests first: `tests/it/test_sim_runs_it.py` and `tests/it/test_sim_engine_it.py`.
   - [ ] 3.2 `engine/lua/refresh_simulate.lua` (step 300, skip rule, `LOAD_RUN_ID`), integration tests first in `tests/it/test_sim_refresh_step_it.py`.
   - [ ] 3.3 Host wrappers `scripts/sim.py simulate | verify | drop` and their make targets; verify runs the `SUMMARY` mode. CLI tests `tests/cli/test_sim_cli.py`; verify and drop scenarios in `tests/it/test_sim_runs_it.py`.
   - [ ] 3.4 Local performance smoke, in a "local free" window: seed `sim_league(scale="real")` into `PERF_`, deploy, then 3 × `make simulate SIMS=100000 BATCH=10000 ARGS="--prefix PERF_"` at 2026 week 9.
     - Record sizing (`EXA_SYSTEM_EVENTS`, `podman logs` lines), step seconds and min–max against the budget and the 30 s target.
     - Also one 100k `REFRESH_WEEK` with replay (< 2 min).
     - Drop `PERF_`. Write the numbers to `docs/engine-verification.md` (draft).

4. **Calibration and backtest**
   - [ ] 4.1 `make calibrate`: the DB part of `engine/calibrate.py` and `scripts/sim.py calibrate`:
     - steps 1–5 of `sim-engine/calibration`;
     - the views `400_score_model_report.sql` and `410_score_calibration_buckets.sql`;
     - the docs writer and the gate.

     Integration tests first: `tests/it/test_calibrate_it.py`.
   - [ ] 4.2 `make backtest`: `engine/backtest.py` and `scripts/sim.py backtest`, the views `500_backtest_field.sql` and `510_backtest_report.sql`, the docs writer and the gate. Integration tests first: `tests/it/test_backtest_it.py`.

5. **Documentation** (re-read each file first; another agent edits `docs/roadmap.md` now)
   - [ ] 5.1 `docs/user-guide.md`, section "Score model and simulations":
     - the score model in one paragraph, its parameters, and how to read `SCORE_MODEL_REPORT`;
     - the selection rules and their simplifications;
     - `make simulate` / `sim-verify` / `sim-drop` / `calibrate` / `backtest`;
     - the registry columns, reproducibility, sizing (`nrOfCores`, batch sizes per target);
     - the outputs and `SIM_TEAM_ODDS`;
     - the refresh step;
     - the zero-call guarantee.
   - [ ] 5.2 `specs/architecture.md`:
     - § Engine contract: the `spawn(2)` draw layout replacing "season normals, overtime uniforms, …"; game roles `REMAINING`/`PLAYED_CONF`/`CCG`; `NON_FBS` teams; the required parameters and rule codes; the fixed-result method; the one-shape stage output; `TEAM_ID` in `SIM_TEAM`; `SIM_PATH_R<run>` and the roster `SIM_RUN_TEAM` (D20's `TEAMS`); the rating-update flag not implemented;
     - § Score model: the as-of inputs, horizon σ, totals, host calibration with generated seeds, the two params versions;
     - the schema table: `CFB_SIM` contents and `CFB_INFRA.SIM_ENGINE`/`SIMULATE`/`REFRESH_SIMULATE`/`DROP_SIM_RUN`;
     - design question "CFP rules: G6 auto-bid wording" → resolved by [9] with Q1 open.
   - [ ] 5.3 `docs/roadmap.md`:
     - decisions marked `Promotes to ADR: yes`, numbered after the last D-number present;
     - Phase 4 bullets: the 2026 rule set as decided, `HOLDOUT_2023`, the refresh step at 300;
     - mark Phase 4 progress;
     - backlog: per-sim strength offsets, bracket-only postseason sims, 16/24-team formats.
   - [ ] 5.4 `CLAUDE.md` dense bullets:
     - `engine/` + `sim-engine/` domain;
     - numpy 1.26.4 in the main dev venv;
     - `make sim`/`simulate`/`sim-verify`/`sim-drop`/`calibrate`/`backtest`;
     - consumers read only `DONE` runs;
     - calibrated seeds are generated, reviewed and committed;
     - batch-size picks per target.

     `specs/mission.md`:
     - § Commands (the new targets);
     - § Project Structure (`engine/`);
     - Glossary "Resume score / auto-bid" with the 2026 rule ("4 power-conference champions + the best-ranked Group of Six team + Notre Dame if top 12; 7 at-large");
     - Capability 6's selection wording.

6. **Live verification** (last; zero CFBD calls; AWS `default` after "AWS free", local in a "local free" window)
   - [ ] 6.1 Gates and deploy:
     - gates: Phase 2 group J (AWS backfill + snapshot) and Phase 3 group K done;
     - record `make quota TARGET=aws` and `SELECT MAX(LOGGED_AT) FROM CFB_META.FETCH_LOG`;
     - `make deploy TARGET=aws`, which uses the `aws` config block;
     - probe 1.1 (b, c, e, f) with `--target aws` at 1M (c and e) — C vs A confirmed at the demo scale.
   - [ ] 6.2 `make calibrate TARGET=aws` on the real 2013–2025 data:
     - review the report and the targets;
     - record outcomes for Q3, Q7, Q8 and Q10 (decision log);
     - the owner commits the generated seeds (U3);
     - redeploy locally after the restore (6.4).
     - [ ] 6.2b (only if an out-of-sample target is missed) Feature set F2, unit- and integration-tested like 1.7/2.4; recalibrate; log the decision with both reports.
   - [ ] 6.3 `make backtest TARGET=aws SIMS=100000 BATCH=1000`: the gate at k = 0, the report, `docs/backtest-2024-2025.md`; Q6 outcome. A gate miss is logged with the per-season overlap and leads to the review of [11] (resume features), not to a change inside this task.
   - [ ] 6.4 Demo-scale runs:
     - AWS: 3 × `make simulate TARGET=aws SEASON=2026 WEEK=<current> SIMS=1000000 BATCH=10000`, 3 × 100k at `BATCH=1000`, `make sim-verify` on one run;
     - S4's Q1 k = 1 / Q2 sparse texts against the production tables with ≥ 3 runs stored (p95 of 20);
     - local: `make restore TARGET=local NAME=<Phase 2 snapshot> CONFIRM=yes` if needed, `make deploy`, 3 × 100k at `BATCH=10000`, and `REFRESH_WEEK(<current>)` in replay with step 300 (< 2 min);
     - record sizing and step breakdowns.
   - [ ] 6.5 Zero-call proof and write-up:
     - `make quota TARGET=aws` used calls equal 6.1's, and `FETCH_LOG` has no row after 6.1's maximum;
     - `docs/engine-verification.md`, derived numbers only: calibration metrics, backtest metrics, timings per step and target with sizing, verify results, versions;
     - the demo beat's N for 100k local and 1M AWS.

## Parallelization

| Parallel Group | Tasks |
|----------------|-------|
| Group A | 1.1, 1.2, 1.3, 1.4, 1.7, 1.8, 1.9 |
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
- Before group B: Phase 3's groups A–H (deployer, model views, league machinery, harness) and Phase 2's 3.1, 4.1, 4.2, 4.3, 6.1, 6.2.
- 2.1 needs Phase 3's `sqlobjects.py`. 2.2 needs 1.2, 1.6, 1.9, 2.1, 2.3 and 1.1(a, b). 2.4 needs 1.1(h), 1.8 and 2.2. 2.5 needs 2.4.
- 3.1 needs 1.1(a, c, d, g, i), 2.5 and Phase 2's `loadlib.lua`. 3.2 needs 3.1 and Phase 2's 4.3. 3.3 needs 3.1. 3.4 needs 3.1, 3.2, 1.8 (`scale="real"`) and a "local free" window.
- 4.1 needs 1.7, 2.4 and 2.5 (the resume fit reads `ENGINE_TEAM_AS_OF`). 4.2 needs 3.1 and 3.3; its tests load the test parameters into both versions, so it does not wait for a real calibration.
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
| score-model: Engine-facing values are fixed-scale decimals | Integration | `tests/it/test_score_model_it.py` | `test_engine_facing_decimals_and_normal_cdf` |
| calibration: Calibration writes the parameters and the report | Integration (CLI + DB) | `tests/it/test_calibrate_it.py` | `test_calibrate_writes_params_and_report` |
| calibration: Training rows hold only what was known at their point | Integration (CLI + DB) | `tests/it/test_calibrate_it.py` | `test_training_rows_leak_free` |
| calibration: The holdout parameters never see the evaluation seasons | Integration (CLI + DB) | `tests/it/test_calibrate_it.py` | `test_holdout_never_sees_evaluation_seasons` |
| calibration: The report compares the model with the betting line | Integration (CLI + DB) | `tests/it/test_calibrate_it.py` | `test_report_compares_with_line` |
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
| sim-runs: A run is refused while another run holds the lock | Integration | `tests/it/test_sim_runs_it.py` | `test_run_refused_while_locked` |
| sim-runs: A failed run leaves no outputs | Integration | `tests/it/test_sim_runs_it.py` | `test_failed_run_leaves_no_outputs` |
| sim-runs: An input error stops the run before the engine | Integration | `tests/it/test_sim_variants_it.py` | `test_input_error_stops_before_engine` |
| sim-runs: Points after the regular season and uneven batches are refused | Integration | `tests/it/test_sim_runs_it.py` | `test_postseason_point_and_uneven_batches_refused` |
| sim-runs: A run's outputs can be dropped | Integration (CLI + DB) | `tests/it/test_sim_runs_it.py` | `test_sim_drop` |
| refresh-step: The refresh simulates the season after the model step | Integration | `tests/it/test_sim_refresh_step_it.py` | `test_refresh_simulates_after_model_step` |
| refresh-step: The step follows the deployment's configuration | Integration | `tests/it/test_sim_refresh_step_it.py` | `test_step_follows_configuration` |
| refresh-step: A refresh after the regular season skips the simulation | Integration | `tests/it/test_sim_refresh_step_it.py` | `test_postseason_refresh_skips_simulation` |
| refresh-step: A failed simulation stops the refresh and keeps the fetched data | Integration | `tests/it/test_sim_refresh_step_it.py` | `test_failed_simulation_stops_refresh` |
| backtest: The backtest simulates each season from fixed points under its own rules | Integration (CLI + DB) | `tests/it/test_backtest_it.py` | `test_backtest_runs_per_season_and_point` |
| backtest: The actual field comes from the stored playoff games | Integration | `tests/it/test_backtest_it.py` | `test_actual_field_from_playoff_games` |
| backtest: The report scores the odds against the actual field | Integration | `tests/it/test_backtest_it.py` | `test_report_scores_odds` |
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
| sim-engine/deploy | `make sim`, then again; `make sim TARGET=aws` | Report: 4 reference tables, 2 functions, 14 views, 12 data tables, 4 scripts, comments 100 %; the second run drops nothing and keeps every run; AWS shows `SIM_CONFIG` 1,000,000 / 10,000 |
| sim-engine/score-model | `SELECT G.HOME_TEAM_ID, G.AWAY_TEAM_ID, S.MU, S.SIGMA, S.HOME_POINTS_PRED, S.AWAY_POINTS_PRED, S.HOME_WIN_PROB FROM CFB_SIM.GAME_SCORE_AS_OF S JOIN CFB_MODEL.GAME G ON G.GAME_ID = S.GAME_ID JOIN CFB_MODEL.CURRENT_AS_OF C ON C.AS_OF_SEASON = S.AS_OF_SEASON AND C.AS_OF_WEEK_SEQ = S.AS_OF_WEEK_SEQ WHERE S.PARAMS_VERSION = 'CURRENT' AND S.HORIZON_WEEKS = 1 ORDER BY S.HOME_WIN_PROB DESC LIMIT 10` | Next week's games with plausible margins (|μ| < 45), σ ≈ `SIGMA_1`, probabilities between 0 and 1 |
| sim-engine/calibration | `make calibrate TARGET=aws` | Report per version and horizon; `HOLDOUT_2023` on 2024–2025 at horizon 1 within the targets (or a logged F2 decision); seeds written; `git diff --stat sql/sim/010_score_model_params.sql sql/sim/012_resume_weights.sql` shows the new coefficients |
| sim-engine/win-probability | `SELECT SEASON, HOME_TEAM_ID, AWAY_TEAM_ID, PROB_SOURCE, UPSET_MAGNITUDE FROM CFB_SIM.GAME_WIN_PROB ORDER BY UPSET_MAGNITUDE DESC NULLS LAST LIMIT 10`; `SELECT PROB_SOURCE, MIN(SEASON), COUNT(*) FROM CFB_SIM.GAME_WIN_PROB GROUP BY 1` | The biggest upsets of all stored history; `ELO` from 1890, `LINE` from 2013, NULL before Elo coverage |
| sim-engine/engine-contract | `make sim-verify RUN=<id>` for a run of each target | "8/8 batches match" (or the run's batch count), the run's model digest |
| sim-engine/selection-rules | `SELECT * FROM CFB_SIM.SELECTION_RULES ORDER BY SEASON_FROM`; in a run at the last regular week of 2025: `SELECT * FROM CFB_SIM.BACKTEST_REPORT WHERE RUN_LABEL LIKE 'BACKTEST 2025 k=0'` | Three rule sets as published; the engine's 2025 field vs the actual one (`SELECTED_OVERLAP` ≥ 10) |
| sim-engine/sim-runs | `make simulate SEASON=2026 WEEK=<current>`; `SELECT RUN_ID, N_SIMS, SECONDS_TOTAL, SIMS_PER_SECOND, INSTANCES, DB_RAM_GIB, ENGINE_VERSION FROM CFB_SIM.SIM_RUN ORDER BY RUN_ID DESC LIMIT 3`; `SELECT * FROM CFB_SIM.SIM_TEAM_ODDS WHERE RUN_ID = <id> ORDER BY P_IN_CFP DESC LIMIT 15` | Timeline of 7 steps; 100,000 sims in ≤ 30 s with sizing recorded; top 15 playoff odds that add up with the rest to 12 |
| sim-engine/sim-runs (AWS dial) | `make simulate TARGET=aws SEASON=2026 WEEK=<current> SIMS=1000000 BATCH=10000` | 1,000,000 sims in ≤ 60 s; `INSTANCES` 12 |
| sim-engine/refresh-step | `make refresh SEASON=2026 WEEK=<current>` in replay mode (`FETCH_MODE=replay`) | Timeline `CHECK`, `FETCH` ×4, `MODEL`, `SIMULATE` all `DONE`, 0 calls; a new `REFRESH` run in `SIM_RUN` |
| sim-engine/backtest | `make backtest TARGET=aws SIMS=100000 BATCH=1000` | 8 runs; report per season and k; gate passed (or logged); `docs/backtest-2024-2025.md` written |

### Checklist

| Step | Command | Expected |
|------|---------|----------|
| Generate | `make generate && git diff --exit-code -- vs/catalog sql/core` | Exit 0 |
| Build | `uv run python -m vs.bundle && uv run python -m loader.bundle && uv run python -m engine.bundle` | Exit 0; `build/sql/` holds the adapter, fetch, loader, model step and `sim_engine.sql` + the sim Lua scripts, all below 1,990,000 characters |
| Sim lint | `uv run python -m scripts.sim deploy --lint-only` | Exit 0; "lint ok" |
| Test | `make test` | 0 failures (unit + CLI, no DB) |
| Integration | `flock <scratchpad>/cfb-it.lock make test-it` | 0 failures; mock and replay only; 0 requests to `api.collegefootballdata.com` |
| Performance | task 3.4 (local, "local free" window) | Targets met, or a logged decision with the step breakdown |
| Lint | `make lint` | 0 ruff errors, 0 mypy errors (`vs`, `scripts`, `tests`, `loader`, `model`, `engine`) |
| Format | `uv run ruff format --check .` | No changes |
| Coverage | `make coverage` | ≥ 90 % line coverage for `vs/cfbd_vs`, `vs/gen`, `loader`, `model`, `engine` |
| Deploy | `make deploy && make deploy TARGET=aws` | Exit 0 both; `CFB_SIM` comments 100 %; a second run drops nothing |
| Layering | `git grep -n -i -e 'CFBD_VS' -e 'CFB_CORE' -e 'CFB_META' -e '{VS_NAME}' -- sql/sim` | No match |
| Reproducibility | `make sim-verify RUN=<latest>` on local and AWS | All batches match |
| Zero calls | `make quota TARGET=aws` before and after group J | `/info` used calls unchanged |
| No data in git | `git ls-files '*.parquet' '*.cfbd.json'` | Empty |
