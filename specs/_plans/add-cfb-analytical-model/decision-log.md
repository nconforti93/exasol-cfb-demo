# Decision Log: add-cfb-analytical-model

Date: 2026-10-22

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

## Design Decisions

### [1] The model covers the stored demo data only; everything else is named, not wrapped

- **Decision:** `CFB_MODEL` reads only the `demo` profile tables (`GAMES`, `METRICS_WP_PREGAME`, `RATINGS_SP`, `RANKINGS_POLLS_RANKS`, `CALENDAR`, `CONFERENCES_AFFILIATIONS`, `TEAMS`, `VENUES`, `CONFERENCES`).
  - Datasets the architecture lists but the demo does not store (players, coaches, plays, drives, box scores, full lines, SRS, FPI, recruiting, transfers, draft, rosters, matchup, preview, weather) appear in the reference table `DATASET` as `ON_DEMAND` or `UNAVAILABLE`, with their CFBD endpoint and `CFBD_VS` table.
  - `SEASON_COVERAGE` points to them per season.
  - No model object reads `CFBD_VS`.
- **Alternatives:**
  - Pass-through views over `CFBD_VS` in the model: a model object could then trigger API calls, and the architecture gives on-demand access to `CFB_API` views that carry the required filters.
  - Silent omission: the chat could not explain a gap.
- **Rationale:** "The model costs zero calls" stays provable (lint + dependency test). The gap list is data the chat can read.
- **Promotes to ADR:** yes

### [2] Every derived model object is a view; no materialized model tables

- **Decision:** dimensions, facts, coverage, as-of helpers and checks are views over `CFB_CORE`, the loader's plan views and other model objects. The only tables are five seeded reference tables. A view is materialized only if live verification (task 7.3) measures it more than 2× over its target. It then becomes a table rebuilt by the `MODEL` step, and that decision is logged.
- **Alternatives:**
  - Materialize `TEAM_SEASON`, `GAME` or as-of snapshots in a `REFRESH_STEPS` step: `BACKFILL` and `make restore` run no refresh steps, so materialized tables would go stale after them, and every phase would have to remember a rebuild.
  - Materialize "for safety": no measured need. All history is about 250k games.
- **Rationale:** always consistent with the stored data; no rebuild order to get wrong; small data for Exasol. A measured, pre-agreed fallback keeps performance honest.
- **Promotes to ADR:** yes

### [3] Curated era knowledge lives in seeded reference tables

- **Decision:** `ERA`, `CLASSIFICATION_LABEL`, `CONFERENCE_TIER`, `POLL` and `DATASET` are defined with their rows in `sql/model/01x_*.sql`, and replaced on every deploy.
- **Alternatives:** constants inside views (not inspectable by the chat); a YAML file compiled at deploy (one more format, no benefit).
- **Rationale:** reviewed in pull requests, queryable and commented, and identical in every deployment.
- **Promotes to ADR:** no

### [4] As-of visibility follows event time or knowledge time

- **Decision:** an as-of point means "after that week's games, before the next week's first kickoff".
  - Event facts become visible when they happen: results, completed flags, postgame values (weeks ≤ P), pregame Elo (weeks ≤ the next week), polls (`VISIBLE_AT_WEEK_SEQ` ≤ P), and the prior season's final SP+.
  - Values CFBD overwrites during a season become visible only from their fetch time (`REQ_AT` < `CUTOFF_AT`): the current season's SP+ and next-week lines. Played games show their (closing) line.
  - An invariance test proves that no fact after the point changes an as-of answer.
- **Alternatives:**
  - Week labels only: this would leak historic closing lines and end-of-season SP+ into mid-season points.
  - Snapshot tables per week ([7]).
- **Rationale:** leak-free without history tables, explainable per column, and testable as a property.
- **Promotes to ADR:** yes

### [5] Points are addressed by a week sequence that matches `REFRESH_WEEK`

- **Decision:** `WEEK_SEQ`: 0 for the preseason, n for regular week n, and 100 + n for postseason week n. `AS_OF_POINT` maps `(season, week, season_type)` 1:1, the same arguments `REFRESH_WEEK` and its registered steps receive.
- **Alternatives:** a calendar ordinal (needs a calendar, which is stored only from 2013); a timestamp cutoff (historic games have date-only kickoffs, and pages think in weeks).
- **Rationale:** deterministic for every season since 1869, and it lines up with Phase 2's step contract. Known limit: as-of points inside the postseason are no finer than CFBD's postseason weeks (Q3).
- **Promotes to ADR:** yes

