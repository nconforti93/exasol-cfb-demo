# Decision Log: add-cfb-analyses

Date: 2026-10-22 (planned)

## Interview

The owner (Nico) was away and authorized the orchestrator to decide and log. The answers below are the orchestrator's, as passed to the planner.

**Q:** What is the scope?
**A:** Roadmap Phase 5 ("Phase 5: Analyses") in `docs/roadmap.md`:
- `CFB_SIM` what-if: conditional odds, a rooting guide, forced re-sim, and week-over-week movement;
- `CFB_PRED`: frozen weekly predictions, a scorecard, and upsets with an all-time rank;
- head-to-head: the all-time series on demand via `/teams/matchup` (one call per click), the next meeting plus our prediction, and an on-demand preview through `CFB_API`;
- `CFB_API` serving views and functions, all commented.

They feed the Phase 6 app pages (odds, predictions + scorecard, upsets, head-to-head) and the Phase 7 chat over MCP. Demo beat 4 is in the roadmap's Demo flow.

**Q (1) Predecessors:** What is binding?
**A:** All predecessor plans; read them all:
- Phase 2 loader/refresh (`specs/_plans/add-cfb-persistence-refresh/`, plus its worktree code in `../exasol-cfb-demo-p2`);
- Phase 3 model (`specs/_plans/add-cfb-analytical-model/`);
- Phase 4 engine (`specs/_plans/add-cfb-score-engine/`, just revised): its sim-runs and engine-contract specs, `PATH_TABLE`, `SIMULATE_FROM` (forced re-sim from a base run's snapshot with fixed results), `LEVERAGE`, sparse `SIM_TEAM`, layout C per-run tables, and refresh step 300 `SIMULATE`. **Step 200 is reserved for this phase's `PREDICT` step;**
- the foundation's recorded specs in `specs/cfbd-vs/` and `specs/cfbd-fetch/`, plus `specs/decision-log.md`.

**Q (2) What-if performance:** What are the numbers and what is open?
**A:** Input S4 (`docs/spikes/s4-sim-storage.md`, D20). Measured at 1M on AWS: what-if p95 is 0.52 s at k = 1 and 0.21 s at k = 3; the unconditional rooting guide takes 0.08 s via `LEVERAGE`. **The conditional rooting guide at k = 3 is about 2.2 s and is unmet, deferred to Phase 5.** Design a solution and give it a verify step. Options include precomputing per-condition leverage for the top-N bubble teams, restricting the guide to games in the next week, or caching per what-if.

**Q (3) Predictions:** How are they made and graded?
**A:** Predictions freeze before kickoff, as-of leak-free via Phase 3's as-of views and Phase 4's score model / `MARGIN_WIN_PROB`. The scorecard compares frozen predictions to results (for example "model went 41–9"). An upset ranked all-time needs a pre-2013 Elo-based probability (Phase 4) over history from 1869.

**Q (4) Head-to-head:** What is the UX contract?
**A:**
- S1 measured `/teams/matchup` at 4.3 s at CFBD (4.9 s end to end), above the mission's 2 s on-demand target.
- Decide the UX contract: show stored pairs instantly, otherwise a fetching state, or prefer stored `GAME` history from Phase 3, which covers all-time series at zero calls (Phase 3 decision [18] recommends that).
- Per-click calls go through the VS with read-through, and the ledger and quota apply.
- `PROMOTE()` (storing federated results) was deferred to "after Phase 6". Keep it deferred unless this phase needs it.

**Q (5) `CFB_API`:** What are its rules?
**A:** It is the only schema the app and the chat read. Every view and function is commented, with a grain-first comment rule like Phase 3. Use read-only roles: `CFB_APP`, and `CFB_MCP` for Phase 7. Write the contract down precisely, because Phase 6 builds on it.

**Q (6) Refresh chain:** Which steps?
**A:** Step 200 is `PREDICT` (freeze predictions). Week-over-week movement needs per-week run retention; take that from Phase 4's retention decision.

**Q (7) Targets:** Where does it run and how is it verified?
**A:** Local Personal 2026.2 and AWS 2026.1.1 (3 nodes). Integration tests run on local with the harness and synthetic data. Live verification runs on AWS with zero or minimal CFBD calls through the ledger. The October cap is 400 total, and about 21 + 217 + ~5 are already planned.

**Q (constraints, from the orchestrator):** What limits apply to planning?
**A:**
- No DB sessions; others are using them.
- No CFBD calls.
- Don't edit `docs/roadmap.md`, `specs/architecture.md` or `CLAUDE.md`. List the needed edits as tasks.
- Invoke `exasol:exasol-database`, `exasol:exasol-udfs` and `exasol:exasol-distributed-ml` via the Skill tool before making Exasol claims (the owner's standing rule).
- TDD tiers as in the other plans.
- Spec deltas in domains such as `analyses/` and `api/`, plus `plan.md` and `decision-log.md` in `specs/_plans/add-cfb-analyses/`, with the same structure as the other plans (Verification with Scenario Coverage, Manual Testing and Checklist). Tag `[expert]` sparingly. Run `speq plan validate add-cfb-analyses`.

## Design Decisions

### [1] Domains `analyses/` and `api/`; code in `analyses/`

- **Decision:** nine features under `analyses/` (the behavior of `CFB_PRED`, the steps and the what-if library) and three under `api/` (the `CFB_API` contract, its roles, the on-demand preview). Python in `analyses/`, Lua in `analyses/lua/`, SQL in `sql/pred/` and `sql/api/`.
- **Alternatives:** one `analyses/` domain for everything; an `app/` domain for the API.
- **Rationale:** the orchestrator named both domains. The API is a contract that Phases 6 and 7 consume, distinct from how the analyses compute. `CLAUDE.md` lists `analyses/` and gets `api/` through task 6.4.
- **Promotes to ADR:** no

### [2] A simulation run is shown after an explicit publication; step 400 `PUBLISH`

- **Decision:** a `DONE` run of origin `REFRESH` or `MANUAL` with params `CURRENT` becomes what the pages show for its point when it is published:
  - step 400 `PUBLISH` publishes the refresh's own run, after Phase 4's step 300;
  - `make publish RUN=` (`CFB_INFRA.PUBLISH_RUN`) publishes a manual run, for example the 1M run of the scale dial on AWS;
  - the latest publication of a point wins, and earlier ones stay as history.

  Forced runs, other parameter versions and unfinished runs are refused.
- **Alternatives:**
  - "the newest `DONE` run of the point wins" as a rule: a backtest, a test run or a forced run could take over the odds page, and nothing would trigger the regeneration of the what-if views;
  - refresh runs only: the scale-dial run could not be shown;
  - fold publication into step 300: Phase 4's step is closed.
- **Rationale:** one deliberate answer to "which run do the pages show", one trigger for everything that must follow a new run (frozen odds, what-if views), and the demo's "now 1M on AWS" beat works by a single command. The timeline reads "fetch → model → predictions → simulations → publish".
- **Promotes to ADR:** yes

### [3] Week-over-week movement reads frozen published odds, not old runs

- **Decision:** each publication copies the run's `SIM_TEAM_ODDS` per team into `CFB_PRED.PUBLISHED_ODDS`. `ODDS_MOVEMENT` compares a point's latest publication with the nearest earlier published point of the season. Phase 4's retention rule (weekly refresh runs kept for the season) stays as documented; it now serves only the rooting guides of earlier points.
- **Alternatives:** compare `SIM_TEAM_ODDS` of two retained runs at query time, as the interview's wording ("per-week run retention") suggested.
- **Rationale:** the arrows survive `make sim-drop`, redeploys and a cleanup of old runs. They cost about 136 rows per point instead of an aggregation over 12M rows per run, and they compare with what was actually shown. Retention is still honored, and nothing depends on it for movement.
- **Promotes to ADR:** yes

### [4] What-ifs read generated `CFB_API` views with game-id columns and a literal run id

- **Decision:** each publication of the newest point regenerates `CFB_API.WHATIF_RUN`, `WHATIF_OUTCOMES` and `WHATIF_PLAYOFF` in the publishing transaction, and `make analyses` does the same after a deploy.
  - `WHATIF_OUTCOMES` renames the run's `SIM_PATH_R<run>` columns to `G<game id>` and comments each with the week and teams. For layout A it decodes with `SUBSTR`.
  - `WHATIF_PLAYOFF` filters `SIM_TEAM` by the literal run id.
  - Before the first publication they exist with no rows.
- **Alternatives:**
  - the backend generates SQL against `CFB_SIM.SIM_PATH_R<n>`: it breaks "apps read only `CFB_API`" and needs privileges on `CFB_SIM`;
  - per-run views in `CFB_API`: they clutter what the chat sees and still need generation;
  - copy the run into a fixed table: 400 columns × 1M rows at every publication, like the layout-C derivation (≈ 10 s on AWS);
  - a Lua script returning rows: read-only roles and the MCP server's queries cannot use `EXECUTE SCRIPT`.
- **Rationale:** read-only users and the chat can reach a million seasons under one stable name, the literal run id prunes `SIM_TEAM`'s partition, game-id names are stable from week to week and self-describing, and the view hides Phase 4's layout decision. Phase 4 anticipated generated SQL ("Per-run table names force generated SQL in Phase 5's `CFB_API`").
- **Promotes to ADR:** yes

### [5] A pure host library builds every what-if query

- **Decision:** `analyses/whatif.py` (stdlib only) builds the conditional-odds and the conditional-guide SQL from validated conditions, against `CFB_API` names only. The backend (Phase 6), the chat's app-state tools (Phase 7) and `make what-if` use it.
- **Alternatives:** parameterized views (Exasol has none) or table functions (not usable in `FROM`, Phase 3); a Lua script (not usable by the roles or by MCP); SQL written separately in each consumer.
- **Rationale:** written and tested once, with host oracles in the integration tests. Validation admits only integer game ids from the run's own list and the literals `TRUE`/`FALSE`, so the text cannot be injected. The chat can still write its own read-only SQL, guided by the view comments.
- **Promotes to ADR:** yes

### [6] Rooting guide: a lookup without toggles, the coming week with toggles

- **Decision:**
  - **Without toggles**, the guide reads Phase 4's precomputed `LEVERAGE` for every remaining game of the season (S4: 0.078 s at 1M on AWS).
  - **With toggles**, the conditional guide is computed over the coming week's games only (`WHATIF_GAMES.IS_NEXT_WEEK`, about 60 at the demo's points), minus the games the toggles fix.
  - **A full-season guide under chosen results** is the unconditional guide of a forced re-sim with those results.
  - **Verify step:** probe 1.1(a) on S4's synthetic family on AWS at 1M and locally at 100k, before group B. Gate: k = 3 p95 < 1 s on AWS and < 0.5 s locally. Re-measured on real data in task 7.2.
  - **Fallbacks, in order:**
    - F1: the conditional guide over the team's top 25 coming-week games by unconditional |leverage|;
    - F2: accept up to 2 s with a "computing" state in the page contract.

    Each is logged.
- **Alternatives:**
  - precomputing per-condition leverage for the top-N bubble teams: it covers only k = 1, and its cost is about G² pairs per team and run (400 × 400 × 2 cells per team), built at every publication;
  - caching per what-if: Exasol's query cache or the backend help only an identical repeat, never the first click;
  - accept 2.2 s: the mission's < 1 s;
  - `DISTRIBUTE BY` on the path table: unmeasured, and S4 located the cost in counting columns, not in distribution.
- **Rationale:**
  - S4 put the conditional guide's cost in counting 400 game columns over the matching seasons; Q2 with only k condition columns takes < 0.2 s. Counting about 60 columns should cost about a seventh: ≈ 0.2 s fixed + 2.0 s × 60/400 ≈ 0.5 s at 1M (estimate).
  - "Who should I root for this Saturday" is what a rooting guide answers; the season-wide view stays available without toggles and through forced re-sims.
- **Promotes to ADR:** yes

### [7] Sample thresholds from standard errors

- **Decision:** seeded in `CFB_PRED.ANALYSIS_SETTINGS` and served by `CFB_API.WHATIF_SETTINGS`:
  - `MIN_EFFECTIVE_SAMPLE` 1,000: `LOW_SAMPLE` below it; a probability's standard error is then at most 1.6 points;
  - `MIN_BRANCH_SAMPLE` 100 for a guide branch (S4's thinnest branch at k = 3 was 102 at 1M);
  - `MOVE_THRESHOLD` 0.0050 for the arrows (more than 3 SE at 100k: SE ≤ 0.16 points).
- **Alternatives:** no flags; a share of the run's size; per-page constants in Phase 6.
- **Rationale:** S4 left the threshold to Phase 5. Standard errors make it explainable; one table makes it visible to the app and the chat. It also makes the scale dial visible: k = 8 is flagged at 100k (median 944 seasons) and not at 1M.
- **Promotes to ADR:** no

### [8] Forced re-sims stay owner actions; conditioning and forcing are both named

- **Decision:**
  - A forced re-sim is Phase 4's `SIMULATE_FROM` (`make simulate BASE= FIX=`). It writes a run and takes the lock, so the read-only roles cannot start one; a test proves it.
  - `CFB_PRED.FORCED_RUN_ODDS` serves its odds next to the base run's, and `ROOTING_GUIDE` its full-season guide.
  - Forced runs are never published.
  - The what-if spec documents why conditional and forced answers differ: conditioning also selects seasons in which the winners were stronger (shared team offsets), forcing does not.
- **Alternatives:** grant the roles `EXECUTE` on a wrapper script (makes a read-only role able to write and to block a refresh); a request queue table (a write path for the app).
- **Rationale:** interview item 5 asks for read-only roles. A forced run at 1M takes about 40 s on AWS, a deliberate act rather than a click. Whether Phase 6 adds a separate owner path is its decision (hand-off).
- **Promotes to ADR:** yes

### [9] Predictions are frozen once; gaps are reconstructed at their own point and flagged; current season only

- **Decision:**
  - Step 200 and `make predict` freeze every game of the current season up to the point's next week that has no prediction yet. Each game is frozen at its freeze point (the point before its week) from `GAME_SCORE_AS_OF` with `CURRENT` at horizon 1.
  - The first freeze wins: no code updates or deletes a prediction.
  - `FROZEN_BEFORE_KICKOFF` records whether the freeze preceded the game.
  - Other seasons are skipped or refused.
- **Alternatives:**
  - re-freeze every refresh: a recalibration would rewrite history;
  - freeze only next week: a skipped refresh or a new deployment leaves holes in the scorecard;
  - reconstruct at the current point: it leaks later facts;
  - predict past seasons with `CURRENT`: in-sample, because `CURRENT` is fitted on 2014–2025.
- **Rationale:** honest grades ("predictions made in advance", architecture § CFB_PRED), complete scorecards, and no reconstruction that pretends to be earlier than it was. The as-of views make reconstructions leak-free by construction (Phase 3 and 4 invariance tests).
- **Promotes to ADR:** yes

### [10] Predictions and publications are deployment history, outside the snapshot

- **Decision:** `PREDICTION`, `PUBLICATION` and `PUBLISHED_ODDS` are not added to the snapshot scope. A restore leaves them as they are. A new deployment reconstructs its predictions (flagged) and publishes its own runs.
- **Alternatives:** add `CFB_PRED` to the load plan's `snapshot.schemas`. That would replace the reference table with old rows and clash run ids across databases (an AWS publication naming run 7 restored onto a laptop whose run 7 is another run). It would also need table-level scope in Phase 2 (a new hand-off).
- **Rationale:** consistent with Phase 2's rule that run and step logs stay per deployment; no cross-plan change. Rehearsals ("restore Sunday after week N") keep the laptop's own predictions and publications. Phase 8's rehearsal recipe creates the base publication at week N (`make simulate` + `make publish`, 0 calls) so the arrows appear after the live refresh.
- **Promotes to ADR:** yes

### [11] Prediction scope follows the score model

- **Decision:** regular-season games that involve an FBS team, conference championship games included once their teams are known (`GAME_SCORE_AS_OF`). No bowl or playoff predictions.
- **Alternatives:** extend the score model to the postseason here.
- **Rationale:** Phase 4 refuses postseason points and its score model covers the regular season. The demo lives in weeks 8–14. Bowl and playoff predictions go to the backlog (task 6.3).
- **Promotes to ADR:** no

### [12] Scorecard: two populations, pick-probability buckets, the line on the same games

- **Decision:**
  - Populations `FBS_INVOLVED` (every prediction) and `FBS_VS_FBS`.
  - Measures: record (`41–9`), accuracy, margin MAE and bias, Brier, totals MAE.
  - Calibration in five buckets of the pick's probability.
  - CFBD's line probability on exactly the graded games that have one, with the model's Brier on the same games, labeled as closing-line information.
- **Alternatives:** one population (FBS vs FCS games inflate the record); buckets of the home probability (less readable on a page); no line (no yardstick).
- **Rationale:** the mission's "record, margin MAE, Brier score, calibration buckets", with a fair comparison the audience can read.
- **Promotes to ADR:** no

### [13] Upsets: one all-time rank across sources plus a line-era rank

- **Decision:**
  - Population: completed games that involve an FBS team, with a winner and a pregame probability from `GAME_WIN_PROB` (line since 2013, Elo before and where a line is missing).
  - Ranks: `ALL_TIME_RANK`, `LINE_ERA_RANK`, `SEASON_RANK` and `WEEK_RANK` by magnitude, against stored history as it is now.
  - Our frozen probability for the winner is shown beside it.
- **Alternatives:** line era only (contradicts "ranked against all of history"); ranks as of the game's time (a non-equi self-join for little value); FBS vs FBS only (drops famous FCS-over-FBS upsets).
- **Rationale:** the mission's definition. Mixed sources are labeled on every row, and the line-era rank gives a clean comparison. Q8 checks that the all-time top is plausible on real data.
- **Promotes to ADR:** no

### [14] Head-to-head: the series from stored games, the preview as the live beat, no `/teams/matchup`

- **Decision:**
  - **Series and meetings** come from stored games (`TEAM_GAME`): 0 calls, instant, offline and in replay.
  - **The next meeting** carries our prediction (frozen or current) and the playoff stakes.
  - **The only API call** is CFBD's game preview of one game, fetched on a click (`CFB_API.GAME_PREVIEW`, 0.7 s at CFBD).
  - **The UX contract for Phase 6:** never fetched on page load, one literal id per query, a "fetching from CFBD" state, the `REQ_*` columns for the inspector.
  - **`/teams/matchup`** is not used by any analysis or serving view. `PROMOTE()` stays deferred.
- **Alternatives:**
  - D13's on-demand matchup (one call per click): 4.9 s end to end, above the 2 s target, and redundant with 157 stored seasons;
  - stored pairs instantly, else fetch: every pair is stored;
  - a `MATCHUP_LIVE` serving view: quota and latency for the chat with no new information.
- **Rationale:** D15's rule, "data scanned broadly is stored; the API is used selectively", is exactly this split. Phase 3 [18] recommended it. The preview keeps the demo's "detail nobody stores is fetched when someone asks" beat honest and under 2 s. Task 7.3 spends one call to show that the stored series equals CFBD's for one pair. This changes the roadmap's Phase 5 and D13 wording; the owner reviews it (U3, task 6.3).
- **Promotes to ADR:** yes

### [15] The next meeting's prediction and stakes

- **Decision:**
  - `MATCHUP_NEXT` takes the earliest `NEXT`/`REMAINING` game of the pair at the current point.
  - Its prediction is the frozen one if it exists, else the current point's (`PREDICTION_KIND`).
  - The stakes are both teams' playoff odds if they win or lose, from the what-if run's `LEVERAGE` when the game is in that run.
- **Alternatives:** frozen predictions only (most next meetings are weeks away); no stakes.
- **Rationale:** the roadmap's "next meeting + our prediction" with a clear label, and a bridge from head-to-head to the odds pages at no extra cost.
- **Promotes to ADR:** no

### [16] `CFB_API` is a thin, view-only serving layer with a per-object allowlist

- **Decision:**
  - 21 views from files plus 3 generated. No tables, functions or scripts.
  - Analysis logic stays in `CFB_PRED`; `CFB_API` adds names, filters and joins for pages.
  - Allowed references: `CFB_API`, `CFB_PRED`, `CFB_SIM`, `CFB_MODEL`, four `CFB_META` objects (`REFRESH_LOG`, `LOAD_RUN`, `FETCH_LOG`, `QUOTA`), and only `GAMES_PREVIEW` of the virtual schema. Never `CFB_CORE`.
  - The transparency views (`REFRESH_TIMELINE`, `FETCHES`, `QUOTA`) are included because the architecture routes the inspector and the timeline through `CFB_API`.
- **Alternatives:**
  - "views and functions" as the roadmap words it: Exasol functions return one value, and no function was needed;
  - logic in `CFB_API`;
  - leave the transparency views to Phase 6.
- **Rationale:** one contract for Phases 6 and 7, curated names for the chat, and exactly one view that can reach CFBD (plus `/info`, which is free). `QUOTA` needs the network or a recording in replay mode, and its comment says so.
- **Promotes to ADR:** yes

### [17] Roles `CFB_APP` and `CFB_MCP` with `SELECT ON SCHEMA CFB_API` only

- **Decision:** `make grants` creates both roles (idempotent, prefix-aware) and grants each `SELECT` on the schema `CFB_API`, nothing else. Users are created by the owner and granted a role; no password in any file.
- **Alternatives:**
  - per-object grants, re-granted after every regeneration;
  - users created by the tooling (a password to manage);
  - `CFB_MCP` without `GAME_PREVIEW`: not expressible with a schema grant; Phase 7 decides.
- **Rationale:** views run with their owner's rights (probe 1.1(b) confirms before the design depends on it), so one schema grant gives the serving layer and nothing else. It survives the regenerated views (probe 1.1(c)). The architecture's "+ execute on its functions" becomes moot ([16]).
- **Promotes to ADR:** yes

### [18] The analyses live in `CFB_PRED`; nothing is added to `CFB_SIM`

- **Decision:** `CFB_PRED` holds every Phase 5 table and view (predictions, scorecard, upsets, publications, movement, rooting guide, forced odds, head-to-head). The architecture's "`CFB_SIM`: … what-if" is read as "the what-if data are `CFB_SIM`'s; the what-if queries are served by `CFB_API`".
- **Alternatives:** what-if and movement objects in `sql/sim/`, deployed by `make sim`.
- **Rationale:** Phase 4's deploy drops every `CFB_SIM` object that no file in `sql/sim/` defines and pins its object counts in a scenario. Adding there would change an unrecorded contract of another plan. A separate schema keeps both deploys independent.
- **Promotes to ADR:** yes

### [19] Standalone scripts take the run lock as kind `SIMULATE`

- **Decision:** `CFB_INFRA.PREDICT` and `PUBLISH_RUN` call H1a's `acquire_lock('SIMULATE', '<script> <args>')`. `make analyses` takes `DEPLOY` (H1b). The refresh steps run under the refresh's lock.
- **Alternatives:** a new run kind `ANALYZE` through another Phase 2 hand-off.
- **Rationale:** the same stale rule (scripts: idle means stale) and no new cross-plan edit. `RUN_ARGS` names the script, so the log stays readable.
- **Promotes to ADR:** no

### [20] Grain-first comments and a generated dictionary

- **Decision:**
  - Every `CFB_PRED` and `CFB_API` object and column is commented, every table and view comment starting with "One row per". The generated what-if views carry generated comments (run, point, week and teams per game column).
  - `make api-docs` writes `docs/api-dictionary.md`, reusing Phase 3's generator.
- **Alternatives:** document the API only in the spec.
- **Rationale:** interview item 5; the chat reads comments through MCP, Phase 6 developers read the dictionary.
- **Promotes to ADR:** no

### [21] Test data: the sim league, a history variant before 1998, and host oracles

- **Decision:**
  - What-if, odds, predictions and scorecard tests use Phase 4's sim league with 4,000-season runs.
  - Upsets and head-to-head add `analyses_history.py`: five games in 1885–1995 with exact Elo values. A separate variant adds one 2026 FCS-only game for the predictions exclusion scenario; the engine's inputs never include it.
  - Expected values for random outputs come from host oracles over the run's own rows (`tests/analyses/oracle.py`).
- **Alternatives:** hard-coded numbers from a seeded run (break on any engine change); a separate league (a second fixture to maintain); adding games to 2013–2026 (would change Phase 4's runs and checks).
- **Rationale:** exact scenarios where the numbers are designed (0.9998, 0.9321, 0.4356, 0.8289, 0.8330), oracles where they are random. Seasons before 1998 touch no power-tier, simulation or calibration season.
- **Promotes to ADR:** no

### [22] Live verification spends at most 6 calls, 4 planned

- **Decision:** 3 preview queries on AWS for the latency median and 1 `/teams/matchup` query to show that the stored series equals CFBD's, all through `make record --query --plan add-cfb-analyses --cap 6`. The local preview check uses the existing recording in replay mode (0 calls).
- **Alternatives:** 0 calls (the 2 s target would stay unmeasured); more samples.
- **Rationale:** interview item 7 ("zero or minimal"); October stays far below 400 (21 + 217 + ~5 + ≤ 6).
- **Promotes to ADR:** no

### [23] Probe first: the guide's latency and the platform facts

- **Decision:** probe 1.1 runs in group A:
  - (a) the decisive rooting-guide and what-if latencies on S4's synthetic family, AWS 1M and local 100k;
  - (b) views over the virtual schema with the owner's rights, pushdown through an alias, `EXPLAIN VIRTUAL` through a view;
  - (c) a schema grant covering later views;
  - (d) generating a 900-column commented view;
  - (e) transactional regeneration with a concurrent reader.

  Each item has a fallback.
- **Alternatives:** build first, measure in the live verification.
- **Rationale:** the one unmet target of D20 and the role model are the plan's two load-bearing assumptions; both are cheap to settle on synthetic data at zero calls.
- **Promotes to ADR:** no

### [24] Probabilities from simulations use `DECIMAL(7,6)`

- **Decision:** every probability this plan derives from simulated seasons (published odds, conditional odds, rooting-guide probabilities and leverage) is `DECIMAL(7,6)`. Predictions keep the score model's `DECIMAL(5,4)`.
- **Alternatives:** `DECIMAL(5,4)` throughout (1 in 1,000,000 rounds to 0); `DOUBLE` (the project keeps engine-facing values decimal).
- **Rationale:** at 1M seasons one season is 0.000001; a long-shot team's odds should not round to zero on the scale-dial run.
- **Promotes to ADR:** no

### [25] Gap reconstruction freezes oldest point first

- **Decision:** `analib.freeze` loops over the missing freeze points of the current season up to the point, oldest first, with one `INSERT … SELECT` from `GAME_SCORE_AS_OF` per point and a commit per point.
- **Alternatives:** one statement over all points (evaluates the as-of views for every point at once; Phase 3 bounds them per point).
- **Rationale:** the normal refresh evaluates one point (< 5 s budget); a full fill of ten points stays < 30 s and leaves committed progress if interrupted.
- **Promotes to ADR:** no

## Open Questions (assumption + verify step)

| # | Assumption | Verify step | If wrong |
|---|---|---|---|
| Q1 | The conditional rooting guide over the coming week (≈ 60 games) meets < 1 s p95 at k = 3 with 1M seasons on AWS (estimate ≈ 0.5 s, because the cost follows the columns counted) | Probe 1.1(a) on S4's synthetic family (AWS 1M, local 100k), before group B; task 7.2 on real data | F1 (top 25 coming-week games by unconditional \|leverage\|); then F2 (accept ≤ 2 s with a "computing" state); logged with the numbers |
| Q2 | Exasol views run with their owner's rights: a role with only `SELECT` on `CFB_API` reads views over `CFB_PRED`, `CFB_SIM`, `CFB_MODEL`, `CFB_META` and the virtual schema without privileges on them, and never the connection | Probe 1.1(b) in the `TEST_` namespace (mock, 0 calls); `api/roles` integration tests | Escalate to the owner before group B: the "apps read only `CFB_API`" rule would need object grants on the underlying schemas (a security decision, not the planner's) |
| Q3 | `GRANT SELECT ON SCHEMA CFB_API` also covers views created or replaced later | Probe 1.1(c) | `analib.publish` and `make analyses` re-grant `SELECT` on the three generated views after regenerating them (one statement each, inside the transaction) |
| Q4 | A literal predicate on `CFB_API.GAME_PREVIEW.GAME_ID` (an alias of `P_GAME_ID`) is pushed to the adapter, and `EXPLAIN VIRTUAL` works through the view | Probe 1.1(b) | Expose `P_GAME_ID` under its own name in the view and document it in the UX contract |
| Q5 | Regenerating a `CFB_API` view inside a transaction rolls back cleanly, and a concurrent autocommit reader makes the publication wait, not fail | Probe 1.1(e) | The publication retries its transaction up to 3 times with backoff; the page contract says the backend retries a read that hits a transaction conflict |
| Q6 | CFBD's game preview answers in under about 1.4 s for upcoming games (S1: 0.7 s, one sample), so `GAME_PREVIEW` meets < 2 s end to end | Task 7.3: 3 ledgered queries on AWS, median | The fetching state is already the contract; record the numbers; `PROMOTE()` of previews moves up the backlog |
| Q7 | The coming week at the demo's points (weeks 8–13) holds at most about 70 games that involve an FBS team | Task 7.2: `SELECT COUNT(*) FROM CFB_API.WHATIF_GAMES WHERE IS_NEXT_WEEK` at the live points | Apply F1's cap of 25 games for weeks above the measured limit |
| Q8 | The all-time upset top 20 is plausible and not dominated by early Elo artifacts | Task 7.2: the top 20 by `ALL_TIME_RANK` and by `LINE_ERA_RANK`, with source and season; spot checks of well-known upsets in the top 100 | Add a setting for the first season of the all-time rank (for example the AP era, 1936), logged; `LINE_ERA_RANK` unaffected |
| Q9 | Phase 3's `TEAM_GAME` exposes the opponent's team id, the team-side result and points as its spec describes (exact column names come from its deployed files) | Task 3.4 reads Phase 3's `sql/model/200_team_game.sql` | Compute the series from `GAME` with both orientations (a `UNION ALL` of home and away sides) |
| Q10 | Exasol's query cache may answer an identical repeated what-if faster; nothing relies on it | Probe 1.1(a): one warm repeat with the cache on | Nothing; Phase 6 may cache in the backend |
| Q11 | Phase 4's `SIM_TEAM_ODDS` probabilities have at most 6 decimals, so `DECIMAL(7,6)` holds them exactly | Task 3.5 reads Phase 4's `sql/sim/300_sim_team_odds.sql` | Freeze at the view's scale and widen the column, logged; movement and thresholds are unaffected |
| Q12 | Generating the `WHATIF_OUTCOMES` DDL for about 900 game columns (preseason) works with `LISTAGG`, below the statement limits | Probe 1.1(d) | Build the DDL text in Lua by iterating the game rows |
| Q13 | The owner accepts the stored series instead of `/teams/matchup` for head-to-head ([14]) | Owner review of this log (U3) when the roadmap is edited (task 6.3) | Add `CFB_API.MATCHUP_LIVE` over `TEAMS_MATCHUP` (required `P_TEAM1`, `P_TEAM2`) with the fetching state; one call per click |
| Q14 | No backend or chat queries the generated what-if views while the published run is being dropped; `make sim-drop` of the published what-if run is an operator error | Documented in the user guide (task 6.1) and the recorder note for `sim-engine/sim-runs` | `WHATIF_RUN.RUN_STATUS` shows `DROPPED`; `make analyses` or the next publication restores views for the newest `DONE` publication |

## Review Findings

<!-- Populated by speq-implement after code review. -->
