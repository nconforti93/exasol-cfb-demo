# Decision Log: add-cfb-score-engine

Date: 2026-10-22 (planned)

## Interview

The owner (Nico) was away and authorized the orchestrator to decide and log. The answers below are the orchestrator's, as passed to the planner.

**Q:** What is the scope?
**A:** Roadmap Phase 4 ("Score model + engine") in `docs/roadmap.md`:
- a margin/total score model calibrated on 2014–2025 (MAE, Brier, calibration), with an Elo-based probability for pre-2013 upsets;
- a selection config for the 2026 CFP rules, with simplified tiebreakers;
- a vectorized Python season simulation engine inside Exasol (SET UDFs, seeded per batch) and a run registry;
- a backtest of the 2024 and 2025 CFP fields.

Demo beat: "100k seasons in N seconds, in parallel on every core, in plain Python inside the database". The sim count is the scale dial: local 100k, AWS 1M+.

**Q (1) Engine design:** Where does it come from?
**A:** From the finished spikes, treated as binding:
- the sources: `docs/spikes/s3-python-engine-throughput.md` (D19), `docs/spikes/s4-sim-storage.md` (D20), `specs/architecture.md` § Engine contract, `specs/_plans/add-engine-spikes/`, and `spikes/s3`, `spikes/s4` (prototype code and result CSVs);
- the measured numbers: Python engine GO; 100k compute about 7 s local and about 1.5 s on AWS; full mode with sparse `SIM_TEAM` estimated at about 20 s locally;
- the settings: `sims_per_batch` 1k–10k, at least 2 batches per core slot, one DataFrame per batch, explicit `EMITS`;
- storage: layout C, `SIM_TEAM` with in-CFP rows only, plus per-run `LEVERAGE` and a `TEAMS` roster;
- code: reuse the prototype where it fits, but production code lives outside `spikes/` (for example `engine/`);
- `CLAUDE.md` § Environment holds the platform bullets: SLC numpy pin plus batch size, `nrOfCores` concurrency, `getpid` = 1 → `vm_id`, string → DOUBLE rounding.

**Q (2) Model inputs:** What may the score model and the engine read?
**A:** Only `CFB_MODEL`. The Phase 3 plan (`specs/_plans/add-cfb-analytical-model/`, just revised) maps the engine contract inputs in plan.md Key Interfaces and decision [31]:
- `GAME_AS_OF` with `IS_NEUTRAL_SITE`, `IS_CONFERENCE_GAME`, `MARGIN`, the CFP/postseason columns and `PARTICIPANTS_RULE`;
- `TEAM_SEASON_AS_OF`, `RATING_SP` and `RANKING_AS_OF`;
- `DECIMAL` types.

Phase 3 deliberately leaves indices, μ, σ, totals, ratings, SOS and `capped_pd` to Phase 4. The backtest must use as-of views only, so it is leak-free by construction.

**Q (3) Selection config:** Which rules?
**A:** For 2026, the 12-team CFP. Check the current rules (auto-bids, seeding and byes for 2025 vs 2026) via web research or `docs/` if present, decide, and log it. Tiebreakers are simplified and documented.

**Q (4) Calibration:** How sophisticated?
**A:** The simplest model that hits sensible MAE and Brier targets (for example a linear margin model on SP+/Elo/home field with a normal margin distribution), justified. YAGNI: no ML frameworks unless clearly needed; runtime code must run in the builtin PYTHON3 SLC with its numpy pin.

**Q (5) Run registry:** What does it record?
**A:** run_id, as-of point, seed, sims, batch config, timings per step, code version.

**Q (6) Integration:** How does the engine join the weekly refresh?
**A:** As a step in Phase 2's refresh chain (`REFRESH_STEPS`), after Phase 3's `MODEL` step 100, so that `REFRESH_WEEK` → … → model → predictions → sims shows in the demo timeline. See the Phase 2 plan `specs/_plans/add-cfb-persistence-refresh/` (run-logs and refresh-week specs).

**Q (7) Targets:** Where does it run?
**A:**
- Local Personal 2026.2 nano and AWS 2026.1.1 (3 nodes); see `specs/_plans/ORCHESTRATOR-STATUS.md` → "Facts learned on AWS".
- Integration tests run on local in a `TEST_` namespace with the existing harness and synthetic data.
- The live 1M demo run is on AWS and makes zero CFBD calls.

