# Decision Log: add-cfb-analytical-model

Date: 2026-10-22 (planned); revised 2026-10-22 after the independent plan review (`review.md`)

## Interview

The owner (Nico) was away. He authorized the orchestrator to decide and log. The answers below are the orchestrator's, as passed to the planner.

**Q:** What is the scope?
**A:** Roadmap Phase 3 ("Analytical model"):
- Build `CFB_MODEL` on the stored `CFB_CORE` data, with era-aware dimensions and facts, coverage flags, `GEOGRAPHY` venues, as-of helpers, and `COMMENT ON` throughout.
- Done when an analysis can be written without touching `CFB_CORE`.
- It must serve Phase 4 (score model + engine), Phase 5 (`CFB_SIM`, `CFB_PRED`, head-to-head, `CFB_API` views) and Phase 6 (odds, predictions + scorecard, upsets and head-to-head pages).

**Q (1) Inputs:** What may the model read?
**A:** Only what the Phase 2 demo profile stores (about 217 calls of tables; `loader/load-plan`). Anything beyond that must be read through the VS on demand, or listed as a gap. `RANKINGS_POLLS_RANKS` carries its own `SEASON`/`SEASON_TYPE`/`WEEK`/`POLL` keys (foundation fix).

**Q (2) Implementation:** Views or tables, and how are they deployed?
**A:** Plain SQL views and tables in `CFB_MODEL`, deployed idempotently by a make target, as SQL files plus a small deployer that reuses `vs/host/target.py` and the deploy patterns. Decide which objects are views and which are materialized tables refreshed by a `REFRESH_STEPS` step. Prefer views unless performance or as-of semantics need materialization, and justify each.

**Q (3) Era awareness:** What must the model know about history?
**A:** College football runs from 1869:
- divisions and classifications;
- conferences changing over time;
- the postseason: bowls → BCS → CFP 4 → CFP 12 from 2024;
- data coverage by era (`docs/cfbd-data-availability.md`).

Coverage flags must say what is trustworthy per season.

**Q (4) As-of helpers:** What do they answer?
**A:** "State of the world as of season S, week W", for frozen weekly predictions and the replay "Sunday after week N". They must be leak-free: nothing after the as-of point.

**Q (5) Geography:** How are venues located?
**A:** Venue `GEOGRAPHY` from latitude and longitude. Check Exasol's spatial type support (`GEOMETRY` with SRID) with the `exasol-database` skill.

**Q (6) Comments:** How much documentation lives in the database?
**A:** `COMMENT ON` every table, view and column. The mission promises self-describing data for the Phase 7 chat over MCP.

**Q (constraints, from the orchestrator):** What limits apply to planning?
**A:**
- No DB sessions: a parallel session has an exclusive benchmark window on both DBs, and even a SELECT breaks it.
- No CFBD calls.
- Don't edit `docs/roadmap.md`, `specs/architecture.md` or `CLAUDE.md`; list the needed edits as tasks.
- Invoke the `exasol-database` skill before any Exasol SQL claim.
- Test tiers: unit tests (SQL lint and parse, deployer), and integration tests on local Personal in a `TEST_` namespace with the existing harness and synthetic data staged into `TEST_CFB_CORE`. Zero CFBD calls. Live verification runs on AWS after Phase 2's backfill and makes no calls.
- The foundation and Phase 2 must be implemented first.

**Q (review resolution, 2026-10-22):** How is each finding of the independent review resolved?
**A:** The orchestrator decided, for the away owner:
- **B1, accept.** Hide conference championship games, bowls and CFP rounds in the as-of views until their participants can be determined at the cutoff. Expose `IS_CONF_CHAMPIONSHIP`, `POSTSEASON_KIND` and `CFP_ROUND` in `GAME_AS_OF`. Make the invariance test's `mutate_after` swap participants, and add the new scenario. Check that the pregame Elo of later rounds cannot leak.
- **M1, accept.** One definition of the cutoff. A poll is visible by its week label, or when its `REQ_AT < CUTOFF_AT`. Extend Q2 to the selection-day ranking.
- **M2, accept.** Make Q6 concrete (Appalachian State 2007 and Liberty 2017 must be `fcs` in the games). Decide both branches now: if game codes are today's codes, take the classification from a seeded transitions table.
- **M3, accept.** A seeded reference of the conferences that hold a championship game, by season range. `NOTES` is a WARNING cross-check. Q7 covers 1992–2025.
- **M4, accept.** The materialization fallback also rebuilds in `make model` and after backfill and restore. An early performance smoke on a scaled synthetic league runs before the team-season and as-of work.
- **M5, accept.** Move the duplicate game to a variant. Give the base league a member in every power conference for each stored season. Fix Ashford's record.
- **M6, accept.** Reorder or split the Group C tasks to match the real read dependencies.
- **Minors:** fix all cheap ones, specifically: a conference-game flag (the engine needs `conf_game`); fixed-scale `DECIMAL` for engine-facing values (S3); the SP+ `nationalAverages` pseudo-team; store or derive the committee's `isFinal`; line coverage measured on FBS-vs-FBS games only; deduplicate every slice-scoped source; fix the `CURRENT_AS_OF` Thursday edge case; `CREATE OR REPLACE FORCE VIEW` if the skills confirm it; lint for unquoted upper-case identifiers; update the catalog hash reference to `vs/catalog/catalog.json` `catalog_hash`.
- **YAGNI:** drop `TRAVEL_KM` and its scenario; drop the per-season coverage rows of on-demand datasets; keep `CONFERENCE_SEASON` only if Phase 4/5 needs conference standings, otherwise drop it.
- **New input:** the engine spikes are done (`docs/spikes/s3-python-engine-throughput.md` → D19, `docs/spikes/s4-sim-storage.md` → D20, `specs/architecture.md` § Engine contract). The model must provide exactly what the contract's Input needs, with no gaps and no extras.
- **Constraints of the revision:** no DB sessions, no CFBD calls, no edits outside this plan's directory, `speq plan validate` warning-free if feasible, and a "Review resolution" section in `review.md`.

## Design Decisions

### [1] The model covers the stored demo data only; everything else is named, not wrapped

