# Decision Log: add-cfb-analyses

Date: 2026-10-22 (planned); revised 2026-10-22 to resolve the independent review (`review.md`). Replanned light and demo-first on 2026-10-23: interview § "Light replan interview", decisions [41]–[56], status of [1]–[40] and of the open questions below.

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

### Review resolution (orchestrator decisions, 2026-10-22)

The owner (Nico) was away and delegated these decisions on the independent review (`review.md`) to the orchestrator. They are recorded as the orchestrator passed them; the design entries [26]–[40] below implement them.

**Q (M1) Restore vs "the newest published point":** accept?
**A:** Accept. The what-if publication is the newest published point at or before `CURRENT_AS_OF`. Flag points after it. `WHATIF_VIEWS` is re-run after a restore, with a scenario for that. → [26]

**Q (M2) One definition of the what-if run; generated views missing on a fresh deploy:** accept?
**A:** Accept. One `CFB_PRED.WHATIF_PUBLICATION` view, used by all consumers. The deploy creates the empty generated views inside its transaction, before the file views, so the "every view compiled, 100 % comments" check passes on a fresh deploy. → [26], [27]

**Q (M3) Unescaped school names in generated DDL:** accept?
**A:** Accept. Escape `'` as `''` in every DDL and comment generator path. Add an apostrophe team ("Hawai'i") to the synthetic league. Unit tests for the escaping. The Phase 4 hand-off is already recorded in that plan's `review.md`. → [28]

**Q (M4) The scorecard headline mixes predictions made in advance with reconstructions:** accept?
**A:** Accept. Add a `FREEZE_KIND` dimension (in advance vs reconstructed) to the three scorecard views. The page contract shows in-advance predictions for the headline, or labels the headline as a reconstruction. → [29]

**Q (M5) The all-time upset rank's population and resolution:** accept?
**A:** Accept. An `ALL_TIME_FROM_SEASON` setting, with its default justified (for example 1978, or the first season where the Elo fit is sane). Rank on an unrounded magnitude. Move the Q8 check earlier, to task 3.3, on restored or synthetic data. → [30]

**Q (M6) The performance harness could spend quota:** accept?
**A:** Accept. `perf.py` excludes `GAME_PREVIEW` and `QUOTA`, with a unit test. The live cap stays ≤ 6, all through the ledger. → [31]

**Q (M7) `CFB_API` has no source for the chat's questions outside the pages:** accept?
**A:** Accept. Add a small set of documented pass-through `CFB_API` views over `CFB_MODEL` for the chat: games, team seasons, rankings, ratings, and whatever else the beat-5 "question outside the pages" plausibly needs. Grain-first comments. Keep the set minimal. → [32]

**Q (roles, m6) A schema grant cannot exclude `GAME_PREVIEW` from `CFB_MCP`:** what shape?
**A:** Put the on-demand view(s) (`GAME_PREVIEW` and any other view that calls CFBD) in a separate schema, `CFB_API_LIVE`. `CFB_APP` gets `SELECT` on both schemas; `CFB_MCP` on `CFB_API` only, so the chat cannot spend quota. Phase 7 can widen this later. → [33]

**Q (head-to-head, m7/m8) Most pairs have no click; the matchup cross-check costs a call:** what to do?
**A:** Allow the live preview on the latest meeting when there is no meeting left this season. Make the 7.3 `/teams/matchup` cross-check optional: compare the preview's `RAW_JSON` recent meetings with the stored series instead. That gives 3 planned calls. → [34]

**Q (rooting guide, m4/m5) The review's minor additions:** adopt?
**A:** Adopt: the probe's alternative shape (S4's two-pass shape cut to 60 columns); confirm the `IN`-subquery flag inside `SUM(CASE)`; gate k = 1 as well. The forced-re-sim full-season guide drops the forced game and is labeled as being under forcing semantics. → [6] (revised), [37]

**Q (m1) Arrow threshold:** which?
**A:** Use 0.0070, or a per-team SE-based threshold, whichever is simpler. Justify it. → [35]

**Q (m3) Probe 1.1(e):** widen?
**A:** Widen it per the review: a reader with autocommit off, readers of `PUBLISHED_ODDS`, and a long guide query running across the commit. → [23] (revised)

**Q (m1–m15) The other minors:** how?
**A:** Fix them all, including reordering integration tests that reference views built in a later group. → [36]–[40], and `review.md` § Review resolution

**Q (U3/Q13) Dropping `/teams/matchup`:** decide now?
**A:** Keep it as an owner-review item in this log. Don't block on it. → Q13

### Light replan interview (2026-10-23)

The owner changed the process; the orchestrator relayed the brief and the planner logs it here, close to verbatim.

**Q (process):** What changes?
**A:** The owner: "this is a demo, not a full-blown product offering... we don't need a crazy amount of tests." Replan Phase 5 (`add-cfb-analyses`, the `CFB_API` views) as a light, demo-first plan with thin slices and few tests. Planning only: no DB use, no CFBD calls, no code.

**Q (what changed since the heavy plan):** What is binding now?
**A:**
- **Phase 3 is recorded** (`specs/model/`, ADR-031..046). `CFB_MODEL` has 8 derived tables (`GAME`, `SEASON_WEEK`, `TEAM`, `RATING_SP`, `RANKING`, `TEAM_MEMBERSHIP`, `TEAM_SEASON`, `AS_OF_POINT`) plus views (`TEAM_GAME`, `VENUE`, `CONFERENCE`, the as-of views, `SEASON_COVERAGE`, `MODEL_CHECKS`). `TEAM` has `COLOR`, `ALTERNATE_COLOR`, `LOGO_URL`, `LOGO_DARK_URL`, `LATITUDE`, `LONGITUDE`. Rule: filter the as-of views by literal `AS_OF_SEASON` / `AS_OF_WEEK_SEQ`; never join `CURRENT_AS_OF`.
- **Phase 4 was replanned and owns more** (its plan § Cross-plan hand-offs and Key Interfaces): predictions, publication and movement; `CFB_SIM` tables and views `SIM_RUN`, `POINT_RUN`, `CURRENT_RUN`, `ODDS_MOVEMENT`, `ROOTING_GUIDE`, `PREDICTION`/`PREDICTION_GRADED`/`PREDICTION_SCORECARD`/`PREDICTION_CALIBRATION`, `GAME_WIN_PROB`, `SIM_RUN_GAME`, `SIM_PATH` (layout A strings), `SIM_TEAM`, `LEVERAGE`, and `engine/whatif.py`. Superseded in Phase 5: `CFB_PRED.PREDICTION`, `PUBLICATION`, `PUBLISHED_ODDS`, refresh steps 200/400, `WHATIF_PUBLICATION`, the generated what-if views, and review items P5-M3 and P5-m13. Phase 5's engine-facing views become thin `CFB_API` views over Phase 4 objects: `PLAYOFF_ODDS` over `ODDS_MOVEMENT`, `PUBLISHED_RUNS`, `WHATIF_GAMES`, `WHATIF_OUTCOMES`, `WHATIF_PLAYOFF`, `ROOTING_GUIDE`, `PREDICTIONS`, the scorecard views, upsets ranked from `GAME_WIN_PROB`.
- **Phase 6's UI was revised** (its plan § "UI revision (2026-10-23)", hand-offs P6-UI-b/c/d): a map home, a team page (`/team/:id`), league tabs This week, Odds, Predictions, Upsets, Stats. `scripts/app_fixtures.py` already has working SQL for the map, team page, this week and stats against `CFB_MODEL`; reuse it as the basis for the `CFB_API` views. Contract types: `app/frontend/src/api/types.ts`.
- **Phase 7's chat** reads `CFB_API` through the read-only role (`CFB_MCP`; its hand-off P7-f `NOTES`, plus whatever it lists).
- **Owner decision:** head-to-head uses stored games, not `/teams/matchup` (U3, confirmed).

