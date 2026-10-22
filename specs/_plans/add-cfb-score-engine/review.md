# Review: add-cfb-score-engine

Reviewer: independent plan review, 2026-10-22. Read-only: no DB session, no CFBD calls. Files read: `plan.md`, `decision-log.md`, all 9 `sim-engine/*/spec.md`; `specs/mission.md`, `docs/roadmap.md` (Phases 4–5, demo flow), `specs/architecture.md` § Engine contract, `docs/spikes/s3-*.md`, `docs/spikes/s4-*.md`, the Phase 3 plan (Key Interfaces, `model/as-of`, `model/teams-conferences`, `model/games`, `model/ratings-rankings`), the Phase 2 specs `loader/refresh-week` and `loader/run-logs`, and the Phase 2 worktree (`sql/meta/load_run.sql`, `loader/plan.py`). Skills loaded first: `exasol:exasol-udfs`, `exasol:exasol-database`, `exasol:exasol-distributed-ml`. `speq plan validate add-cfb-score-engine` → **passed (9 delta specs)**.

## Verdict: **Approve with changes**

The plan is a faithful translation of D19/D20 into an engine, a run registry and a leak-free score model. Its spec arithmetic checks out to the fourth decimal, and the M2 draw-order fix is sound. There are no blockers. Six findings are major, though, and two of them have to be settled **before task 1.4**, because the draw layout and the C/A layout become normative there:

1. **M1:** per-game horizon noise without shared team-strength noise makes the season odds overconfident.
2. **M4:** the layout-C gate thresholds are inconsistent with the 30 s and 60 s targets, and the 1M AWS derivation is only measured in group J.

The other four (M2 calibration-bucket gate, M3 in-season drift, M5 abandoned runs, M6 the selection-weekend point) are spec edits that can land before group B.

| # | Finding | Severity | Fix before |
|---|---|---|---|
| M1 | Horizon σ is independent per game, so team-season outcomes are too narrow | major | 1.4 (draw layout) |
| M2 | Calibration-bucket gate tolerance ≈ 1 SE, so it fails by noise | major | 1.7 / 4.1 |
| M3 | Constant SP+/Elo blend and pooled gate hide drift within the season | major | 1.7 / 4.1 |
| M4 | Performance budget incomplete, C gate inconsistent with targets, AWS 1M probe too late | major | 1.1 |
| M5 | Killed or abandoned runs unspecified (RUNNING rows, orphan per-run tables, stale-lock rule) | major | 3.1 |
| M6 | `LAST_REGULAR_WEEK_SEQ` is the Army–Navy week on real data, not selection weekend | major (verify first) | 4.2 / 6.3 |
| m1–m16 | see below | minor | — |

---

## 1. Statistical soundness

### M1 (major): horizon uncertainty is modelled as independent per-game noise, so playoff odds are overconfident

**Evidence**
- `score-model`: σ = √(`SIGMA_1`² + `TAU`²(h − 1)).
- `engine-contract`: each game draws its own `z`.
- Decision [3] rejects per-sim team offsets and defers them to a reversal trigger.

The growth of residual variance with the horizon is mostly **rating staleness**: the team's true strength drifts away from its week-P rating. That drift is shared by all of a team's remaining games. Spreading it as independent per-game noise keeps each game's marginal probability right but under-disperses every team's win total, so P(in CFP) is too extreme. FiveThirtyEight-style "hot" sims exist for exactly this reason.

The reversal trigger has very little power. It fires only if more than one actual field team has `P_IN_CFP` < 0.05 at k = 3, which is 24 field teams over 2 seasons.

**Fix.** Do it now. It costs nothing in parameters and keeps the predictions and the simulations consistent:
- Add one fixed-shape block `standard_normal((S, T))` to the season stream, after `z` and `u`, so common random numbers are kept.
- Give each team a per-sim offset δ_t. In game g at horizon h, use offset `δ_t · TAU·√(h − 1)/√2` and a per-game noise of `SIGMA_1`.
- The marginal `Var(margin)` = `SIGMA_1`² + `TAU`²(h − 1) is unchanged, so `MARGIN_WIN_PROB` and the outcome-share scenario (0.8289 ± 0.03) still hold. The team's games become correlated, as the data say.

Amend `engine-contract` (Randomness, Margins), [3] and the 5.2 architecture text before 1.4 freezes the draw layout. Then add a loop-reference check: the win-total variance must exceed the independent case.

If the owner prefers to defer, at least **reserve** the `(S, T)` block in the normative layout now, and replace the trigger with a calibration check of the backtest's `P_IN_CFP` buckets pooled over k = 5, 3, 1.

### M2 (major): the calibration-bucket gate will fail by sampling noise

**Evidence.** `calibration` requires "every calibration bucket with at least 100 games within 0.05 of its observed rate". The gate population is `HOLDOUT_2023` on 2024–2025, horizon 1, FBS-vs-FBS: roughly 1,300 games, so the middle deciles hold about 100–150 games. At p ≈ 0.5 and n = 100 the standard error is 0.05. A perfectly calibrated model therefore misses the 0.05 band in each bucket with probability ≈ 0.3, and across about 8 qualifying buckets the gate fails almost surely.