- **Decision:** `CFB_MODEL` reads only the `demo` profile tables (`GAMES`, `METRICS_WP_PREGAME`, `RATINGS_SP`, `RANKINGS_POLLS_RANKS`, `CALENDAR`, `CONFERENCES_AFFILIATIONS`, `TEAMS`, `VENUES`, `CONFERENCES`).
  - Datasets the architecture lists but the demo does not store (players, coaches, plays, drives, box scores, full lines, SRS, FPI, recruiting, transfers, draft, rosters, matchup, preview, weather) appear in the reference table `DATASET` as `ON_DEMAND` or `UNAVAILABLE`, with their CFBD endpoint, declared seasons and `CFBD_VS` table.
  - No model object reads `CFBD_VS`.
- **Alternatives:**
  - Pass-through views over `CFBD_VS` in the model: a model object could then trigger API calls, and the architecture gives on-demand access to `CFB_API` views that carry the required filters.
  - Silent omission: the chat could not explain a gap.
- **Rationale:** "The model costs zero calls" stays provable (lint + dependency test). The gap list is data the chat can read.
- **Promotes to ADR:** yes

### [2] Every derived model object is a view; a measured fallback with rebuild hooks, decided early

- **Decision:** dimensions, facts, coverage, as-of helpers and checks are views over `CFB_CORE`, the loader's plan views and other model objects. The only tables are seven seeded reference tables.
  - A scaled synthetic league (about 250,000 games) measures the `GAME` stack and a prototype of the as-of join before the team-season and as-of views are built (task 3.7), and the full model again in 4.3 and on AWS in 7.3.
  - A view more than 2× over its target is first restructured. Only if that fails does it become a table, rebuilt by every `make model`, by `REFRESH_MODEL` before its checks, and by a post-hook that `make backfill` and `make restore` call; a `MATERIALIZED_STALE` ERROR check catches a backfill run directly through `EXECUTE SCRIPT`.
- **Revision (review M4):** the earlier text let the `MODEL` step alone rebuild a materialized table, which contradicted this decision's own rationale (backfill and restore run no steps), and decided only at the end, after the as-of views existed.
- **Alternatives:**
  - Materialize `TEAM_SEASON`, `GAME` or as-of snapshots in a `REFRESH_STEPS` step: `BACKFILL` and `make restore` run no refresh steps, so materialized tables would go stale after them, and every phase would have to remember a rebuild.
  - Materialize "for safety": no measured need. All history is about 250k games.
- **Rationale:** always consistent with the stored data; no rebuild order to get wrong; small data for Exasol. The fallback, if ever needed, covers every path that changes the data, and the early smoke keeps a redesign before the dependent views exist.
- **Promotes to ADR:** yes

### [3] Curated era knowledge lives in seeded reference tables

- **Decision:** `ERA`, `CLASSIFICATION_LABEL`, `CONFERENCE_TIER`, `CONFERENCE_CHAMPIONSHIP`, `POLL`, `DATASET` and `CLASSIFICATION_TRANSITION` are defined with their rows in `sql/model/01x_*.sql`, and replaced on every deploy.
- **Alternatives:** constants inside views (not inspectable by the chat); a YAML file compiled at deploy (one more format, no benefit).
- **Rationale:** reviewed in pull requests, queryable and commented, and identical in every deployment.
- **Promotes to ADR:** no

### [4] As-of visibility has one horizon: event time or fetch time against the next kickoff

- **Decision:** an as-of point means "everything known after that week's games and before the next week's first kickoff" (`CUTOFF_AT`). "The Sunday after week N" is a name for that point, not a second horizon.
  - Event facts become visible when they happen: results, completed flags, postgame values (weeks ≤ P), pregame Elo (weeks ≤ the next week), polls (`VISIBLE_AT_WEEK_SEQ` ≤ P), and the previous season's SP+.
  - Values CFBD overwrites during a season become visible from their fetch time (`REQ_AT` < `CUTOFF_AT`): the current season's SP+, next-week lines, and the current season's polls. A slice fetched before the cutoff can only hold values released before it.
  - Games whose participants depend on results follow [21].
  - An invariance test proves that no fact after the point changes an as-of answer.
- **Revision (review M1):** polls used "Sunday after the week" while SP+ and lines used the next kickoff. With the committee lead of 0, a Tuesday committee ranking became visible one week late, so the live demo would show last week's ranking although this week's was stored.
- **Alternatives:**
  - Week labels only: this would leak historic closing lines and end-of-season SP+ into mid-season points.
  - Snapshot tables per week ([7]).
  - "Sunday after week N" for everything: Wednesday lines and Tuesday rankings would be invisible at the current point, and frozen predictions are made before kickoff.
- **Rationale:** one definition everywhere, the one Phase 5's frozen predictions need; leak-free without history tables; explainable per column; testable as a property.
- **Promotes to ADR:** yes

### [5] Points are addressed by a week sequence that matches `REFRESH_WEEK`

- **Decision:** `WEEK_SEQ`: 0 for the preseason, n for regular week n, and 100 + n for postseason week n. `AS_OF_POINT` maps `(season, week, season_type)` 1:1, the same arguments `REFRESH_WEEK` and its registered steps receive.
- **Alternatives:** a calendar ordinal (needs a calendar, which is stored only from 2013); a timestamp cutoff (historic games have date-only kickoffs, and pages think in weeks).
- **Rationale:** deterministic for every season since 1869, and it lines up with Phase 2's step contract. Known limit: as-of points inside the postseason are no finer than CFBD's postseason weeks (Q3); with one postseason week, the whole postseason becomes known at once at P = 101.
- **Promotes to ADR:** yes

### [6] As-of views hold only the point's season

- **Decision:** `GAME_AS_OF`, `TEAM_SEASON_AS_OF` and `RANKING_AS_OF` join each point only to its own season. Earlier seasons come from `GAME`, `TEAM_GAME` and `TEAM_SEASON`, which are fully known.
- **Alternatives:** include every earlier season in each point. An unfiltered query would expand to about 2,700 points × 250k games.
- **Rationale:** a careless chat query stays bounded, and the semantics stay unchanged.
- **Promotes to ADR:** no

### [7] No in-season snapshot history

- **Decision:** the model keeps no per-week history of current-season SP+ or lines.
  - A replay of an earlier week of the current season shows them as NULL. The latest fetch is visible only from its fetch time.
  - The leak-free prior for season S is the SP+ of S − 1 ([24]), available from 2014, which covers Phase 4's 2014–2025 calibration.
  - Phase 5's frozen predictions keep the inputs they used.