**Q (slices):** What goes where?
**A:**
- Slice A (now, no engine needed): `CFB_API` views for `TEAMS` with marks and coordinates, `GAMES`, `TEAM_SEASONS`, `RANKINGS`, `SP_RATINGS`, `TEAM_TRAVEL` (via `GREAT_CIRCLE_KM`), `CURRENT_POINT`, `AS_OF_POINTS`, `WEEK_GAMES` (this week), the stats views (road warriors, SP+ movers, conference strength, win streaks), head-to-head / series, and all-time upsets from stored results. Grants for the app role and the chat role; deploy with `make api TARGET=…` or whatever the plan has. Keep the publication columns NULL until the engine exists.
- Slice B (after Phase 4 slice 1/2): the thin views over the Phase 4 objects listed above, matching their exact names.
- Superseded heavy tasks marked `[-]` with a reason in a fresh `tasks.md`. Per task: files, a couple of tests, a definition of done; 4–8 tasks in total.
- Log the decisions here; add a header to `review.md` saying it was superseded and how its major findings are handled; list what Phase 6's 9.7/9.8 routes read, view by view.

**Q (rules for this pass):**
**A:** No DB use, no CFBD calls. Run `speq plan validate add-cfb-analyses` and fix errors. The orchestrator authorizes the commit (overriding the git-discipline skill) with the message "add-cfb-analyses: light demo-first replan"; do not push.

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

- **Decision:** a publication that becomes the what-if publication ([26]) regenerates `CFB_API.WHATIF_RUN`, `WHATIF_OUTCOMES` and `WHATIF_PLAYOFF` in the publishing transaction, and `make analyses` does the same after a deploy or a restore. (Revised after review M1/M2: previously "each publication of the newest point".)
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
  - **A full-season guide under chosen results** is the unconditional guide of a forced re-sim with those results, under forcing semantics and labeled so ([37]).
  - **Verify step:** probe 1.1(a) on S4's synthetic family on AWS at 1M and locally at 100k, before group B. Gate: p95 < 1 s on AWS and < 0.5 s locally, at k = 1 and at k = 3 (k = 1 has the most matching seasons). Re-measured on real data in task 7.2.
  - **Measured alternative:** S4's two-pass shape cut to the same 60 columns, so a fallback with provenance exists. The probe also confirms that the `IN (SELECT …)` flag inside `SUM(CASE …)` is accepted and evaluated once (a semi-join), else a `LEFT JOIN` flag.
  - (Revised after review m4/m5.)
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
  - S4 put the conditional guide's cost in counting game columns over the matching seasons; Q2 with only k condition columns takes < 0.2 s. S4's raw data (review § 2) show the cost follows the aggregated columns, not the matching seasons (5× fewer seasons, 13 % less time), and S4's shape aggregated 2 × 400 columns. The one-pass shape over about 60 games aggregates about 120: ≈ 0.12 s + 2.0 s × 120/800 ≈ 0.4 s at 1M (estimate).
  - "Who should I root for this Saturday" is what a rooting guide answers; the season-wide view stays available without toggles and through forced re-sims.
- **Promotes to ADR:** yes

### [7] Sample thresholds from standard errors