### [6] As-of views hold only the point's season

- **Decision:** `GAME_AS_OF`, `TEAM_SEASON_AS_OF` and `RANKING_AS_OF` join each point only to its own season. Earlier seasons come from `GAME`, `TEAM_GAME` and `TEAM_SEASON`, which are fully known.
- **Alternatives:** include every earlier season in each point. An unfiltered query would expand to about 2,700 points × 250k games.
- **Rationale:** a careless chat query stays bounded, and the semantics stay unchanged.
- **Promotes to ADR:** no

### [7] No in-season snapshot history; the prior season's final SP+ is the prior

- **Decision:** the model keeps no per-week history of current-season SP+ or lines.
  - A replay of an earlier week of the current season shows them as NULL. The latest fetch is visible only from its fetch time.
  - The leak-free prior for season S is the final SP+ of S − 1 (`SP_PRIOR_RATING`), available from 2014, which covers Phase 4's 2014–2025 calibration.
  - Phase 5's frozen predictions keep the inputs they used.
- **Alternatives:** an append-only snapshot table written by the `MODEL` step. It costs storage and refresh logic for a replay case the demo does not need, and it would be lost on restore unless snapshots were extended. Moved to the backlog (task 6.3).
- **Rationale:** YAGNI with a clear upgrade path.
- **Promotes to ADR:** yes

### [8] Poll visibility uses a per-poll label lead, conservative for the committee

- **Decision:** `POLL.LABEL_LEAD_WEEKS`: AP 1, Coaches 1, committee 0, unknown polls 0. A regular-season poll becomes visible at its `WEEK_SEQ` minus the lead, and postseason (final) polls at their own `WEEK_SEQ`.
- **Alternatives:** release timestamps (CFBD has none); one rule for all polls (would leak Tuesday committee rankings into "Sunday after week N").
- **Rationale:** leak-free by default, and adjustable in one seed row after verification (Q1, Q2).
- **Promotes to ADR:** no

### [9] Team-season membership: each slice speaks for its own request, games decide classification

- **Decision:**
  - Affiliation spells expand only over the seasons their slice requested (`P_YEAR`, or `P_MIN_YEAR`–`P_MAX_YEAR`). The newest fetched slice wins, and same-slice conflicts take the later `START_YEAR` and are flagged.
  - Without a membership, the conference comes from the games.
  - Classification comes from the team's games first, then from the membership.
- **Alternatives:** trust open spells of the static 1869–2025 range slice (they would claim later seasons after a move); the membership's classification (it is the conference's, which is wrong in transition seasons).
- **Rationale:** deterministic answers that survive realignment, with the source recorded per row.
- **Promotes to ADR:** no

### [10] Conference tiers are a reviewed reference keyed by CFBD names, guarded by an error check

- **Decision:** `CONFERENCE_TIER` has the following rows:
  - AQ 1998–2013, P5 2014–2023, P4 2024–;
  - `INDEPENDENT` for FBS Independents;
  - NULL-name defaults `NON_AQ`, `G5`, `G6` for the other FBS conferences.

  `POWER_CONFERENCE_MISSING` (ERROR) fails a season in which a power name of the table matches no team-season.
- **Alternatives:** derive "power" from rankings or results (wrong: it is an era rule); hard-code it in Phase 4 (hidden from analyses and the chat).
- **Rationale:** the 2026 auto-bids depend on it. A CFBD rename must stop the refresh, not silently change the playoff field.
- **Promotes to ADR:** no

### [11] Conference championship games are recognized by their schedule

- **Decision:** from 1992, a regular-season conference game between two members of the same conference that is the conference's only conference game in its last regular-season conference week, after a week with more than one conference game.
- **Alternatives:** the `NOTES` text (not known to be populated); "the calendar's last regular week" (Army–Navy-type weeks and seasons in progress defeat it).
- **Rationale:** works for completed and in-progress seasons. Verified against known counts in 7.2 (Q7).
- **Promotes to ADR:** no

### [12] Venues use `GEOMETRY(4326)`; distances come from a great-circle function

- **Decision:** `VENUE.GEO_POINT` is `GEOMETRY(4326)` (x = longitude, y = latitude). `GREAT_CIRCLE_KM` (haversine, R = 6,371.0088 km) gives kilometres, and `TEAM_GAME.TRAVEL_KM` uses it.
- **Alternatives:**
  - `GEOGRAPHY`: Exasol has no such type (skill data types, documentation).
  - `ST_DISTANCE`: it computes in the plane of the coordinates, and its unit on SRID 4326 is undocumented (probe 1.1c).
  - `ST_TRANSFORM` to a metric CRS: it depends on `EXA_SPATIAL_REF_SYS` contents and distorts over long distances.