- **Alternatives:** an append-only snapshot table written by the `MODEL` step. It costs storage and refresh logic for a replay case the demo does not need, and it would be lost on restore unless snapshots were extended. Moved to the backlog (task 6.3).
- **Rationale:** YAGNI with a clear upgrade path.
- **Promotes to ADR:** yes

### [8] Poll visibility: label rule or fetch time; the committee's selection ranking before the postseason

- **Decision:**
  - `POLL.LABEL_LEAD_WEEKS`: AP 1, Coaches 1, committee 0, unknown polls 0. A regular-season poll becomes visible at its `WEEK_SEQ` minus the lead. A lead that is too small only delays visibility; one that is too large would leak, so the committee keeps 0 until Q2 shows how CFBD labels it.
  - Postseason-labeled polls are visible at their own `WEEK_SEQ`, except polls whose final ranking comes before the postseason (`POLL.FINAL_BEFORE_POSTSEASON`, the committee): visible at the season's last regular week.
  - The as-of views also show any ranking whose `REQ_AT` is before `CUTOFF_AT` ([4]). Historic seasons, fetched in 2026, use the label rule only.
- **Revision (review M1):** the earlier rule justified lead 0 with "released on Tuesdays, after the Sunday point", which [4] no longer has, and could hide the selection-day ranking at selection Sunday.
- **Alternatives:** release timestamps (CFBD has none); one rule for all polls; lead −1 for the committee (would leak if CFBD labels by the completed week).
- **Rationale:** leak-free by default, the live demo sees a Tuesday ranking at once, and the selection ranking is visible when it was released whatever its label. Adjustable in one seed row after verification (Q1, Q2).
- **Promotes to ADR:** no

### [9] Team-season membership and classification

- **Decision:**
  - Affiliation spells expand only over the seasons their slice requested (`P_YEAR`, or `P_MIN_YEAR`–`P_MAX_YEAR`). The newest fetched slice wins, and same-slice conflicts take the later `START_YEAR` and are flagged.
  - Without a membership, the conference comes from the team's games with `FIXED` participants.
  - Classification: a `CLASSIFICATION_TRANSITION` seed row for the team and season first, then the code on most of the team's `FIXED` games, then the membership. `GAME` applies the same seed to each team's code on a game, so `INVOLVES_FBS`, the coverage populations and the engine's FBS team set agree.
  - Both outcomes of Q6 are decided now. If CFBD's games carry the code of their time (Appalachian State 2007 and Liberty 2017 recorded as `fcs`), the seed stays empty and the games decide. If they carry today's code, the seed lists every team and season range since 1978 whose classification differs, reviewed like the tier table. Q6 changes seed rows only, never the views, so it can run whenever stored data first exists.
- **Revision (review M2):** "games first" contradicted the plan's own premise that CFBD stamps today's codes; in transition years, games would then be the worst source. Game-derived attributes now use only games with fixed participants ([21]), so a swapped championship participant cannot change them.
- **Alternatives:**
  - Trust open spells of the static 1869–2025 range slice: they would claim later seasons after a move.
  - The affiliation spell's classification first (the review's branch B): a spell's code may be the conference's classification of today (the Western Athletic is FCS today), so it can be as wrong as the games.
  - Decide the code path only after Q6: the stored data may arrive after the team-season view is built, which would force a late redesign.
- **Rationale:** deterministic answers that survive realignment, with the source recorded per row, and one code path for both Q6 outcomes.
- **Promotes to ADR:** no

### [10] Conference tiers are a reviewed reference keyed by CFBD names, guarded by an error check

- **Decision:** `CONFERENCE_TIER` has the following rows:
  - AQ 1998–2013, P5 2014–2023, P4 2024–;
  - `INDEPENDENT` for FBS Independents;
  - NULL-name defaults `NON_AQ`, `G5`, `G6` for the other FBS conferences.

  `POWER_CONFERENCE_MISSING` (ERROR) fails a season in which a power name of the table matches no team-season. The synthetic league has a member in every power conference of every stored season from 1998, so its happy-path tests pass (review M5).
- **Alternatives:** derive "power" from rankings or results (wrong: it is an era rule); hard-code it in Phase 4 (hidden from analyses and the chat).
- **Rationale:** the 2026 auto-bids depend on it. A CFBD rename must stop the refresh, not silently change the playoff field.
- **Promotes to ADR:** no

### [11] Conference championship games are detected inside seeded championship seasons

- **Decision:**
  - The reference table `CONFERENCE_CHAMPIONSHIP` lists per CFBD conference name the season ranges with a championship game: SEC 1992–, Western Athletic 1996–1998, Big 12 1996–2010 and 2017–, Mid-American 1997–, ACC and Conference USA 2005–, Big Ten 2011–, Pac-12 2011–2023, Mountain West 2013–, American Athletic 2015–, Sun Belt 2018–.
  - Inside a range of conference C, `CONFERENCE_CHAMPIONSHIP_GAME` takes the regular-season game between two teams recorded in C and flagged as a conference game that is C's only such game in its week, in C's last regular-season week with such a game. It is its own small view, joined into `GAME` by id.
  - `NOTES` is a cross-check only: `CONF_CHAMPIONSHIP_NOTES_MISMATCH` and `CONF_CHAMPIONSHIP_MISSING` are warnings.
  - Q7 verifies one game per conference and season for 1992–2025. If CFBD does not flag championship games as conference games, or flags Army–Navy-type games after them, the pre-decided fallback is: the only C-vs-C game in the first week after C's last week with two or more C-vs-C games. Seasons that stay wrong (2020 is likely) are listed in `docs/model-verification.md`, and the warnings name them.
  - Champions of conferences without a championship game come from standings: Phase 4's job, stated in `cfp_field_inputs.sql`.
  - Only FBS conferences are seeded; lower-division championship games are not detected (known limit, [21]).
- **Revision (X1, implementation):** detection looks only at weeks up to the season's power-conference championship week ([32]).
- **Revision (review M3):** the schedule shape alone had false positives (conferences without a championship game and one isolated final-week game), false negatives (a same-conference game after the championship week) and was verified only for 2014–2025, while the CFP auto-bids need every champion.
- **Alternatives:** the schedule shape alone (above); `NOTES` text as the rule (not known to be populated); "the calendar's last regular week" (Army–Navy-type weeks and seasons in progress defeat it).
- **Rationale:** the seed removes the false positives at their root, the conference flag skips non-counting games, and two warnings report what the rule still misses.
- **Promotes to ADR:** no