- **Decision:** seeded in `CFB_PRED.ANALYSIS_SETTINGS` and served by `CFB_API.ANALYSIS_SETTINGS` (renamed from `WHATIF_SETTINGS`, [40]):
  - `MIN_EFFECTIVE_SAMPLE` 1,000: `LOW_SAMPLE` below it; a probability's standard error is then at most 1.6 points;
  - `MIN_BRANCH_SAMPLE` 100 for a guide branch (S4's thinnest branch at k = 3 was 102 at 1M);
  - `MOVE_THRESHOLD` for the arrows: 0.0070 ([35]; was 0.0050, whose "3 SE" ignored that an arrow compares two runs).
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
- **Rationale:** consistent with Phase 2's rule that run and step logs stay per deployment; no cross-plan change. Rehearsals ("restore Sunday after week N") keep the laptop's own predictions and publications. Phase 8's rehearsal recipe runs `make analyses` after the restore (the what-if follows the restored point, [26]) and creates the base publication at week N (`make simulate` + `make publish`, 0 calls) if the laptop has none, so the arrows appear after the live refresh.
- **Consequence (review M1):** a restore keeps publications of later points. [26] keeps them out of the what-if and flags them (`IS_AFTER_CURRENT`).
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
  - Ranks: `ALL_TIME_RANK` (since `ALL_TIME_FROM_SEASON`, [30]), `LINE_ERA_RANK`, `SEASON_RANK` and `WEEK_RANK` by the unrounded magnitude, against stored history as it is now.
  - Our frozen probability for the winner is shown beside it.
- **Alternatives:** line era only (contradicts "ranked against all of history"); ranks as of the game's time (a non-equi self-join for little value); FBS vs FBS only (drops famous FCS-over-FBS upsets).
- **Rationale:** the mission's definition. Mixed sources are labeled on every row, and the line-era rank gives a clean comparison. Q8 checks that the all-time top is plausible on real data.
- **Promotes to ADR:** no

### [14] Head-to-head: the series from stored games, the preview as the live beat, no `/teams/matchup`

- **Decision:**
  - **Series and meetings** come from stored games (`TEAM_GAME`): 0 calls, instant, offline and in replay.
  - **The next meeting** carries our prediction (frozen or current) and the playoff stakes.
  - **The only API call** is CFBD's game preview of one game, fetched on a click (`CFB_API_LIVE.GAME_PREVIEW`, 0.7 s at CFBD): of the next meeting, or of the latest one when the pair does not meet again this season ([34]).
  - **The UX contract for Phase 6:** never fetched on page load, one literal id per query, a "fetching from CFBD" state, the `REQ_*` columns for the inspector.
  - **`/teams/matchup`** is not used by any analysis or serving view. `PROMOTE()` stays deferred.
- **Alternatives:**
  - D13's on-demand matchup (one call per click): 4.9 s end to end, above the 2 s target, and redundant with 157 stored seasons;
  - stored pairs instantly, else fetch: every pair is stored;
  - a `MATCHUP_LIVE` serving view: quota and latency for the chat with no new information.
- **Rationale:** D15's rule, "data scanned broadly is stored; the API is used selectively", is exactly this split. Phase 3 [18] recommended it. The preview keeps the demo's "detail nobody stores is fetched when someone asks" beat honest and under 2 s. Task 7.3 shows that the stored series equals CFBD's from the previews' recent meetings, at no extra call; the `/teams/matchup` query is optional ([34]). This changes the roadmap's Phase 5 and D13 wording; the owner reviews it (U3, Q13, task 6.3), without blocking any task.
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

- **Decision** (revised after review M7 and m6):
  - `CFB_API`: 23 views from files (4 of them pass-through views over the model, [32]) plus 3 generated. No tables, functions or scripts, and no view that reaches the network.
  - `CFB_API_LIVE`: `GAME_PREVIEW` and `QUOTA`, the only serving views that reach CFBD ([33]).
  - Analysis logic stays in `CFB_PRED`; `CFB_API` adds names, filters and joins for pages.
  - Allowed references: `CFB_API` may reference `CFB_API`, `CFB_PRED`, `CFB_SIM`, `CFB_MODEL` and three `CFB_META` objects (`REFRESH_LOG`, `LOAD_RUN`, `FETCH_LOG`); `CFB_API_LIVE` only `GAMES_PREVIEW` of the virtual schema and `CFB_META.QUOTA`. Never `CFB_CORE`.
  - The transparency views (`REFRESH_TIMELINE`, `FETCHES`, `QUOTA`) are included because the architecture routes the inspector and the timeline through the serving layer.
- **Alternatives:**
  - "views and functions" as the roadmap words it: Exasol functions return one value, and no function was needed;
  - logic in `CFB_API`;
  - leave the transparency views to Phase 6.
- **Rationale:** one contract for Phases 6 and 7, curated names for the chat, and exactly one view that can spend quota, in its own schema (plus `/info`, which is free). `QUOTA` needs the network or a recording in replay mode, and its comment says so.
- **Promotes to ADR:** yes

### [17] Roles `CFB_APP` and `CFB_MCP` with schema grants on the serving schemas only

- **Decision** (revised after review m6, see [33]): `make grants` creates both roles (idempotent, prefix-aware). `CFB_APP` gets `SELECT` on the schemas `CFB_API` and `CFB_API_LIVE`; `CFB_MCP` on `CFB_API` only. Nothing else. Users are created by the owner and granted a role; no password in any file.
- **Alternatives:**
  - per-object grants, re-granted after every regeneration;
  - users created by the tooling (a password to manage);
  - one serving schema for both roles, with the chat's quota use left to Phase 7 (the first version of this decision).
- **Rationale:** views run with their owner's rights (probe 1.1(b) confirms before the design depends on it), and a schema's objects belong to the schema's owner, so schema grants give the serving layer and nothing else. They survive the regenerated views (probe 1.1(c)). The architecture's "+ execute on its functions" becomes moot ([16]).
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
  - Every `CFB_PRED`, `CFB_API` and `CFB_API_LIVE` object and column is commented, every table and view comment starting with "One row per". The generated what-if views carry generated comments (run, point, week and teams per game column), escaped by one rule ([28]).
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

### [22] Live verification spends at most 6 calls, 3 planned

- **Decision** (revised after review m8, see [34]): 3 preview queries on AWS, all through `make record --query --plan add-cfb-analyses --cap 6`: two games of the coming week and the latest meeting of a pair without a meeting left. They give the latency median, show that CFBD answers for a played game, and their recent meetings show that the stored series equals CFBD's. A `/teams/matchup` query is optional, only if no preview carries recent meetings. The local preview check uses the existing recording in replay mode (0 calls). `make analyses-perf` never calls a live view ([31]).
- **Alternatives:** 0 calls (the 2 s target would stay unmeasured); more samples; 4 planned with a mandatory matchup query (the first version).
- **Rationale:** interview item 7 ("zero or minimal"); October stays far below 400 (21 + 217 + ~5 + ≤ 6).
- **Promotes to ADR:** no

### [23] Probe first: the guide's latency and the platform facts

- **Decision:** probe 1.1 runs in group A:
  - (a) the decisive rooting-guide and what-if latencies on S4's synthetic family, AWS 1M and local 100k;
  - (b) views over the virtual schema with the owner's rights, pushdown through an alias, `EXPLAIN VIRTUAL` through a view;
  - (c) a schema grant covering later views;
  - (d) the DDL time of a 900-column commented view built row by row ([39]);
  - (e) transactional regeneration with concurrent readers, widened after review m3: an autocommit reader, a reader with autocommit off that read before the publication and again after it, readers of the published odds, and a long guide query across the commit.

  Each item has a fallback. `spikes/s4/` must be committed before (a) starts.
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

### [26] The what-if run is defined once, never after the current point (review M1, M2)

- **Decision:**
  - `CFB_PRED.WHATIF_PUBLICATION` (≤ 1 row): among the publications whose run is `DONE` and whose point is at or before `CFB_MODEL.CURRENT_AS_OF`, the newest point, then its latest publication.
  - Every consumer reads it: `WHATIF_VIEWS` (what to generate), `analib.publish` (whether to regenerate, read inside its own transaction after its inserts), `CFB_API.WHATIF_GAMES`, `CFB_API.CURRENT_POINT` and `MATCHUP_NEXT`.
  - Points after the current one are flagged (`IS_AFTER_CURRENT` in `POINT_PUBLICATION`, `ODDS_MOVEMENT`, `PLAYOFF_ODDS`, `AS_OF_POINTS`) and cannot be published.
  - `make analyses` is the last step of the restore recipe and regenerates the views for the restored point.
  - When the generated views lag behind it (after a restore or a dropped run, before `make analyses`), `CURRENT_POINT.WHATIF_VIEWS_CURRENT` is FALSE and `WHATIF_GAMES` is empty, so no toggle acts on stale columns.
- **Alternatives:**
  - "the newest published point" (the first version): after "restore Sunday after week N", pages stay on a later week whose games are already played;
  - four separate derivations (generator, publication, `WHATIF_GAMES`, `MATCHUP_NEXT`): they disagree after a drop or a restore;
  - a change to Phase 2's `make restore` that calls the regeneration: a cross-plan edit for one recipe line.
- **Rationale:** one rule, in one place, relative to the data's own current point. The rehearsal ("restore Sunday after week N") works without manual cleanup, and the lag is visible instead of wrong.
- **Promotes to ADR:** yes

### [27] The generated views exist in empty form from the first deploy (review M2)

- **Decision:** `make analyses` creates the empty form of each generated what-if view that is missing (or whose fixed columns differ from its template) inside its transaction, after `CFB_PRED` and before the `CFB_API` file views. Existing generated views are kept. After the commit, `WHATIF_VIEWS` regenerates as before.
- **Alternatives:** file views read only `CFB_PRED` (then `CURRENT_POINT` could not report whether the generated views are current); `FORCE` views compiled later (invalid views fail the 100 %-comments read-back); always rewrite the generated views empty in the deploy (an empty what-if between commit and regeneration).
- **Rationale:** a fresh namespace compiles every view and reads back every comment in one transaction; a redeploy never shows an empty what-if.
- **Promotes to ADR:** no

### [28] One escaping rule for strings in generated SQL, with a host reference renderer (review M3)

- **Decision:**
  - Every string literal that a generator writes into SQL text (column comments with school names, view comments with labels, `--setting` values, seed notes) goes through one rule, `'` doubled: `analyses/sqltext.py` `sql_str` on the host, `analib.sql_str` in Lua. Identifiers in generated SQL are built from integers only.
  - `analyses/whatif_ddl.py` renders the generated views' names, order and comments on the host. Unit tests pin it; the integration tests compare the Lua output with it.
  - The sim league has the Mountain West school `Hawai'i` (Phase 4 hand-off P5-M3; this plan's fallback variant otherwise).
  - The Lua generator builds the text by iterating rows ordered by game index ([39]).
- **Alternatives:** escape ad hoc in each generator; keep names out of comments (the comments are what the chat reads); build the DDL in SQL with `REPLACE` (no unit tier for it).
- **Rationale:** CFBD's FBS school "Hawai'i" would have made every publication with a Hawai'i game left invalid and rolled back the weekly refresh's last step. Lua has no unit tier, so the host renderer is what makes the rule testable.
- **ADR scope:** every generator of SQL text in the project follows the rule.
- **Promotes to ADR:** yes

### [29] The scorecard grades predictions made in advance and reconstructions apart (review M4, m14)

- **Decision:**
  - `FREEZE_KIND` (`IN_ADVANCE`, `RECONSTRUCTED`, `ALL`) is a dimension of `SCORECARD_WEEK`, `SCORECARD_SEASON` and `SCORECARD_CALIBRATION`; the `ALL` row keeps the totals.
  - `IN_ADVANCE` needs the stored `FROZEN_BEFORE_KICKOFF` and a freeze before the game's current kickoff; a kickoff moved before the freeze makes a reconstruction, while the stored row never changes.
  - `IS_HEADLINE` marks the `IN_ADVANCE` row when it has graded games, else the `ALL` row, which pages label "reconstructed at the as-of point (leak-free, made after kickoff)".
  - Games are graded in the week they are played (`IS_RESCHEDULED` when it differs from the frozen week); a frozen game is never frozen again; an added game is frozen at its own freeze point.
  - Phase 8 hand-off: the rehearsal's headline will be the labeled reconstruction unless predictions were frozen live during the season.
- **Alternatives:** one mixed record with a count of frozen-in-advance games (the first version); dropping reconstructions from the scorecard; grading postponed games in their frozen week.
- **Rationale:** "predictions made in advance" is the architecture's own claim. On AWS the weeks before this phase's deployment, and in a rehearsal every graded week, are reconstructions; a headline must not present them as forecasts. They stay useful: they are leak-free by construction.
- **Promotes to ADR:** yes

### [30] The all-time upset rank starts in 1978 and ranks the unrounded magnitude (review M5, m15)

- **Decision:**
  - `ALL_TIME_FROM_SEASON` in `ANALYSIS_SETTINGS`, default 1978, shown on every `UPSET` row; older games keep `SEASON_RANK` and `WEEK_RANK`.
  - `RANK_MAGNITUDE` (`DOUBLE`) is the magnitude before rounding: for Elo rows recomputed in the view from the pregame Elo values with `CURRENT`'s Elo parameters; for line rows the stored four-decimal value. All ranks use it; `UPSET_MAGNITUDE` stays `DECIMAL(5,4)` for display. The test pins the two together (|difference| ≤ 0.00005).
  - `ALL_TIME_TIED` and `LINE_ERA_TIED` flag shared ranks; pages show "T-37".
  - Q8's real-data check moves from task 7.2 to task 3.3 (restored snapshot, else synthetic for the mechanics), and 7.2 repeats it.
- **Alternatives:**
  - every season since 1890 (the first version): the population "involves an FBS team" is a retroactive label before 1978 (Phase 3's Q6), and Elo gaps of 1,000+ points lie far outside the 2014–2025 fit and crowd at 0.9999;
  - 1936 (the AP era): still before Division I-A, so the population problem stays; possible later with a logged decision if Q8 shows 1978 is too strict;
  - widen `GAME_WIN_PROB.UPSET_MAGNITUDE` in Phase 4 to `DECIMAL(9,8)`: a cross-plan edit; the recomputation here needs none.
- **Rationale:** 1978 is the first Division I-A season, the first season where "involves an FBS team" means what it says. Ranking the rounded value made ties that were not ties. "The 37th biggest since 1978" is a sentence the data can defend.
- **ADR scope:** the mission's "ranked against all of history" becomes "since 1978".
- **Promotes to ADR:** yes

### [31] The performance harness never touches network-reaching views (review M6)

- **Decision:** `analyses/perf.py` times a fixed catalog of queries over `CFB_API` only. A unit test asserts that no catalog query names `GAME_PREVIEW`, `QUOTA`, `CFB_API_LIVE` or a virtual schema. The preview's latency comes only from task 7.3's 3 ledgered calls.
- **Alternatives:** time the preview under the S4 protocol (about 28 unledgered calls per run, and `QUOTA` would reach `/info` on every sample); a runtime guard only.
- **Rationale:** keeps the ≤ 6 cap and the "ledger = `/info` drop" proof true by construction.
- **ADR scope:** benchmarks, Phase 8's included, measure live endpoints only through the ledger.
- **Promotes to ADR:** yes

### [32] Four pass-through views over the model for the chat (review M7)

- **Decision:** `CFB_API` adds `GAMES` (`CFB_MODEL.GAME`), `TEAM_SEASONS` (`TEAM_SEASON`: records, conference, tier, final ranks, SP+ prior), `RANKINGS` (`RANKING`) and `SP_RATINGS` (`RATING_SP`, strength of schedule included). They select the source's columns unchanged with its column comments; their view comments are grain first, say that they show stored history as it is now (not as of a point), and point to the page views.
- **Alternatives:**
  - a Phase 7 hand-off and no pass-through views: beat 5 ("a question outside the pages") would have no source;
  - more views (`TEAM_SEASONS_AS_OF`, venues, conferences, our model's ratings per point): not needed for the plausible questions ("how did Michigan do in 1997?", "which unbeaten teams have the weakest schedule?", "who is #1 in the AP poll?"); "new views may be added" covers them later.
- **Rationale:** the smallest set that answers the plausible beat-5 questions at 0 calls, within the existing allowlist (`CFB_MODEL`).
- **Promotes to ADR:** no

### [33] Network-reaching views live in `CFB_API_LIVE`; the chat role cannot read them (review m6)

- **Decision:** `GAME_PREVIEW` and `QUOTA` move to the schema `CFB_API_LIVE`. `CFB_APP` gets `SELECT` on `CFB_API` and `CFB_API_LIVE`, `CFB_MCP` on `CFB_API` only. Phase 7 can widen the chat's access with one grant. Every `CFB_API` view is then free of network access.
- **Alternatives:** keep both in `CFB_API` and leave the chat's quota use to Phase 7 (a later move would break the contract); per-object grants (re-granted after every regeneration); only `GAME_PREVIEW` moves (`QUOTA` is free, but it still needs the network).
- **Rationale:** a schema grant cannot exclude one view; a second schema makes "the chat cannot spend quota" a grant fact that the roles' tests prove.
- **ADR scope:** the security model.
- **Promotes to ADR:** yes

### [34] The preview covers the latest meeting; the stored series is checked at no extra call (review m7, m8)

- **Decision:** the page previews the next meeting (`MATCHUP_NEXT.GAME_ID`) when there is one, else the latest meeting (`MATCHUP_SERIES.LAST_GAME_ID`). Task 7.3 plans 3 calls: two coming-week games and one latest meeting (which also shows that CFBD answers for a played game, Q15). The previews' `RAW_JSON` recent meetings are compared with `MATCHUP_GAMES` on the host; only the result and counts are written down. The `/teams/matchup` query is optional, only if no preview carries recent meetings.
- **Alternatives:** preview only meetings still to play (most pairs have no click); fix the demo pair to a rivalry-week game in the script; keep the mandatory matchup call (4 planned).
- **Rationale:** every pair that has met gets the live beat, and the "stored = CFBD" evidence comes free with the calls the latency check spends anyway.
- **Promotes to ADR:** no

### [35] The arrow threshold is 0.0070 (review m1)

- **Decision:** `MOVE_THRESHOLD` 0.0070, a constant, valid for runs of at least 100,000 seasons; the setting's note says so.
- **Alternatives:** 0.0050 (the first version: 2.2 SE of a difference, a few spurious arrows per week near the bubble); a per-team threshold 3·√(p(1 − p)(1/n₁ + 1/n₂)) in `ODDS_MOVEMENT`.
- **Rationale:** an arrow compares two independent runs, so the noise is the standard error of a difference, largest at p = 0.5: √(0.25/n₁ + 0.25/n₂). At 100k vs 100k that is 0.0022, so 0.0070 is 3.1 SE (false-arrow rate about 0.2 % per team-week at p = 0.5, less elsewhere); 1M vs 100k gives 4.2 SE, 1M vs 1M 9.9. The per-team threshold would also cover small runs, but the demo never publishes one, and a constant is simpler to explain on a page and to test.
- **Promotes to ADR:** no

### [36] Every what-if query names its run (review m2)

- **Decision:** `what_if_odds_sql` and `rooting_guide_sql` take `run_id`, filter `WHATIF_OUTCOMES` on it, anchor every row on `WHATIF_RUN` with it, and return `RUN_ID` on every row. A query for a superseded run returns no rows (same games) or an unknown-column error (other games); the page contract says to reload `WHATIF_RUN` and `WHATIF_GAMES` on either, and consumers read with autocommit.
- **Alternatives:** no guard (a page loaded before beat 4's publication would show the new run's numbers under the old labels); a version column the backend polls.
- **Rationale:** beat 3 → beat 4 is exactly the moment a page holds stale state; the guard costs one predicate.
- **Promotes to ADR:** no

### [37] A forced run's guide drops its forced games and is labeled (review m5)

- **Decision:** `ROOTING_GUIDE` leaves out games whose result the run fixed (`FIXED_HOME_WIN` set) and carries `RUN_ORIGIN` and `FIXED_RESULTS` on every row. Pages label a forced run's guide "with these results forced"; the toggled guide is labeled conditional. The forced-resim tests read the `CFB_PRED` objects.
- **Alternatives:** keep the forced game's row with an empty branch (the first version; the conditional guide drops the same situation); no label.
- **Rationale:** consistency between the two guides, and the honest name for what each number means ([8]).
- **Promotes to ADR:** no

### [38] Steps 200 and 400 are registered by the loader deploy; tests use plan variants (review m9)

- **Decision:** the rows of `REFRESH_STEPS` come from `loader/load-plan.yaml` through Phase 2's loader deploy, like 100 and 300; `make analyses` reports them and warns about a missing script or row. Integration tests deploy the loader with plan variants (`load-plan.analyses.yaml` here); task 1.6 confirms the earlier phases' tests do too. Task 2.2 deploys stub scripts, so every registered step exists from the first analyses deploy.
- **Alternatives:** `make analyses` writes its own rows (a second writer of one table, which the next loader deploy would replace); `REFRESH_WEEK` skips steps whose script is missing (a Phase 2 change that would hide errors).
- **Rationale:** one writer per table; the partial-deploy gap is documented and visible in the report.
- **Promotes to ADR:** no

### [39] The generator builds the DDL row by row; no `LISTAGG` (review m12)

- **Decision:** `whatif_views.lua` reads the run's remaining games `ORDER BY GAME_IDX` and builds the text in Lua through `analib.sql_str`. Probe 1.1(d) only times a 900-column DDL.
- **Alternatives:** `LISTAGG … WITHIN GROUP (ORDER BY GAME_IDX)` (fine on length, about 100k characters against 2,000,000, but a second place for escaping and ordering).
- **Rationale:** one code path, deterministic order, and the same structure as the host reference renderer ([28]).
- **Promotes to ADR:** no

### [40] `CFB_API.WHATIF_SETTINGS` becomes `ANALYSIS_SETTINGS`

- **Decision:** the serving view of the settings is named after its source, because it now also holds `MOVE_THRESHOLD` and `ALL_TIME_FROM_SEASON`, which are not what-if settings.
- **Alternatives:** keep the name.
- **Rationale:** the contract is unrecorded, so the rename is free now and avoids a misleading name for the chat.
- **Promotes to ADR:** no

### [41] A light, demo-first plan in two slices

- **Decision:** Phase 5 becomes 8 tasks in two slices. Slice A serves everything that needs no simulation now (deploy, roles, the thin slice for the map and the team page, travel, this week, stats, head-to-head, upsets from stored lines, the live preview and the refresh timeline). Slice B adds the views over Phase 4's objects once Phase 4's slice 2 is merged. The heavy plan's 34 tasks are marked absorbed or superseded in `tasks.md`; its nine `analyses/*` spec deltas are removed.
- **Alternatives:** implement the reviewed heavy plan; wait for Phase 4 and do one slice.
- **Rationale:** the owner's light process (same as Phase 4's [44]); Phase 6's S2 slice waits only for slice A, so it should not wait for the engine.
- **Promotes to ADR:** no

### [42] `CFB_API` is a thin view layer over `CFB_MODEL` and `CFB_SIM`; there is no `CFB_PRED`

- **Decision:** Phase 5 creates no schema of its own besides `CFB_API` and `CFB_API_LIVE`, no data table, no Lua script and no refresh step. Every number that needs freezing, grading or a choice of run is Phase 4's (`PREDICTION`, `POINT_RUN`, `CURRENT_RUN`, `ODDS_MOVEMENT`, `ROOTING_GUIDE`, `GAME_WIN_PROB`); `CFB_API` passes it through under the serving names. Supersedes [2], [3], [18], [19], [38].
- **Alternatives:** keep `CFB_PRED` with publications and frozen published odds (the heavy plan).
- **Rationale:** one definition per number. Phase 4's [51] took predictions, grades, movement and the shown run; a second schema would recompute or re-freeze them, and the pages, the chat and Phase 4's own checks could disagree.
- **Promotes to ADR:** yes

### [43] The spec domain is `api/`; the code stays in the package `analyses/`

- **Decision:** all nine features are `api/*`. The `analyses/` spec domain is dropped (`CLAUDE.md` is updated in task 1.5). The Python package keeps the name `analyses/` (`deploy.py`, `live_views.py`, `docs.py`, `whatif.py`).
- **Alternatives:** keep `analyses/*` specs for upsets and head-to-head; rename the package `api/`.
- **Rationale:** every Phase 5 feature is now a serving contract; Phases 6 and 7 import `analyses/whatif.py`, so the package name stays.
- **Promotes to ADR:** no

### [44] Final names in slice A; the simulation columns stay NULL until slice B fills them in place

- **Decision:** `CURRENT_POINT`, `AS_OF_POINTS`, `WEEK_GAMES`, `MATCHUP_NEXT`, `MATCHUP_GAMES` and `UPSETS` ship in slice A with their final names and columns. The columns that need the engine are NULL (flags FALSE); slice B edits the same files to fill them. Once a view file reads `CFB_SIM`, `make api` refuses to run before `make sim`, and the test harness deploys Phase 4's schema (without runs) first, so the slice-A tests keep holding.
- **Alternatives:** separate "lite" views that slice B replaces with new names; wait for the engine.
- **Rationale:** Phase 6's hand-off P6-UI-d asks exactly this; no contract change between the slices.
- **Promotes to ADR:** no

### [45] A `CFB_API` twin for every statement of Phase 6's real-data snapshot

- **Decision:** the model tables are served as pass-through views with the source's columns and comments: `GAMES`, `TEAM_GAMES` (added), `TEAM_SEASONS`, `RANKINGS`, `SP_RATINGS`; `TEAMS` adds the home venue. The computed panels of `scripts/app_fixtures.py` become page views: `TEAM_TRAVEL`, `WEEK_GAMES`, `ROAD_WARRIORS`, `SP_MOVERS`, `CONFERENCE_STRENGTH`, `WIN_STREAKS`. Phase 6's 9.7 replaces each snapshot statement with a query of the same shape (plan § Cross-plan hand-offs).
- **Alternatives:** only the four chat pass-throughs of [32], with the routes orienting `GAMES` per team themselves; no stats views (the route aggregates `TEAM_TRAVEL`).
- **Rationale:** the snapshot SQL already works on real data; the twins make 9.7 a rename, give the chat the same answers, and keep each card's SQL short.
- **Promotes to ADR:** no

### [46] Views with a point carry it as columns, are built on `AS_OF_POINT` and the derived tables, and are filtered with literals

- **Decision:** `WEEK_GAMES`, `ROAD_WARRIORS`, `CONFERENCE_STRENGTH` and `WIN_STREAKS` have one row per as-of point and subject, joined from `CFB_MODEL.AS_OF_POINT` to the derived tables and `TEAM_GAME`; a game counts at a point when it is completed and at or before the point. No serving view reads an as-of view (`GAME_AS_OF`, `RANKING_AS_OF`, `TEAM_SEASON_AS_OF`) or joins `CURRENT_AS_OF` to one. Consumers read `CURRENT_POINT` first and filter with literal `AS_OF_SEASON` and `AS_OF_WEEK_SEQ`. `SP_MOVERS` has season grain (SP+ does not change with the week, only with the fetch).
- **Alternatives:** season grain "as stored now" for every stats view (simpler, but the as-of picker would show later games on an earlier week); views over the as-of views (Phase 3's 21 s trap).
- **Rationale:** honest cards at any point, one point evaluated per query; the rule is the one future serving views must follow.
- **Promotes to ADR:** yes

### [47] AP ranks in serving views use the label rule from `RANKING`

- **Decision:** `WEEK_GAMES` and `CONFERENCE_STRENGTH` take a team's AP rank from the latest AP poll of the season whose `VISIBLE_AT_WEEK_SEQ` is at most the point, as the snapshot SQL does. A ranking fetched early (a Tuesday committee ranking) is not shown before its label allows; the AP poll is not affected in practice.
- **Alternatives:** `RANKING_AS_OF` (an as-of view inside a serving view, [46]).
- **Rationale:** a derived table with a simple rule; the same numbers as Phase 6's preview.
- **Promotes to ADR:** no

### [48] Upsets: stored lines first, Phase 4's `GAME_WIN_PROB` since 1978 in slice B; ranks on the stored magnitude

- **Decision:** `UPSETS` holds the underdog wins of completed FBS-involved games. Slice A uses the stored line (from 2013, `ALL_TIME_FROM_SEASON` 2013, `PROB_SOURCE` LINE). Slice B switches the source to `CFB_SIM.GAME_WIN_PROB` (LINE else ELO), the window to 1978 ([30] kept) and adds our frozen probability. Ranks are `RANK()` on `UPSET_MAGNITUDE` as Phase 4 rounds it; equal magnitudes share a rank and are flagged (`*_TIED`). `RANK_MAGNITUDE` is dropped. Revises [13] and [30].
- **Alternatives:** wait for the engine; recompute an unrounded Elo magnitude in Phase 5 ([30] as reviewed).
- **Rationale:** the upsets tab works today; one probability definition (Phase 4's [56]); a shared rank shown as "T-n" is honest about rounding, and the window from 1978 already removes the saturated early-Elo games.
- **Promotes to ADR:** no

### [49] Head-to-head from stored games; the next meeting from the schedule, then with our prediction and the stakes

- **Decision:** `MATCHUP_SERIES` and `MATCHUP_GAMES` come from `TEAM_GAME` (U3, confirmed by the owner on 2026-10-22). `MATCHUP_NEXT` lists each pair's next not-completed game of the current season from the schedule with CFBD's line; in slice B it adds `PREDICTION_KIND` FROZEN (the frozen prediction) or CURRENT (the what-if run's probability from `SIM_RUN_GAME`, without points), and each team's playoff odds if it wins or loses from the run's `ROOTING_GUIDE`. Keeps [14], revises [15].
- **Alternatives:** `/teams/matchup` (4.9 s, redundant); predictions from `GAME_SCORE_AS_OF` ([53]).
- **Rationale:** zero calls and instant; only Phase 4's numbers.
- **Promotes to ADR:** no

### [50] No conditional rooting guide

- **Decision:** the rooting guide is Phase 4's precomputed `ROOTING_GUIDE` (from `LEVERAGE`), for every done run, with its coming-week rank. There is no guide that follows what-if toggles; `rooting_guide_sql` is dropped, and with it Phase 6's stretch item and probe S8 (a). Supersedes [6] and [37].
- **Alternatives:** the heavy plan's one-pass guide over the coming week with a probe and fallbacks.
- **Rationale:** not on the demo path (Phase 7 already uses the plain guide; Phase 6 had it as a stretch), and it was the one unmet latency target (S4: 2.2 s). Recorded as a deliberate scope cut so it is not re-planned by default.
- **Promotes to ADR:** yes

### [51] What-if through a thin adapter over `engine/whatif.py`, with static views

- **Decision:** `WHATIF_OUTCOMES`, `WHATIF_PLAYOFF` and `WHATIF_TEAMS` are plain projections of `SIM_PATH`, `SIM_TEAM` and `SIM_RUN_TEAM` (every done run); `WHATIF_RUN` and `WHATIF_GAMES` follow `CFB_SIM.CURRENT_RUN`. `analyses/whatif.py` maps game ids to `PATH_POS` (from `WHATIF_GAMES`), validates, and calls Phase 4's builder with the serving names. No run guard: every query filters its own `RUN_ID`, and runs are never dropped. Supersedes [4], [26], [27], [28], [36], [39]; revises [5].
- **Alternatives:** an own builder; generated per-run views with a run guard (heavy plan, layout C).
- **Rationale:** layout A (Phase 4's [47]) makes the views static; one builder; Phases 6 and 7 keep their import and their game-id conditions.
- **Promotes to ADR:** no

### [52] `CURRENT_POINT` keeps the publication columns as aliases of run ids

- **Decision:** `PUBLICATION_ID` is an alias of `RUN_ID` (the run shown at the current point, `POINT_RUN`), `WHATIF_PUBLICATION_ID` of `WHATIF_RUN_ID` (`CURRENT_RUN`); `WHATIF_VIEWS_CURRENT` is TRUE whenever a what-if run exists. `HAS_PUBLICATION` means "a run is shown at the point".
- **Alternatives:** drop the publication names.
- **Rationale:** Phase 6's `CurrentPoint` and Phase 7's tools already use them; Phase 4's hand-off says `publication_id` is the run id.
- **Promotes to ADR:** no

### [53] No serving view over `GAME_SCORE_AS_OF`

- **Decision:** pages show frozen predictions only (`PREDICTIONS`, `WEEK_GAMES`); before a freeze the team page's "this week" is unavailable. `MATCHUP_NEXT`'s CURRENT kind uses the run's snapshot probability instead.
- **Alternatives:** a `CFB_API` view over `GAME_SCORE_AS_OF` that consumers filter with literals (Phase 4's hand-off row "current predictions").
- **Rationale:** a serving view over an as-of view invites the unfiltered or joined query that evaluates every point; every point with a run has its next week frozen (step 2 of `SIMULATE`), so the gap is only the hours between a week ending and the next run.
- **Promotes to ADR:** no

### [54] `CFB_API_LIVE` stays, with `P_GAME_ID` unrenamed and no live calls in this plan; the refresh timeline views stay

- **Decision:** `GAME_PREVIEW` shows the virtual table's columns under their own names, rendered from the catalog, including `REQ_ROWS` (Phase 6's P6-a is met by the virtual table itself) and `P_GAME_ID` as the filter column. `QUOTA` stays. This plan spends no CFBD call: tests use the mock and replay, and the preview's live latency and the stored-series check against its `RAW_JSON` move to Phase 6's live verification under its own cap. `REFRESH_TIMELINE` and `FETCHES` stay in `CFB_API` for beat 3. Revises [22], [33], [34]; Q4 and Q6 are avoided or moved.
- **Alternatives:** rename to `GAME_ID` (needed a pushdown probe); spend 3 ledgered calls here.
- **Rationale:** no pushdown question; Phase 6 calls the preview anyway; October's budget stays untouched.
- **Promotes to ADR:** no

### [55] Tests: a few integration tests per feature, with the sources as oracle

- **Decision:** 26 scenarios, one integration test each, on Phase 3's synthetic league (slice A) plus Phase 4's harness and runs (slice B). Each test compares a view with its `CFB_MODEL`/`CFB_SIM` source, or with sums over another serving view; where the league lacks a fact, the test stores an extra fetch with `tests/it/mirror_edit.py`. Unit tests only for the lint, the grants, the rendered preview view, the dictionary and the adapter. Supersedes [21].
- **Alternatives:** host oracles, a history variant of the league, a dedicated sim league (heavy plan).
- **Rationale:** the views are thin; checking them against their sources catches the mistakes a thin view can make.
- **Promotes to ADR:** no

### [56] Slice A takes Phase 4's generalized deployer as identical text

- **Decision:** task 1.1 copies Phase 4's changes to `vs/host/sqlobjects.py`, `namespace.py` and `metaddl.py` from commit 74af62c unchanged, then adds the API names (`api_schema`, `api_live_schema`, `app_role`, `mcp_role`, `{API_SCHEMA}`, `{API_LIVE_SCHEMA}`). Slice B merges Phase 4's branch.
- **Alternatives:** generalize the deployer a second time; wait for Phase 4's merge.
- **Rationale:** slice A must not wait for the engine; identical hunks merge cleanly, and the API lines are small, adjacent additions.
- **Promotes to ADR:** no

### Status of [1]–[40] under the light plan

| Decision | Status |
|---|---|
| [1] domains `analyses/`, `api/` | superseded by [43] |
| [2] explicit publication, step 400 | superseded by [42] (Phase 4's `POINT_RUN`) |
| [3] movement from frozen published odds | superseded: Phase 4's `ODDS_MOVEMENT` over retained runs (no run is dropped) |
| [4] generated what-if views | superseded by [51] |
| [5] pure host library | revised by [51]: Phase 4's builder plus a thin adapter |
| [6] split rooting guide | superseded by [50] |
| [7] sample thresholds | kept as settings: `MIN_EFFECTIVE_SAMPLE` 1000 in `ANALYSIS_SETTINGS`, `MIN_BRANCH_SAMPLE` 100 in Phase 4's `SIM_PARAMS` |
| [8] forced re-sims as owner actions | superseded: Phase 4 has no forced re-sims |
| [9], [11], [12], [25] predictions, scope, scorecard, reconstruction order | moved to Phase 4 (`sim-engine/predictions`), unchanged in substance |
| [10] predictions as deployment history | moved to Phase 4 (`CFB_SIM` data tables, outside the snapshot) |
| [13] all-time plus line-era rank | revised by [48] |
| [14] head-to-head from stored games | kept ([49]); U3 confirmed by the owner |
| [15] next meeting with prediction and stakes | revised by [49], [53] |
| [16] thin view-only `CFB_API` with an allowlist | kept ([42]) |
| [17] roles `CFB_APP`, `CFB_MCP` | kept |
| [18] analyses in `CFB_PRED` | superseded by [42] |
| [19] standalone scripts lock as `SIMULATE` | superseded: no scripts |
| [20] grain-first comments and a generated dictionary | kept |
| [21] sim league, history variant, oracles | superseded by [55] |
| [22] live verification with at most 6 calls | superseded by [54]: 0 calls |
| [23] probe first | superseded: owner's rights confirmed by Phase 6's probe S9 (a); the rest is moot |
| [24] `DECIMAL(7,6)` probabilities | Phase 4's types apply |
| [26], [27] the what-if run defined once; empty generated views | superseded by [51], [52] (Phase 4's `CURRENT_RUN`) |
| [28], [39] escaping rule; no `LISTAGG` | moot: nothing is generated |
| [29] freeze kinds and the headline | Phase 4: `IS_HEADLINE` only on `IN_ADVANCE` rows; the page labels the `ALL` row |
| [30] all-time window 1978, unrounded magnitude | 1978 kept; unrounded magnitude dropped ([48]) |
| [31] perf harness never touches live views | moot: no perf harness; timings in 1.5 and 2.3 cover `CFB_API` only |
| [32] pass-through views for the chat | kept and extended by [45] |
| [33] network views in `CFB_API_LIVE` | kept ([54]) |
| [34] preview of the latest meeting | kept as the page rule; the `RAW_JSON` check moves to Phase 6 ([54]) |
| [35] arrow threshold 0.0070 | Phase 4's `MOVE_THRESHOLD` |
| [36] every what-if names its run | kept trivially ([51]) |
| [37] forced guide label | moot ([8]) |
| [38] steps 200/400 | superseded: Phase 4's 200/300 |
| [40] `ANALYSIS_SETTINGS` | kept, as a view over `SIM_PARAMS` and constants |

## Open Questions (assumption + verify step)

| # | Assumption | Verify step | If wrong |
|---|---|---|---|
| Q1 | The conditional rooting guide over the coming week (≈ 60 games) meets < 1 s p95 at k = 1 and k = 3 with 1M seasons on AWS (estimate ≈ 0.4 s, because the cost follows the aggregated columns: ≈ 120 instead of S4's 800) | Probe 1.1(a) on S4's synthetic family (AWS 1M, local 100k), before group B, against S4's two-pass shape cut to 60 columns; task 7.2 on real data | F1 (top 25 coming-week games by unconditional \|leverage\|); then F2 (accept ≤ 2 s with a "computing" state); logged with the numbers |
| Q2 | Exasol views run with their owner's rights (the schema's owner): a role with only `SELECT` on `CFB_API` and `CFB_API_LIVE` reads views over `CFB_PRED`, `CFB_SIM`, `CFB_MODEL`, `CFB_META` and the virtual schema without privileges on them, and never the connection | Probe 1.1(b) in the `TEST_` namespace (mock, 0 calls); `api/roles` integration tests | Escalate to the owner before group B: the "apps read only the serving schemas" rule would need object grants on the underlying schemas (a security decision, not the planner's) |
| Q3 | `GRANT SELECT ON SCHEMA` also covers views created or replaced later, and a grant on one schema gives nothing in the other | Probe 1.1(c) | `analib.publish` and `make analyses` re-grant `SELECT` on the three generated views after regenerating them (one statement each, inside the transaction) |
| Q4 | A literal predicate on `CFB_API_LIVE.GAME_PREVIEW.GAME_ID` (an alias of `P_GAME_ID`) is pushed to the adapter, and `EXPLAIN VIRTUAL` works through the view | Probe 1.1(b) | Expose `P_GAME_ID` under its own name in the view and document it in the UX contract |
| Q5 | Regenerating a `CFB_API` view inside a transaction rolls back cleanly; concurrent readers (autocommit on, autocommit off across the publication, readers of the published odds, a 2 s guide query across the commit) make the publication wait, not fail | Probe 1.1(e), widened after review m3 | The publication retries its transaction up to 3 times with backoff; the page contract keeps "consumers use autocommit" and adds that the backend retries a read that hits a transaction conflict |
| Q6 | CFBD's game preview answers in under about 1.4 s for upcoming games (S1: 0.7 s, one sample), so `GAME_PREVIEW` meets < 2 s end to end | Task 7.3: 3 ledgered queries on AWS, median | The fetching state is already the contract; record the numbers; `PROMOTE()` of previews moves up the backlog |
| Q7 | The coming week at the demo's points (weeks 8–13) holds at most about 70 games that involve an FBS team | Task 7.2: `SELECT COUNT(*) FROM CFB_API.WHATIF_GAMES WHERE IS_NEXT_WEEK` at the live points | Apply F1's cap of 25 games for weeks above the measured limit |
| Q8 | With `ALL_TIME_FROM_SEASON` 1978 and ranks on the unrounded magnitude, the all-time top 20 is plausible: not dominated by one era, by ties or by Elo artifacts | Task 3.3 (moved from 7.2, review M5): the top 20 by `ALL_TIME_RANK` and by `LINE_ERA_RANK`, with source, season and tie counts; spot checks of well-known upsets in the top 100; on the restored Phase 2 snapshot, else synthetic data for the mechanics only. Repeated in 7.2 | Change the default (later, for example 1985, or earlier, for example 1936), logged; `LINE_ERA_RANK` unaffected |
| Q9 | Phase 3's `TEAM_GAME` exposes the opponent's team id, the team-side result and points as its spec describes (exact column names come from its deployed files) | Task 3.4 reads Phase 3's `sql/model/200_team_game.sql` | Compute the series from `GAME` with both orientations (a `UNION ALL` of home and away sides) |
| Q10 | Exasol's query cache may answer an identical repeated what-if faster; nothing relies on it | Probe 1.1(a): one warm repeat with the cache on | Nothing; Phase 6 may cache in the backend |
| Q11 | Phase 4's `SIM_TEAM_ODDS` probabilities have at most 6 decimals, so `DECIMAL(7,6)` holds them exactly | Task 3.5 reads Phase 4's `sql/sim/300_sim_team_odds.sql` | Freeze at the view's scale and widen the column, logged; movement and thresholds are unaffected |
| Q12 | ~~Generating the DDL with `LISTAGG`~~ Resolved by [39]: the Lua generator iterates the rows; the 900-column DDL builds within step `PUBLISH`'s budget | Probe 1.1(d) times it | Split the comment text from the column list, or shorten the comments, logged |
| Q13 | **Owner review (U3), not blocking:** the owner accepts the stored series instead of `/teams/matchup` for head-to-head ([14], [34]) | Owner review of this log when the roadmap is edited (task 6.3) | Add `CFB_API_LIVE.MATCHUP_LIVE` over `TEAMS_MATCHUP` (required `P_TEAM1`, `P_TEAM2`) with the fetching state; one call per click; app role only |
| Q14 | `make sim-drop` of the what-if publication's run is an operator error, refused by Phase 4 once hand-off P5-m13 lands | Documented in the user guide (task 6.1), the recorder note for `sim-engine/sim-runs`, and hand-off P5-m13 | `WHATIF_PUBLICATION` falls back to the newest publication whose run is `DONE`; `CURRENT_POINT.WHATIF_VIEWS_CURRENT` is FALSE and `WHATIF_GAMES` empty until `make analyses` or the next publication |
| Q15 | CFBD's game preview answers for a played game (the latest meeting), with its points and the recent meetings in `RAW_JSON` | Task 7.3: one of the 3 planned calls | The page offers the preview only for a meeting still to play, and the demo script fixes a rivalry-week pair; the optional `/teams/matchup` query then provides the stored-series check |
| Q16 | Exasol accepts `"SIM_ID" IN (SELECT …)` as a predicate inside `SUM(CASE …)` in the select list and evaluates it once | Probe 1.1(a) (statement accepted; a semi-join in the profile) | The `LEFT JOIN` flag shape; the library's unit tests pin whichever is adopted |
| Q17 | Phase 4 adopts hand-off P5-M3 (its per-run DDL escapes `'`, and its sim league has `Hawai'i`) before this plan's group B | Task 1.5's unit test on `sim_league()` | This plan's `with_apostrophe_school` variant supplies the school; if Phase 4's DDL does not escape, this plan's tests fail at Phase 4's `SIMULATE`, and the orchestrator routes the fix to Phase 4 |

### Status under the light plan (2026-10-23)

| # | Status |
|---|---|
| Q1, Q16 | moot: no conditional rooting guide ([50]) |
| Q2 | confirmed by Phase 6's probe S9 (a): views read with their owner's rights |
| Q3 | asserted by the roles test of task 1.1 after a redeploy |
| Q4 | avoided: `P_GAME_ID` keeps its name ([54]); see N1 |
| Q5, Q12, Q14, Q17 | moot: nothing is generated, published or dropped |
| Q6, Q15 | moved to Phase 6's live verification ([54]) |
| Q7 | moot: the guide is precomputed for every game |
| Q8 | task 2.3 looks at the all-time top 20 on AWS (not dominated by one era or by ties); the window stays 1978 unless it fails, logged |
| Q9 | confirmed by `docs/model-dictionary.md` (`TEAM_GAME.OPPONENT_ID`, `GAME_RESULT`, `POINTS_FOR`, `MARGIN`) |
| Q10 | moot |
| Q11 | Phase 4's types apply |
| Q13 | confirmed by the owner (2026-10-22) |

New open questions:

| # | Assumption | Verify step | If wrong |
|---|---|---|---|
| N1 | A literal filter on `CFB_API_LIVE.GAME_PREVIEW.P_GAME_ID` reaches the adapter as the required parameter, and the deploy's compile pass (`SELECT 1 FROM view WHERE 1 = 0`) on the view makes no request | Task 1.4's integration tests: one request per query, none during the deploy | Create the live views without `FORCE` (compiled at creation, which reads only metadata) and skip their compile query; if the filter itself is not pushed, the preview moves to Phase 6's operator path, logged |
| N2 | A literal filter on `AS_OF_SEASON` and `AS_OF_WEEK_SEQ` of a point-grain view is applied to `AS_OF_POINT` before the joins, so one point costs under 1 s on AWS | Tasks 1.2 (local) and 1.5 (AWS) time each view at one point | Rewrite the view so the point is the first join (a derived table of `AS_OF_POINT` in the `FROM` list); else Phase 6 queries `TEAM_TRAVEL` and `TEAM_GAMES` with literal filters (the snapshot SQL) and the view stays for the chat |
| N3 | Phase 4's hand-off column names hold as built | Phase 4's task 2.3 and this plan's equality tests in 2.1 and 2.2 | The serving view keeps the name in this plan's specs through an alias, logged |
| N4 | Phase 3's synthetic league has what the slice-A scenarios need (stored lines with underdog wins, an upcoming 2026 meeting, a team with a home and an away venue with coordinates and one without) | Tasks 1.2 and 1.3 | The test stores an extra fetch with `tests/it/mirror_edit.py` and removes it again |

## Review Findings

<!-- Populated by speq-implement after code review. -->


### Owner confirmation (2026-10-22)

- U3/Q13 confirmed by Nico: head-to-head uses the stored-games series (0 calls) plus a one-call `GAME_PREVIEW`. `/teams/matchup` is dropped.