- **Rationale:** the only spatial type, correct distances, and still usable with Exasol's `ST_*` functions. The mission's "GEOGRAPHY" wording changes to "GEOMETRY(4326)" (tasks 6.3, 6.4).
- **Promotes to ADR:** yes

### [13] Comments inline, grain first, reserved-free names, verified after deploy

- **Decision:**
  - Views carry their comments inline in `CREATE OR REPLACE VIEW`, because the grammar has no `COMMENT ON VIEW`; tables do the same in `CREATE OR REPLACE TABLE`.
  - Every table and view comment starts with "One row per …".
  - No column name is a reserved word.
  - The deploy reads the comments back and fails below 100 %.
  - `docs/model-dictionary.md` is generated from the SQL files.
- **Alternatives:** `COMMENT ON` after creation (impossible for views, and not atomic with a replace); free-form comments.
- **Rationale:** the chat and analysts get the grain without guessing, and plain SQL works without quoting.
- **Promotes to ADR:** yes

### [14] A generic one-transaction SQL-object deployer

- **Decision:** `vs/host/sqlobjects.py` (load, lint, render, deploy) deploys a directory of object files in one transaction, drops orphans in the target schema and reads comments back. `model/deploy.py` binds it to `sql/model/`. Phases 4–5 reuse it for `CFB_SIM`, `CFB_PRED` and `CFB_API`.
- **Alternatives:** per-phase scripts; `CREATE` without dropping orphans (stale views would mislead the chat).
- **Rationale:** atomic, idempotent and lint-gated deploys, written once. If probe 1.1b shows that DDL is not transactional, the fallback deploys to a validation schema first.
- **Promotes to ADR:** yes

### [15] The `MODEL` refresh step checks; it does not rebuild

- **Decision:** step 100 `MODEL` = `CFB_INFRA.REFRESH_MODEL(run_id, season, week, season_type)`. It evaluates `MODEL_CHECKS` for the season and the week, logs them to `CFB_META.MODEL_CHECK_LOG`, commits, and fails on any failed ERROR check. `make model-check` prints the same checks without the step.
- **Alternatives:** no step (renames and broken inputs would reach the simulations); a rebuild step (nothing to rebuild, [2]).
- **Rationale:** the timeline shows "model" as the mission describes, and the chain stops before Phases 4–5 consume bad inputs.
- **Promotes to ADR:** yes

### [16] The model reads the loader's plan views for coverage

- **Decision:** model views may read `CFB_META.LOAD_PLAN`, `LOAD_PLAN_GAPS` and `LOAD_SLICES` (planned, stored, final, gap), and nothing else in `CFB_META`. `specs/architecture.md` is updated (task 6.2).
- **Alternatives:** derive "stored" from mirror rows (empty and gap slices have none, and finality is not in the rows); copy load state into the model.
- **Rationale:** one owner per contract. Coverage tells "not loaded yet" from "does not exist".
- **Promotes to ADR:** yes

### [17] The test league goes through the real flattener and is inserted directly

- **Decision:** `tests/synthetic/league.py` defines API-shaped bodies with the specs' facts. The foundation's flattener turns them into mirror rows, which the harness inserts directly into `TEST_CFB_CORE` with `LOAD_LOG` rows. The same bodies serve as replay recordings for the refresh-step test. One generator-shape test also stages `fake.py` responses through the virtual schema.
- **Alternatives:** hand-written mirror rows (drift from real shapes); staging every league slice through the virtual schema per test (slow).
- **Rationale:** faithful rows, fast tests, and one source of truth for the facts.
- **Promotes to ADR:** no

### [18] Head-to-head can come from stored games (recommendation to Phase 5)

- **Decision:** `TEAM_GAME` makes the all-time series between two teams computable at zero calls, from games stored since 1869 (`head_to_head.sql`). Phase 5 decides whether the page uses it, with `/teams/matchup` kept for the "live" beat, or keeps D13's on-demand matchup.
- **Alternatives:** none decided here.
- **Rationale:** offline and replay-safe, and no per-click call. It is recorded so that Phase 5 does not rediscover it.
- **Promotes to ADR:** no

### [19] The current point is derived from the data