Exit 3 then triggers task 6.2b (F2) for no real reason.

**Fix.** Gate each bucket on `|obs − pred| ≤ max(0.05, 2·√(p(1−p)/n))`, or gate an aggregate (ECE ≤ 0.03, or the Spiegelhalter/Hosmer–Lemeshow p-value > 0.01). Keep the per-bucket table as a report. Add a unit test in `test_calibrate.py`: perfectly calibrated synthetic data must pass the gate in at least 95 % of seeds.

### M3 (major): one SP+/Elo blend for the whole season, and a gate that pools all weeks

**Evidence.**
- [2] fits `B_SP` and `B_ELO` once, on all horizon-1 rows of weeks 1–15.
- `SCORE_MODEL_REPORT` splits by horizon only, not by week of season.

The information content of the two features moves in opposite directions over a season: the prior dominates in September, and in-season Elo dominates in November. A pooled fit is a compromise that is biased at both ends. The demo lives at weeks 8–14, and the pooled gate can pass while late-season predictions are measurably worse.

**Fix.**
- Add a `WEEK_PHASE` dimension (for example weeks 0–4, 5–9, 10+) to `SCORE_MODEL_REPORT`, `SCORE_CALIBRATION_BUCKETS` and the gate.
- Make phase-specific coefficients the first fallback before F2: 3 × 2 OLS coefficients, still one sentence to explain.
- Also report bias by `RATING_SOURCE` pair. `ELO_CENTER` is a parameter but is not fitted in step 2, and it matters as soon as an Elo-only team meets an SP-only team (m4).

### Confirmed sound

- **Win-probability formula.** With margin `rint(μ + σz)` and a 50/50 overtime on 0, P(home) = 1 − Φ((½ − μ)/σ) + ½[Φ((½ − μ)/σ) − Φ((−½ − μ)/σ)] = ½[Φ((μ − ½)/σ) + Φ((μ + ½)/σ)]. This matches decision [4] exactly; the round-half-even boundaries have measure zero.
- **Arithmetic re-computed** (Python, `statistics.NormalDist`):

  | Quantity | Spec value |
  |---|---|
  | Ratings | 24.00 / 12.00 / 2.50 |
  | μ | 14.50 / 12.00 |
  | σ | 15.52 / 15.26 / 15.00 |
  | `TOTAL` | 62.5 |
  | Predicted points | 38.5 / 24.0 |
  | `HOME_WIN_PROB` | **0.8289** |
  | Elo probabilities | 0.7094 / 0.6401 |

  All are correct. A&S 26.2.17 has a maximum absolute error of **7.45e-8** over −8…8 in steps of 0.01, so it passes the 1e-7 scenario, with thin but real headroom.
- **Leak-freedom.**
  - Features come only from `*_AS_OF` plus the S − 1 SP+. The Phase 3 rules are good: `ELO_AS_OF` is the postgame Elo of the last game ≤ P, and lines are fetch-time masked.
  - The closing line (`GAME.PREGAME_SPREAD`, `HOME_PREGAME_WIN_PROB`) is a yardstick only.
  - `HOLDOUT_2023` is fitted on seasons up to 2023 only, including the **resume weights** (calibration scenario "fitted on the seasons up to 2023 only").
  - The "+7 to every 2024/2025 margin" test is a good falsifiable holdout check.
- **The backtest is leak-free by construction** (as-of snapshot plus `HOLDOUT_2023`). Its k = 0 gate isolates the selection formula as claimed, subject to M6.
- The relative targets (MAE ≤ line + 1.5, Brier ≤ line + 0.020) are realistic for an SP+/Elo model against closing lines. The absolute caps of 15.0 and 0.190 are sensible guards.

## 2. CFP selection rules

### Q1 (G6 bid): the plan's choice is confirmed

| Source | Date | Wording |
|---|---|---|
| Yahoo/On3 | 2026-01-23 | "highest-ranked Group of Six **champion** … which now includes the revamped Pac-12" (the first-day report) |
| SI, citing Chris Vannini (The Athletic) | 2026-01-27 | the G6 qualifier "does not have to be a conference champion … the highest-ranked Group of Six **team** in the final CFP rankings" |
| NCAA.com explainer (as indexed) | 2026-02-03 | same as SI; the latest source |

`G6_BID = TEAM` is right. Keep the cfp.org check before the first committee ranking (about 2026-11-03), since that is days away.

**Add (m1):**
- The Pac-12 is a G6 conference from 2026. Phase 3's `CONFERENCE_TIER` already maps it to `G6` through the NULL-name row from 2024, but no scenario pins it. Add one in which a Pac-12 team is the best-ranked G6 team in 2026.
- Q4 (a 2026 Pac-12 championship game) only affects `P_CONF_CHAMPION` under `TEAM`, as the plan says.