**Q (constraints, from the orchestrator):** What limits apply to planning?
**A:**
- No DB sessions (other agents use the databases): read the spike CSVs instead.
- No CFBD calls.
- Don't edit `docs/roadmap.md`, `specs/architecture.md` or `CLAUDE.md`: list the edits as tasks (another agent is editing the roadmap now).
- Invoke `exasol:exasol-udfs`, `exasol:exasol-database` and `exasol:exasol-distributed-ml` before Exasol UDF/SQL claims.
- TDD tiers as in the other plans.
- Spec deltas in domain `engine/` plus anything else needed; plan.md and decision-log.md with the same structure as the other plans; `[expert]` sparingly; `speq plan validate`.

## Design Decisions

### [1] The spec domain is `sim-engine/`; the code lives in `engine/`

- **Decision:** the nine features live under `sim-engine/`. Python code is in `engine/`, SQL in `sql/sim/`, Lua in `engine/lua/`.
- **Alternatives:** domain `engine/`, as the orchestrator's brief worded it.
- **Rationale:** `CLAUDE.md` plans the domain as `sim-engine/` ("The engine contract (inputs, RNG, outputs) is a spec in `sim-engine/`"), and so does the S3 write-up. The project rule wins over a loose brief, and renaming the directory is trivial if the orchestrator insists. Reported back.
- **Promotes to ADR:** no

### [2] Score model: linear margin on the previous season's SP+, the current Elo and home field

- **Decision:** a team's rating at point P is `B_SP × SP+ prior + B_ELO × (Elo as of P − 1500)`.
  - It falls back to one-feature fits (Elo only, SP+ only) and to class defaults (FBS, non-FBS).
  - μ = home field (off neutral sites) + the rating difference. Margins are normal around μ.
  - Coefficients are fitted by least squares on next-week games 2014–2025.
- **Alternatives:**
  - market lines as μ: not a model, not available at future horizons, and the mission excludes betting picks;
  - ratings solved from margins in SQL (SRS-style iteration): more machinery; kept as the F2 fallback feature;
  - gradient boosting or other ML frameworks: rejected by YAGNI and the SLC constraint;
  - the current season's SP+ as a feature: invisible at historic points (fetched after the season), so it cannot be calibrated leak-free.
- **Rationale:** the simplest model that uses only what is live at every point; it fits in seconds, explains in one sentence, and both inputs exist from 2014 (SP+ from 2013 as prior) and back to 1890 (Elo).
- **Promotes to ADR:** yes

### [3] Uncertainty grows with the horizon; no per-simulation team strength offsets

- **Decision:** `SIGMA` = √(`SIGMA_1`² + `TAU`² × (weeks ahead − 1)), fitted on residual variances per horizon. Ratings are static inside a simulation. The contract's "rating-update flag" is not implemented.
- **Alternatives:**
  - one σ for every horizon: overconfident for games weeks away;
  - per-sim team strength offsets (correlated outcomes, "hot" sims): one more random block and parameter, not measurable on per-game metrics.
- **Rationale:** two parameters, measured directly by the per-horizon report.
- **Reversal trigger:** the backtest shows overconfident playoff odds, i.e. actual field teams with `P_IN_CFP` below 0.05 at k = 3 in either season (`MISSES` > 1). Then add per-sim offsets as a new engine version.
- **Promotes to ADR:** yes

### [4] Win probability matches the engine's rounding; the normal CDF is a SQL function

- **Decision:** `HOME_WIN_PROB` = ½ (Φ((μ − ½)/σ) + Φ((μ + ½)/σ)). `NORMAL_CDF` is a SQL function implementing Abramowitz–Stegun 26.2.17 (error < 7.5e-8).
- **Alternatives:**
  - Φ(μ/σ): disagrees slightly with the engine's `rint` plus overtime coin;
  - a Python scalar UDF: VM start-up per query.
- **Rationale:** the predictions page and the simulations then agree by construction. The `exasol-database` grammar has `EXP` and `SQRT` but no error function.
- **Promotes to ADR:** no

### [5] Totals from the previous season's SP+ offense and defense