- **Decision:** `CURRENT_AS_OF` is the newest (season, `WEEK_SEQ`) with a completed game. No state table.
- **Alternatives:** a row written by the `MODEL` step. After a restore (the rehearsal reset), it would point at the wrong week.
- **Rationale:** always matches the stored data. Pages can pass any other point.
- **Promotes to ADR:** no

### [20] Example analyses are the done-when proof

- **Decision:** `sql/model/examples/` holds four analyses (engine inputs as of a week, all-time upsets, head-to-head, CFP field inputs). They read only `CFB_MODEL` and run as integration tests with expected answers.
- **Alternatives:** a reviewer's judgement of "analysis-ready".
- **Rationale:** the roadmap's criterion becomes executable, and the examples double as documentation.
- **Promotes to ADR:** no

## Open Questions (assumption + verify step; all verified in task 7.2 at zero calls)

| # | Assumption | Verify step (on AWS after Phase 2's backfill) | If wrong |
|---|---|---|---|
| Q1 | CFBD labels the preseason AP poll as regular week 1, and the poll released after week W as week W + 1 | `SELECT POLL_RANK, SCHOOL FROM CFB_MODEL.RANKING WHERE SEASON = 2024 AND POLL_CODE = 'AP' AND SEASON_TYPE = 'regular' AND WEEK IN (1, 2) AND POLL_RANK <= 5`: week 1 must be the preseason order (Georgia, Ohio State, Oregon, …), and week 2 must reflect week-1 results | Set the AP and Coaches `LABEL_LEAD_WEEKS` to 0 |
| Q2 | Committee rankings are labeled by the upcoming week (lead 0 = visible one week after release, which is conservative) | The first 2024 committee ranking (released 2024-11-05, after week 10 games) has label 11 | Label 10 → lead −1 |
| Q3 | CFBD puts all bowls and CFP rounds into postseason week 1 | `SELECT SEASON_TYPE, WEEK, START_AT, END_AT FROM CFB_MODEL.SEASON_WEEK WHERE SEASON = 2024` | One postseason week: record it as Phase 4/5's open item (as-of inside the postseason by date); several weeks: nothing to do |
| Q4 | `/conferences/affiliations` returns spells (team, conference, division, start, end), split on division changes, and the range call returns spells that overlap the range | `TEAM_SEASON` for Texas 2023/2024 (Big 12 → SEC) and Alabama 2023/2024 (division West → none) | Adapt the expansion in `210_team_season.sql` (per-year rows need no expansion) |
| Q5 | CFBD conference names match the tier seed (`SEC`, `Big Ten`, `Big 12`, `ACC`, `Pac-10` to 2010 / `Pac-12`, `Big East`, `American Athletic`, `FBS Independents`) | `POWER_CONFERENCE_MISSING` passes for 1998–2026 | Fix the seed names and redeploy |
| Q6 | CFBD's game classification is populated for old seasons, retroactively | `SELECT FLOOR(SEASON / 10) * 10, CLASSIFICATION_SOURCE, COUNT(*) FROM CFB_MODEL.TEAM_SEASON GROUP BY 1, 2` | Classification from memberships, or `NONE`; labels unaffected |
| Q7 | The championship-game rule finds exactly the FBS championship games | Per-season counts 2014–2025 equal the known counts (for example, 2024: 9), and no 2026 game is marked before championship week | Refine the rule ([11]) |
| Q8 | `/metrics/wp/pregame` `spread` is from the home side, negative when home is favored | `SELECT CORR(PREGAME_SPREAD, HOME_PREGAME_WIN_PROB) FROM CFB_MODEL.GAME WHERE SEASON = 2024` < 0 | Fix the comment and the `TEAM_SPREAD` sign |
| Q9 | `/games` without a classification returns every division (Phase 2 A4) | Distinct game classifications in 2024 include `fcs`, `ii` and `iii` | Coverage populations stay FBS-based; record FCS and below as partial |
| Q10 | `ST_DISTANCE` on SRID 4326 is planar (degrees), and the WKT from two `DOUBLE`s parses | Probe 1.1c | If geodesic, keep `GREAT_CIRCLE_KM` and document `ST_DISTANCE` as equivalent |
| Q11 | Exasol DDL is transactional, and inline view comments are readable | Probe 1.1a, 1.1b | The fallbacks recorded in 1.1 |
| Q12 | SP+ team names equal `TEAMS.SCHOOL` for 2013–2026 | `SP_TEAM_UNMATCHED` passes | Add an alias seed (name → team id) and match through it |

## Review Findings

<!-- Populated by speq-implement after code review. -->