### Q2 (Notre Dame): confirmed

Every source says "guaranteed with a top-12 finish in the final CFP rankings", and Yahoo calls it "a fifth automatic bid". The plan's semantics match: ND counts as an automatic bid, and the at-large places shrink. The scenario arithmetic (ND at 12 in, ranks 7–11 out; ND at 13 out) is correct. Close Q2 against cfp.org together with Q1.

### 2024 and 2025: correct

- **2024:** 5 best-ranked champions; byes to the 4 best-ranked champions.
- **2025:** 5 best-ranked champions; straight seeding with the top 4 on byes.

I re-derived all six selection scenarios by hand (fields, the displaced 12th team, seeds), and they are consistent.

Minor: "From 2025 on, seeding is straight" runs the 2024 crafted season under `CFP12_2026` without naming the champions' conferences, which the 2026 auto-bid rule needs. State them (m2).

### M6 (major, verify first): the "last regular week" is not selection weekend on real CFBD data

**Evidence.**
- The backtest points are `LAST_REGULAR_WEEK_SEQ − k`. The refresh skip and the refusal also key on `LAST_REGULAR_WEEK_SEQ`.
- In 2024 and 2025, Army–Navy (Dec 14 and Dec 13) was played one week **after** the championship games and after the committee's selection Sunday. In CFBD it is a regular-season game in its own week, so `LAST_REGULAR_WEEK_SEQ` is the Army–Navy week.

This is from memory: verify on the stored 2024/2025 calendar before acting.

**Consequences:**
- k = 0 is a post-selection point, and the engine's resume counts a game the committee never saw.
- "k = 1" is in fact selection weekend.
- The demo moment "Sunday before championship week" is k = 2.

The sim league ends with the championship games in week 15, so no test can catch this.

**Fix.**
- Derive `SELECTION_WEEK_SEQ` per season as the week of the last `IS_CONF_CHAMPIONSHIP` game. Use it for k, for the refusal and skip rules, and for leaving post-selection regular games out of the resume (or keep refusing, but label it honestly).
- Add a sim-league variant with one regular game in the week after the championship games.

## 3. Engine and spike fidelity

### D19 and D20: followed

- `GROUP BY batch_id` fan-out, explicit `EMITS`, DataFrame emit and one engine pass per batch. The single `SIM_STAGE` shape neatly reconciles S3's "explicit EMITS per shape" with its "single-pass staging".
- `sims_per_batch` 1k–10k picks, Philox per `(seed, batch)`, `sims_per_batch` in the key, DECIMAL inputs.
- Sparse `SIM_TEAM`, `LEVERAGE` built after the load, roster = `SIM_RUN_TEAM`, `TEAM_WINS` / `CONF_CHAMP` tallies, no per-sim margins, M0 off.

### M2 draw-order fix (S3 M2): sound and reproducible

`SeedSequence([seed, batch]).spawn(2)` with fixed `(S, G)` and `(S, C + 11)` shapes makes the postseason independent of the tie count and of G. The scenarios "one game change keeps the other draws" and "the fixed result keeps the other draws" plus the pinned stream digest (`test_engine_stream.py`) make it checkable.

Gaps (m3):
- Define C as the number of distinct non-NULL FBS `CONFERENCE` values of the `TEAM` rows, including conferences below `MIN_CONFERENCE_TEAMS`. Otherwise one conference crossing the size threshold shifts the bracket columns.
- Clamp the fixed-result `inv_cdf` argument to (ε, 1 − ε): `random()` can return 0.0, and `NormalDist.inv_cdf(0)` raises.
- `engine-contract` says championship games use `post_sigma`, while known `CCG` rows carry their own `SIGMA`. Say which wins.

### M4 (major): performance budget and the C/A gate

**Evidence.** The budget rows themselves trace correctly to the CSVs (checked):
- local fixed cost 3.2 s and compute 7.06 s;
- 1.2M T rows at 0.35M rows/s ≈ 3.4 s locally, 12M at 1.84M rows/s ≈ 6.5 s on AWS;
- outcome strings 3.75 s locally and the S4 AWS A load 7.2 s;
- `LEVERAGE` 4.75 s.

But:
- **Rows that are missing from the budget:**
  - the `INPUTS` snapshot over the Phase 3 as-of views (Phase 3 targets < 1 s per view per point, but `ENGINE_*_AS_OF` stack several);
  - the second write of 12M `SIM_TEAM` rows from the stage at 1M;
  - clearing the stage (a DELETE of about 13M rows; use `TRUNCATE`, which the lock makes safe);
  - the 7 step-row commits and the per-run DDL with up to 900 column comments;
  - the wider stage row (13 columns incl. `VARCHAR(2000)` / `VARCHAR(20000)`) versus S3's 8-column emission, which probe 1.1a measures but the budget does not carry.