- **Decision:** `TOTAL` = `TOTAL_INTERCEPT` + `TOTAL_SLOPE` × (both teams' offense + defense ratings), else `TOTAL_DEFAULT`. The gate is only that totals MAE beats the mean-total baseline. The engine carries `TOTAL`, unused.
- **Alternatives:** season-to-date scoring rates; no totals at all.
- **Rationale:** the roadmap asks for a margin/total model, and predicted scores need a total. One linear term is enough for v1; the totals never feed the odds.
- **Promotes to ADR:** no

### [6] Calibration runs on the host and commits generated seeds, in two versions

- **Decision:** `make calibrate` fits with numpy 1.26.4 on the host and writes `sql/sim/010_score_model_params.sql` and `012_resume_weights.sql`, generated and reviewed. It deploys them and reports. The versions are `CURRENT` (2014–2025) and `HOLDOUT_2023` (2014–2023). Until the first real calibration (task 6.2), both rows hold `UNCALIBRATED` S3 defaults.
- **Alternatives:**
  - a fit UDF inside Exasol (parameters live only in one database);
  - refit at every deploy (silent drift);
  - one version (no honest out-of-sample check or backtest).
- **Rationale:** parameters travel in git, so local, restored and AWS runs use the same model. A calibration is a deliberate, diffable act like `make generate`. The prediction formula exists only in SQL, and the host fits from the same view's raw columns.
- **Promotes to ADR:** yes

### [7] Leak-free calibration with the closing line as a yardstick

- **Decision:**
  - Features come only from the as-of views and the previous season's SP+; labels come only from completed games.
  - The closing line (`GAME.PREGAME_SPREAD`, `HOME_PREGAME_WIN_PROB`) is compared with, never used.
  - The gate is out-of-sample (`HOLDOUT_2023` on 2024–2025, horizon 1, FBS-vs-FBS): MAE ≤ line + 1.5 and ≤ 15.0; Brier ≤ line + 0.020 and ≤ 0.190; |bias| ≤ 1.0; calibration buckets within 0.05; totals beat the mean.
- **Alternatives:** absolute targets only (no grounding); in-sample metrics (optimistic); the line as a feature (it knows more than the model at the point and is not available weeks ahead).
- **Rationale:** relative targets stay meaningful whatever the data's noise level, and the absolute caps catch a broken line.
- **Promotes to ADR:** yes

### [8] Elo win probability for games without a line; upset magnitude in the view

- **Decision:**
  - `GAME_WIN_PROB` uses the stored line's probability from 2013, else 1 / (1 + 10^(−(ΔElo + `ELO_PROB_HFA` × not neutral) / `ELO_PROB_SCALE`)).
  - Scale and home field are fitted by maximum likelihood on 2014–2025.
  - `UPSET_MAGNITUDE` = 1 − the winner's pregame probability, the mission glossary's definition.
- **Alternatives:** the score model's probability for historic games (no SP+ before 2013); CFBD's Elo formula constants unfitted.
- **Rationale:** a consistent probability back to 1890, checked against the lines where both exist. Phase 5 ranks the upsets.
- **Promotes to ADR:** no

### [9] 2026 selection rules: four power champions, the best-ranked Group of Six team, the Notre Dame guarantee

- **Decision:** `CFP12_2026`:
  - 12 teams; automatic bids for the SEC, Big Ten, Big 12 and ACC champions, plus the best-ranked team of a G6 conference, champion or not (`G6_BID` `TEAM`);
  - Notre Dame (`GUARANTEE_SCHOOL`) in if ranked within 12;
  - at-large to 12; straight seeding; top 4 byes; fixed bracket.
- **Alternatives:** "highest-ranked G6 champion" (On3/Yahoo, 2026-01-23); the mission glossary's "4 power conference champions + highest-ranked G6" (silent on champion vs team and on Notre Dame).
- **Rationale:** web research on 2026-10-22. The CFP management committee kept 12 teams for 2026 after the commissioners could not agree on expansion.
  - SI (2026-01-27, citing The Athletic) and an NCAA explainer: the best-ranked G6 team, champion or not.
  - On3/Yahoo (2026-01-23): "highest-ranked Group of Six champion". The later, more specific source wins.
  - All sources agree on the Notre Dame top-12 guarantee and on straight seeding since 2025.

  `G6_BID` is one seed value, so a correction is cheap (Q1).
- **Promotes to ADR:** yes

### [10] Rule sets for 2024 and 2025 as well; no 4-team era

- **Decision:** `CFP12_2024` (five best-ranked conference champions; the four best-ranked champions get seeds 1–4) and `CFP12_2025` (five best-ranked champions; straight seeding) are seeded next to `CFP12_2026`. Seasons before 2024 have no rule set, and runs for them are refused.
- **Alternatives:** backtest 2024/2025 under 2026 rules; add the 4-team rules for 2014–2023.
- **Rationale:** a backtest against real fields needs the rules they were selected under: in 2025 ACC champion Duke was left out behind Tulane and James Madison, which 2026 rules would have reversed. The 4-team era is not needed for any v1 page.
- **Promotes to ADR:** yes

### [11] The committee is a resume formula with a rating term, fitted to final rankings

- **Decision:** resume = `w_win_pct` × win % + `w_sos` × SOS + `w_pd` × capped point differential per game + `w_rating` × rating.
  - `w_win_pct` is fixed at 100. The other weights come from a fixed grid that maximizes the overlap with the committee's final top 12 for 2014–2025 (ties: Spearman over the top 25, then the smallest weights).
  - The inputs come from the same SQL the engine reads.
- **Alternatives:** the S3 prototype's fixed weights (100, 2.0, 0.5); an ordinal regression; replaying the in-season committee rankings.
- **Rationale:** the committee visibly rewards strength beyond record. A deterministic grid is explainable and reproducible. The backtest's selection gate (Q6) tests it.
- **Promotes to ADR:** yes

### [12] Simplified conference rules

- **Decision:** S3's standings order is kept: win %, two-way head-to-head, conference point differential, rating, index.
  - A conference needs at least 6 FBS teams to have a champion (2024–2025 Pac-12: none).
  - Conferences with a championship game in `CFB_MODEL.CONFERENCE_CHAMPIONSHIP` play it: the known pairing (and result) when the game is visible, else the top two, simulated on a neutral site.
  - Other conferences crown their standings leader.
- **Alternatives:** full tiebreaker rules per conference (out of scope per the mission); always simulating championships (wrong in the week before them and in the backtest).
- **Rationale:** the "Sunday before championship week" is a demo moment and a backtest point; using the real pairing there costs one input role.
- **Promotes to ADR:** no

### [13] Fixed-shape draws from two child streams per batch

- **Decision:** `SeedSequence([run_seed, batch_id]).spawn(2)`. The season stream draws `standard_normal((S, G))`, then `random((S, G))`. The postseason stream draws `standard_normal((S, C + 11))`, then `random((S, C + 11))`.
- **Alternatives:** the prototype's order, with a data-dependent tie block before the postseason (S3 M2: a +0.3 μ change re-randomized 64 % of champions); one fixed overtime block without child streams.
- **Rationale:** common random numbers across model deltas and fixed results. The postseason is independent of season ties and of the season's game count, while scheduling independence is kept.
- **Promotes to ADR:** yes

### [14] Fixed results are in the engine now

- **Decision:** a `REMAINING` game with `FIXED_HOME_WIN` keeps its winner. Its margin maps the game's own uniform into the winner's side of N(μ, σ) through `statistics.NormalDist().inv_cdf`, at least 1 point.
- **Alternatives:** leave fixed results to Phase 5 (an engine change and a new version later); reflect the unconditional margin (wrong distribution for upsets).
- **Rationale:** the architecture's engine contract states them; Phase 5's forced re-sim then only writes input rows. Only the forced game's cell changes.
- **Promotes to ADR:** no

### [15] One engine pass per batch, one explicit stage shape, SQL splits

- **Decision:** `CFB_INFRA.SIM_ENGINE` has one explicit `EMITS` (`SIM_STAGE`). Its tagged rows are playoff rows, outcome strings, wins and title tallies, and one batch summary. Plain SQL moves them into the output tables.
- **Alternatives:** one UDF per output shape (S3's wording: one compute pass per output, about 7 s per 100k locally each); variadic `EMITS` (estimated 0.34 s per statement).
- **Rationale:** reconciles S3's two recommendations ("explicit `EMITS` per shape" and "single-pass staging"): the stage is the one shape. Emission dominates, so the extra SQL pass over 1.3M rows per 100k is cheap.
- **Promotes to ADR:** yes

### [16] Layout C is derived in SQL from the emitted outcome string, in a per-run table

- **Decision:** the engine emits one outcome string per season (S4's layout-A emission cost). `SIMULATE` creates `SIM_PATH_R<run>` with one commented `BOOLEAN` column per remaining game and fills it with `SUBSTR(…) = '1'`. Probe 1.1c gates this: if the derivation takes more than 10 s at 100k locally (or more than C's direct emission at 1M on AWS), the run stores layout A instead (D20's fallback).
- **Alternatives:** emitting C directly from the UDF (S4: 30.6 s vs 7.2 s at 1M on AWS, and the stage shape would carry 400+ columns on every row); storing A only (contradicts D20 without evidence).
- **Rationale:** keeps D20's read layout at A's emission cost, measured before anything depends on it. Per-run tables follow from C's per-run game set.
- **Promotes to ADR:** yes

### [17] Shared sparse output tables, partitioned by run; the engine emits team ids

- **Decision:**
  - `SIM_TEAM` (in-CFP rows, D20) and `LEVERAGE` are shared tables `PARTITION BY RUN_ID`, with CFBD `TEAM_ID`s emitted by the engine.
  - `TEAM_WINS` and `CONF_CHAMP` are summed from per-batch tallies (D20's per-run aggregates). `LEVERAGE` is built in SQL after the load (D20).
  - The roster is `SIM_RUN_TEAM` (D20's `TEAMS`). No M0 histogram.
- **Alternatives:** per-run tables for everything (Phase 5's cross-run queries would need dynamic SQL); emitting team indices (an index → id join over 12M rows at 1M).
- **Rationale:** what-if and movement queries stay plain SQL. Probe 1.1e and task 6.4 verify that several stored runs keep S4's latency.
- **Promotes to ADR:** yes

### [18] Each run snapshots its inputs and can be verified

- **Decision:** `SIMULATE` copies the engine inputs into `SIM_RUN_TEAM`, `SIM_RUN_GAME` and `SIM_RUN_PARAM`, and the engine reads only that snapshot. `make sim-verify` re-runs the engine in summary mode and compares checksums. The registry holds the full key: seed, batch size, sims, numpy and Python versions, model digest, engine version.
- **Alternatives:** read the live views (a run could not be reproduced after the next refresh); store only the digest.
- **Rationale:** the mission's "runs MUST be reproducible from inputs + seed", made checkable. The snapshot is a few thousand rows.
- **Promotes to ADR:** yes

### [19] Simulation runs join the loader's run lock

- **Decision:** a manual `SIMULATE`, `DROP_SIM_RUN` and `make sim` take Phase 2's run lock (`LOAD_RUN`, new run kind `SIMULATE`, same stale-row rules). The refresh step runs under the refresh's own lock.
- **Alternatives:** a separate sim lock (a second stale-session implementation, and backfills could interleave with a run's input snapshot); no lock (two runs would share `SIM_STAGE`).
- **Rationale:** one lock for everything that writes. The cost is one more run kind in Phase 2's list (recorder note in plan.md).
- **Promotes to ADR:** yes

### [20] Refresh step 300, configured per target, with a deterministic seed

- **Decision:**
  - `REFRESH_SIMULATE` is registered as step 300 and uses `SIM_CONFIG` from `engine/sim-config.yaml` (`local` 100k / 10k, `aws` 1M / 10k).
  - The seed is season × 1,000 + week sequence, with `CURRENT` parameters.
  - It is skipped (`DONE`, "skipped: playoff field set") after the last regular week.
- **Alternatives:** step 200 (Phase 2's examples already use 200 for `PREDICT`, Phase 5); random seeds; one global default.
- **Rationale:** "model → predictions → sims" in the timeline, weekly runs reproducible by default, and the scale dial set per deployment rather than remembered by the presenter.
- **Promotes to ADR:** yes

### [21] No runs after the regular season or before 2024

- **Decision:** points after `LAST_REGULAR_WEEK_SEQ` and seasons without a rule set are refused.
- **Alternatives:** a bracket-only mode with the actual field (needs seeds 1–4 that CFBD shows only in quarterfinal games); 4-team rules.
- **Rationale:** the demo is in-season, the backtest's last point is the selection weekend, and Phase 3 has one postseason week (its Q3). Backlog.
- **Promotes to ADR:** no

### [22] Non-FBS opponents are ineligible teams; records leave out championship games

- **Decision:** the engine's teams are the FBS team-seasons plus the non-FBS opponents of remaining games (`NON_FBS`, never ranked or selected). Records passed to the engine exclude a played championship game, because the engine adds the championship game uniformly, played or simulated.
- **Alternatives:** drop FBS-vs-FCS games (wrong records); let the model's records include championship games (double counting in the resume).
- **Rationale:** real schedules include FCS games in November too; one rule for the championship game keeps the resume consistent.
- **Promotes to ADR:** no

### [23] Batch sizes and bounds follow D19

- **Decision:** defaults: local 10,000 per batch (D19's measured laptop pick), AWS 10,000 for 1M (100 batches over 12 slots), and 1,000 for a 100k AWS run (passed explicitly). Accepted range 100–20,000, `n_sims` a multiple, at most 10,000 batches. `BATCHES_PER_INSTANCE` is recorded, not enforced.
- **Alternatives:** enforce 2 batches per slot (would forbid D19's own laptop pick on an 8-core start); allow uneven last batches (a smaller run would not be a prefix).
- **Rationale:** D19 is binding; the lower bound of 100 keeps integration tests fast.
- **Promotes to ADR:** no

### [24] The engine has no parameter defaults

- **Decision:** every `PARAM` key is required. The only optional key is the test hook `fail_batch`, which only `TEST_` sets.
- **Alternatives:** S3's defaults overridden by `PARAM` rows.
- **Rationale:** the snapshot then shows every value a run used; nothing hides in code between engine versions.
- **Promotes to ADR:** no

### [25] Backtest design

- **Decision:** for 2024 and 2025, runs at `LAST_REGULAR_WEEK_SEQ − k` for k = 5, 3, 1, 0, under each season's rule set and `HOLDOUT_2023`.
  - Metrics: Brier score of `P_IN_CFP`, expected overlap, misses below 0.05, the actual champion's probability; at k = 0 also the selected overlap, auto-bid matches, bye overlap and seed MAE.
  - Gate: selected overlap ≥ 10 at k = 0. Probability metrics are reported, not gated.
- **Alternatives:** gate on Brier scores (two seasons are too few to set a meaningful bar); 2026 rules (see [10]).
- **Rationale:** k = 0 isolates the selection formula from the game model; the earlier points show how the odds converge.
- **Promotes to ADR:** yes

### [26] A dedicated synthetic sim league

- **Decision:** `tests/synthetic/sim_league.py` generates 2013–2026 with four 10-team power conferences, G6 conferences, the Pac-12's shrink, independents including "Notre Dame", FCS opponents, championship games, polls, committee rankings and 12-team fields for 2024/2025. It reuses Phase 3's machinery (flattener, slice keys, load log, replay). Its committee and field selection are its own plain-Python implementation (an independent oracle). A `scale="real"` option serves the performance smoke.
- **Alternatives:** Phase 3's league (no conference structure for 12-team selection); Phase 3's scaled league (performance shapes only); real data in tests (forbidden).
- **Rationale:** every scenario needs exact, controllable facts. Real conference names and "Notre Dame" are reference keys, like Phase 3's seeds.
- **Promotes to ADR:** no

### [27] Run data survives deploys; schema changes are explicit

- **Decision:** `CFB_SIM` data tables are created if missing and never replaced. A column difference stops the deploy, and per-run outcome tables are excluded from the orphan drop (extensions to `vs/host/sqlobjects.py`).
- **Alternatives:** `CREATE OR REPLACE` like reference tables (loses runs); silent `ALTER TABLE` migrations.
- **Rationale:** simulations are data; a schema change should be a deliberate migration with its own task.
- **Promotes to ADR:** no

### [28] Scope trims

- **Decision:** not in this plan:
  - per-sim margins and M0 (D20);
  - current-season SP+ or lines as features;
  - ML frameworks or custom SLCs;
  - transitional-FBS ineligibility (Q13);
  - 14/16/24-team formats;
  - the conditional rooting guide (D20's unmet item, Phase 5);
  - the benchmark harness (Phase 8).
- **Alternatives:** —
- **Rationale:** YAGNI; each item has a named owner phase or a verify step.
- **Promotes to ADR:** no

## Open Questions (assumption + verify step)

| # | Assumption | Verify step | If wrong |
|---|---|---|---|
| Q1 | The 2026 G6 automatic bid goes to the best-ranked G6 team, champion or not | Check the official CFP format/selection protocol on cfp.org before the first 2026 committee ranking (early November 2026); record the source in `SELECTION_RULES.SOURCE_NOTE` | Set `G6_BID` to `CHAMPION` in `011_selection_rules.sql` (one value) and redeploy; no engine change |
| Q2 | The Notre Dame guarantee means "selected if ranked 12 or better in the final ranking", counted as an automatic bid | Same source as Q1 | Adjust the guarantee semantics in `engine/core.py` (new engine version) and the selection-rules scenario |
| Q3 | CFBD carries pregame Elo for FCS teams in their games against FBS teams, so most non-FBS opponents are rated by Elo | `SELECT CLASSIFICATION, COUNT(*), COUNT(ELO_AS_OF) FROM CFB_MODEL.TEAM_SEASON_AS_OF WHERE AS_OF_SEASON = 2024 AND AS_OF_WEEK_SEQ = 9 GROUP BY 1` on AWS (6.2) | `CLASS_DEFAULT` covers them; record the shares and check the `NON_FBS_RATING` fit size |
| Q4 | The 2026 Pac-12 holds a championship game, which the Phase 3 seed `CONFERENCE_CHAMPIONSHIP` (Pac-12 2011–2023) does not list. Under 2026 rules it affects only `P_CONF_CHAMPION`, not selection | Look for a week-15 Pac-12 conference game in the stored 2026 schedule after the regular season, or the conference's announcement | Add a Pac-12 row from 2026 to Phase 3's seed (owned by `model/games`); no change here |
| Q5 | Deriving layout C from the outcome string is cheaper than emitting C | Probe 1.1c (100k local), 6.1 (1M AWS) | Store layout A per D20's fallback ([16]) |
| Q6 | The fitted resume formula selects at least 10 of the 12 actual teams in 2024 and 2025 given all results | Task 6.3 (`SELECTED_OVERLAP` at k = 0) | Review the resume features ([11]), e.g. a quality-wins term; a follow-up decision, not a change inside 6.3 |
| Q7 | The SP+-and-Elo model meets the out-of-sample targets against the line | Task 6.2 report | Feature set F2 (season-to-date point differential), task 6.2b |
| Q8 | The previous season's SP+ offense and defense ratings predict totals better than the mean total | Task 6.2 totals MAE vs baseline | `TOTAL_SLOPE` 0 (constant totals), logged |
| Q9 | The builtin SLC still ships Python 3.12.3 and numpy 1.26.4 on local 2026.2 and AWS 2026.1.1 | Probe 1.1f on both targets | Re-run the stream comparison against the SLC before any run; the version is in the reproducibility key either way |
| Q10 | No conference pair meets twice in the regular season of 2024–2026 | `SIM_INPUT_CHECKS` at every backtest and demo point (6.3, 6.4) | Keep the later meeting for head-to-head in the input view instead of failing (`CONFERENCE_PAIR_REPEATED` → WARNING), with a new scenario |
| Q11 | Partitioning `SIM_TEAM` by run keeps S4's what-if latency with several runs stored | Probe 1.1e, task 6.4 | No partitioning, and the `DISTRIBUTE BY SIM_ID` retest ([17]) |
| Q12 | Exasol accepts at least 2,002 columns per table; a preseason point has fewer than 2,000 remaining FBS games | Probe 1.1b; `N_GAMES` of a preseason run | Layout A for runs above the limit |
| Q13 | Teams new to FBS in 2026 are playoff-eligible | NCAA reclassification rules for the 2026 newcomers | A small reviewed `INELIGIBLE` seed (school, seasons) read by `ENGINE_TEAM_AS_OF` |

## Review Findings

<!-- Populated by speq-implement after code review. -->