### [12] Venues use `GEOMETRY(4326)`; distances come from a great-circle function; no travel distance

- **Decision:** `VENUE.GEO_POINT` is `GEOMETRY(4326)` (x = longitude, y = latitude). `GREAT_CIRCLE_KM` (haversine, R = 6,371.0088 km) gives kilometres for ad-hoc questions. `TEAM_GAME.TRAVEL_KM` is dropped.
- **Revision (review m14, orchestrator YAGNI):** no v1 page or engine input uses travel; the engine has a constant home-field edge. The function stays because it is what makes a `GEOMETRY(4326)` point answer a distance in km, and it costs one file and one scenario. Probe 1.1d drops its 1M-row benchmark.
- **Alternatives:**
  - `GEOGRAPHY`: Exasol has no such type (skill data types, documentation).
  - `ST_DISTANCE`: it computes in the plane of the coordinates, and its unit on SRID 4326 is undocumented (probe 1.1c).
  - `ST_TRANSFORM` to a metric CRS: it depends on `EXA_SPATIAL_REF_SYS` contents and distorts over long distances.
  - Drop the function too: the chat would lose the only correct distance.
- **Rationale:** the only spatial type, correct distances, still usable with Exasol's `ST_*` functions. The mission's "GEOGRAPHY" wording changes to "GEOMETRY(4326)" (tasks 6.3, 6.4).
- **Promotes to ADR:** yes

### [13] Comments inline, grain first, unquoted upper-case names, verified after deploy

- **Decision:**
  - Views carry their comments inline in `CREATE OR REPLACE FORCE VIEW`; tables do the same in `CREATE OR REPLACE TABLE`.
  - Every table and view comment starts with "One row per …".
  - DDL object and column names are unquoted, upper case and not reserved; the lint rejects anything else. Query bodies quote every identifier.
  - The deploy compiles every view and reads the comments back, and fails below 100 %.
  - `docs/model-dictionary.md` is generated from the SQL files.
- **Revision (review m15):** "impossible for views" became "not in the grammar for views (`COMMENT ON [TABLE] <view>` may work, probe 1.1a); inline is atomic". The skill rule "never use quoted identifiers in DDL statements" is followed instead of quoting upper-case names.
- **Alternatives:** `COMMENT ON` after creation (not in the grammar for views, and not atomic with a replace); quoted DDL names (harmless only while upper case, against the skill rule); free-form comments.
- **Rationale:** the chat and analysts get the grain without guessing, and plain SQL works without quoting.
- **Promotes to ADR:** yes

### [14] A generic one-transaction SQL-object deployer

- **Decision:** `vs/host/sqlobjects.py` (load, lint, render, deploy) deploys a directory of object files in one transaction under the loader's run lock, creates views with `FORCE`, drops orphans in the target schema, compiles every view and reads comments back. `model/deploy.py` binds it to `sql/model/`. Phases 4–5 reuse it for `CFB_SIM`, `CFB_PRED` and `CFB_API`.
- **Revision (review m15, m19):** `CREATE OR REPLACE FORCE VIEW` is in the skill grammar (`create_view_statement`), so a view may reference a later file; probe 1.1f verifies the semantics, with plain views plus an order lint as the fallback. The deploy joins the run lock ([29]).
- **Alternatives:** per-phase scripts; `CREATE` without dropping orphans (stale views would mislead the chat); plain views in file order (fragile).
- **Rationale:** atomic, idempotent and lint-gated deploys, written once. If probe 1.1b shows that DDL is not transactional, the fallback deploys to a validation schema first.
- **Promotes to ADR:** yes

### [15] The `MODEL` refresh step checks; it does not rebuild

- **Decision:** step 100 `MODEL` = `CFB_INFRA.REFRESH_MODEL(run_id, season, week, season_type)`. It evaluates `MODEL_CHECKS` for the season and the week, logs them to `CFB_META.MODEL_CHECK_LOG`, commits, and fails on any failed ERROR check. `make model-check` prints the same checks without the step.
  - The nested `COMMIT` ends the caller's transaction too; that is harmless because the refresh commits its step row before a step starts (`loader/refresh-week`, logging; review m16).
  - Each check filters by season before it aggregates (probe 1.1g, review m17).
- **Alternatives:** no step (renames and broken inputs would reach the simulations); a rebuild step (nothing to rebuild, [2]).
- **Rationale:** the timeline shows "model" as the mission describes, and the chain stops before Phases 4–5 consume bad inputs.
- **Promotes to ADR:** yes

### [16] The model reads the loader's plan views for coverage

- **Decision:** model views may read `CFB_META.LOAD_PLAN`, `LOAD_PLAN_GAPS` and `LOAD_SLICES` (planned, stored, final, gap), and nothing else in `CFB_META`. `specs/architecture.md` is updated (task 6.2).
- **Alternatives:** derive "stored" from mirror rows (empty and gap slices have none, and finality is not in the rows); copy load state into the model.
- **Rationale:** one owner per contract. Coverage tells "not loaded yet" from "does not exist".
- **Promotes to ADR:** yes

### [17] The test leagues go through the real flattener and are inserted directly

- **Decision:** `tests/synthetic/league.py` defines API-shaped bodies with the specs' facts. The foundation's flattener turns them into mirror rows, which the harness inserts directly into `TEST_CFB_CORE` with `LOAD_LOG` rows. The same bodies serve as replay recordings for the refresh-step test. One generator-shape test also stages `fake.py` responses through the virtual schema, for four seasons only.
  - `tests/synthetic/scaled_league.py` replicates the league to about 250,000 games for the `PERF_` namespace ([2]).
  - No ERROR check fails on the base league in any season, guarded by a unit test that mirrors the ERROR rules and an integration test; the duplicate game moved to the `with_duplicate_rows` variant, which runs no loader step (review M5).
  - Read-only test modules seed once; the variants module and the refresh-step module re-seed before every test (review m19).
- **Alternatives:** hand-written mirror rows (drift from real shapes); staging every league slice through the virtual schema per test (slow).
- **Rationale:** faithful rows, fast tests, one source of truth for the facts, and no state leaking between test modules.
- **Promotes to ADR:** no

### [18] Head-to-head can come from stored games (recommendation to Phase 5)