- **The gate thresholds contradict the targets:**
  - Probe 1.1c passes C if the derivation takes ≤ 10 s locally. The listed local components already sum to about 18.5 s, so 10 s more plus the missing rows lands at or over 30 s.
  - On AWS, [16] keeps C while the derivation is ≤ C's direct emission (30.6 s). With about 35 s of known components that blows the 60 s target.
- **The decisive 1M measurement comes too late.** C derivation at 1M on AWS is first measured in **group J (6.1)**, after Phase 5 work may have started on layout C. A per-target flip (C locally, A on AWS) would split Phase 5's what-if SQL.
- **Local drift.** S3's local full mode ranged 40–76 s for the same cell. A ≤ 30 s target judged on "min–max of 3" needs an explicit rule: judge the median, report the max.

**Fix.**
- Add the missing rows.
- Set the 1.1c gates from the remaining budget: about ≤ 5 s at 100k locally and ≤ 10 s at 1M on AWS.
- Run probe 1.1c (and 1.1b, 1.1e) on AWS at 1M **in group A**. It needs only synthetic data and an "AWS free" window, not Phase 2's backfill.
- Decide C or A once, for both targets.

### Layout-C risks at 1M: real, but bounded

- **Columns per game.** A preseason point has about 800–900 remaining FBS-involved games, well below 2,000. The table-column limit is not in the skills (my recollection of Exasol's documented limit is 10,000, unverified), so probe 1.1b is the right gate.
- **Per-run DDL.** It costs one `CREATE TABLE` with up to 900 commented columns per run, and the tables accumulate: about 20–40 per season from weekly refreshes, backtests and manual runs. They are kept forever by the deployer's keep rule unless dropped (see M5 and m12).
- **Static `CFB_API` views cannot select from a table whose name changes per run.** Phase 5 must generate SQL from `SIM_RUN_GAME.PATH_COLUMN` (m11).

## 4. Interfaces

### `CFB_MODEL` inputs: checked against the Phase 3 specs

Present:
- `GAME_AS_OF`: `IS_NEUTRAL_SITE`, `IS_CONFERENCE_GAME`, `IS_CONF_CHAMPIONSHIP`, `POSTSEASON_KIND`, `CFP_ROUND`, `PARTICIPANTS_RULE`, `GAME_STATUS_AS_OF`, `HAS_TEAMS`, `MARGIN`.
- `TEAM_SEASON_AS_OF`: `CLASSIFICATION`, `CONFERENCE_TIER`, `WINS`/`LOSSES`, `CONF_WINS`/`CONF_LOSSES` and `CONF_POINT_DIFF` (both without championship games, as the plan assumes), `POINTS_FOR`/`POINTS_AGAINST` for F2, `ELO_AS_OF`, `SP_PRIOR_RATING`.
- `GAME`: `INVOLVES_FBS`, `PREGAME_SPREAD`, `HOME_PREGAME_WIN_PROB`, CFP seeds.
- `RATING_SP` offense and defense as `DECIMAL(6,2)`; `CONFERENCE_CHAMPIONSHIP`.

Gaps (m5):
- The resume fit reads the committee's final rankings, but no `RANKING` / `RANKING_AS_OF` read appears in Key Interfaces or the `ENGINE_TEAM_AS_OF` row.
- The fit runs `ENGINE_TEAM_AS_OF` over 2014–2023, where Phase 3's tiers are `AQ`/`P5`/`G5`/`NON_AQ`. The mapping table knows only `P4`/`G6`/`INDEPENDENT`.

Name both, and state that the tier is irrelevant to the resume. The roster view can then emit the raw tier for seasons before 2024.

### Cross-plan edits: feasible, but ownership is soft (m6)

- `LOAD_RUN.RUN_KIND` is `VARCHAR(16)` with no constraint in the Phase 2 worktree, so `SIMULATE` fits.
- `loadlib.lua` does not exist yet: the worktree is at Phase 2 groups A–B. "Coordinate with its owner" should become a named Phase 2 task, or a hand-off line in Phase 2's plan.
- The **stale rule** for the new kind is unspecified. A `SIMULATE` script should follow the "idle session = stale" rule. A host-driven `make sim` or `make calibrate` deploy that holds the lock between statements needs the "session ended" rule, like `SNAPSHOT`; Phase 3 leaves its deploy-lock kind unspecified too.
- The `sqlobjects.py` extension (task 2.1, data-table kind, keep rules) is correctly sequenced after Phase 3 and marked "no parallel edits".

### Refresh step 300: fits the Phase 2 contract

`loader/plan.py` accepts step numbers 100–999, unique numbers and names, the `(run_id, season, week, season_type)` call and a failed step that stops the chain. A nested `COMMIT` inside the step also commits the refresh's transaction, which is harmless because the refresh commits its step row first (Phase 3 probe 1.1h, re-checked in 1.1i).

### M5 (major): killed and abandoned runs

**Evidence.**
- `sim-runs` promises that failure cleanup "never leaves a `RUNNING` row or a partial output", but cleanup runs only on a caught error.
- A `KILL STATEMENT`, a client Ctrl-C or a lost session (likely mid-demo) leaves:
  - `SIM_RUN` / `SIM_RUN_STEP` rows `RUNNING` forever;
  - stage rows;
  - a `SIM_PATH_R<n>` table that the deployer's keep rule protects forever.
- `ABANDONED` exists as a status, but no rule sets it. Consumers are safe (they read only `DONE` runs), but the registry, the timeline and storage are not.

**Fix.**
- At lock acquisition, `SIMULATE` / `DROP_SIM_RUN` reconcile: every `SIM_RUN` that is `RUNNING` and whose `LOAD_RUN` row is not `RUNNING` (or whose session is gone) becomes `ABANDONED`. Its outputs are deleted, its path table dropped and its stage rows removed.
- Add a scenario "A killed run is cleaned up by the next run".

## 5. Demo fit

**Delivered:**
- Beat 3: the step-300 timeline with `SIMULATE` seconds, `INSTANCES`, `SIMS_PER_SECOND` and `SECONDS_ENGINE`.
- The scale dial: per-target `SIM_CONFIG` plus `SIMS=` / `BATCH=`.
- Beat 4 inputs: `SIM_TEAM_ODDS`, `LEVERAGE`, `GAME_SCORE_AS_OF`, `GAME_WIN_PROB`.

Suggestions and gaps:
- **m7, "every core":** the local default of 10k/batch gives 10 batches on 8 slots. The second wave keeps 6 of 8 slots idle (S3's own note), so the visible parallelism undercuts the line on stage. D19 says 5k–10k are equal within noise. Use 5,000 locally (20 batches, 2.5 per slot) or size from the measured `nrOfCores`.
- **m8, Phase 5 forced re-sim:** [14] claims "Phase 5's forced re-sim then only writes input rows", but `SIMULATE` always snapshots from the live views, so there is no entry point that runs from a base run's snapshot with `FIXED_HOME_WIN` overrides. Common random numbers also need the base run's seed and batch size. Add `SIMULATE_FROM(base_run_id, overrides_table, label)` now (the `SUMMARY` / verify path already runs the engine from a snapshot), or record the Lua change as Phase 5 work explicitly.
- **Week-over-week movement:** this needs "the `DONE` `REFRESH` run per point"; a one-line view would help Phase 5. The conditional rooting guide (2.2 s at 1M) is correctly left to Phase 5.
- **YAGNI (m9):**
  - `TOTAL_SIGMA` is fitted and stored but read by nothing.
  - `GAME_SCORE_AS_OF` computes both params versions for every point (twice the view cost) when only `make calibrate` and the backtest need `HOLDOUT_2023`. Consider a version filter or a separate view.

  Everything else earns its place: the 2024/2025 rule sets for the backtest, `sim-verify` for the mission's reproducibility clause, and Elo to 1890 for the upsets.

## 6. Testability

- `speq plan validate` passes. There are **68 scenarios**, each mapped to exactly one named test: engine 8, selection 10, score 9, calibration 7, win probability 4, runs 13, refresh 4, backtest 6, deploy 7.
- The tiers are right: the engine core is unit-tested against a loop oracle that never imports `engine.core`, the database is checked against the host by checksum, and every SQL/Lua task writes its integration test first.
- The dependency chain is coherent. A2 runs alongside B, and 4.2 deliberately does not wait for a real calibration.

Gaps:
- **m10, missing scenarios:**
  - a killed run (M5);
  - a known but **unplayed** championship pairing in `selection-rules` (only "played" is a scenario; the loop reference covers both);
  - a Pac-12 team as the 2026 G6 bid (m1);
  - post-selection regular games (M6);
  - for M1, win-total dispersion.
- **m13, IT fragility:** `test_resume_weights_fitted` asserts an overlap of at least 10 per season. The league's committee ranks with **test-parameter** ratings, while step 3 fits over ratings from the **just-fitted** parameters (recovered from the league's truth, μ = 2.5·home + strength, σ = 15). The two differ, so the assertion may flake. Either generate the committee from the fitted-param ratings, or assert on a tolerance derived from the generator.
- **m14:** probe 1.1e checks `PARTITION BY RUN_ID` with 3 runs. A season stores 20–40 runs, and the database skill warns against over-partitioning. Probe with at least 30 runs.

## Exasol claims re-checked against the skills

| Claim | Skill evidence | Result |
|---|---|---|
| `PYTHON3 SET … EMITS (…)`, `ctx.get_dataframe(num_rows=n)`, `ctx.emit(DataFrame)` with column count = `EMITS` | `exasol-udfs` `udf-python.md` | OK. NULLs in DataFrames remain probe 1.1a |
| `GROUP BY batch_id` parallelism = `nrOfCores` × nodes | S3 measured; the skill says "parallelism is automatic" | OK |
| `VALUES BETWEEN` | `exasol-grammar.md` `value_range_table`; `from_item` allows `"(" value_range_table ")" [AS] alias (col)` | OK. **m15:** the plan's sketch `(SELECT … FROM VALUES BETWEEN …)` should read `FROM (VALUES BETWEEN 0 AND n) AS b(BATCH_ID)` |
| `PARTITION BY` on `DECIMAL` | `table-design.md` supported types | OK; see m14 |
| `JSON_VALUE … RETURNING type` | `exasol-grammar-functions.md` `json_value_1` | OK |
| Lua `query`/`pquery`, nesting ≤ 255, results materialized in Lua memory | `lua-execute-scripts.md` | OK. Never `SELECT` the stage into Lua (the plan doesn't) |
| No `ERF`; `NORMAL_CDF` as a SQL `FUNCTION` | the grammar has no erf; `create_function_statement` | OK, error 7.45e-8. **m16:** PL-style SQL functions run per row; time `GAME_SCORE_AS_OF` over the about 300k training rows, and keep an inlined-polynomial fallback |
| Maximum columns per table | not in the skills | Probe 1.1b; correctly listed as unverified |

## Minor findings index

| # | Finding |
|---|---|
| m1 | Pac-12 as G6 from 2026 not pinned by a scenario |
| m2 | Straight-seeding scenario leaves the champions' conferences unstated under 2026 rules |
| m3 | Definition of C; `inv_cdf` clamp; `post_sigma` vs `CCG.SIGMA` |
| m4 | `ELO_CENTER` not fitted; no bias report by rating-source pair |
| m5 | Committee-ranking input unnamed; pre-2024 tier mapping |
| m6 | `SIMULATE` lock kind needs a named Phase 2 owner and a stale rule; deploy-lock kind unspecified |
| m7 | Local default of 10k/batch leaves slots idle; prefer 5k for the "every core" beat |
| m8 | No run-from-snapshot-with-overrides entry point for the forced re-sim |
| m9 | `TOTAL_SIGMA` unused; `HOLDOUT_2023` computed at every point |
| m10 | Missing scenarios (listed in § 6) |
| m11 | Per-run table names force generated SQL in `CFB_API`; record it as a Phase 5 consequence of D20 |
| m12 | Per-run tables accumulate; document a retention policy or `make sim-drop` hygiene |
| m13 | The resume-fit IT may flake |
| m14 | Partition probe with too few runs |
| m15 | `VALUES BETWEEN` needs the parenthesized `from_item` form |
| m16 | `NORMAL_CDF` SQL-function cost at training scale |

## What's good

- **Spike discipline.** Every engine and storage choice traces to a measured D19/D20 number. The single-stage-shape idea is a genuinely better synthesis of S3's two recommendations than either alone, and it saves a compute pass per output.
- **Honest, falsifiable model checks:** the closing line as a yardstick only, `HOLDOUT_2023` covering the resume weights too, the "+7 margins leave the holdout unchanged" test, and invariance tests that also prove they can detect a change.
- **Predictions and simulations agree by construction.** The win-probability formula is exactly the probability under the engine's rounding and overtime, and the outcome-share scenario ties them together.
- **Reproducibility as a feature.** The input snapshot, an engine without defaults, the model digest plus engine version, the pinned numpy stream digest and `make sim-verify` make the mission's "reproducible from inputs + seed" provable after any refresh.
- **Correct, cheap-to-flip CFP rules.** The 2024, 2025 and 2026 rule sets are right. The G6 wording is a single seed value, and web research confirms the plan's pick.
- **The specs are precise enough to test.** Every number in the scenarios re-derives exactly, 68 scenarios map one to one, and validation passes.

---

## Review resolution

Resolved on 2026-10-22 by the planner, under the orchestrator's decisions for the absent owner (logged verbatim in `decision-log.md` § Revision interview).
- **Ground rules:** no DB session, zero CFBD calls, no edit outside this plan directory.
- **Skills loaded first:** `exasol:exasol-udfs`, `exasol:exasol-database` and `exasol:exasol-distributed-ml`.
- **Evidence:**
  - the Phase 2 loader probe (`../exasol-cfb-demo-p2/docs/spikes/s5-loader-probe.md`);
  - the Phase 3 specs;
  - the S3/S4 write-ups;
  - the stored replay recordings `cache/replay/games/*.cfbd.json` (2024 and 2026 `/games`, keyed on `ID`) and `cache/replay/rankings/*.cfbd.json` (2024);
  - seeded host simulations for the gate tolerances and the M1 scenario.

`speq plan validate add-cfb-score-engine` passes with no warnings: 9 delta specs, 81 scenarios (68 before), each mapped to one named test in plan.md.

### Majors

| # | Decision | What changed | Where |
|---|---|---|---|
| M1 | Accepted | Per-sim team offsets: a `standard_normal((S, T))` block after `z` and `u` in the season stream. Margin = `rint(MU + TEAM_SIGMA·(d_home − d_away) + game_sigma·z)` with `TEAM_SIGMA` = `TAU`·√(h − 1)/√2 and `game_sigma` = `SIGMA_1`; postseason games use `post_team_sigma`, which replaces `post_sigma`. The engine validates √(`game_sigma`² + 2·`TEAM_SIGMA`²) ≈ `SIGMA`, so single-game probabilities, `MARGIN_WIN_PROB` and the 0.8289 check are unchanged. New scenario "Shared team strength widens the spread of season win totals": simulated 3.37–3.42 vs 1.99–2.01. The old 0.05-tail reversal trigger is replaced by `BACKTEST_CALIBRATION`: Brier and Spiegelhalter Z of `P_IN_CFP` over every eligible team × point, plus the win-total dispersion with a clustered SE (reported, review trigger at 3 SE). | `engine-contract`, `backtest`, `sim-runs` (`TEAM_SIGMA` 2.00 on Ashford–Brookvale); plan Key Interfaces, tasks 1.3–1.5, 4.2, 6.3; decisions [29], [30], [3] superseded in part |
| M2 | Accepted, with a stricter multiplier | Bucket tolerance max(0.05, k·SE) plus an aggregate Spiegelhalter gate per phase, as decided, but with k = 3 rather than 2. A seeded simulation of the gate population fails a perfectly calibrated model in 28.5 % of samples with 2·SE (about 8 qualifying buckets), which would recreate M2. With 3·SE the whole gate family fails 4.3 %. The unit test asserts ≥ 94 % pass for a perfect model and ≥ 50 % fail for one with σ 20 % too small (simulated 69 %). | `calibration` (targets, new scenario "A well-calibrated model passes the gate…"); task 1.7; decision [31] |
| M3 | Accepted | `WEEK_PHASE` (`EARLY` 0–4, `MID` 5–9, `LATE` 10+). `SCORE_MODEL_PARAMS` has one row per version × phase. Report, buckets and gate are split by phase. Fallback order `SP_ELO` → `SP_ELO_PHASED` (3 × 2 coefficients) → F2. `ELO_CENTER` is fitted as the mean FBS Elo, and the new `SCORE_SOURCE_REPORT` gives bias by rating-source pair. Gating per phase also exposed a second noise trap: a fixed \|bias\| ≤ 1.0 per phase fails a perfect model 47.5 % of the time. It is now max(1.0, 3 SE). | `score-model` (new phase scenario), `calibration` (new phased-fit scenario), `win-probability` (one line); tasks 1.7, 2.4, 4.1, 6.2; decisions [32], [33] |
| M4 | Accepted | Budget completed: input snapshot, checks, DDL and 14 commits, the wider stage row, the second `SIM_TEAM` write, the stage `TRUNCATE`. One correction beyond the review: the old local budget added S3's 3.2 s fixed cost on top of a compute figure that already contains it. The sums without C are about 22 s local and 38 s AWS, so the C gates become ≤ 5 s (100k local) and ≤ 10 s (1M AWS), leaving 3 s and 12 s of reserve. Probe 1.1 now runs (b, c, e, f) on AWS in group A on synthetic data (AWS free: deploy and run only). C or A is decided once for both targets. Targets are judged on the median of 3, with min and max reported. | plan Performance budget, Requirements, task 1.1, 6.1; decisions [34], [35], [16] amended |
| M5 | Accepted | Lock kinds `SIMULATE` (script rule: gone, own, or idle) and `DEPLOY` (host rule: gone or own, like snapshots). The next lock holder, and the refresh step at its start, marks other `RUNNING` runs `ABANDONED`, deletes their outputs, drops their path tables and truncates the stage. New scenarios "A killed run is cleaned up by the next run" (`KILL STATEMENT` during a test-hook sleep; probe 1.1j checks the premise) and "An idle deploy keeps the lock until its session ends". | `sim-runs`, `refresh-step`, `deploy`, `calibration`; tasks 1.1(j), 3.1, 3.2; decision [36] |
| M6 | Accepted, the rule refined | Verified at zero calls on the stored 2024 recordings: CCGs in regular week 15, Army–Navy alone in week 16, and the selection ranking labeled week 16. `CFB_SIM.SELECTION_WEEK` = the week of the season's last *power-conference* championship game, with a December rule while none is stored (the 2026 schedule has no week-14 rows yet). "Last championship game of any conference" would fail on the stored 2026 schedule: Army–Navy is flagged as an American Athletic conference game alone in week 15, and Phase 3's detection would call it a championship game (X1). Points after the selection week are refused or skipped. Later games are left out of the engine with warnings, and the backtest's points are `SELECTION_WEEK_SEQ − k`. 2025 is not on disk and is checked in 6.1 (Q14). New scenarios for the week, for left-out games and for the week-16 skip. | `sim-runs`, `refresh-step`, `backtest`, `calibration` (resume fit at the selection point), `engine-contract`; tasks 1.8, 2.5, 6.1; decision [37] |

### Minors

| # | Resolution |
|---|---|
| m1 | Two scenarios: a Pac-12 team takes the 2026 G6 bid (unit), and `ENGINE_TEAM_AS_OF` gives every Pac-12 team tier `G6` in 2025 and 2026 (integration) |
| m2 | The straight-seeding scenario now names the champions' conferences, the G6 team and Notre Dame's absence, and asserts the same field under all three rule sets |
| m3 | C counts every conference; inverse-CDF clamp [1e−12, 1 − 1e−12]; a known unplayed CCG uses its own `MU`/`SIGMA`/`TEAM_SIGMA`; empty outcome string = NULL ([41]) |
| m4 | `ELO_CENTER` fitted; `SCORE_SOURCE_REPORT` ([33]) |
| m5 | Committee input named: `CFB_MODEL.RANKING`, `POLL_CODE` `CFP` (reference `POLL`), `IS_FINAL_POLL`. It is read as a label rather than `RANKING_AS_OF` (X2). Pre-2024 tiers mapped `AQ`/`P5` → `P4`, `NON_AQ`/`G5` → `G6` ([40]) |
| m6 | Hand-off H1 is a named cross-plan prerequisite, task 1.10. Owners: Phase 2 task 4.2 (`loadlib.lua`, kind `SIMULATE`, callable lock functions) and 5.2 (host `DEPLOY` helper), relayed by the orchestrator. Tasks 2.2 and 3.1 wait for it. Phase 3's deploy is advised to use `DEPLOY` |
| m7 | Local default 5,000 per batch (20 batches, 2.5 per slot) ([39]) |
| m8 | `CFB_INFRA.SIMULATE_FROM(base_run_id, fixed_results, run_label)` and `make simulate BASE= FIX=`. New scenario proves every other outcome column equals the base run's ([38]) |
| m9 | `TOTAL_SIGMA` dropped; probe 1.1h checks that the version filter prunes ([42]) |
| m10 | Missing scenarios added: killed run, unplayed known CCG pairing, Pac-12, post-selection games, win-total dispersion |
| m11 | `SIM_RUN.PATH_TABLE` added; Phase 5's generated SQL recorded in Non-Goals, [16] and task 5.3 |
| m12 | Retention documented in task 5.1 (weekly refresh runs kept for the season, others dropped with `make sim-drop`); automatic pruning to the backlog |
| m13 | `--resume-only` fits over the generator's own ratings; the IT asserts overlap 12 and Spearman 1.0 ([43]) |
| m14 | Probe 1.1e stores 30 runs of 100k locally and 20 runs of 1M on AWS |
| m15 | `CROSS JOIN (VALUES BETWEEN 0 AND :last_batch) AS b("BATCH_ID")` per the grammar's `from_item`; bound vs verbatim limit in probe 1.1d |
| m16 | Probe 1.1h times `NORMAL_CDF` over 300k rows against the inlined polynomial and adopts the inlined form if the function is more than 2× slower |

### Q1 / Q2

Kept as read: G6 bid = best-ranked G6 team; Notre Dame top-12 guarantee as an automatic bid. The cfp.org check is now an explicit task (1.11) due before the first committee ranking, about 2026-11-03.

### Not adopted, with reasons

- **2·SE bucket multiplier (M2):** replaced by 3·SE on the simulation evidence above. The structure the orchestrator decided (per-bucket SE tolerance plus an aggregate gate) is kept.
- **"Last conference championship game" of any conference (M6):** replaced by the last power-conference championship game, with a December fallback, on the stored 2026 evidence above. The intent (selection week from the championship weekend) is kept.
- **Unit-test bar of 95 % (review M2 text):** 94 % of 1,000 seeds. The gate's expected pass rate is 95.7 %, so a 95 % bar would fail by sampling error about one time in eight.
- **A hard gate on the M1 calibration check:** reported with a 3-SE review trigger instead. Two correlated seasons are too few to fail a build on, and the other probability metrics of [25] are reported too.
- Nothing else was rejected.

### Cross-plan findings for the orchestrator

- **X1 (Phase 3, time-bound).** The stored 2026 schedule flags Army–Navy (12 December, week 15) as an American Athletic conference game, alone in its week. Phase 3's `CONFERENCE_CHAMPIONSHIP_GAME` rule ("C's only conference game in its week, in C's last week with such a game") will therefore take it for the American Athletic's 2026 championship game. Once the real title game is stored in week 14, the rule will miss that one.
  - This plan is protected for the selection week and leaves the flagged game out.
  - The undetected real title game would enter 2026 runs from the week-13 point (late November) as a regular conference game and can stop them with `CONFERENCE_PAIR_REPEATED`.
  - Suggested Phase 3 fix: restrict the detection to weeks up to the season's power-conference championship week.
- **X2 (Phase 3, information).** The 2024 selection ranking is labeled regular week 16 (`isFinal`), so with the committee's label lead of 0 it becomes visible one point after selection. This plan reads final rankings only as labels and is unaffected. Phase 3's Q2 may want the observation.
