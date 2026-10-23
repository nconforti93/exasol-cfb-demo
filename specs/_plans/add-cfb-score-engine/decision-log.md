# Decision Log: add-cfb-score-engine

Date: 2026-10-22 (planned; revised the same day to resolve the independent review in `review.md`); replanned light and demo-first on 2026-10-23 ([44]–[57])

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

### Revision interview (2026-10-22): resolving the review

The owner was away and delegated these decisions to the orchestrator, who passed them to the planner with the instruction to log each one. The answers are the orchestrator's.

**Q (review M1, horizon noise):** Shared team-strength noise or not?
**A:** Accept.
- Add a per-team `(S, T)` normal block to the season stream, scaled by TAU·√(h − 1)/√2, and set per-game noise to `SIGMA_1`.
- Keep single-game probabilities, `MARGIN_WIN_PROB` and the 0.8289 check unchanged.
- Add a scenario showing win-total variance widens, plus a backtest calibration check with real power (for example, the Brier score of P(in CFP) over all FBS teams × points rather than the 0.05-tail trigger).

**Q (review M2, calibration buckets):** How should the bucket gate tolerate noise?
**A:** Accept. Use bucket tolerance max(0.05, 2·SE) plus an aggregate gate.

**Q (review M3, drift within the season):** One pooled fit and gate?
**A:** Accept.
- Report and gate by week phase (early/mid/late).
- Try per-phase coefficients before the F2 fallback.
- Fit `ELO_CENTER` or justify fixing it.

**Q (review M4, performance budget and the C/A gate):** What changes?
**A:** Accept.
- Complete the performance budget with the missing items: input snapshot, the second `SIM_TEAM` write, `TRUNCATE` of the stage, per-run DDL and commits, and the wider stage row.
- Move the decisive 1M layout-C-vs-A measurement into group A as a synthetic-data probe on AWS. AWS is now free for this work: deploy and run only, never stop, start or resize it, and make zero CFBD calls.
- Decide C vs A once for both targets, with gates consistent with the 30 s local / 60 s AWS targets.

**Q (review M5, killed runs):** Who cleans up?
**A:** Accept.
- Clean up abandoned `SIMULATE` runs (`RUNNING` rows, stage rows, `SIM_PATH_R<n>` tables) when the next lock is taken.
- Define the stale rule for kind `SIMULATE` and for host-driven `make sim`, consistent with Phase 2's lock rule (Phase 2 probe findings in `../exasol-cfb-demo-p2/docs/spikes/s5-loader-probe.md`: `EXA_ALL_SESSIONS`, IDLE/gone, R0003 after kill).
- Add the scenarios.

**Q (review M6, selection weekend):** Which week is the selection point?
**A:** Accept.
- Define a selection week per season, taken from the week of the last conference championship game, and verify it on the stored 2024/2025 data at zero calls.
- Recompute the backtest's k offsets from it.
- Note: S1 found that `/games` row order is unstable between requests and has to be keyed on `ID`, which Phase 3 already does.

**Q (review minors):** Which to fix?
**A:** Fix all of these:
- name the committee-rankings input (Phase 3 `RANKING_AS_OF` / `POLL`);
- map the 2014–2023 tiers using Phase 3's `CONFERENCE_TIER` (AQ/P5/G5/…);
- add an explicit Phase 2 hand-off task for the `SIMULATE` lock kind. The Phase 2 Lua `loadlib.lua` is being written in its worktree under task 4.2; name it as a cross-plan prerequisite with an owner;
- set the local default to 5k sims per batch so every core works;
- add a forced re-sim from a base run's snapshot with fixed results, or downgrade the "no engine change for Phase 5" claim;
- fix the batch-id SQL syntax;
- make the partition probe store 20–40 runs;
- stabilize the resume-fit IT;
- drop `TOTAL_SIGMA` or use it;
- add a scenario pinning the Pac-12 as Group of Six in 2026.

**Q (review Q1/Q2, G6 bid and Notre Dame):** Change the reading?
**A:** Keep the current reading, and keep the cfp.org verification step before early November.

**Q (constraints for the revision):** What limits apply?
**A:**
- No DB sessions during planning: the M4 probe is an implementation task, not something to run now.
- No CFBD calls.
- No edits outside the plan dir.
- `speq plan validate add-cfb-score-engine` must be warning-free.
- Append a "Review resolution" section to `review.md`.
- Invoke `exasol:exasol-udfs`, `exasol:exasol-database` and `exasol:exasol-distributed-ml` first.

### Light replan interview (2026-10-23)

The owner changed the process; the orchestrator relayed the brief and the planner logs it here, close to verbatim.

**Q (process):** What changes?
**A:** The owner: "this is a demo, not a full-blown product offering... we don't need a crazy amount of tests." The plan was written for the heavy process. Restructure it into thin, demo-first slices, sized for 3–5 implementer tasks before the first usable result. Planning only: no DB use, no CFBD calls, no code.

**Q (demo needs):** What must Phase 4 deliver for the demo?
**A:**
- Playoff odds per FBS team as of a week: P(make the 12-team CFP), P(first-round bye), P(champion), expected wins, mean seed, and movement since the last run.
- What-if. The owner asked: "if Auburn wins this weekend, how does that change their playoff %?" It works by filtering stored per-sim outcomes. Spike S4 chose layout C (one BOOLEAN per game), sparse `SIM_TEAM` and a per-run `LEVERAGE` table; what-if p95 was 0.52 s at 1M sims on AWS.
- "Games to watch" / rooting guide: leverage per game for a team.
- Weekly predictions: win probability and predicted score per game, frozen before kickoff, then graded; the calibration chart lives on the Predictions page.
- Upsets: Phase 5 may own these; check.