- **Decision:** `TEAM_GAME` makes the all-time series between two teams computable at zero calls, from games stored since 1869 (`head_to_head.sql`). Phase 5 decides whether the page uses it, with `/teams/matchup` kept for the "live" beat, or keeps D13's on-demand matchup.
- **Alternatives:** none decided here.
- **Rationale:** offline and replay-safe, and no per-click call. It is recorded so that Phase 5 does not rediscover it.
- **Promotes to ADR:** no

### [19] The current point is derived from the data: the newest finished week

- **Decision:** `CURRENT_AS_OF` is, in the current season, the newest week with `SEASON_WEEK.IS_DONE` (all games completed, or the season's games were last fetched at least a day after the week ended), or the preseason point before any week is done. No state table.
- **Revision (review m2):** "the newest week with a completed game" jumped to week 10 as soon as its Thursday game completed, leaving the Saturday games of week 10 `NOT_PLAYED` inside the point.
- **Alternatives:** a row written by the `MODEL` step (after a restore, the rehearsal reset, it would point at the wrong week); "all games completed" alone (a postponed game would keep a past week open forever).
- **Rationale:** always matches the stored data. Pages can pass any other point.
- **Promotes to ADR:** no

### [20] Example analyses are the done-when proof

- **Decision:** `sql/model/examples/` holds four analyses (engine inputs as of a week, all-time upsets, head-to-head, CFP field inputs). They read only `CFB_MODEL` and run as integration tests with expected answers.
- **Alternatives:** a reviewer's judgement of "analysis-ready".
- **Rationale:** the roadmap's criterion becomes executable, and the examples double as documentation.
- **Promotes to ADR:** no

### [21] Games whose participants depend on results are hidden until their participants are known

- **Decision:** `GAME.PARTICIPANTS_RULE` says when a game's teams were known, and `GAME_AS_OF` has no row for the game before that point:
  - `CONF_CHAMPIONSHIP`: from the point after the week before the game (the game is `NEXT`);
  - `SELECTION` (bowls with an FBS team, the season's first CFP round by kickoff order): from the season's last regular week;
  - `PREVIOUS_ROUND` (later CFP rounds, every postseason game without an FBS team): from its own week, so never as `NEXT` or `REMAINING` while CFBD has one postseason week;
  - `FIXED` (every other game, placeholders included): always.

  `GAME_AS_OF` exposes `IS_CONF_CHAMPIONSHIP`, `POSTSEASON_KIND`, `CFP_ROUND` and `PARTICIPANTS_RULE`, so Phase 4 can drop what the engine simulates. `TEAM_SEASON_AS_OF` counts remaining and next games over `GAME_AS_OF` only, and game-derived conference and classification use `FIXED` games only. The invariance test swaps the participants of the 2024 SEC championship game and quarterfinal and adds a bowl.
- **Pregame Elo:** a hidden game shows no pregame Elo, so the quarterfinal's Elo (which encodes the first-round result) cannot leak; a `SELECTION` game's pregame Elo depends only on regular-season games; `ELO_AS_OF` uses completed games up to the point only.
- **Known limit:** lower-division conference championship games are not detected ([11]), so their participants show early. Games with an FBS team are unaffected.
- **Alternatives:**
  - Rows with NULL teams and the status `TBD`: an added or cancelled bowl would still change the row set, which leaks; Phase 4 needs no placeholder rows, because the engine simulates the championships and the bracket from its own structure.
  - Always show the schedule (the original rule): leaks championship pairings, the playoff field and seeds into every historic point (review B1, blocker).
  - Hide all postseason games until P = 101: delays bowls and the first round, which are known after selection and are part of the "Sunday after the last week" picture.
- **Rationale:** the Phase 4 backtest of the 2024/2025 fields must not see the real pairings; rows that do not exist cannot leak their teams, existence or Elo.
- **Promotes to ADR:** yes

### [22] Engine-facing real values are fixed-scale `DECIMAL`

- **Decision:** every real value that the score model or the engine reads is cast once in the model: spreads `DECIMAL(6,2)`, win probabilities `DECIMAL(5,4)`, SP+ overall, offense, defense and special-teams ratings `DECIMAL(6,2)`. Points, Elo and ranks are integers already. Other real values (SP+ parts, postgame win probability, excitement, coordinates) stay as CFBD reports them. Probe 1.1i verifies that the cast rounds; the fallback is `ROUND(x, s)` first.
- **Alternatives:** keep `DOUBLE` and let Phase 4 cast (every consumer would have to remember it); `DECIMAL` everywhere (no consumer needs it for the parts).
- **Rationale:** the engine contract requires it (S3: `CAST('-0.6' AS DOUBLE)` is 1 ulp off), and one cast in the model keeps host checks, the UDF and every page bit-identical.
- **Promotes to ADR:** yes

### [23] The newest fetch wins for every slice-scoped source; duplicates are a warning

- **Decision:** games, pregame lines, SP+ (season and team name), rankings (season, type, week, poll, team; ties of fetch time broken by `POLLS_NO`), calendar weeks, teams, venues and conferences are deduplicated by their natural key, newest `REQ_AT` first. `DUPLICATE_SOURCE_ROW` (WARNING, replacing the ERROR `DUPLICATE_GAME`) reports fetched duplicates of the season-scoped sources.
- **Alternatives:** deduplicate games only (a promoted team or week slice would break the grains of `RATING_SP` and `RANKING`); duplicates as an ERROR (would stop every refresh after a promotion, although the views already resolve them).
- **Rationale:** `PROMOTE()` will make overlapping slices normal; the grain must hold regardless.
- **Promotes to ADR:** yes

### [24] The prior is the previous season's SP+ as last fetched, final or not

- **Decision:** `SP_PRIOR_RATING` takes the previous season's stored SP+ whatever its finality, with `SP_PRIOR_IS_FINAL` in `TEAM_SEASON` (not in the as-of view). `SP_PRIOR_NOT_FINAL` (WARNING) reports a season whose priors come from a slice that is not final. The user guide explains that a backfill after 15 February makes it final, and that historic SP+, Elo and win probability may embed later methodology.
- **Alternatives:** require a final slice (the review's reading): if no backfill ran between 15 February and the season start, every prior of the new season would be NULL, and the engine would lose its prior.
- **Rationale:** a previous season is in the past at every point of a season, so the prior never leaks; finality is a quality signal, not a visibility rule. The invariance test can flip the previous season's finality without changing an answer (review m18).
- **Promotes to ADR:** no

### [25] Conference games come from CFBD's flag; conference records leave out championship games

- **Decision:** `IS_CONFERENCE_GAME` (CFBD `conferenceGame`) is on `GAME`, `TEAM_GAME` and `GAME_AS_OF`. Conference records and `CONF_POINT_DIFF` count completed flagged games without conference championship games; overall records count every completed game. `CONFERENCE_GAME_MISMATCH` (WARNING) reports flagged games between teams of different conferences, which the engine would reject.
- **Alternatives:** equal conference names on both teams (counts non-counting games such as Army–Navy 2024 and misses realignment-year flags); include the championship game in conference records (the engine's standings would double-count it).
- **Rationale:** the engine contract needs `conf_game`, and standings must rank the regular conference season.
- **Promotes to ADR:** no

### [26] The committee's final ranking is derived

- **Decision:** the stored ranks do not carry CFBD's `isFinal` (Phase 2 [27]). `IS_FINAL_POLL` is TRUE for postseason-labeled polls released after the postseason, and for the committee's last ranking of a season whose regular weeks are all done. `FINAL_CFP_RANK` uses it, so a season in progress has none. Q2 checks that (poll, season, type, week, team) is unique for the committee; if it is not, `POLLS_NO` decides, and the foundation is asked for an `IS_FINAL` parent key like `SEASON_TYPE`.
- **Alternatives:** store `IS_FINAL` (needs a foundation and Phase 2 change outside this plan); "the last committee ranking" without the done condition (would call a mid-season ranking final).
- **Rationale:** correct for every finished season, conservative for the current one, and no change to other plans.
- **Promotes to ADR:** no

### [27] Coverage: stored datasets only, FBS-vs-FBS lines, a per-dataset threshold

- **Decision:** `SEASON_COVERAGE` has rows only for stored season-scoped datasets; `DATASET` describes on-demand and unavailable datasets (declared seasons, gaps, endpoint, virtual table). `PREGAME_WP` is measured on completed FBS-vs-FBS games. `DATASET.COMPLETE_SHARE` (0.95) is the threshold per dataset, calibrated by Q13.
- **Alternatives:** per-season rows for the 14 on-demand datasets × 158 seasons (add nothing the chat cannot derive from `DATASET`); every FBS-involved game as the lines population (CFBD omits games without a spread, so every season would be `PARTIAL`).
- **Rationale:** smaller and truthful coverage; calibration changes a seed row, not a view.
- **Promotes to ADR:** no

### [28] No conference-season view

- **Decision:** `CONFERENCE_SEASON` is dropped. Members, divisions and counts per conference and season are a `GROUP BY` over `TEAM_SEASON`.
- **Alternatives:** keep it for conference standings.
- **Rationale:** the orchestrator kept it only if Phase 4/5 needs conference standings. They do not: the engine ranks standings per sim from its `TEAM` rows (S3), Phase 5's pages show playoff odds, not standings, and champions of conferences without a championship game are Phase 4's job. The engine's validation (a conference with fewer than two teams) is a `GROUP BY` over the engine's own input.
- **Promotes to ADR:** no

### [29] The model deploy joins the loader's run lock

- **Decision:** `make model` refuses to start while a loader run, snapshot or restore holds the run lock (`loader/run-logs`), and holds the lock while it deploys.
- **Alternatives:** deploy any time (replacing reference tables while a `REFRESH_WEEK` reads them causes a transaction conflict, and Exasol rolls one side back).
- **Rationale:** no conflict with a refresh in the middle of a demo, at the cost of one lock call (review m19).
- **Promotes to ADR:** no

### [30] Placeholder games stay in `GAME`

- **Decision:** a game that CFBD lists before naming its teams stays in `GAME` with `HAS_TEAMS` FALSE and `PARTICIPANTS_RULE` `FIXED`; `TEAM_GAME` has no row for an unnamed side. No check reports placeholders, because they are normal before championship week and selection.
- **Alternatives:** drop them (the chat could not say "SEC championship, teams TBD"); an INFO check (noise every season).
- **Rationale:** a placeholder reveals only a date and a venue, so it is leak-free, and Phase 4 filters on `HAS_TEAMS` (review m12).
- **Promotes to ADR:** no

### [31] The model provides the engine's data inputs; Phase 4 computes the rest

- **Decision:** `GAME_AS_OF` and `TEAM_SEASON_AS_OF` hold every data input of `specs/architecture.md` § Engine contract (the mapping in plan.md, Key Interfaces): neutral site, conference flag, final conference-game margins, team, conference, tier, records, conference records, conference point differential. Phase 4 computes indices, μ, σ, totals, ratings, strength of schedule and the capped point differential (with its own `margin_cap`). Nothing in the model exists only for the engine: `TRAVEL_KM` and `CONFERENCE_SEASON` are dropped ([12], [28]).
- **Alternatives:** an engine-shaped `MODEL_ROWS` view in the model (couples the model to engine parameters and to an unsettled Phase 4 rule set); capped differentials in the model (duplicates the `margin_cap` parameter).
- **Rationale:** the model stays general and the engine owns its parameters, while no input the engine needs from data is missing.
- **Promotes to ADR:** yes

### [32] Championship detection stops at the season's power-conference championship week (cross-plan X1)

- **Context:** Phase 4's revision (its Q15, relayed as X1 on 2026-10-22) found on the stored 2026 schedule, at zero calls: weeks 1–13, then Army–Navy alone in week 15 (12 December), flagged as an American Athletic conference game, and no week-14 rows until the championship pairings are set. Rule [11] takes Army–Navy for the American Athletic's championship game and, once the real title game is stored in week 14, misses it. That game would then enter 2026 runs as a regular conference game and stop them with `CONFERENCE_PAIR_REPEATED` from the week-13 point on (late November).
- **Decision:**
  - `CONFERENCE_CHAMPIONSHIP_GAME` looks only at weeks up to `POWER_CHAMPIONSHIP_WEEK`: the last regular-season week in which a conference that `CONFERENCE_TIER` marks as power for the season plays a flagged conference game between two of its teams. Inside that limit, rule [11] is unchanged.
  - A season without such a game has no limit. That covers 1992–1997, before the tier seed, when Army and Navy were independents.
  - The view exposes the week, so the chat and `MODEL_CHECKS` can explain a miss.
  - New scenario "A conference game after the power conferences' championship week is not taken for a championship game", on the league's 2025 season: power championship week 14, then Marlow vs Jessup, a flagged American Athletic game alone in week 15. Test `test_conference_game_after_power_championship_week_not_marked`. The league oracle `tests/synthetic/rules.py` applies the same limit; a unit test shows that the rule without it takes the week-15 game.
- **Why it holds on real data:**
  - Power conferences play no regular conference game after their title games. So once their championship games are stored, the limit is their championship week, and every seeded conference's title game falls in it or earlier as long as the conferences hold them on one weekend. Q7 checks that on the stored seasons 1992–2025.
  - Before the pairings are stored (2026 now), the limit is the power conferences' last scheduled week (13). Army–Navy is still excluded, and nothing is detected for the American Athletic until its title game appears. `CONF_CHAMPIONSHIP_MISSING` skips the current season.
  - A power conference's own title game is never cut, because the limit is at least its week. Phase 4's `SELECTION_WEEK` (power championship games) is unchanged.
- **Leak check:** the limit depends on the schedule (weeks, flags, recorded conferences), not on results. `mutate_after` changes results and swaps title-game participants within their conference, which leaves the limit and the detection unchanged.
- **Alternatives:**
  - The last championship game of any conference: circular, and it gives week 15, the Army–Navy week, again (Phase 4 [37]).
  - Leave Army–Navy out by team names or notes: brittle data knowledge, and CFBD's notes are not reliable (the reason for [11]).
  - The calendar's last regular week: on real data that is the Army–Navy week.
  - The first Saturday of December: Phase 4's fallback for its selection week, but the CFBD week of the title games moves (week 15 in 2024, week 14 in 2026).
- **Known limit:** a seeded non-power conference whose title game comes after every power conference's last conference game would be missed. Q7 would show it, and `CONF_CHAMPIONSHIP_MISSING` names it.
- **Promotes to ADR:** no

## Open Questions (assumption + verify step; all verified in task 7.2 at zero calls)

| # | Assumption | Verify step (on AWS after Phase 2's backfill) | If wrong |
|---|---|---|---|
| Q1 | CFBD labels the preseason AP poll as regular week 1, and the poll released after week W as week W + 1 | `SELECT POLL_RANK, SCHOOL FROM CFB_MODEL.RANKING WHERE SEASON = 2024 AND POLL_CODE = 'AP' AND SEASON_TYPE = 'regular' AND WEEK IN (1, 2) AND POLL_RANK <= 5`: week 1 must be the preseason order (Georgia, Ohio State, Oregon, …), and week 2 must reflect week-1 results | Set the AP and Coaches `LABEL_LEAD_WEEKS` to 0 |
| Q2 | Committee labels: lead 0 is safe whatever CFBD does; the selection ranking is visible at the last regular week; (poll, season, type, week, team) is unique | (a) The first 2024 committee ranking (released 2024-11-05, after week-10 games) has label 10 or 11. (b) The 2024 and 2025 selection rankings (released 2024-12-08 and 2025-12-07): regular label or postseason week 1, and `VISIBLE_AT_WEEK_SEQ` equals the last regular week. (c) `SELECT SEASON, SEASON_TYPE, WEEK, TEAM_ID, COUNT(*) FROM CFB_CORE.RANKINGS_POLLS_RANKS WHERE POLL = 'Playoff Committee Rankings' GROUP BY 1, 2, 3, 4 HAVING COUNT(*) > 1` is empty | (a) Label 11 → committee lead 1. (b) A regular label after the last regular week, or postseason → nothing to do (the final-before-postseason rule covers it); a selection ranking invisible at the last regular week → fix the lead. (c) Duplicates → `POLLS_NO` decides (verify it orders the final snapshot last) and recommend an `IS_FINAL` parent key to the foundation |
| Q3 | CFBD puts all bowls and CFP rounds into postseason week 1, and fills `playoff.competition` = `cfp` and `playoff.round` for 2014–2025 | `SELECT SEASON_TYPE, WEEK, START_AT, END_AT FROM CFB_MODEL.SEASON_WEEK WHERE SEASON = 2024`; `SELECT SEASON, POSTSEASON_KIND, CFP_ROUND, CFP_ROUND_SEQ, COUNT(*) FROM CFB_MODEL.GAME WHERE SEASON BETWEEN 2014 AND 2025 AND SEASON_TYPE = 'postseason' AND INVOLVES_FBS GROUP BY 1, 2, 3, 4` | One postseason week: record it as Phase 4/5's open item (as-of inside the postseason by date). Several weeks: `PREVIOUS_ROUND` games appear from their own week, nothing to do. `cfp` missing for 2014–2023: the season's last FBS postseason game of the `CFP_4` era is the final (`PREVIOUS_ROUND`), the semifinals stay `SELECTION` |
| Q4 | `/conferences/affiliations` returns spells (team, conference, division, start, end), split on division changes, and the range call returns spells that overlap the range | `TEAM_SEASON` for Texas 2023/2024 (Big 12 → SEC) and Alabama 2023/2024 (division West → none) | Adapt the expansion in `210_team_season.sql` (per-year rows need no expansion) |
| Q5 | CFBD conference names match the seeds (`SEC`, `Big Ten`, `Big 12`, `ACC`, `Pac-10` to 2010 / `Pac-12`, `Big East`, `American Athletic`, `FBS Independents`, `Mid-American`, `Conference USA`, `Mountain West`, `Sun Belt`, `Western Athletic`) | `POWER_CONFERENCE_MISSING` passes for 1998–2026; `CONF_CHAMPIONSHIP_MISSING` names no conference because of its name | Fix the seed names and redeploy |
| Q6 | CFBD's game classification is the code of its time | `SELECT SEASON, HOME_TEAM, HOME_CLASSIFICATION, AWAY_TEAM, AWAY_CLASSIFICATION FROM CFB_MODEL.GAME WHERE (SEASON = 2007 AND 'Appalachian State' IN (HOME_TEAM, AWAY_TEAM)) OR (SEASON = 2017 AND 'Liberty' IN (HOME_TEAM, AWAY_TEAM))` (seed still empty): both teams must be `fcs`; then `SELECT FLOOR(SEASON / 10) * 10, CLASSIFICATION_SOURCE, COUNT(*) FROM CFB_MODEL.TEAM_SEASON GROUP BY 1, 2`. Run as soon as stored 2007 and 2017 games exist on any target | `fbs` → fill `CLASSIFICATION_TRANSITION` with every team and season range since 1978 whose classification differs from today's code, review it like the tier seed, redeploy (seed rows only) |
| Q7 | The championship rule finds exactly one game per conference and season of the seed, 1992–2025 | `SELECT SEASON, COUNT(*) FROM CFB_MODEL.CONFERENCE_CHAMPIONSHIP_GAME GROUP BY 1` equals the seed's conferences per season (2024: 9); `CONF_CHAMPIONSHIP_NOTES_MISMATCH` and `CONF_CHAMPIONSHIP_MISSING` pass; no 2026 game is marked before championship week, and the 2026 Army–Navy game (week 15) is never marked ([32]) | Army–Navy-type misdetections or championship games not flagged as conference games → the fallback rule of [11]; remaining exceptions (2020) listed in `docs/model-verification.md` |
| Q8 | `/metrics/wp/pregame` `spread` is from the home side, negative when home is favored | `SELECT CORR(PREGAME_SPREAD, HOME_PREGAME_WIN_PROB) FROM CFB_MODEL.GAME WHERE SEASON = 2024` < 0 | Fix the comment and the `TEAM_SPREAD` sign |
| Q9 | `/games` without a classification returns every division (Phase 2 A4) | Distinct game classifications in 2024 include `fcs`, `ii` and `iii` | Coverage populations stay FBS-based; record FCS and below as partial |
| Q10 | `ST_DISTANCE` on SRID 4326 is planar (degrees), and the WKT from two `DOUBLE`s parses | Probe 1.1c | If geodesic, keep `GREAT_CIRCLE_KM` and document `ST_DISTANCE` as equivalent |
| Q11 | Exasol DDL is transactional; inline view comments are readable; `FORCE` views keep their comments and compile once their objects exist; `CAST(DOUBLE AS DECIMAL)` rounds | Probe 1.1a, 1.1b, 1.1f, 1.1i | The fallbacks recorded in 1.1 |
| Q12 | SP+ team names equal `TEAMS.SCHOOL` for 2013–2026, and the national-averages row is the team `nationalAverages` | `SP_TEAM_UNMATCHED` passes; `SELECT DISTINCT TEAM FROM CFB_CORE.RATINGS_SP WHERE TEAM NOT IN (SELECT SCHOOL FROM CFB_CORE.TEAMS)` | Add an alias seed (name → team id) and match through it; fix the pseudo-team name |
| Q13 | 0.95 of completed FBS-vs-FBS games have a pregame line in every finished season from 2013 | `SELECT SEASON, MEASURED_SHARE, TRUST_LEVEL FROM CFB_MODEL.SEASON_COVERAGE WHERE DATASET_CODE = 'PREGAME_WP' ORDER BY 1` | Set `DATASET.COMPLETE_SHARE` for `PREGAME_WP` just below the typical share of finished seasons, and record the shares |

### [33] Pushdown-driven restructurings from the performance smoke (task 3.7)

- **Decision:**
  - `SEASON_WEEK` reads `CFB_CORE.GAMES` with `GAME`'s deduplication rule, instead of reading `GAME`.
  - Window functions sit in their own step, never in the `GROUP BY` step.
  - `TEAM_GAME` reads `GAME` once, cross-joined with `(VALUES (1),(2)) AS x(SIDE)`, instead of a two-branch `UNION ALL`.
  - Later as-of views read `GAME` at most once per query.
- **Alternatives:** keep the plan's read graph and materialize the slow views (3.7b).
- **Rationale:** measured on the 252k-game scaled league, with output proven identical by `MINUS` in both directions.
  - Exasol evaluated a view referenced twice in one query once, without the season filter. Example: one as-of point read all 250k games, 1.75 s → 0.37 s.
  - A window in the `GROUP BY` step blocked the season filter: one season of `SEASON_WEEK` took 1.0 s → 0.2 s.
  - Cost: the dedup rule exists twice (`GAME` and `SEASON_WEEK`).
  - All 16 probes pass their targets, and 3.7b is not needed.
- **Promotes to ADR:** no

### [34] `TEAM_SEASON` details the specs leave open (task 3.8)

- **Decision:**
  - "Every other FBS conference" (the NULL-name tier rows) is a conference whose team-seasons with a classification are mostly `fbs` in that season. The tier stays a conference attribute: Fairhaven 2013 (`fcs` by its games, Mountain West member) is `NON_AQ` like its conference.
  - An affiliation request without a year speaks from 1869 (or `P_MIN_YEAR`) to `P_MAX_YEAR`, else to the year of its fetch, so an open spell never runs into the future. Seasons are generated by a 300-row `VALUES` cross join (no `SYS.DUAL`, no `CFB_META.SEASON_NUMBERS`, which the layering lint does not allow).
  - The conference or classification on most `FIXED` games: a tie goes to the code of the latest such game. Games without a recorded conference do not vote.
  - Counts and point sums are 0, not NULL, for a team-season without completed games; `GAMES` = 0 marks a season known only from a membership. `FIXED_GAMES` is exposed because `TEAM_SEASON_AS_OF` keeps only team-seasons with a membership or a `FIXED` game.
  - `SCHOOL` is the team-list name, else the name on the team's latest game of the season, else the membership's name. It is read from `CFB_CORE.TEAMS`, not from `TEAM`.
  - `RATING_SP` now matches SP+ names against the stored team list (`CFB_CORE.TEAMS`, newest fetch per id) instead of the `TEAM` view, as its spec says ("the team of the stored team list"). `TEAM` reads every stored game, so `TEAM_SEASON` (which reads `GAME` through `TEAM_GAME`) would have read `GAME` twice, unfiltered ([33]). Same rows on the base league; a game-only team name no longer matches (new test).
  - Memberships and game aggregates are merged by a `UNION ALL` grouped by team and season, so a season filter reaches both; SP+ (season and `SEASON - 1`) and the final ranks are left joins.
- **Measured (base league, `PERF_` namespace, local, warm, query cache off):** one season 1.06 s median, of which about 0.77 s is compile time of the view stack (`COMPILE / EXECUTE` in the profile); grouped by season 0.60 s. The profile shows the season filter on both `GAMES` scans and on `RANKINGS_POLLS_RANKS`; `RATINGS_SP` is read whole (the prior join) and the membership expansion is computed for all seasons (small). Not yet measured on the scaled league: the owner asked for a lighter process; 4.3 measures it with the as-of views.
- **Rationale:** a deterministic answer for every case the league can produce, and no second read of `GAME`.
- **Promotes to ADR:** no

## Review Findings

<!-- Populated by speq-implement after code review. -->
