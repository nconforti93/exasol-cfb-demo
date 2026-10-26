# Tasks: add-cfb-score-engine

Worktree: ../exasol-cfb-demo-p4 (branch add-cfb-score-engine, branched from add-cfb-analytical-model; Phase 3 3.9–3.11 merged in at 42b2121)

Light, demo-first replan of 2026-10-23 (decision-log [44]). Details, files, tests and definitions of done per task: plan.md § Implementation Tasks. Markers: `[ ]` open, `[~]` in progress, `[x]` done, `[-]` superseded.

## Slice 1: 2026 odds and what-if on AWS (first usable result)

### Group S1-A
- [x] 1.1 Engine core `engine/core.py` from the S3 prototype: fixed-shape draws, team offsets, 2026 rules, tagged stage frame with tallies [expert]
- [x] 1.2 Simulation schema, deploy (`make sim`) and the engine script (`engine/udf.py`, `engine/bundle.py`, data tables)

### Group S1-B
- [x] 1.3 Score model, engine input views and the fit (`make sim-fit`, seed committed after one AWS fit)

### Group S1-C
- [x] 1.4 `CFB_INFRA.SIMULATE`, outputs, `SIM_TEAM_ODDS`, `engine/whatif.py`, `make simulate` and `make what-if`

### Group S1-D
- [x] 1.5 Live on AWS: 2026 after week 7, 100k and 1M; Auburn's what-if; timings in `docs/engine-verification.md`

## Slice 2: predictions, the rooting guide and movement

### Group S2-A
- [x] 2.1 Predictions: freeze (`PREDICT`, step 2 of `SIMULATE`), grades, scorecard, calibration buckets, `GAME_WIN_PROB`
- [x] 2.2 `POINT_RUN`, `CURRENT_RUN`, `ODDS_MOVEMENT`, `ROOTING_GUIDE`

### Group S2-B
- [x] 2.3 Live slice 2 on AWS (reconstructed weeks 1–8, the week-6 run for movement) and the hand-off column check

## Slice 3: hardening, only as far as the demo needs

### Group S3-A
- [~] 3.1 Refresh steps 200 `PREDICT` and 300 `SIMULATE`, per-target defaults, killed-run cleanup, `make deploy` includes the sim
- [ ] 3.2 Calibration report (`make sim-fit ARGS=--report`), reported, never gated
- [x] 3.3 CFP rules check on cfp.org before about 2026-11-03 (official sources confirm the seed values; source and cutoff caveat recorded)
- [ ] 3.5 (optional) BucketFS parameter file as a showcase beat; never a dependency
- [ ] 3.6 (only if triggered by 1.5 or 2.3) What-if storage fallback: `PARTITION BY RUN_ID`, or layout C

### Group S3-B
- [ ] 3.4 Docs: user guide, `CLAUDE.md`, architecture § Engine contract, roadmap

## Heavy plan tasks (commit 29fb149), status under the light plan