**Q (inputs):** What to read and assume?
**A:** The plan, `review.md`, `decision-log.md`; the S3 and S4 write-ups in the main tree (read-only, another session's; do not copy them into git); the main tree's `HANDOVER.md`; Phase 6's "UI revision" and `app/frontend/src/api/types.ts`. S3: the Python UDF engine is a GO; 100k compute-only about 7 s locally and 1.5 s on AWS; full mode about 12 s on AWS, 75 % of it emitting `SIM_TEAM`; 1k–10k per batch, at least 2 batches per slot, explicit `EMITS`. Phase 3's `CFB_MODEL` views are live on AWS and local; assume `TEAM_SEASON_AS_OF` and `GAME_AS_OF` per the as-of spec; 2026 data runs through regular week 7; `CFB_CORE.METRICS_WP_PREGAME` holds CFBD's pregame win probability.

**Q (slices):** What goes where?
**A:**
- Slice 1: one engine run for 2026 after week 7 on AWS, producing odds and what-if storage, with the fewest tasks. Rating model: SP+-based margin, home field, normal error; the simplest credible approach, parameters from the existing decisions where sound; calibration can be a simple fitted constant. CFP rule: "12 teams, 5 highest-ranked conference champions plus 7 at-large, with the top 4 champions getting byes (the 2025+ rule). Check the plan's decisions; keep it simple."
- Slice 2: predictions with freeze and grade, the rooting guide and leverage, movement between runs.
- Slice 3: hardening (locks, the refresh hook, calibration report) only as far as the demo needs.
- Per task: files, a couple of tests, the definition of done. Mark superseded heavy items `[-]` with a one-line reason. Hand-offs to Phase 5 (`CFB_API`) and Phase 6 (9.8 outlook) with exact table and column names. Log the light decisions.

**Q (BucketFS):** Is it needed?
**A:** The owner asked in passing; not a requirement. If a small parameter or calibration file in BucketFS fits cheaply, list it as an optional task with its cost; never a dependency.

**Q (rules for this pass):**
**A:** Run `speq plan validate add-cfb-score-engine` and fix errors. The orchestrator authorizes the commit (overriding the git-discipline skill) with the message "add-cfb-score-engine: light demo-first replan"; do not push.

**Q (coordinator update during planning, 2026-10-23):** What do the as-of views look like as built?
**A:**
- Phase 3's 3.9–3.11 landed (475c534) and are merged into this branch.
- `AS_OF_POINT`/`CURRENT_AS_OF`, `GAME_AS_OF` and `TEAM_SEASON_AS_OF` have the columns listed in plan.md § Context; the current point is 2026, after week 7. Title games appear from the point before their week; selection games at `LAST_REGULAR_WEEK_SEQ`.
- At 2026 week 7 `SP_CURRENT_RATING` is NULL (fetched after the cutoff): use `SP_PRIOR_RATING` plus Elo, or decide a rule. Read `RATING_SP` of season − 1 directly.
- Filter the as-of views with literal or bound `AS_OF_SEASON` and `AS_OF_WEEK_SEQ`; a join to `CURRENT_AS_OF` is not pushed down and evaluates every point (about 21 s); the old plan's manual-test query did that join, fix it.
- AWS per point: `GAME_AS_OF` 1.75 s, `TEAM_SEASON_AS_OF` 3.2 s, `RANKING_AS_OF` 0.64 s; fine for one engine-input read per run. Phase 3's 4.3 may materialize `GAME`.
- About 87–92 % of completed FBS games have Elo; the 2025 committee ranking shows only from point 16.

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
- **Amended (review, 2026-10-22):** `ELO_CENTER` is fitted ([33]); `B_SP` and `B_ELO` may be fitted per week phase as the first fallback ([32]).
- **Promotes to ADR:** yes

### [3] Uncertainty grows with the horizon; no per-simulation team strength offsets

- **Decision:** `SIGMA` = √(`SIGMA_1`² + `TAU`² × (weeks ahead − 1)), fitted on residual variances per horizon. Ratings are static inside a simulation. The contract's "rating-update flag" is not implemented.
- **Alternatives:**
  - one σ for every horizon: overconfident for games weeks away;
  - per-sim team strength offsets (correlated outcomes, "hot" sims): one more random block and parameter, not measurable on per-game metrics.
- **Rationale:** two parameters, measured directly by the per-horizon report.
- **Reversal trigger:** the backtest shows overconfident playoff odds, i.e. actual field teams with `P_IN_CFP` below 0.05 at k = 3 in either season (`MISSES` > 1). Then add per-sim offsets as a new engine version.
- **Superseded in part (review M1, 2026-10-22):** the horizon σ stays, but its growth is now shared per team as per-sim strength offsets ([29]), and the reversal trigger is replaced by the pooled calibration check ([30]). The rating-update flag stays unimplemented.
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
- **Amended (review, 2026-10-22):** the parameters seed holds three week-phase rows per version ([32]).
- **Promotes to ADR:** yes

### [7] Leak-free calibration with the closing line as a yardstick

- **Decision:**
  - Features come only from the as-of views and the previous season's SP+; labels come only from completed games.
  - The closing line (`GAME.PREGAME_SPREAD`, `HOME_PREGAME_WIN_PROB`) is compared with, never used.
  - The gate is out-of-sample (`HOLDOUT_2023` on 2024–2025, horizon 1, FBS-vs-FBS): MAE ≤ line + 1.5 and ≤ 15.0; Brier ≤ line + 0.020 and ≤ 0.190; |bias| ≤ 1.0; calibration buckets within 0.05; totals beat the mean.
- **Alternatives:** absolute targets only (no grounding); in-sample metrics (optimistic); the line as a feature (it knows more than the model at the point and is not available weeks ahead).
- **Rationale:** relative targets stay meaningful whatever the data's noise level, and the absolute caps catch a broken line.
- **Amended (review M2/M3, 2026-10-22):** the targets apply per week phase; the bias tolerance becomes max(1.0, 3 SE), and the bucket tolerance becomes max(0.05, 3·SE) plus a Spiegelhalter Z per phase ([31], [32]).
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
- **Amended (review M6/m5, 2026-10-22):** the inputs are taken at each season's selection point ([37]); the target is the final committee ranking from `CFB_MODEL.RANKING` ([40]).
- **Promotes to ADR:** yes

### [12] Simplified conference rules

- **Decision:** S3's standings order is kept: win %, two-way head-to-head, conference point differential, rating, index.
  - A conference needs at least 6 FBS teams to have a champion (2024–2025 Pac-12: none).
  - Conferences with a championship game in `CFB_MODEL.CONFERENCE_CHAMPIONSHIP` play it: the known pairing (and result) when the game is visible, else the top two, simulated on a neutral site.
  - Other conferences crown their standings leader.
- **Alternatives:** full tiebreaker rules per conference (out of scope per the mission); always simulating championships (wrong in the week before them and in the backtest).
- **Rationale:** the "Sunday before championship week" is a demo moment and a backtest point; using the real pairing there costs one input role.
- **Amended (review m3, 2026-10-22):** a known but unplayed championship game uses its own `MU`, `SIGMA` and `TEAM_SIGMA`; only simulated pairings use the postseason parameters ([41]).
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
- **Amended (review m8, 2026-10-22):** the claim needed an entry point that runs from a base run's snapshot. `SIMULATE_FROM` provides it ([38]).
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
- **Amended (review M4, 2026-10-22):** the gates and the timing of the decision change ([34]). Per-run table names force generated SQL in Phase 5's `CFB_API`, which reads `SIM_RUN.PATH_TABLE` and `SIM_RUN_GAME.PATH_COLUMN` (review m11).
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
- **Amended (review M5/m6, 2026-10-22):** two kinds with different stale rules, cleanup of killed runs by the next lock holder, and a named Phase 2 owner (hand-off H1) ([36]).
- **Promotes to ADR:** yes

### [20] Refresh step 300, configured per target, with a deterministic seed

- **Decision:**
  - `REFRESH_SIMULATE` is registered as step 300 and uses `SIM_CONFIG` from `engine/sim-config.yaml` (`local` 100k / 10k, `aws` 1M / 10k).
  - The seed is season × 1,000 + week sequence, with `CURRENT` parameters.
  - It is skipped (`DONE`, "skipped: playoff field set") after the last regular week.
- **Alternatives:** step 200 (Phase 2's examples already use 200 for `PREDICT`, Phase 5); random seeds; one global default.
- **Rationale:** "model → predictions → sims" in the timeline, weekly runs reproducible by default, and the scale dial set per deployment rather than remembered by the presenter.
- **Amended (review, 2026-10-22):** the skip applies after the selection week ([37]); the local default is 5,000 per batch ([39]).
- **Promotes to ADR:** yes

### [21] No runs after the regular season or before 2024

- **Decision:** points after `LAST_REGULAR_WEEK_SEQ` and seasons without a rule set are refused.
- **Alternatives:** a bracket-only mode with the actual field (needs seeds 1–4 that CFBD shows only in quarterfinal games); 4-team rules.
- **Rationale:** the demo is in-season, the backtest's last point is the selection weekend, and Phase 3 has one postseason week (its Q3). Backlog.
- **Amended (review M6, 2026-10-22):** the cutoff is the selection week, not `LAST_REGULAR_WEEK_SEQ` ([37]).
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
- **Amended (review m7, 2026-10-22):** the local default becomes 5,000 per batch ([39]).
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
- **Amended (review M1/M6, 2026-10-22):** the points are `SELECTION_WEEK_SEQ − k` ([37]), and a pooled calibration check over every team is reported ([30]).
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

### [29] Each simulated season gives each team a strength offset shared by all its games (review M1)

- **Decision:**
  - The season stream draws one more fixed-shape block after `z` and `u`: `standard_normal((S, T))`, one offset d_t per team and season.
  - A remaining game at horizon h has margin `rint(MU + TEAM_SIGMA × (d_home − d_away) + game_sigma × z)`, with `TEAM_SIGMA` = `TAU`·√(h − 1)/√2 (computed in `ENGINE_GAME_AS_OF`, `DECIMAL(5,2)`) and `game_sigma` = `SIGMA_1`.
  - Simulated championship and bracket games use `post_team_sigma` (the scale at the selection week's horizon) in place of the old `post_sigma`.
  - A known unplayed championship game uses its own row ([41]). A fixed result ignores the offsets.
  - The engine checks that √(`game_sigma`² + 2·`TEAM_SIGMA`²) matches `SIGMA` within 0.02.
- **Alternatives:**
  - independent per-game noise (the old [3]): every marginal right, but win totals and playoff odds too narrow;
  - a third child stream for the offsets: independent of G, but the orchestrator decided the season stream, and a what-if never changes G;
  - a random walk per team (offsets growing week by week): closer to how ratings go stale, but more machinery for a difference the backtest cannot measure on two seasons.
- **Rationale:**
  - The marginal variance stays `SIGMA_1`² + `TAU`²(h − 1), so `HOME_WIN_PROB`, `MARGIN_WIN_PROB` and the 0.8289 outcome-share scenario are unchanged.
  - A team's games are correlated, as staleness of its rating implies.
  - Checked by simulation for the new engine scenario: 8 home games at `MU` 0 with `TEAM_SIGMA` 7.07 and `game_sigma` 15 give a win-total variance of 3.37–3.42 over five seeds, against 1.99–2.01 for independent noise of the same `SIGMA` 18.03; every game stays at 0.49–0.51.
  - With `TAU` 0 (the uncalibrated defaults) nothing changes.
- **Promotes to ADR:** yes

### [30] The backtest checks calibration over every team and point (review M1)

- **Decision:** `CFB_SIM.BACKTEST_CALIBRATION` pools the backtest runs at k = 5, 3 and 1 over every eligible team (a team-point each):
  - `BRIER_IN_CFP` and `CFP_CALIBRATION_Z` (Spiegelhalter's Z) of `P_IN_CFP`;
  - `WIN_DISPERSION`, the mean of (y − m)²/v of each FBS team's actual regular-season wins up to the selection week against its `TEAM_WINS` distribution, with a standard error clustered by team and season.

  `make backtest` prints a calibration review when |Z| > 3 or the dispersion is more than 3 SE from 1. Task 6.3 logs a triggered review as a decision on [29]. It is not gated, like the other probability metrics ([25]).
- **Alternatives:** the old trigger (field teams with `P_IN_CFP` < 0.05 at k = 3, `MISSES` > 1): about 24 field teams over two seasons, very little power; a hard gate: two correlated seasons are too few to fail a build on.
- **Rationale:** every FBS team at every point carries information, and the win-total dispersion measures exactly what [29] changes. A host-side pure function recomputes the statistics as the integration test's oracle.
- **Promotes to ADR:** no

### [31] Calibration tolerances are at least three standard errors wide (review M2)

- **Decision:** over the pooled horizon-1 gate population, every tenth with ≥ 100 games must lie within max(0.05, 3·√(p̄(1 − p̄)/n)) of its observed rate. In each week phase, the aggregate Spiegelhalter Z must satisfy |Z| ≤ 3.0. `SCORE_CALIBRATION_BUCKETS` shows each bucket's tolerance.
- **Alternatives:**
  - the orchestrator's max(0.05, 2·SE) per bucket;
  - ECE ≤ 0.03: its noise on about 1,300 games is close to the bar;
  - Hosmer–Lemeshow: equivalent here, and harder to explain.
- **Rationale (deviation from the orchestrator's 2·SE, kept within its structure):** a seeded simulation of gate populations (300/450/550 horizon-1 games per phase, realistic predictions) measured the false-fail rates of a perfectly calibrated model:
  - 2·SE buckets: 28.5 % of 2,000 samples (about 8 qualifying buckets), so the gate would still fail a good model about one time in three and recreate M2;
  - 3·SE: 2.4 %;
  - the full family of [31] and [32]: 4.3 % (3,000 samples);
  - a model with σ about 20 % too small fails the per-phase Z alone in 69 % of 500 samples.

  The unit test `test_gate_tolerates_noise_and_catches_overconfidence` pins both properties (pass ≥ 94 % of 1,000; overconfident fail ≥ 50 % of 1,000). The bar is below the review's 95 %: the expected pass rate is 95.7 %, and 94 % leaves room for the sampling error of 1,000 seeds.
- **Promotes to ADR:** yes

### [32] Report and gate by week phase; phased coefficients are the first fallback (review M3)

- **Decision:**
  - Week phases of the point: `EARLY` 0–4, `MID` 5–9, `LATE` 10+.
  - `SCORE_MODEL_PARAMS` has one row per version and phase. The rows are equal under `SP_ELO`; `SP_ELO_PHASED` fits `B_SP` and `B_ELO` per phase (3 × 2 coefficients), and every other column stays shared.
  - `SCORE_MODEL_REPORT` and `SCORE_CALIBRATION_BUCKETS` gain the phase (with `ALL`).
  - The relative MAE/Brier targets, the bias and Z apply per phase.
  - The bias tolerance becomes max(1.0, 3 SE).
  - The fallback order on a miss in 6.2 is `SP_ELO` → `SP_ELO_PHASED` → F2 (6.2b).
- **Alternatives:** a pooled gate (can pass while late-season predictions are worse); per-phase models with all parameters free (more to fit and explain); a fixed |bias| ≤ 1.0 per phase.
- **Rationale:**
  - The prior dominates in September and in-season Elo in November, and the demo lives at weeks 8–14.
  - A fixed ±1.0 bias gated per phase fails a perfect model in 47.5 % of simulated samples (SE ≈ 0.7–0.9 points at 300–550 games), which is why the tolerance scales with its SE.
  - The `EARLY` phase is the likeliest miss against the line (Q16); the fallback chain and the log handle it.
- **Promotes to ADR:** yes

### [33] `ELO_CENTER` is fitted as the mean FBS Elo; bias is reported by rating-source pair (review M3, m4)

- **Decision:** `make calibrate` sets `ELO_CENTER` to the rounded mean `ELO_AS_OF` of the FBS teams over the version's training points, before the margin fit. `CFB_SIM.SCORE_SOURCE_REPORT` reports bias and MAE per (home, away) rating-source pair over all horizon-1 rows, FCS opponents included; it is reported, not gated.
- **Alternatives:** keep 1500 fixed (CFBD Elo's nominal mean, but the FBS mean is not 1500 because lower divisions share the scale); fit the center by least squares on mixed-source pairs (few rows, unstable).
- **Rationale:**
  - The SP+ prior is centered on the average FBS team, and `RATING` is "points against an average FBS team".
  - Centering Elo on the FBS mean makes an Elo-only team (such as a new FBS member) and an SP-only team comparable with `SP_ELO` teams.
  - For same-source pairs the center cancels.
  - The source-pair report shows whether the mixed pairs are fair.
- **Promotes to ADR:** no

### [34] The performance budget is complete; C or A is decided once, early, on both targets (review M4)

- **Decision:**
  - The budget carries every step:
    - the input snapshot;
    - checks;
    - DDL and the 14 step commits;
    - compute;
    - playoff rows and the wider stage row;
    - outcome strings;
    - the second `SIM_TEAM` write;
    - `LEVERAGE`;
    - the stage `TRUNCATE`.
  - Without the C derivation it sums to about 22 s locally (100k) and 38 s on AWS (1M).
  - The gates are what remains of the targets minus a reserve: C if the median derivation at 400 games is ≤ 5 s locally and ≤ 10 s on AWS. Otherwise layout A on both targets.
  - Probe 1.1 measures this in group A, on synthetic data, on both targets. AWS is free for this work: deploy and run only.
  - Targets are judged on the median of three runs, with min and max reported, at the demo shape. A preseason run is recorded, not gated.
- **Alternatives:**
  - the old gates (≤ 10 s local, or ≤ C's direct emission of 30.6 s on AWS): together with the known rows they reach or pass both targets;
  - measuring at 1M in group J: after Phase 5 may depend on C;
  - deciding per target: it would split Phase 5's what-if SQL.
- **Rationale:** the review's re-trace of the budget, plus one correction found in the revision. The old local budget counted the 3.2 s fixed cost next to the 7.06 s compute figure, but S3 measures it inside that figure.
- **Promotes to ADR:** yes

### [35] The stage is truncated, and `ENGINE_TEAM_AS_OF` is evaluated once per run (review M4)

- **Decision:**
  - `FINISH`, the failure path and the abandoned-run cleanup clear `SIM_STAGE` with `TRUNCATE TABLE`.
  - The `INPUTS` step writes `SIM_RUN_TEAM` first. Games and parameters come from views that carry team ids, and the snapshot resolves the indices, the same-conference flag and the guarantee index through `SIM_RUN_TEAM`.
- **Alternatives:** `DELETE … WHERE RUN_ID` (about 13M rows at 1M); views that join `ENGINE_TEAM_AS_OF` again (three evaluations of a stacked as-of view per run).
- **Rationale:**
  - The run lock makes a run the stage's only writer, so truncating it is safe.
  - The grammar lists `TRUNCATE` under DML. Probe 1.1i checks that it rolls back inside the run's transaction.
  - At Phase 3's < 1 s per as-of view, the snapshot stays at about 2–3 s.
- **Promotes to ADR:** no

### [36] Two lock kinds; the next lock holder cleans up killed runs (review M5, m6)

- **Decision:**
  - `SIMULATE`, `SIMULATE_FROM` and `DROP_SIM_RUN` lock as kind `SIMULATE`, stale under Phase 2's script rule: session gone, own session, or idle.
  - `make sim` and `make calibrate` lock from the host as kind `DEPLOY`, stale only when the session is gone or is the caller's own (the snapshot rule).
  - Right after taking the lock, and at the start of the refresh step, the script marks every other `RUNNING` `SIM_RUN` as `ABANDONED`. It sets the running steps to `ABANDONED`, deletes the run's output rows, drops its outcome table and truncates the stage. Registry, steps, batches and inputs stay.
  - Hand-off H1 asks Phase 2's single owner of `loadlib.lua` (task 4.2; the host lock rows in 5.2) for both kinds and callable lock functions. The orchestrator relays it (task 1.10); this plan never edits Phase 2 files.
  - Phase 3's model deploy should take `DEPLOY` too.
- **Alternatives:**
  - cleanup only in the failure path: cannot run after a kill, because every later statement fails with `R0003` (Phase 2 probe 1.1j);
  - a periodic sweeper;
  - one kind for both (the idle rule would steal a live host deploy's lock).
- **Rationale:** only the lock holder runs, so any other `RUNNING` run is dead and safe to clean. A host session is idle between statements, so the idle rule must not apply to it. Consumers read only `DONE` runs and never see the gap.
- **Promotes to ADR:** yes

### [37] The selection week comes from the power championship games (review M6)

- **Decision:**
  - `CFB_SIM.SELECTION_WEEK` gives each season the week of its last championship game of a power conference (source `CCG`).
  - While none is stored, it is one more than the last regular week that ends before the first Saturday of December, UTC (source `DECEMBER`, with the `SELECTION_WEEK_ESTIMATED` warning).
  - Points after it are refused, and the refresh step skips them.
  - Regular-season games and flagged championship games after it are left out of the engine (`GAMES_AFTER_SELECTION`, `CCG_AFTER_SELECTION`). Wins, SOS and the resume count games up to it.
  - `post_team_sigma` uses its horizon.
  - The backtest's points are `SELECTION_WEEK_SEQ − k`, and the resume fit's inputs are taken at it.
- **Alternatives:**
  - `LAST_REGULAR_WEEK_SEQ` (the Army–Navy week on real data);
  - the orchestrator's "last conference championship game" of any conference;
  - a seeded week per season (manual upkeep);
  - simulating post-selection games but leaving them out of the resume.
- **Evidence (zero calls, stored replay recordings, keyed on game `ID`):**
  - 2024: regular week 15 holds the nine championship games (6–7 December), week 16 only Army–Navy (14 December, flagged non-conference), and the committee's `isFinal` ranking is labeled week 16, so `LAST_REGULAR_WEEK_SEQ` is 16 and the selection week 15.
  - 2026: weeks 1–13, then Army–Navy alone in week 15 (12 December), flagged as an American Athletic conference game, and no week-14 rows yet; first Saturdays of December 2024–2026: 7, 6 and 5 December.
  - 2025 is not on disk and is checked in 6.1 (Q14).
- **Rationale (deviation in the detail, not the intent):**
  - Phase 3's detection would take the 2026 Army–Navy game for the American Athletic's championship game (X1, Q15). "Last championship game of any conference" would then give week 15, the Army–Navy week again.
  - The power conferences play no regular game after their title games, so their week is robust.
  - The December rule covers the current season before its pairings are set.
  - The week describes the schedule, not results, so it is leak-free like `LAST_REGULAR_WEEK_SEQ`.
- **Promotes to ADR:** yes

### [38] `SIMULATE_FROM` re-simulates a stored run with forced results (review m8)

- **Decision:**
  - `CFB_INFRA.SIMULATE_FROM(base_run_id, fixed_results, run_label)` copies a `DONE` or `DROPPED` base run's snapshot with `FIXED_HOME_WIN` set on the named remaining games.
  - It runs steps 2–7 with the base run's seed, batch size and number of seasons, under kind `SIMULATE`, with `RUN_ORIGIN` `FORCED` and `BASE_RUN_ID`.
  - `make simulate BASE= FIX=` wraps it.
- **Alternatives:** downgrade [14]'s claim and leave the entry point to Phase 5 (a Lua change then).
- **Rationale:** the snapshot and the fixed-shape draws already make it cheap. Its scenario proves common random numbers end to end: every other game's outcome column equals the base run's, season by season. Phase 5 needs no engine change.
- **Promotes to ADR:** yes

### [39] The laptop default is 5,000 per batch (review m7)

- **Decision:** `engine/sim-config.yaml` `local`: 100,000 / 5,000 (20 batches, 2.5 per slot on an 8-core start). AWS stays 1,000,000 / 10,000.
- **Alternatives:** 10,000 (D19's measured laptop pick); sizing from the measured `nrOfCores` at run time (the defaults would no longer be a reviewed value).
- **Rationale:** D19 measured 5,000 and 10,000 as equal (7.11 s vs 7.06 s compute-only). 10 batches on 8 slots leave 6 of 8 slots idle in the second wave, which undercuts "in parallel on every core" on stage.
- **Promotes to ADR:** no

### [40] The resume fit reads the final committee ranking as a label, and maps the pre-2024 tiers (review m5)

- **Decision:**
  - The target ranking is `CFB_MODEL.RANKING` with `POLL_CODE` `CFP` (reference table `CFB_MODEL.POLL`) and `IS_FINAL_POLL` TRUE, ranks 1–25.
  - `ENGINE_TEAM_AS_OF` maps `AQ`/`P5` → `P4` and `NON_AQ`/`G5` → `G6` for seasons before 2024.
  - The tier never enters the resume.
- **Alternatives:** `RANKING_AS_OF` at the selection point; leaving pre-2024 tiers unmapped.
- **Rationale:**
  - A final ranking is a label, like a final score.
  - On real 2024 data the selection ranking is labeled regular week 16, so Phase 3's label rule makes it visible only after the selection point (X2): `RANKING_AS_OF` at that point would return the previous Tuesday's ranking.
  - The mapping keeps the engine input valid for 2014–2023 without changing its codes.
- **Promotes to ADR:** no

### [41] Engine contract precision fixes (review m3)

- **Decision:**
  - C counts every distinct non-NULL conference of the `TEAM` rows, small and no-CCG conferences included.
  - The inverse normal CDF's argument is clamped to [1e−12, 1 − 1e−12].
  - A known unplayed championship game uses its own `MU`, `SIGMA` and `TEAM_SIGMA`; simulated pairings use the rating difference and `post_team_sigma`.
  - An empty outcome string (G = 0) is stored as NULL.
- **Alternatives:** C over conferences with a champion; no clamp (`random()` can return 0.0 and `inv_cdf(0)` raises); `post_sigma` for every championship game.
- **Rationale:** a conference crossing the size threshold must not shift the bracket columns. The row's own prediction is the score model's answer for that game. Exasol stores `''` as NULL, and k = 0 has no remaining game.
- **Promotes to ADR:** no

### [42] `TOTAL_SIGMA` is dropped; the params-version filter is checked (review m9)

- **Decision:** remove `TOTAL_SIGMA` from `SCORE_MODEL_PARAMS` and the fit. Probe 1.1h checks that a `PARAMS_VERSION` filter on the views prunes the other version.
- **Alternatives:** keep it for Phase 5's score ranges; a separate holdout view.
- **Rationale:** nothing reads it (YAGNI); Phase 5 can add it with its consumer. Consumers already filter the version, so a second view is unnecessary if the filter prunes.
- **Promotes to ADR:** no

### [43] The resume-fit integration test fits over the generator's own ratings (review m13)

- **Decision:** `make calibrate ARGS="--resume-only"` fits only the resume weights over the deployed score parameters. The integration test deploys the test parameters (which the generator's committee used) and asserts an overlap of 12 in every season and a mean Spearman of 1.0.
- **Alternatives:** keep the full calibration and assert ≥ 10 per season (ratings from the just-fitted parameters differ from the generator's, so the overlap may flake); a tolerance derived from the generator.
- **Rationale:** with the generator's ratings and no resume gap below 0.5 in the top 30, the oracle is exact. The full calibration path keeps its own scenarios.
- **Promotes to ADR:** no

**Light replan decisions (2026-10-23).** These decisions follow the light-replan interview above. Where they conflict with [1]–[43], they win; the table after [57] gives the status of every earlier decision.

### [44] A light, demo-first plan in three slices

- **Decision:** Phase 4 is rebuilt as three thin slices. Slice 1 (5 tasks) ends with odds and what-if storage for 2026 after week 7 on AWS. Slice 2 adds predictions, the rooting guide and movement. Slice 3 adds the refresh steps, a calibration report, the cfp.org check and docs. 7 features with 30 scenarios replace 9 features with 81. `tasks.md` marks every heavy task as absorbed, kept or superseded with a reason; the reviewed heavy plan stays in git (29fb149) as the reference for anything picked up later.
- **Alternatives:** implement the reviewed heavy plan; keep its scope and cut only tests.
- **Rationale:** the owner's process change, the demo window (16–20 November) and the rule that each slice ends in something the owner can look at. Not an ADR: the light process is the owner's project-wide rule; this entry applies it to Phase 4.
- **Promotes to ADR:** no

### [45] Score model: prior SP+ blended with current Elo, one host fit, no gates

- **Decision:** a team's rating is `B_SP` × `SP_PRIOR_RATING` + `B_ELO` × (`ELO_AS_OF` − `ELO_CENTER`), with fallbacks to one feature and to class defaults (`sim-engine/score-model`); μ adds `HFA` off neutral sites; margins are normal. One fit, `make sim-fit` (host numpy, read-only), estimates the constants by least squares on the completed regular-season games of 2014–2025, using each game's pregame Elo, which is the team's Elo as of the point before the game, so the fit is leak-free one week ahead. It writes the committed seed `sql/sim/010_sim_params.sql`. No week phases, no `HOLDOUT_2023` version, no calibration gate; slice 3 reports the out-of-sample numbers. The current season's SP+ is not a feature.
- **Alternatives:**
  - current-season SP+ when visible, a literal reading of "SP+-based": it is not visible at the demo point (week 8 opened 2026-10-20 23:00 UTC, the 2026 slice was fetched on 2026-10-22/23; the coordinator confirmed `SP_CURRENT_RATING` is NULL at week 7), it would switch sources once weekly refreshes fetch it before a cutoff, and past seasons' in-season SP+ was never stored, so it cannot be calibrated leak-free;
  - prior SP+ only: blind to the 2026 results;
  - Elo only: drops the SP+ the brief asks for;
  - the heavy fit with phases, versions and gates ([6], [7], [31], [32]): more than a demo needs.
- **Rationale:** it keeps [2]'s model, which the review found sound. Elo carries the season's results, the prior SP+ the preseason strength, and the fallbacks cover the 8–13 % of games without Elo. It explains in one sentence.
- **Promotes to ADR:** yes

### [46] Horizon uncertainty is fitted from lagged Elo; shared team offsets stay

- **Decision:** `SIGMA_1` is the residual standard deviation one week ahead. `TAU` is the growth of the residual variance per week, measured with each team's Elo taken k = 1–6 games earlier; if the growth is not positive, `TAU` is 1.5 (`ASSUMED`). Each game gets `TEAM_SIGMA` = `TAU` × √(h − 1) / √2 for the shared team offsets of [29]; simulated postseason games use `post_team_sigma` at the last remaining week.
- **Alternatives:** an assumed `TAU` only; independent per-game noise (the original [3]).
- **Rationale:** without shared drift the season odds are overconfident (review M1). The lag fit reads data the fit already has. One week ahead the prediction and the simulation agree exactly. It amends [3] and [29].
- **Promotes to ADR:** no

### [47] Outcome layout A: one outcome string per simulated season in a fixed table

- **Decision:** `SIM_PATH (RUN_ID, SIM_ID, OUTCOMES)`, character `PATH_POS` = `GAME_IDX` + 1, `1` for a home win. A what-if condition is `SUBSTR("OUTCOMES", PATH_POS, 1) = '1'` or `'0'`. No per-run tables, no generated views.
- **Alternatives:** D20's layout C: a per-run table with one `BOOLEAN` column per game, derived in SQL from the string ([16]), with serving views regenerated by Phase 5 per publication.
- **Rationale:**
  - D20 names A the near-tie fallback. At 100k, S4 measured A within 2–19 ms of C on every what-if cell, best on the conditional guide, and smallest and fastest to load (AWS 1M: 7.2 s against 30.6 s). At 1M the latency sits in `SIM_TEAM`, which stays sparse as D20 decided.
  - C costs per-run DDL with up to about 900 commented columns, a derivation step never timed at 1M (the heavy plan needed a probe and a gate for it), escaping school names in generated DDL (Phase 5 review P5-M3), and generated `CFB_API` views.
  - Risk: A at 1M with the sparse table was not measured on AWS. Expected: one team 0.1–0.3 s, all teams 0.5–1.0 s at k = 1.
- **Verify and trigger:** task 1.5 times both shapes at 1M on AWS (p95 of 20, k = 1 and 3). If the one-team what-if misses 1 s or the all-team one misses 1.5 s, task 3.6 adds layout C as a per-run table derived for the what-if only.
- **Supersedes:** [16], [34]; amends D20 (task 3.4).
- **Promotes to ADR:** yes

### [48] `LEVERAGE` comes from engine tallies; batches of at least 5,000 seasons

- **Decision:** per batch the engine emits `L` rows (team × remaining game: seasons in which the team made the field and the home team won, nonzero only) and `G` rows (home wins per game), as one float32 matrix product, exact below 2²⁴. The split sums them and derives `N_AWAY` = `N_SIMS` − `N_HOME` and `N_AWAY_IN_CFP` = the team's seasons in the field − `N_HOME_IN_CFP`. `LEVERAGE` keeps D20's columns. Tally rows grow with the number of batches (about batches × 100 teams × 400 games), so runs use at least 5,000 seasons per batch: local 100k at 5,000, AWS 100k at 5,000 (not D19's 1,000), AWS 1M at 10,000.
- **Alternatives:** building `LEVERAGE` in SQL after the load (D20; S4: 4.75 s at 1M with C, and with A an unpivot of `SUBSTR` over 1M strings and 400 games); one encoded string per team and batch.
- **Rationale:** the product costs milliseconds per batch; about 4.4M small rows at 1M emit in about 2.5 s on AWS; the guide no longer depends on the outcome layout. D19 measured 1k–10k per batch as equal on AWS (1.54–1.65 s compute-only per 100k).
- **Promotes to ADR:** yes

### [49] `SIMULATE` is Lua from slice 1, with a light registry

- **Decision:** a run is one Lua script from the start: Phase 2's lock (kind `SIMULATE`), register, freeze (slice 2), snapshot, engine, split, finish, and cleanup on failure. Timings and sizing are columns of `SIM_RUN`. No step table, no batch table, no `SIMULATE_FROM`, `DROP_SIM_RUN` or `make sim-verify`. Cleanup of killed runs comes in slice 3.
- **Alternatives:** host-orchestrated runs first (quicker, then rewritten for the refresh step); the heavy registry.
- **Rationale:** the refresh step becomes a short wrapper, and the demo shows one SQL call. The snapshot, seed and versions keep the mission's reproducibility rule without a verify command.
- **Supersedes:** [14], [38]; amends [18] (snapshot kept, verify dropped) and [36] (cleanup in slice 3).
- **Promotes to ADR:** no

### [50] The 2026 selection rules of [9], kept and simplified

- **Decision:** one `SEASON_RULES` row for 2026: the four power champions plus the best-ranked Group of Six team (`POWER_CHAMPIONS_PLUS_G6`, `G6_BID` `TEAM`), Notre Dame in when ranked within 12, seven at-large, straight seeding with byes for the top four. `AUTO_BID_RULE` and `G6_BID` are switches, so the brief's "five highest-ranked champions" (`TOP_CHAMPIONS`) is one value away. `SELECTION_AT` (2026-12-06 12:00 UTC) replaces the selection-week view: later games (Army–Navy, 12 December) are left out, and points whose cutoff is not before it are refused. Title games are always simulated between the top two of the simulated standings. Resume weights are the S3 prototype's (100, 2.0, 0.5, rating 0), not fitted. No 2024/2025 rule sets.
- **Alternatives:**
  - the brief's wording, "5 highest-ranked conference champions plus 7 at-large, with the top 4 champions getting byes (the 2025+ rule)": its byes are the 2024 rule (2025 moved to straight seeding) and its bids the 2024–2025 rule, which the January 2026 decision replaced according to [9]'s sources (On3/Yahoo, SI citing The Athletic, an NCAA explainer; confirmed by the review);
  - the selection-week view ([37]), known title-game pairings ([12], [41]), fitted resume weights ([11]), 2024/2025 rule sets ([10]).
- **Rationale:** [9] is researched and reviewed, and a correction is a seed edit. Known pairings matter only from the week-13 point (29 November), after the demo window. Selection day is one published date. Task 3.3 checks all of it on cfp.org before the first committee ranking.
- **Promotes to ADR:** yes

### [51] Predictions, grades, movement and the shown run belong to Phase 4

- **Decision:** `CFB_SIM.PREDICTION` (frozen once; missed weeks reconstructed at their own point and flagged), `PREDICTION_GRADED`, `PREDICTION_SCORECARD`, `PREDICTION_CALIBRATION`, `POINT_RUN` (the latest `DONE` run of a point is the one shown), `CURRENT_RUN`, `ODDS_MOVEMENT` (threshold 0.0070) and `ROOTING_GUIDE`. There is no publication table.
- **Alternatives:** Phase 5's `CFB_PRED` with `PUBLICATION`, `PUBLISHED_ODDS`, steps 200 and 400, `WHATIF_PUBLICATION` and generated views.
- **Rationale:** the brief puts them here, and the numbers are computed here. A freeze is one `INSERT … SELECT` per point from `GAME_SCORE_AS_OF`. Runs are never dropped ([54]), so the arrows need no frozen copy of the odds. Phase 5 becomes thin `CFB_API` views (hand-off in plan.md).
- **Promotes to ADR:** yes

### [52] Tests use Phase 3's synthetic league and crafted models

- **Decision:** integration tests run on Phase 3's league at 2026 week 9 (about 29 FBS teams, a 10-team SEC with a title game, weeks 10–15 scheduled) and assert relations (12 rows per season, counts equal to recounts, values equal to the view), not league-specific odds. Engine and selection rules are unit-tested on crafted models. 30 scenarios in all.
- **Alternatives:** the dedicated sim league ([26]); the loop-reference oracle.
- **Rationale:** the light process; the database-equals-host test and the invariants catch what the oracle would.
- **Supersedes:** [26], [43].
- **Promotes to ADR:** no

### [53] Deploy through Phase 3's `sqlobjects`, generalized a little

- **Decision:** `sqlobjects.lint` takes the schema placeholder; `deploy(…, keep=…)` exempts named tables from the orphan drop; the data tables are `CREATE TABLE IF NOT EXISTS` statements passed as extra statements; a column that differs from its file is reported, not migrated.
- **Alternatives:** the heavy data-table kind with column comparison ([27]); a separate deployer or data schema.
- **Rationale:** the smallest change to proven code; schema changes during the demo are rare and done by hand.
- **Promotes to ADR:** no

### [54] No partitioning and no run drop

- **Decision:** `SIM_TEAM` and `SIM_PATH` are plain tables; runs stay for the season.
- **Alternatives:** `PARTITION BY RUN_ID` ([17], probe 1.1e); `make sim-drop` with retention rules.
- **Rationale:** a season stores a few dozen runs, about 0.2 GB per 1M run on AWS and 20 MB per 100k run. Task 3.6 adds partitioning if what-if latency grows with the stored runs.
- **Promotes to ADR:** no

### [55] The BucketFS parameter file is optional

- **Decision:** task 3.5, about half a day: `make sim-fit` writes a JSON copy of the constants, `make sim-bfs` uploads it, and the UDF records its digest when present. The seed and the run snapshot stay the source.
- **Alternatives:** the constants only in BucketFS (two sources; write credentials and reachability on the AWS deployment unverified); skip it.
- **Rationale:** the owner's passing idea as an Exasol showcase beat; it must never become a dependency.
- **Promotes to ADR:** no

### [56] Upsets: Phase 5 ranks, Phase 4 keeps `GAME_WIN_PROB`

- **Decision:** `GAME_WIN_PROB` gives each completed game its pregame probability (the stored line since 2013, else Elo with `ELO_PROB_SCALE` and `ELO_PROB_HFA` from a grid search in the fit) and `UPSET_MAGNITUDE`. Ranking and pages are Phase 5's.
- **Alternatives:** move the view to Phase 5; the maximum-likelihood fit of [8].
- **Rationale:** Phase 5's upset view already reads it; a grid search is simpler and good enough for a ranking.
- **Promotes to ADR:** no

### [57] Sentinels instead of NULLs in the emitted stage frame

- **Decision:** numeric stage columns that do not apply to a row kind carry 0 or FALSE; only `OUTCOMES` and `INFO` carry None.
- **Alternatives:** nullable pandas dtypes (heavy probe 1.1a).
- **Rationale:** NULLs in emitted DataFrames were never probed; S3 emitted strings, and the split filters by `ROW_KIND` anyway.
- **Promotes to ADR:** no

### [58] Restore the Pac-12 championship flag from 2026

- **Decision:** add `('Pac-12', 2026, NULL)` to the Phase 3 `CONFERENCE_CHAMPIONSHIP` seed, leaving its 2011–2023 interval intact. Redeploy the model before simulating. The 2026 rebuilt conference plays a championship game, so its teams need `HAS_CCG = TRUE` in the engine snapshot.
- **Alternatives:** keep the old seed and treat the Pac-12 as a conference without a title game in the simulation.
- **Rationale:** the old seed covered the two-member gap in 2024–2025 but excluded the current season. The interval keeps that gap and restores the current format; it affects conference-title odds and the resume path.
- **Promotes to ADR:** yes

### Status of [1]–[43] under the light plan

| Status | Decisions |
|---|---|
| Kept | [1], [2] (refined by [45]), [4], [5], [9] (as [50]), [13], [15], [19] (the lock), [20] (steps 200/300, the seed rule; the skip uses `SELECTION_AT`), [22], [24], [28] (widened by the Non-Goals), [29] (with [46]), [35], [39] |
| Amended | [3] by [46]; [8] by [56]; [12] by [50] (no known pairings); [17] by [47], [48], [54]; [18] and [36] by [49]; [21] by [50] (2026 only, cutoff before `SELECTION_AT`); [23] by [48] (at least 5,000 per batch); [27] by [53]; [33] (`ELO_CENTER` kept, source-pair report dropped); [41] (all conferences counted, G = 0 gives NULL; the clamp and known title games are moot) |
| Superseded | [6], [7], [31], [32] by [45]; [10], [11], [37] by [50]; [14], [38] by [49]; [16], [34] by [47]; [25], [30] (no backtest; slice 3 reports calibration); [26], [43] by [52]; [40] (no resume fit); [42] (moot) |

## Open Questions (assumption + verify step)

| # | Assumption | Verify step | If wrong |
|---|---|---|---|
| Q1 | The 2026 G6 automatic bid goes to the best-ranked G6 team, champion or not (confirmed by the review's sources) | Task 1.11: check the official CFP format/selection protocol on cfp.org before the first 2026 committee ranking (about 2026-11-03); record the source in `SELECTION_RULES.SOURCE_NOTE` | Set `G6_BID` to `CHAMPION` in `011_selection_rules.sql` (one value) and redeploy; no engine change |
| Q2 | The Notre Dame guarantee means "selected if ranked 12 or better in the final ranking", counted as an automatic bid (confirmed by the review's sources) | Same source as Q1 (task 1.11) | Adjust the guarantee semantics in `engine/core.py` (new engine version) and the selection-rules scenario |
| Q3 | CFBD carries pregame Elo for FCS teams in their games against FBS teams, so most non-FBS opponents are rated by Elo | `SELECT CLASSIFICATION, COUNT(*), COUNT(ELO_AS_OF) FROM CFB_MODEL.TEAM_SEASON_AS_OF WHERE AS_OF_SEASON = 2024 AND AS_OF_WEEK_SEQ = 9 GROUP BY 1` on AWS (6.2) | `CLASS_DEFAULT` covers them; record the shares and check the `NON_FBS_RATING` fit size |
| Q4 | The 2026 Pac-12 holds a championship game, which the Phase 3 seed `CONFERENCE_CHAMPIONSHIP` (Pac-12 2011–2023) does not list. Under 2026 rules it affects only `P_CONF_CHAMPION`, not selection | Look for a week-15 Pac-12 conference game in the stored 2026 schedule after the regular season, or the conference's announcement | Add a Pac-12 row from 2026 to Phase 3's seed (owned by `model/games`); no change here |
| Q5 | Deriving layout C at 400 games takes ≤ 5 s at 100k locally and ≤ 10 s at 1M on AWS | Probe 1.1c, both targets, group A ([34]) | Store layout A on both targets per D20's fallback ([16]) |
| Q6 | The fitted resume formula selects at least 10 of the 12 actual teams in 2024 and 2025 given all results | Task 6.3 (`SELECTED_OVERLAP` at k = 0) | Review the resume features ([11]), e.g. a quality-wins term; a follow-up decision, not a change inside 6.3 |
| Q7 | The SP+-and-Elo model meets the out-of-sample targets against the line | Task 6.2 report | Feature set F2 (season-to-date point differential), task 6.2b |
| Q8 | The previous season's SP+ offense and defense ratings predict totals better than the mean total | Task 6.2 totals MAE vs baseline | `TOTAL_SLOPE` 0 (constant totals), logged |
| Q9 | The builtin SLC still ships Python 3.12.3 and numpy 1.26.4 on local 2026.2 and AWS 2026.1.1 | Probe 1.1f on both targets | Re-run the stream comparison against the SLC before any run; the version is in the reproducibility key either way |
| Q10 | No conference pair meets twice in the regular season of 2024–2026 | `SIM_INPUT_CHECKS` at every backtest and demo point (6.3, 6.4) | Keep the later meeting for head-to-head in the input view instead of failing (`CONFERENCE_PAIR_REPEATED` → WARNING), with a new scenario |
| Q11 | Partitioning `SIM_TEAM` by run keeps S4's what-if latency with a season's worth of runs stored | Probe 1.1e (30 runs of 100k locally, 20 of 1M on AWS), task 6.4 | No partitioning, and the `DISTRIBUTE BY SIM_ID` retest ([17]) |
| Q12 | Exasol accepts at least 2,002 columns per table; a preseason point has fewer than 2,000 remaining FBS games | Probe 1.1b; `N_GAMES` of a preseason run | Layout A for runs above the limit |
| Q13 | Teams new to FBS in 2026 are playoff-eligible | NCAA reclassification rules for the 2026 newcomers | A small reviewed `INELIGIBLE` seed (school, seasons) read by `ENGINE_TEAM_AS_OF` |
| Q14 | 2025 has the same shape as 2024: power championship games in regular week 15 and Army–Navy alone in week 16 (from memory: 6 and 13 December 2025) | Task 6.1: `CFB_SIM.SELECTION_WEEK` and `CFB_MODEL.SEASON_WEEK` for 2025 on the stored data, zero calls | `SELECTION_WEEK` follows the stored championship games whatever their week; only the backtest's k points move, logged |
| Q15 | (Cross-plan X1) Phase 3's championship detection takes the 2026 Army–Navy game, flagged as an American Athletic conference game and alone in week 15, for the American Athletic's title game, and then misses the real one in week 14 | Task 6.1 on the stored 2026 data; the orchestrator relays it to Phase 3 (`model/games`, `CONFERENCE_CHAMPIONSHIP_GAME`) now | This plan leaves the flagged game out (`CCG_AFTER_SELECTION`) and keys the selection week on power conferences. The real game would enter as a regular conference game and can stop 2026 runs from week 13 on with `CONFERENCE_PAIR_REPEATED` until Phase 3 fixes the detection, for example by restricting it to weeks up to the season's power championship week |
| Q16 | The `EARLY` phase meets the relative targets against the line | Task 6.2 per-phase report | `SP_ELO_PHASED`, then F2; a remaining `EARLY` miss is logged (the demo lives in `MID` and `LATE`) |
| Q17 | `KILL STATEMENT` stops a Lua script that waits in `os.sleep`, leaving its committed `RUNNING` row | Probe 1.1j | The cleanup test's hook ends the script without cleanup instead |

### Status under the light plan (2026-10-23)

| # | Status |
|---|---|
| Q1, Q2 | Task 3.3 (cfp.org) |
| Q3 | Non-FBS teams without Elo take `NON_FBS_RATING` from the fit; the fit prints the share |
| Q4 | Resolved by [58]: the Phase 3 seed includes the Pac-12 again from 2026; model redeploy updates `HAS_CCG` |
| Q5, Q11, Q12 | Replaced by Q18 and [54]; no wide DDL, strings up to 2,000 games |
| Q6, Q14, Q16 | Moot (no backtest, no selection-week view, no phases) |
| Q7, Q8 | Task 3.2 report |
| Q9 | Recorded per run (`NUMPY_VERSION`, `PYTHON_VERSION`) |
| Q10 | Handled: a pair that met twice decides head-to-head by the later game; no rejection |
| Q13 | Eligible = FBS with a game in the season; new FBS members count |
| Q15 | Fixed by Phase 3 (`CONFERENCE_CHAMPIONSHIP_GAME.POWER_CHAMPIONSHIP_WEEK` limits the detection) |
| Q17 | Replaced by the cleanup test of task 3.1 (a `RUNNING` row of a gone session) |
| Q18 (new) | Layout A's what-if latency at 1M on AWS with the sparse table: task 1.5; trigger in [47] |
| Q19 (new) | Selection day 2026-12-06: task 3.3 |

## Review Findings

<!-- Populated by speq-implement after code review. -->