### Group 1 (probe, scaffolding, pure code, hand-offs)
- [-] 1.1 Engine platform probe (a)–(j). Superseded: sentinels avoid the NULL-emit question (a); layout A needs no wide DDL (b); C vs A decided by [47] and timed in 1.5 (c); batch ids are a whole number in the text (d); no partitioning (e, fallback 3.6); versions come from the UDF's `INFO` row (f); no JSON (g); `NORMAL_CDF` is checked by its IT (h); the Lua patterns are Phase 2's (i); the kill premise is replaced by the cleanup test of 3.1 (j).
- [-] 1.2 `Namespace.sim_schema` and `{SIM_SCHEMA}`. Absorbed into new 1.2.
- [-] 1.3 Toolchain. The numpy pin is absorbed into new 1.1; the stream-digest test is dropped (the run records the numpy version).
- [-] 1.4 Engine core. Absorbed into new 1.1 with less scope: no fixed results, no known championship pairings, no 2024/2025 bye rule.
- [-] 1.5 Loop reference oracle. Superseded by the ported invariant test, the tally recount and the database-equals-host test.
- [-] 1.6 UDF wrapper and bundle. Absorbed into new 1.2; no `SUMMARY` mode.
- [-] 1.7 Calibration fits and gates. The fits are absorbed into new 1.3 (one fit, no phases, no versions); gates, resume grid and the gate-noise test are dropped ([45]).
- [-] 1.8 Synthetic sim league. Superseded: tests use Phase 3's league and crafted models ([52]).
- [-] 1.9 Seeds and configuration. Absorbed into new 1.2, 1.3 and 3.1 (one `SEASON_RULES` row, `SIM_PARAMS`, `sim-config.yaml`).
- [x] 1.10 Hand-off H1. Delivered by Phase 2: run kinds `SIMULATE` and `DEPLOY`, `acquire_lock`/`release_lock` in `loader/lua/loadlib.lua`, `loader/runlock.py`.
- [-] 1.11 cfp.org check. Kept as new 3.3.

### Group 2 (schema and deploy)
- [-] 2.1 `sqlobjects.py` data-table kind with column comparison. Reduced into new 1.2: a keep list and a schema placeholder; differences reported, not compared column by column ([53]).
- [-] 2.2 `engine/deploy.py`, `make sim`. Absorbed into new 1.2 (refresh-step registration in 3.1).
- [-] 2.3 Data tables. Absorbed into new 1.2 (ten tables, no step, batch or calibration tables) and 2.1 (`PREDICTION`).
- [-] 2.4 Score views and functions. Absorbed into new 1.3 without week phases, versions or training views; `GAME_WIN_PROB` moved to new 2.1.
- [-] 2.5 Selection week and engine input views. Absorbed into new 1.3; the selection-week view is replaced by `SEASON_RULES.SELECTION_AT` ([50]); no input-check view (the engine validates).

### Group 3 (runs)
- [-] 3.1 Lua `SIMULATE`, `SIMULATE_FROM`, `DROP_SIM_RUN`. `SIMULATE` absorbed into new 1.4 (abandoned-run cleanup in 3.1); `SIMULATE_FROM`, `DROP_SIM_RUN` and the sleep hooks dropped (no forced re-sims, no drops: [49], [54]).
- [-] 3.2 `refresh_simulate.lua`. Kept as part of new 3.1.
- [-] 3.3 Host wrappers. `simulate` and `what-if` absorbed into new 1.4; `verify` and `drop` dropped.
- [-] 3.4 Local performance smoke on a `PERF_` league. Superseded by the timings of new 1.4 (local) and 1.5 (AWS), recorded once, not gated.

### Group 4 (calibration and backtest)
- [-] 4.1 `make calibrate` with gates and reports. Reduced to `make sim-fit` (new 1.3) and the report (new 3.2).
- [-] 4.2 `make backtest`. Superseded: no demo page needs it; the graded 2026 predictions and their calibration buckets are the demo's evidence.

### Group 5 (documentation)
- [-] 5.1–5.4 User guide, architecture, roadmap, `CLAUDE.md`/mission. Merged into new 3.4.

### Group 6 (live verification)
- [-] 6.1 Gates, deploy, selection-week and X1 checks. Deploy absorbed into new 1.5; the selection-week checks are moot (no such view), X1 is Phase 3's and fixed there.
- [-] 6.2 `make calibrate` on real data. Absorbed into new 1.3 (the AWS fit).
- [-] 6.2b Feature set F2. Dropped.
- [-] 6.3 Backtest on AWS. Dropped with 4.2.
- [-] 6.4 Demo-scale runs. Absorbed into new 1.5 and 2.3 (one run per size, no verify, no forced re-sim).
- [-] 6.5 Zero-call proof and write-up. Folded into 1.5, 2.3 (quota before and after) and 3.4.
