# Plan: add-cfb-analytical-model

## Summary

Roadmap Phase 3: build `CFB_MODEL`, the era-aware analytical model over the stored `CFB_CORE` data. When it is done, Phases 4–6 can write every analysis without touching `CFB_CORE`.

- **Views only.** Dimensions, facts, coverage and as-of helpers are views over the mirrors. Five small reference tables hold curated knowledge that CFBD does not provide: eras, division labels, conference tiers, polls and the dataset catalog. Nothing needs a rebuild after a backfill, a refresh or a restore.
- **Era-aware.**
  - Every season carries its division, postseason, champion, overtime and conference-championship era.
  - Every team-season carries its conference, division, classification (with the name used at the time) and power tier: AQ, P5, P4, G5, G6.
  - `SEASON_COVERAGE` states per season and dataset whether the data is complete, partial, in progress, missing, on demand, gated or not available.
- **Leak-free as-of helpers.** `AS_OF_POINT`, `CURRENT_AS_OF`, `GAME_AS_OF`, `TEAM_SEASON_AS_OF` and `RANKING_AS_OF` answer "the state as of season S, week W".
  - Event facts (results, Elo, polls) are visible once they happen. Values that CFBD overwrites during a season (current SP+, pregame lines) are visible only from their fetch time.
  - A test proves the invariant: no fact after the point changes an as-of answer.
- **Geography.** Venues carry a `GEOMETRY(4326)` point, because Exasol has no `GEOGRAPHY` type. Distances come from a great-circle SQL function.
- **Self-describing.** Every object and column is commented, with the grain first. Column names need no quoting, a data dictionary is generated, and four example analyses prove the done-when criterion.
- **One command.** `make model` (also part of `make deploy`) deploys the model atomically from `sql/model/` and costs zero API calls.
- **Refresh step.** The `MODEL` refresh step checks the fresh week and stops the chain before predictions or simulations run on broken inputs.

Everything is verified at zero CFBD calls: the synthetic league in the `TEST_` namespace, then the AWS data from Phase 2's backfill, read only.

## Design

### Context

The mission promises "a general model over the stored data, aware of eras: conference membership and classification per season, coverage flags per fact, venue GEOGRAPHY, an 'as of week' view for every page". The roadmap's done-when criterion is "an analysis can be written without touching `CFB_CORE`". Phase 2 (`add-cfb-persistence-refresh`) and the foundation (`add-cfbd-vs-foundation`) provide what this phase builds on:

- **The stored data.** The `demo` load profile stores, at about 217 calls once:
  - `GAMES` 1869– (per-game Elo from 1890), `METRICS_WP_PREGAME` and `RATINGS_SP` from 2013, `RANKINGS_POLLS_RANKS` from 2014, `CALENDAR` from 2013;
  - `CONFERENCES_AFFILIATIONS` as one range slice for 1869–2025 plus season slices from 2026;
  - `TEAMS`, `VENUES` and `CONFERENCES`.

  Nothing else is stored (decision [1]).
- **Mirrors and slices.** Each `CFB_CORE` mirror has the virtual table's columns, including the `P_*` parameter echoes and `REQ_*` request metadata (`REQ_SLICE`, `REQ_AT` in UTC).
  - The current season's `weekly` slices are re-fetched by every `REFRESH_WEEK` and stay non-final until the following 15 February.
  - Earlier seasons are final.
  - `CFB_META.LOAD_PLAN` and `CFB_META.LOAD_SLICES` say which slice of which season is planned, stored, final or a gap.
- **Prerequisite P1 is in the catalog.** `RANKINGS_POLLS_RANKS` carries `SEASON`, `SEASON_TYPE`, `WEEK` and `POLL` (catalog `75c2c930…`, checked 2026-10-22). So regular week 1 and postseason week 1 polls are distinct.
- **The refresh chain** `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(season, week, season_type)` runs registered steps as `EXECUTE SCRIPT <script>(run_id, season, week, season_type)` from step 100 on (`REFRESH_STEPS`, `loader/load-plan.yaml`).
- **Consumers:**
  - Phase 4 needs team ratings computed in SQL from games, with SP+ as a prior. Its engine input (`specs/architecture.md`, `add-engine-spikes`) is the remaining schedule (home, away, neutral, conference flag) plus team metadata (conference, power or G6, records, point differential).
  - Phase 5 needs pregame win probability and Elo per game for all-time upsets, the all-time series for head-to-head, frozen weekly predictions, and week-over-week movement.
  - Phase 6 needs an as-of week picker. Phase 7's chat needs a self-describing model.

The forces:

- **Era and coverage.** 157 seasons span six division structures, seven postseason formats, ties before 1996, and power-conference sets that changed in 1998, 2014 and 2024. CFBD stamps today's classification codes onto every season. Data starts at different seasons per dataset, and the demo stores only part of it.
- **Leakage.** Results and polls carry week labels, so their visibility follows from the week. Current-season SP+ and pregame lines are overwritten by each refresh, so the stored value is the latest one. Historic spreads are closing lines, known only on game day.
- **Exasol** (checked with the `exasol-database` skill; see Key Interfaces):
  - no table-valued functions usable in `FROM`, no `COMMENT ON VIEW`, no `GEOGRAPHY` type;
  - `GEOMETRY(srid)` with planar spatial functions;
  - transactional DDL (probe 1.1b).
- **Process.** No DB session during planning. Probes and integration tests run only in "local free" windows, and AWS only after "AWS free". Zero CFBD calls throughout.

**Goals**
- `CFB_MODEL` with these objects (Key Interfaces):
  - dimensions `SEASON`, `SEASON_WEEK`, `TEAM`, `CONFERENCE`, `VENUE`, `TEAM_SEASON`, `CONFERENCE_SEASON`;
  - facts `GAME`, `TEAM_GAME`, `RATING_SP`, `RANKING`;
  - coverage `DATASET`, `SEASON_COVERAGE`;
  - as-of `AS_OF_POINT`, `CURRENT_AS_OF`, `GAME_AS_OF`, `TEAM_SEASON_AS_OF`, `RANKING_AS_OF`;
  - checks `MODEL_CHECKS`;
  - reference tables `ERA`, `CLASSIFICATION_LABEL`, `CONFERENCE_TIER`, `POLL`, `DATASET`, and the function `GREAT_CIRCLE_KM`.
- Era awareness per season (eras, `CFP_TEAMS`, `TIES_POSSIBLE`) and per team-season (membership by season with source and conflict flags, tier, division label with the retroactive flag).
- Coverage flags per game, and a trust level per season and dataset that tells "not stored" (with the virtual-schema table to use) from "does not exist".
- As-of helpers addressed exactly like `REFRESH_WEEK(season, week, season_type)`, leak-free by rule, with an invariance test.
- Venue `GEOMETRY(4326)` points, `GREAT_CIRCLE_KM` and `TEAM_GAME.TRAVEL_KM`.
- 100 % comment coverage with grain sentences, reserved-word-free column names, a generated dictionary, and four example analyses that read only `CFB_MODEL`.
- An idempotent, atomic, linted deploy: `make model`, part of `make deploy`. A generic SQL-object deployer (`vs/host/sqlobjects.py`) that Phases 4–5 reuse for `CFB_SIM`, `CFB_PRED` and `CFB_API`.
- Refresh step 100 `MODEL` (`CFB_INFRA.REFRESH_MODEL`): checks, a log, and failing on errors. `make model-check` for any point.

**Non-Goals**
- Ratings computed from games, the margin and total model, the Elo win-probability formula and upset magnitude. They belong to Phase 4. The model provides their inputs and coverage flags.
- `CFB_SIM`, `CFB_PRED` and `CFB_API` objects, and grants to `CFB_APP` or `CFB_MCP`. Apps and the chat read `CFB_API` only (Phase 5).
- Player, coach, recruit, transfer, draft, play, drive, box-score, full betting-line, SRS, FPI and weather facts. They are not stored in the demo profile. `DATASET` lists them as `ON_DEMAND` (or `UNAVAILABLE`), and `SEASON_COVERAGE` points to their virtual-schema tables ([1]). Pass-through views over `CFBD_VS` are not added, so the model can never trigger a call.
- In-season snapshot history: SP+ and lines per week of the current season. It is not needed to stay leak-free ([7]). Phase 5's frozen predictions keep the inputs they used.
- Historic stadium moves (travel uses the current home venue), historic conference-championship rules before 1992, and BCS title-game detection.
- As-of points inside the postseason finer than CFBD's postseason weeks (open question Q3).
- Changing Phase 2's loader behavior. This plan only registers its step in `loader/load-plan.yaml`.

### Decision

The model is a directory of SQL files (`sql/model/`, one object per file) deployed in one transaction by a generic SQL-object deployer. Reference tables are replaced with their seed rows on every deploy. Every derived object is a view over `CFB_CORE`, the loader's plan views and other model objects.

The as-of helpers are parameter-dimension views: `AS_OF_POINT` is joined to the season's facts, and each column is masked by an event-time or knowledge-time visibility rule. A filter on the `AS_OF_*` columns selects the point. A thin Lua step script evaluates the declarative checks view inside the weekly refresh. Tests run on a synthetic league whose rows the foundation's own flattener produces from API-shaped bodies.

#### Architecture

```
 sql/model/NNN_<object>.sql  (one object each; comments inline; {MODEL_SCHEMA} {CORE_SCHEMA} {META_SCHEMA})
        │  vs/host/sqlobjects.py: parse ─▶ lint (comments, grain, reserved words, schema refs) ─▶ render
        ▼
 make model / make deploy [TARGET=local|aws] ─▶ model/deploy.py ─▶ one transaction:
     CREATE SCHEMA IF NOT EXISTS ; reference tables (CREATE OR REPLACE + INSERT) ; GREAT_CIRCLE_KM ;
     views in file order ; DROP orphans ; read back EXA_ALL_OBJECTS/EXA_ALL_COLUMNS comments ─▶ COMMIT
     + CFB_INFRA.REFRESH_MODEL (Lua, build/sql/model_refresh_model.sql)   + CFB_META.MODEL_CHECK_LOG (meta DDL)

 CFB_CORE mirrors ──┐      CFB_META.LOAD_PLAN / LOAD_SLICES (loader views; plan, stored, final, gap)
                    ▼                    │
 CFB_MODEL (views)  ERA CLASSIFICATION_LABEL CONFERENCE_TIER POLL DATASET   (reference tables, seeded)
   GAME ─▶ SEASON_WEEK ─▶ SEASON            VENUE ─▶ GREAT_CIRCLE_KM
   TEAM, CONFERENCE, RATING_SP, RANKING ─▶ TEAM_GAME ─▶ TEAM_SEASON ─▶ CONFERENCE_SEASON
   AS_OF_POINT ─▶ CURRENT_AS_OF, GAME_AS_OF, RANKING_AS_OF ─▶ TEAM_SEASON_AS_OF
   SEASON_COVERAGE (DATASET × SEASON × LOAD_SLICES × measured shares)      MODEL_CHECKS (all of the above)
        │
        ├─▶ analyses (Phases 4–5: CFB_SIM, CFB_PRED) and CFB_API views ─▶ app, MCP chat
        │
 EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular')
   1 CHECK ─▶ 2..99 FETCH <table> ─▶ 100 MODEL = EXECUTE SCRIPT CFB_INFRA.REFRESH_MODEL(run, 2026, 9, 'regular')
        resolve point (AS_OF_POINT) ─▶ INSERT INTO CFB_META.MODEL_CHECK_LOG SELECT … FROM MODEL_CHECKS ─▶ COMMIT
        ─▶ error("MODEL checks failed: …") on any failed ERROR check    (0 calls)
   ─▶ 200.. predictions, simulations (Phases 4–5)
```

#### Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| Views over the mirrors, no materialized model tables | `sql/model/1xx–5xx` | The model is always consistent with `CFB_CORE` after a backfill, a refresh or a restore, with no rebuild order to get wrong. All history is about 250k games, which is small for Exasol. Rule: a view is materialized only if live verification measures it above its target (Requirements) |
| Reference tables seeded from git | `ERA`, `CLASSIFICATION_LABEL`, `CONFERENCE_TIER`, `POLL`, `DATASET` | Era knowledge that CFBD does not provide is reviewed in pull requests and replaced on every deploy, never edited in the database |
| Parameter dimension joined to facts ("parameterized view") | `AS_OF_POINT` × facts in the `*_AS_OF` views | Exasol has no table function usable in `FROM`. A filter on the point's columns selects the point and is applied before the join (probe 1.1g) |
| Event time vs. knowledge time | the `*_AS_OF` views | Week labels decide when results, Elo and polls become visible, and `REQ_AT` decides it for values that CFBD overwrites. That is leak-free without snapshot history tables |
| As-of views bounded to the point's season | `GAME_AS_OF`, `TEAM_SEASON_AS_OF`, `RANKING_AS_OF` | An unfiltered query costs points × one season, never every point × all of history (≈ 2,700 × 250k rows). Earlier seasons are fully known and come from `GAME` |
| Each slice speaks only for its request | `TEAM_SEASON` (`P_YEAR`, `P_MIN_YEAR`–`P_MAX_YEAR`), `RATING_SP.IS_FINAL` | The static range slice of memberships cannot claim seasons it was not asked for, and the newest fetch wins where slices overlap |
| Coverage = declared × planned × stored × measured | `SEASON_COVERAGE` | Trust is explainable, and "not stored, fetch it on demand" is distinguished from "does not exist" |
| Lint before deploy, read back after | `vs/host/sqlobjects.py` | Comment coverage, grain, reserved words and layering are checked before any statement. The read-back proves what the database holds |
| Deploy in one transaction, drop orphans | `vs/host/sqlobjects.py` | A failed deploy changes nothing, and stale objects never mislead the chat |
| Declarative checks, thin Lua | `MODEL_CHECKS`, `model/lua/refresh_model.lua` | Checks are testable without Lua. Lua only resolves the point, logs, commits and fails. It is the same split as Phase 2's views and scripts |
| Synthetic league through the real flattener | `tests/synthetic/league.py` | Rows are exactly what the virtual schema would store. Direct inserts keep tests fast, and the same bodies serve as replay recordings for the refresh test |
| Example analyses as acceptance tests | `sql/model/examples/` | The done-when criterion becomes executable, and the examples double as the analysis builder's starting point |

#### Key Interfaces

**Objects of `CFB_MODEL`** (file `sql/model/NNN_<object>.sql`. The comment of every table and view starts with "One row per <grain>".)

| File | Object | Kind | Grain | Reads |
|---|---|---|---|---|
| 010 | `ERA` | reference table | era kind and season range | seed |
| 011 | `CLASSIFICATION_LABEL` | reference table | division era and CFBD classification | seed |
| 012 | `CONFERENCE_TIER` | reference table | conference name (NULL = every other FBS conference) and season range | seed |
| 013 | `POLL` | reference table | CFBD poll name | seed |
| 014 | `DATASET` | reference table | CFBD dataset | seed |
| 020 | `GREAT_CIRCLE_KM` | function | one value per call | — |
| 100 | `GAME` | view | game (deduplicated by game id) | `GAMES`, `METRICS_WP_PREGAME`, `ERA` |
| 110 | `SEASON_WEEK` | view | season, season type and week | `GAME`, `CALENDAR` |
| 120 | `SEASON` | view | season from 1869 to the newest season with games | `ERA`, `GAME`, `LOAD_SLICES` |
| 130 | `VENUE` | view | venue | `VENUES` |
| 140 | `TEAM` | view | team, listed or seen in games | `TEAMS`, `GAME` |
| 150 | `CONFERENCE` | view | conference | `CONFERENCES` |
| 160 | `RATING_SP` | view | season and SP+-rated team | `RATINGS_SP`, `TEAM`, `LOAD_SLICES` |
| 170 | `RANKING` | view | poll, season, season type, week and ranked team | `RANKINGS_POLLS_RANKS`, `POLL` |
| 200 | `TEAM_GAME` | view | game and participating team | `GAME`, `TEAM`, `VENUE`, `GREAT_CIRCLE_KM` |
| 210 | `TEAM_SEASON` | view | team and season with a game or a membership | `CONFERENCES_AFFILIATIONS`, `TEAM_GAME`, `CONFERENCE_TIER`, `CLASSIFICATION_LABEL`, `ERA`, `RATING_SP`, `RANKING` |
| 220 | `CONFERENCE_SEASON` | view | conference and season with members | `TEAM_SEASON` |
| 300 | `AS_OF_POINT` | view | season and as-of point (preseason + each week) | `SEASON_WEEK`, `GAME` |
| 310 | `CURRENT_AS_OF` | view | the current point (one row) | `AS_OF_POINT` |
| 320 | `GAME_AS_OF` | view | as-of point and game of its season | `AS_OF_POINT`, `GAME` |
| 330 | `RANKING_AS_OF` | view | as-of point, poll and ranked team | `AS_OF_POINT`, `RANKING` |
| 340 | `TEAM_SEASON_AS_OF` | view | as-of point and team-season of its season | `AS_OF_POINT`, `TEAM_SEASON`, `TEAM_GAME`, `RANKING_AS_OF`, `RATING_SP` |
| 400 | `SEASON_COVERAGE` | view | season and season-scoped dataset | `DATASET`, `SEASON`, `GAME`, `TEAM_SEASON`, `RATING_SP`, `LOAD_PLAN`, `LOAD_SLICES` |
| 500 | `MODEL_CHECKS` | view | check and season (and week for week checks) | the model |

The columns and rules are in the specs: `model/seasons-eras`, `model/teams-conferences`, `model/games`, `model/ratings-rankings`, `model/venues`, `model/coverage`, `model/as-of` and `model/refresh-step`. Outside the model:
- `CFB_META.MODEL_CHECK_LOG` (meta DDL);
- `CFB_INFRA.REFRESH_MODEL` (Lua);
- `sql/model/examples/*.sql`.

**SQL file conventions** (enforced by the lint, `model/deploy` and `model/documentation`)

```sql
-- sql/model/320_game_as_of.sql: identifiers in bodies double-quoted (skill rule); names never reserved
CREATE OR REPLACE VIEW {MODEL_SCHEMA}."GAME_AS_OF" (
  "AS_OF_SEASON"      COMMENT IS 'Season of the as-of point; join or filter with AS_OF_POINT',
  "AS_OF_WEEK_SEQ"    COMMENT IS 'Week sequence of the point: 0 preseason, regular week n = n, postseason week n = 100 + n',
  "GAME_ID"           COMMENT IS 'CFBD game id (GAME.GAME_ID)',
  "GAME_STATUS_AS_OF" COMMENT IS 'PLAYED, NOT_PLAYED, NEXT or REMAINING at the point',
  "HOME_POINTS"       COMMENT IS 'Home points; NULL unless PLAYED at the point',
  "HOME_PREGAME_ELO"  COMMENT IS 'CFBD pregame Elo of the home team; NULL for games after the next week',
  "PREGAME_SPREAD"    COMMENT IS 'Home-side spread as CFBD reports it; NULL unless the game is played by the point or is a next-week game whose line was fetched before CUTOFF_AT'
  -- … every column commented
) AS
SELECT p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", g."GAME_ID",
       CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" AND g."IS_COMPLETED" THEN 'PLAYED'
            WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" THEN 'NOT_PLAYED'
            WHEN g."WEEK_SEQ" = p."NEXT_WEEK_SEQ" THEN 'NEXT' ELSE 'REMAINING' END,
       CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" THEN g."HOME_POINTS" END,
       CASE WHEN g."WEEK_SEQ" <= COALESCE(p."NEXT_WEEK_SEQ", p."AS_OF_WEEK_SEQ") THEN g."HOME_PREGAME_ELO" END,
       CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ"
              OR (g."WEEK_SEQ" = p."NEXT_WEEK_SEQ" AND g."PREGAME_WP_FETCHED_AT" < p."CUTOFF_AT")
            THEN g."PREGAME_SPREAD" END
  FROM {MODEL_SCHEMA}."AS_OF_POINT" p
  JOIN {MODEL_SCHEMA}."GAME" g ON g."SEASON" = p."AS_OF_SEASON"
COMMENT IS 'One row per as-of point and game of the point''s season. Values not known at the point are NULL (event time for results, Elo and polls; fetch time for lines). Earlier seasons are fully known: use GAME.';
```

- Placeholders are `{MODEL_SCHEMA}`, `{CORE_SCHEMA}`, `{META_SCHEMA}` and `{INFRA_SCHEMA}`. They render to the namespace's UPPER_CASE schema names; Phase 2's renderer gains `{MODEL_SCHEMA}`.
- Statements end with `;` at the end of a line. The function body ends with a line holding only `/`.
- Allowed references:
  - `{MODEL_SCHEMA}.*`;
  - `{CORE_SCHEMA}.*`;
  - `{META_SCHEMA}.LOAD_PLAN`, `LOAD_PLAN_GAPS` and `LOAD_SLICES`.

  Anything else is a lint error, in particular `CFBD_VS` or `{VS_NAME}`.
- Reference tables:

  ```sql
  CREATE OR REPLACE TABLE {MODEL_SCHEMA}."ERA" (
    "ERA_KIND" VARCHAR(32) UTF8 NOT NULL COMMENT IS 'DIVISION, POSTSEASON, CHAMPION, OVERTIME or CONF_CHAMPIONSHIP', …)
    COMMENT IS 'One row per era kind and season range …';
  INSERT INTO {MODEL_SCHEMA}."ERA" VALUES (…), …;
  ```

- The function:

  ```sql
  CREATE OR REPLACE FUNCTION {MODEL_SCHEMA}.GREAT_CIRCLE_KM (LAT1 IN DOUBLE, LON1 IN DOUBLE, LAT2 IN DOUBLE, LON2 IN DOUBLE)
  RETURN DOUBLE
  IS
    H DOUBLE;
  BEGIN
    H := POWER(SIN(RADIANS(LAT2 - LAT1) / 2), 2)
       + COS(RADIANS(LAT1)) * COS(RADIANS(LAT2)) * POWER(SIN(RADIANS(LON2 - LON1) / 2), 2);
    RETURN 2 * 6371.0088 * ASIN(LEAST(1, SQRT(H)));
  END GREAT_CIRCLE_KM;
  /
  COMMENT ON FUNCTION {MODEL_SCHEMA}.GREAT_CIRCLE_KM IS 'Great-circle (haversine) distance in km between two WGS 84 points …';
  ```

- `VENUE.GEO_POINT`: `CAST('POINT (' || <lon> || ' ' || <lat> || ')' AS GEOMETRY(4326))`. Probe 1.1c fixes the number formatting (string concatenation or `TO_CHAR`) and whether the cast is needed for the column type to show `GEOMETRY(4326)`.

**As-of semantics** (full rules in `model/as-of`)

| Concept | Definition |
|---|---|
| Point | (season, `WEEK_SEQ`): after the games of that week, before the next week's first kickoff. 0 = preseason; regular week n = n; postseason week n = 100 + n |
| Refresh mapping | `REFRESH_WEEK(S, W, T)` ↔ `AS_OF_POINT` row (`AS_OF_SEASON` = S, `AS_OF_WEEK` = W, `AS_OF_SEASON_TYPE` = T); NULL type = `regular`, as Phase 2 passes it |
| `CUTOFF_AT` | first kickoff of the next week, from `SEASON_WEEK.FIRST_GAME_AT` (calendar `FIRST_GAME_START` if stored, else the games); NULL after the last week |
| Event time | results ≤ P; pregame Elo ≤ next week; polls with `VISIBLE_AT_WEEK_SEQ` ≤ P; prior-season final SP+ |
| Knowledge time | current-season SP+ and next-week lines only if `REQ_AT` < `CUTOFF_AT` (at the last point of a complete season, final values are visible) |
| Current point | newest (season, `WEEK_SEQ`) with a completed game |

**Reference seeds** (contents in the specs; verified on AWS in 7.2)
- `ERA`: `model/seasons-eras` (5 kinds, 25 rows).
- `CLASSIFICATION_LABEL`: 6 division eras × 4 codes (`fbs`, `fcs`, `ii`, `iii`).
- `CONFERENCE_TIER`:
  - named rows for AQ 1998–2013 (ACC, Big East to 2012, American Athletic 2013, Big Ten, Big 12, Pac-10 to 2010, Pac-12 from 2011, SEC), P5 2014–2023 and P4 2024–;
  - `FBS Independents` → `INDEPENDENT`;
  - NULL-name rows for the other FBS conferences: `NON_AQ` 1998–2013, `G5` 2014–2023, `G6` 2024–.
- `POLL`: `model/ratings-rankings` (6 rows; `LABEL_LEAD_WEEKS` AP 1, Coaches 1, CFP 0).
- `DATASET`: 25 rows transcribed from `docs/cfbd-data-availability.md`. Endpoints and virtual tables come from `vs/catalog/catalog.json`, and access classes from D15.

**Meta table and step script**

```sql
-- sql/meta/model_check_log.sql (registered in vs/host/metaddl.py; created with the other meta tables)
CREATE TABLE {META_SCHEMA}.MODEL_CHECK_LOG (RUN_ID DECIMAL(18,0),
  AS_OF_SEASON DECIMAL(4,0) NOT NULL, AS_OF_WEEK_SEQ DECIMAL(3,0) NOT NULL,
  CHECK_NAME VARCHAR(64) UTF8 NOT NULL, SEVERITY VARCHAR(8) UTF8 NOT NULL,
  CHECK_SEASON DECIMAL(4,0) NOT NULL, CHECK_WEEK_SEQ DECIMAL(3,0),
  CHECK_STATUS VARCHAR(4) UTF8 NOT NULL, OBSERVED DECIMAL(18,0) NOT NULL,
  DETAIL VARCHAR(2000) UTF8, CHECKED_AT TIMESTAMP NOT NULL);           -- UTC (Phase 2 probe 1.1l expression)
```

`CFB_INFRA.REFRESH_MODEL(run_id, season, week, season_type)` (Lua, `RETURNS ROWCOUNT`, every statement through `pquery`):
1. Validate the arguments; a NULL season type means `regular`.
2. Resolve `AS_OF_WEEK_SEQ` from `AS_OF_POINT`. An unknown point raises an error and logs nothing.
3. `INSERT INTO MODEL_CHECK_LOG SELECT … FROM MODEL_CHECKS WHERE SEASON = :season AND (WEEK_SEQ IS NULL OR WEEK_SEQ = :seq)`, then `COMMIT`.
4. `error(…)` naming every failed ERROR check.

It is bundled by Phase 2's `loader/bundle.py` (extended with a model entry) into `build/sql/model_refresh_model.sql` and installed by the model deploy. `loader/load-plan.yaml` gets:

```yaml
refresh_steps:
  - {step_no: 100, name: MODEL, script: CFB_INFRA.REFRESH_MODEL}
```

**Deployer** (`vs/host/sqlobjects.py`, generic. `model/deploy.py` binds it to `sql/model/`.)

```python
def load(directory: Path) -> list[SqlObject]                 # parse files in name order; kind, name, statements, columns, comments, references
def lint(objects, *, allowed_refs, reserved: frozenset[str]) -> list[str]   # every problem, "file: object: rule: detail"
def render(objects, namespace: Namespace) -> list[SqlObject]
def deploy(connection, schema: str, objects, *, extra_statements=()) -> DeployReport   # one transaction, orphans dropped, comments read back
```

**Namespace and targets.** `Namespace.model_schema` = `{prefix}CFB_MODEL`. All commands take `TARGET=local|aws` through the foundation's `vs/host/target.py`.

| Make target | Does |
|---|---|
| `make model [TARGET=…] [ARGS="--prefix TEST_"]` | lint, render, deploy the model, the step script and the check-log table; print the report |
| `make deploy` | virtual schema → loader → model |
| `make model-check SEASON=y WEEK=w [SEASON_TYPE=postseason] [TARGET=…]` | print `MODEL_CHECKS` for that point (no log, 0 calls) |
| `make model-docs` | regenerate `docs/model-dictionary.md` from `sql/model/` (no DB) |

**Synthetic league** (`tests/synthetic/league.py`; the facts the specs use)

| Season | Facts |
|---|---|
| 1871 | gap, no games |
| 1885 | Ashford vs Marston (not in the team list) at Old Grounds (no coordinates); no Elo |
| 1950 | Ashford 7 – Brookvale 7; Elo on every game |
| 1985 | Ashford (`fbs`) vs Lowell (`fcs`, no membership, Big Sky on its games); SEC schedule shaped like a championship week |
| 2000 | one game (BCS era) |
| 2013 | 25 completed FBS-involved games, 24 with lines; SP+; Fairhaven `fcs` on its games, `fbs` Mountain West membership |
| 2014 | 25 completed FBS-involved games, 20 with lines; Northam's conflicting spells; Mountain West divisions; Brookvale beats Ashford |
| 2023 | Carrow in the Pac-12 (SP+ 20.0); Desert Bowl Carrow vs Eastport |
| 2024 | Carrow in the SEC; SEC weeks 14 (two conference games) and 15 (Ashford vs Carrow only); Pac-12 ends with two games in week 14; CFP quarterfinal Ashford (1) vs Kingsley (8), Sugar Bowl, Neutral Dome; AP weeks 1–16 + postseason 1, CFP weeks 10–16, "Mystery Poll"; Ashford 10–2, bye in week 9, week-8 postgame Elo 1702, preseason Elo 1650; Ashford Field week 14 vs Brookvale 31–24, spread -7.5, home WP 0.70; "Ashford St." SP+ row |
| 2025 | complete; final SP+ |
| 2026 | weeks 1–9 completed, 10–15 scheduled; Ashford W W L W W W L W W; Carrow vs Dunmore week 3 stored twice; Dunmore moves to the Mountain West; Brookvale vs Ashford week 12; lines for weeks 10–11 |

Fetch times: seasons up to 2025 at 2026-10-20 10:00 UTC, 2026 at 2026-10-25 12:00 UTC. Week 9 of 2026 opens at 2026-10-22 23:30 and week 10 at 2026-10-29 23:30. `NOW_OVERRIDE` is 2026-10-26.

**Exasol claims checked against the `exasol-database` skill and the documentation**

| Claim | Evidence | Result |
|---|---|---|
| `CREATE OR REPLACE VIEW v ("C" COMMENT IS '…', …) AS … COMMENT IS '…'` | `exasol-grammar.md` `create_view_statement`, `…2`, `…3` | Confirmed |
| No `COMMENT ON VIEW` | `comment_object_statement` lists COLUMN, SCHEMA, FUNCTION, SCRIPT, USER, ROLE, CONNECTION, CONSUMER GROUP; `comment_table_statement` is `COMMENT ON [TABLE] table` | Confirmed for the grammar. Whether a view name is accepted as `table` is probe 1.1a; the design does not need it |
| `CREATE OR REPLACE TABLE` with column and table `COMMENT IS` | `create_table_statement_1–3`, `column_definition_2` | Confirmed |
| `CREATE FUNCTION f (p IN DOUBLE, …) RETURN DOUBLE IS v DOUBLE; BEGIN …; RETURN …; END f; /` | `create_function_statement_1–6` (at least one variable, at least one statement before `RETURN`) | Confirmed. Runtime cost: probe 1.1d |
| `GEOMETRY(srid)`; no `GEOGRAPHY` type | `exasol-sql.md` data types; docs example `geo GEOMETRY(4326)` | Confirmed |
| `ST_X`, `ST_Y`, `ST_WITHIN`, `ST_DISTANCE`, `ST_SETSRID`, `ST_TRANSFORM` exist; `ST_SRID` does not | `exasol-grammar-functions.md` `spatial_1–3` | Confirmed. The SRID is checked through the column type |
| Units of `ST_DISTANCE` on SRID 4326 | docs: "Minimal distance between two geospatial objects", no unit | Unverified (probe 1.1c). The model does not rely on it (`GREAT_CIRCLE_KM`) |
| `EXA_SPATIAL_REF_SYS` (`SRID`, `AUTH_NAME`, `AUTH_SRID`, `SRTEXT`, `PROJ4TEXT`) | docs | Columns confirmed; contents probe 1.1c |
| Comments readable: `EXA_ALL_OBJECTS.OBJECT_COMMENT`, `EXA_ALL_COLUMNS.COLUMN_COMMENT` / `COLUMN_TYPE` / `COLUMN_OBJECT_TYPE` | docs | Confirmed |
| View dependencies: `EXA_ALL_DEPENDENCIES` (`REFERENCED_OBJECT_SCHEMA`, direct only, `REFERENCE_TYPE` `VIEW`) | docs | Confirmed. The layering test walks the graph |
| DDL is transactional | docs transaction example rolls back a `CREATE TABLE` (implied, not stated) | Probe 1.1b |
| `QUALIFY`, window functions, `LISTAGG`, `RADIANS`, `SIN`, `COS`, `ASIN`, `SQRT`, `POWER`, `LEAST` | `analytics-qualify.md`, `exasol-grammar-functions.md` | Confirmed |
| Reserved words | every planned object and column name checked against `vs/gen/exasol_reserved_keywords.txt` (2026.1.0) | None reserved. `RESULT`, `STATE`, `SOURCE`, `LEVEL` and `VALUE` are avoided (`GAME_RESULT`, `STATE_CODE`, `CONFERENCE_SOURCE`) |
| Mirror columns `RANK`, `POINTS`, `YEAR_`, `STATE_` | catalog: the generator renames only reserved words | Confirmed. The model renames them to `POLL_RANK`, `POLL_POINTS`, `SEASON`, `STATE_CODE` |

### Consequences

| Decision | Alternatives Considered | Rationale |
|----------|------------------------|-----------|
| Every derived object is a view; no model step rebuild ([2]) | Materialize `TEAM_SEASON` / `GAME` / as-of snapshots, rebuilt by a `REFRESH_STEPS` step | Always consistent after backfill, refresh and restore, which a rebuild step would miss: backfill and restore run no steps. The data is small. A measured fallback rule keeps the door open |
| Event-time and knowledge-time visibility ([4], [7]) | Snapshot history tables per week; week labels only | Leak-free without history. Week labels alone would leak closing lines and end-of-season SP+. Snapshots would cost storage and refresh logic for a replay case the demo does not need |
| As-of addressed by `WEEK_SEQ` (0, n, 100 + n) ([5]) | Dates or timestamps; calendar-ordinal sequence | Matches `REFRESH_WEEK` exactly and works for seasons without a stored calendar. Postseason granularity is the known limit (Q3) |
| As-of views bounded to the point's season ([6]) | All earlier seasons inside each point | Prevents a 750M-row cross join from one careless chat query; earlier seasons are fully known anyway |
| Prior-season final SP+ as the leak-free prior ([7]) | Current-season SP+ at every point | The current SP+ includes later games; the prior exists for every season from 2014 (calibration 2014–2025) |
| Affiliation slices scoped to their request, newest slice wins, classification from games first ([9]) | Trust the range slice's open spells; affiliation classification only | Open spells in a static range slice would claim later seasons. Games record the team itself, which matters in transition years |
| Tiers as a reviewed reference table keyed by CFBD names, with an ERROR check ([10]) | Derive "power" from rankings; hard-code in Phase 4 | Power status is an era rule, not data. A renamed conference would silently break auto-bids, and the check stops the refresh instead |
| Structural rule for conference championship games ([11]) | `NOTES` text; calendar's last week | CFBD does not mark them. Army–Navy-type weeks and in-progress seasons defeat the last-week rule. To be verified against known counts (Q7) |
| `GEOMETRY(4326)` and `GREAT_CIRCLE_KM` ([12]) | `GEOGRAPHY` (not in Exasol); `ST_DISTANCE` after `ST_TRANSFORM` | The only spatial type. The planar distance of degrees is wrong, and a transform depends on `EXA_SPATIAL_REF_SYS` contents |
| Inline comments, grain-first rule, reserved-free names, a read-back check ([13]) | `COMMENT ON` statements after creation; free-form comments | No `COMMENT ON VIEW` exists. Inline comments are replaced atomically with the view. The grain rule makes the chat's job deterministic |
| Generic one-transaction SQL-object deployer that drops orphans ([14]) | Per-phase ad-hoc scripts; `CREATE` without drop | Phases 4–5 reuse it. Atomic deploys and no stale views |
| Model reads `CFB_META.LOAD_PLAN` / `LOAD_PLAN_GAPS` / `LOAD_SLICES` ([16]) | Derive "stored" from mirror rows; copy load state into the model | Empty and gap slices have no rows, and finality lives only in the loader's views. One reader per contract |
| `MODEL` step = checks, not a rebuild ([15]) | No model step; a rebuild step | Catches upstream renames before sims run and puts "model" on the timeline. The views need no rebuild |
| League rows from API-shaped bodies through the foundation flattener, inserted directly ([17]) | Hand-written mirror rows; staging every slice through the virtual schema in replay | Faithful rows without per-test virtual-schema cost. One generator-shape test still stages `fake.py` through the virtual schema |
| No pass-through views over `CFBD_VS` ([1]) | Federated views in the model | A model object could then trigger calls, against "the model costs zero calls". `CFB_API` owns on-demand access (architecture rule) |

## Features

| Feature | Status | Spec |
|---------|--------|------|
| model/deploy | NEW | `model/deploy/spec.md` |
| model/documentation | NEW | `model/documentation/spec.md` |
| model/seasons-eras | NEW | `model/seasons-eras/spec.md` |
| model/teams-conferences | NEW | `model/teams-conferences/spec.md` |
| model/games | NEW | `model/games/spec.md` |
| model/ratings-rankings | NEW | `model/ratings-rankings/spec.md` |
| model/venues | NEW | `model/venues/spec.md` |
| model/coverage | NEW | `model/coverage/spec.md` |
| model/as-of | NEW | `model/as-of/spec.md` |
| model/refresh-step | NEW | `model/refresh-step/spec.md` |

The Phase 2 specs (`loader/*`) are not changed. This plan uses their contracts:
- `loader/refresh-week`: registered steps, `(run_id, season, week, season_type)`, the timeline;
- `loader/load-plan`: `LOAD_PLAN`, `LOAD_SLICES`, finality, gaps.

It adds one `refresh_steps` entry to `loader/load-plan.yaml`. The foundation specs are not changed.

## Requirements

| Requirement | Details |
|-------------|---------|
| Zero CFBD calls | No model object references `CFBD_VS`: lint plus the dependency test (`model/deploy`). Integration tests use the synthetic league, the mock API and replay. The harness guard against `CFBD_API` stays. Live verification only reads `CFB_CORE` and the model; `/info` used calls (free) are unchanged across group 7 |
| Shared DB etiquette | Probe 1.1 and `make test-it` only in "local free" windows of `add-engine-spikes`. AWS `default`: deploy and query only, after "AWS free". No stop, start or resize. No DB session during planning |
| Layering | The model reads `CFB_CORE.*`, `CFB_META.{LOAD_PLAN, LOAD_PLAN_GAPS, LOAD_SLICES}` and itself. Analyses (Phases 4–5) read only `CFB_MODEL` and their own schemas. No grants to `CFB_APP` or `CFB_MCP` |
| Leak-free as-of | The invariant test (`model/as-of`) passes for 2024 week 9, and also for 2026 week 5 (same test, second point) |
| Self-description | 100 % object and column comments, read back after every deploy. Grain sentence first; ≤ 2,000 characters; no reserved column names; dictionary current |
| Exasol SQL | Identifiers in view bodies are double-quoted (skill rule). Every claim used is listed in Key Interfaces, and the unverified ones go through probe 1.1 |
| Atomic, idempotent deploy | One transaction (probe 1.1b, or its recorded fallback). A second deploy yields identical objects, comments and reference rows |
| UTC | All model timestamps are UTC as stored (foundation flattener). `CHECKED_AT` uses Phase 2's UTC expression |
| Ownership | `sys` owns the real model (foundation [20]); `CFB_TEST_OWNER` the test namespace |
| No CFBD data in git | The league is synthetic (fictional teams; real conference names only as reference keys). `docs/model-verification.md` holds derived numbers only. The dictionary and the examples hold no data |
| Runtime code | Host Python 3.12 with pyexasol 2.x and PyYAML; Lua as built into Exasol scripting; no new dependencies |
| Performance (measured; fallback rule) | Warm, query cache off, on AWS and on local after restore: `TEAM_SEASON_AS_OF` and `GAME_AS_OF` for one point < 1 s; `SELECT SEASON, COUNT(*) FROM GAME GROUP BY 1` < 2 s; full `SEASON_COVERAGE` < 3 s; each example < 3 s; `REFRESH_MODEL` < 5 s. A view more than 2× over its target is materialized as a table rebuilt by `REFRESH_MODEL`, decided and logged in task 7.3 |

## Dependencies

- **`add-cfbd-vs-foundation`** (in flight):
  - all groups through I, before this plan's group B;
  - its group J task 9.4 (S1 findings applied, catalog final), before group 7;
  - P1, the `RANKINGS_POLLS_RANKS` parent keys: present in the current catalog, including `SEASON_TYPE`;
  - P2, `vs/host/target.py`: present, used by `scripts/model.py`.
- **`add-cfb-persistence-refresh`** (reviewed, waiting for the foundation). This plan needs, before its group B:
  - its 3.1 (meta renderer with placeholders), 3.2 (`LOAD_SLICES`), 3.3 (loader deploy), 4.1 (`loader/bundle.py`), 4.3 (step registry in `refresh_week.lua`) and 6.1/6.2 (harness: `deploy_plan`, `set_now`, `refresh`, step test scripts, calendar fake);
  - its group I (docs), before this plan's group 6;
  - its group J (AWS demo backfill, snapshot `aws-demo-<date>`), before group 7.

  This plan's task 2.1 calls the model deploy from `scripts/deploy.py` after the loader deploy.
- **`add-engine-spikes`** (parallel):
  - it owns the exclusive benchmark windows;
  - it edits `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md` (D19, D20); this plan edits them only in group 6, after its task 4.1 has landed, re-reading each file first;
  - its S3 input shape informs the engine-input example (`as_of_engine_inputs.sql`); this plan does not depend on its results.
- **Tools.** None new: `uv`, pyexasol 2.4.1, PyYAML.
- **User prerequisites:** U1, the "local free" and "AWS free" signals; U2, Phase 2's AWS snapshot available under `~/.cfb-demo/snapshots/`. No `CONFIRM`: nothing in this plan spends calls.

## Migration

No data migration: `CFB_MODEL` is new and holds no data of its own.
- `make deploy` and `make model` create `CFB_MODEL`, `CFB_META.MODEL_CHECK_LOG` and `CFB_INFRA.REFRESH_MODEL`.
- `loader/load-plan.yaml`'s `refresh_steps` changes from `[]` to the `MODEL` entry. The next `make deploy` registers it, and the next `REFRESH_WEEK` runs it.
- `vs/host/namespace.py` gains `model_schema`. Phase 2's placeholder renderer gains `{MODEL_SCHEMA}`.
- `tests/it/harness.py`:
  - `drop_namespace` and `leftovers` cover `{prefix}CFB_MODEL`;
  - new helpers `deploy_model`, `seed_league`, `write_league_recordings`;
  - `reset_state` leaves the model's reference tables alone (they are in `CFB_MODEL`, which it never truncates);
  - a test plan variant `tests/it/fixtures/load-plan.model.yaml`.
- `.gitignore`: no change (`build/` is already ignored).

## Implementation Tasks

Tasks are TDD per `/speq:code-guardrails`. Host code writes its unit tests first. Each model-object task writes its integration test file first (failing), then the SQL files, until the tests pass. Lua has no unit tier.

1. **Probe, namespace, parser, league, seeds (no DB except 1.1)**
   - [ ] 1.1 Model platform probe `docs/spikes/s6_model_probe.py`, findings in `docs/spikes/s6-model-probe.md`.
     - Rules: sys on local Personal in a "local free" window, `PROBE_*` objects only, 0 CFBD calls. `--target aws` re-runs the read-only items in 7.1. Each item records the adopted fallback.
     - (a) Inline view and table comments, read back via `EXA_ALL_OBJECTS` and `EXA_ALL_COLUMNS`. Also: `COMMENT ON TABLE <view>`, the maximum comment length, `''` in comments.
     - (b) DDL in a transaction (pyexasol autocommit off): `CREATE OR REPLACE` view, table and function, `DROP VIEW`, `INSERT`, then `ROLLBACK`. Do the previous objects, rows and comments come back? Fallback: deploy to `{prefix}CFB_MODEL_NEXT` first as validation, then to the real schema (not atomic; documented).
     - (c) `GEOMETRY(4326)`:
       - a POINT built from two `DOUBLE` columns (concatenation vs `TO_CHAR`), with an `ST_X`/`ST_Y` round trip within 1e-9;
       - the view column type in `EXA_ALL_COLUMNS`;
       - `ST_DISTANCE` units on 4326;
       - `ST_WITHIN` with a 4326 polygon (literal vs `ST_SETSRID`);
       - `EXA_SPATIAL_REF_SYS` rows for 4326 and 5070, and `ST_TRANSFORM`.
     - (d) `CREATE FUNCTION` per Key Interfaces used in a view: results, NULL handling, cost on 1M rows vs the inline expression. Fallback: inline the haversine in `TEAM_GAME` and keep the function for ad-hoc use.
     - (e) `EXA_ALL_DEPENDENCIES` for view → view → table, view → `CFB_META` view, and view → function.
     - (f) Creating a view over a missing object fails (file order matters). The `STATUS` of a view after its referenced table is replaced.
     - (g) A filter on the point columns of a view joining a parameter view to a windowed fact view (`QUALIFY`) is applied before the join: rows read in the profile.
     - (h) A nested Lua step that inserts, `COMMIT`s and then `error()`s: the committed rows survive the caller's rollback. Reuse Phase 2's probe 1.1b/e findings where they cover it.
     - **Gate:** (a) or (b) negative → apply the fallback. (c) fixes the `GEO_POINT` expression. (d) negative → the fallback above.
   - [ ] 1.2 `Namespace.model_schema`, and `{MODEL_SCHEMA}` in Phase 2's placeholder renderer (`vs/host/metaddl.py`). Unit tests in `tests/unit/test_namespace.py` and `test_meta_ddl.py`.
   - [ ] 1.3 `vs/host/sqlobjects.py`, pure part (`load`, `lint`, `render`) per Key Interfaces:
     - statement splitting (`;`, and the `/` line for functions and scripts);
     - object kind and name;
     - declared columns with their comments, and the object comment;
     - references (`{X_SCHEMA}."NAME"` and bare `SCHEMA.NAME`), and placeholders.

     Lint rules per `model/deploy` and `model/documentation`, with reserved words from `vs/gen/exasol_reserved_keywords.txt`. Every problem is reported, none stops at the first. Unit tests `tests/unit/test_sqlobjects.py` with fixture files in `tests/unit/fixtures/sqlobjects/`.
   - [ ] 1.4 Synthetic league `tests/synthetic/league.py`:
     - API-shaped response bodies per slice, holding exactly the facts in Key Interfaces and the spec scenarios;
     - mirror rows from the foundation's flattener (`vs.cfbd_vs.flatten`), plus `P_*` echoes and `REQ_*`, with slice keys from Phase 2's templates and fetch times as specified;
     - `LOAD_LOG` registrations (final up to 2025, non-final 2026);
     - replay-recording export for the 2026 weekly slices;
     - variants: `mutate_after(point)` (the invariance test), `with_unaffiliated_fbs_team`, `with_incomplete_week9_game`, `with_renamed_conference`, `with_duplicate_game`.

     Unit tests `tests/unit/test_league.py`: rows typed per catalog mirror columns, slice keys equal `build_slice_key`, deterministic output, and the scenario counts (2013 24/25, 2014 20/25, Ashford 2024 10–2, Ashford 2026 7–2 at week 9).
   - [ ] 1.5 Reference seeds `sql/model/010_era.sql` … `014_dataset.sql`, with comments, per the specs and Key Interfaces. Unit tests `tests/unit/test_model_seeds.py`:
     - the eras of each kind are contiguous and do not overlap;
     - every `CLASSIFICATION_LABEL` era × code exists;
     - `DATASET` declared seasons and gaps equal `docs/cfbd-data-availability.md` (parsed table);
     - every `VS_TABLE` exists in `vs/catalog/catalog.json` and every `CORE_TABLE` in `sql/core/cfb_core_mirrors.sql`;
     - stored datasets equal the `demo` entries of `loader/load-plan.yaml`.

2. **Deployer, meta table, dictionary, harness**
   - [ ] 2.1 Deploy part of `vs/host/sqlobjects.py` (`deploy`):
     - one transaction per probe 1.1b, or its fallback;
     - create the schema, run statements in file order, drop orphans limited to the schema, read comments back, roll back on any failure;
     - report lines.

     Then:
     - `model/deploy.py` binds `sql/model/`, the step script from `build/sql/model_refresh_model.sql` (skipped with a notice until 4.1) and the namespace.
     - `scripts/model.py` has the subcommands `deploy`, `check` (prints `MODEL_CHECKS` for a point) and `docs`, all through `vs/host/target.py`. Make targets `model`, `model-check` and `model-docs`.
     - `scripts/deploy.py` calls the model deploy after the loader deploy.

     Unit tests `tests/unit/test_model_deploy.py` use a fake connection: statement order, rollback on failure, orphan drop, the comment read-back failure. CLI tests `tests/cli/test_model_cli.py`: lint errors listed with nothing sent, `--prefix`, exit codes.
   - [ ] 2.2 `sql/meta/model_check_log.sql`, registered in `vs/host/metaddl.py`; unit test.
   - [ ] 2.3 `model/dictionary.py` and `make model-docs` → `docs/model-dictionary.md` (grain, columns, comments per object, in file order). Unit test `tests/unit/test_model_dictionary.py`: the committed file equals the generated one.
   - [ ] 2.4 Harness (`tests/it/harness.py`, `tests/it/conftest.py`):
     - model schema in `drop_namespace` and `leftovers`;
     - `deploy_model()`;
     - `seed_league(variant=None)`: direct inserts into the mirrors, `LOAD_LOG`, `NOW_OVERRIDE` 2026-10-26;
     - `write_league_recordings(...)`;
     - a module-scoped `league` fixture; read-only model tests (marker `model_readonly`) skip the per-test truncation, and mutating tests re-seed;
     - the plan variant `tests/it/fixtures/load-plan.model.yaml`: demo-like entries with the league's seasons and gaps, step 100 `TEST_CFB_INFRA.REFRESH_MODEL`, step 200 `TEST_STEP_RECORD`.
   - [ ] 2.5 Deploy integration tests on fixture model directories (`tests/it/fixtures/model_sql/{valid,lint_errors,db_error,orphan}`), in `tests/it/test_model_deploy_it.py`: lint stops before any statement, a failing statement rolls back, the orphan is dropped, the model never reads the virtual schema (dependency walk + mock hits + `FETCH_LOG`).

3. **Model objects** (each task: integration test file first, then `sql/model/` files; the scenarios it makes pass are listed in Verification)
   - [ ] 3.1 `100_game.sql`, `110_season_week.sql`, `120_season.sql`:
     - deduplication by game id (the newest `REQ_AT` wins);
     - `WEEK_SEQ`, results, the line join, postseason kinds, the conference-championship rule (`model/games`), coverage flags, `PLAYS_ACCESS`;
     - week dates from the calendar or the games;
     - seasons from 1869 with eras and counts.

     Tests: `tests/it/test_model_games_it.py` (game parts), `tests/it/test_model_seasons_it.py`.
   - [ ] 3.2 `020_great_circle_km.sql`, `130_venue.sql`, `140_team.sql`, `150_conference.sql`: the point per probe 1.1c, the function, teams including game-only teams, conferences. Tests: `tests/it/test_model_venues_it.py` (point, no coordinates, kilometres, `ST_WITHIN`), and the game-only team test in `test_model_teams_it.py`.
   - [ ] 3.3 `160_rating_sp.sql`, `170_ranking.sql`: name matching, `IS_FINAL` from `LOAD_SLICES`, poll codes, visibility. Tests: `tests/it/test_model_ratings_it.py` (except prior and final ranks, which come in 3.5).
   - [ ] 3.4 `200_team_game.sql`: sides, results, team-side spread and win probability, `TRAVEL_KM`. Tests: the team-game, travel and head-to-head tests (the head-to-head example file is written here and reused in 4.2).
   - [ ] 3.5 `210_team_season.sql`, `220_conference_season.sql`: [expert]
     - membership expansion scoped to each slice's request, newest-slice precedence, conflict resolution and flag;
     - conference from the games when there is no membership;
     - classification from the games first;
     - tier lookup including the NULL-name rows;
     - division labels and the retroactive flag;
     - season records; Elo first and last; SP+ and prior; final ranks;
     - conference-season aggregates.

     Tests: `tests/it/test_model_teams_it.py`, the division-label test in `test_model_seasons_it.py`, and the prior and final-rank tests in `test_model_ratings_it.py`.
   - [ ] 3.6 `300_as_of_point.sql` … `340_team_season_as_of.sql`, per `model/as-of`: [expert]
     - points, cutoffs and the current point;
     - status and masking in `GAME_AS_OF`;
     - the latest visible week per poll;
     - records, Elo, ranks and SP+ at the point.

     Tests: `tests/it/test_model_as_of_it.py`, including the leak-free invariant for 2024 week 9 and 2026 week 5 (`league.mutate_after`, then `MINUS` both ways empty) and its positive control.
   - [ ] 3.7 `400_season_coverage.sql`: declared × planned × stored × measured, the trust-level order, and the notes. Tests: `tests/it/test_model_coverage_it.py`.
   - [ ] 3.8 `500_model_checks.sql`: the 11 checks of `model/refresh-step`, with `DETAIL` listing up to 10 offenders. Tests: the view part of `tests/it/test_model_refresh_step_it.py` (renamed power conference), and the check assertions used by 3.1, 3.3, 3.5 and 3.2.

4. **Refresh step and examples**
   - [ ] 4.1 `model/lua/refresh_model.lua` per Key Interfaces, every statement through `pquery`:
     - `loader/bundle.py` gains a model entry (no Lua logic) that writes `build/sql/model_refresh_model.sql`;
     - the model deploy installs it in `{INFRA_SCHEMA}`;
     - `loader/load-plan.yaml` registers step 100 `MODEL`.

     Test first: `tests/it/test_model_refresh_step_it.py` (chain order, error stops the chain, warnings pass, outside a refresh, unknown point, `make model-check`). Bundle unit tests: no bare `query(`, no `ALTER VIRTUAL SCHEMA`.
   - [ ] 4.2 Example analyses `sql/model/examples/{as_of_engine_inputs,all_time_upsets,head_to_head,cfp_field_inputs}.sql`, with a header comment (question, consumer phase, objects read). Tests `tests/it/test_model_docs_it.py`: comments, grain, unquoted columns, dictionary vs database, examples with expected league answers and the dependency walk. Final-model tests in `tests/it/test_model_deploy_it.py`: install everything, a second deploy, the full deploy, generator shapes from `fake.py`.

5. **Quality gate**
   - [ ] 5.1 Lint, coverage and the integration suite:
     - `make lint` is clean (`mypy` `files` += `model`, `vs/host/sqlobjects.py`);
     - `make coverage` reaches ≥ 90 % line coverage for `model` and `vs/host/sqlobjects.py` (Makefile: add `coverage report --include="model/*,vs/host/sqlobjects.py"`);
     - `make test-it` is green, including the foundation and loader suites;
     - `make model-docs && git diff --exit-code docs/model-dictionary.md`.

6. **Documentation** (after `add-engine-spikes` 4.1 and Phase 2's group I. Re-read each file first, and only append to decision tables.)
   - [ ] 6.1 `docs/user-guide.md` § "The analytical model":
     - the layout and the object list with grain;
     - as-of points and the `REFRESH_WEEK` mapping, with examples;
     - trust levels and on-demand datasets (which virtual-schema table, with required filters);
     - `GEOMETRY(4326)` and `GREAT_CIRCLE_KM`;
     - `make model`, `make model-check`, `make model-docs`;
     - the examples, and the `MODEL` refresh step and its log.
   - [ ] 6.2 `specs/architecture.md`:
     - The `CFB_MODEL` component: views only, the reference tables, reads from `CFB_CORE` + `CFB_META.{LOAD_PLAN, LOAD_PLAN_GAPS, LOAD_SLICES}`, `GEOMETRY(4326)` instead of `GEOGRAPHY`, as-of semantics, refresh step 100.
     - Replace the aspirational dimension and fact list with the stored-only model, and name the on-demand datasets.
     - The schema table: `CFB_MODEL` "Reads from"; `CFB_META.MODEL_CHECK_LOG`; `CFB_INFRA.REFRESH_MODEL`.
     - Open question: as-of granularity inside the postseason (Q3).
   - [ ] 6.3 `docs/roadmap.md`:
     - decisions from the decision-log entries marked `Promotes to ADR: yes`, numbered after the last D-number present;
     - Phase 3 bullets: "venue `GEOMETRY(4326)`", the done-when proof by example analyses;
     - backlog: in-season snapshot history (SP+ and lines per week), if replays of earlier current-season weeks are ever needed;
     - the Phase 5 note: head-to-head from stored games (decision [18]);
     - mark Phase 3 progress.
   - [ ] 6.4 `CLAUDE.md` dense bullets:
     - the model is views only, never reads `CFBD_VS`, and lint rules apply (comments, grain, reserved words);
     - `make model`, `make model-check`, `make model-docs`;
     - the as-of rule (event time vs fetch time);
     - `GEOMETRY(4326)` + `GREAT_CIRCLE_KM`;
     - `MODEL_CHECKS` errors stop the refresh.

     `specs/mission.md`:
     - § Commands: `make model`, `make model-check`, `make model-docs`;
     - Core Capability 5: "venue `GEOMETRY(4326)` point (Exasol has no `GEOGRAPHY` type)";
     - Glossary "As-of (season, week)": "after that week's games, before the next week's first kickoff".

7. **Live verification (last; zero CFBD calls; AWS `default` after "AWS free", local in a "local free" window)**
   - [ ] 7.1 Gates and deploy:
     - gates: Phase 2's group J is done (AWS demo backfill + snapshot), and the foundation's 9.4 is done;
     - record `make quota TARGET=aws` (`/info` used calls, free) and `SELECT MAX(LOGGED_AT) FROM CFB_META.FETCH_LOG`;
     - `make deploy TARGET=aws`;
     - re-run probe 1.1 (a, c, e) with `--target aws`;
     - the deploy report shows 100 % comments.
   - [ ] 7.2 Checks and assumptions on AWS:
     - `make model-check TARGET=aws` for `CURRENT_AS_OF`;
     - `SELECT CHECK_NAME, SEASON, OBSERVED, DETAIL FROM CFB_MODEL.MODEL_CHECKS WHERE CHECK_STATUS = 'FAIL'` over all seasons;
     - verify Q1–Q12 (decision log) with the queries listed there;
     - fix the reference seeds where they differ (conference names, poll names, label leads, tier names) and redeploy (0 calls);
     - record every outcome.
   - [ ] 7.3 Performance and examples:
     - timings per the Requirements row (query cache off, 3 warm-ups, 5 timed runs, median and max) on AWS;
     - run the four examples and keep only derived numbers (row counts, seconds);
     - apply the materialization rule if a target is missed by more than 2×. If it is, log the decision and add a follow-up task; do not change the design inside this task.
   - [ ] 7.4 Local:
     - in a "local free" window, `make restore TARGET=local NAME=<Phase 2 AWS snapshot> CONFIRM=yes` (if not yet restored), then `make deploy TARGET=local`;
     - repeat 7.2's failed-check summary and 7.3's timings;
     - `EXECUTE SCRIPT CFB_INFRA.REFRESH_MODEL(NULL, <current season>, <current week>, 'regular')`.
   - [ ] 7.5 Zero-call proof and write-up:
     - `make quota TARGET=aws`: used calls equal 7.1's value, and `FETCH_LOG` has no row after 7.1's maximum;
     - `docs/model-verification.md`, derived numbers only: trust-level counts per dataset, check results, assumption outcomes, timings, the AWS and local versions.

## Parallelization

| Parallel Group | Tasks |
|----------------|-------|
| Group A | 1.1, 1.2, 1.3, 1.4, 1.5 |
| Group B | 2.1, 2.2, 2.3, 2.4 |
| Group C | 2.5, 3.1, 3.2, 3.3 |
| Group D | 3.4 |
| Group E | 3.5 |
| Group F | 3.6, 3.7, 3.8 |
| Group G | 4.1, 4.2 |
| Group H | 5.1 |
| Group I | 6.1, 6.2, 6.3, 6.4 |
| Group J (sequential, gated) | 7.1 → 7.2 → 7.3 → 7.4 → 7.5 |

Sequential dependencies:
- Group A → B → C → D → E → F → G → H → I → J.
- Before group B: the foundation through group I, and Phase 2 tasks 3.1, 3.2, 3.3, 4.1, 4.3, 6.1 and 6.2.
- 1.1 needs a "local free" window. 2.1 needs 1.1(b), 1.2 and 1.3. 2.4 needs 1.4 and Phase 2's 6.1. 2.5 needs 2.1 and 2.4.
- 3.x need 1.5, 2.1 and 2.4. 3.2 needs 1.1(c, d). 3.4 needs 3.1 and 3.2. 3.5 needs 3.3 and 3.4. 3.6 needs 3.1, 3.3 and 3.5. 3.7 needs 3.1 and 3.5. 3.8 needs 3.1–3.5.
- 4.1 needs 3.6, 3.8 and Phase 2's 4.1 and 4.3. 4.2 needs 3.1–3.8.
- Group I needs `add-engine-spikes` 4.1 and Phase 2's group I.
- Group J needs green groups A–I, Phase 2's group J, the foundation's 9.4, and U1–U2.

## Dead Code Removal

None. This plan adds new objects only, and every helper it touches (`tests/it/harness.py`, `vs/host/metaddl.py`, `loader/bundle.py`) stays in use.

## Verification

### Scenario Coverage

| Scenario | Test Type | Test Location | Test Name |
|----------|-----------|---------------|-----------|
| deploy: One command installs the whole model | Integration | `tests/it/test_model_deploy_it.py` | `test_make_model_installs_every_object` |
| deploy: A second deploy changes nothing | Integration | `tests/it/test_model_deploy_it.py` | `test_second_deploy_changes_nothing` |
| deploy: The full deploy includes the model | Integration | `tests/it/test_model_deploy_it.py` | `test_full_deploy_includes_model` |
| deploy: A file that breaks a lint rule stops the deploy before any statement | Integration (CLI + DB) | `tests/cli/test_model_cli.py`, `tests/it/test_model_deploy_it.py` | `test_lint_errors_listed_nothing_sent`, `test_lint_failure_keeps_deployed_model` |
| deploy: A failing statement rolls the whole deploy back | Integration | `tests/it/test_model_deploy_it.py` | `test_failing_statement_rolls_back` |
| deploy: An object that no file defines any more is dropped | Integration | `tests/it/test_model_deploy_it.py` | `test_orphan_object_dropped` |
| deploy: The model accepts any data the virtual schema can store | Integration | `tests/it/test_model_deploy_it.py` | `test_model_over_generated_shapes` |
| deploy: The model never reads the virtual schema | Integration | `tests/it/test_model_deploy_it.py` | `test_model_never_reads_virtual_schema` |
| documentation: Every object and column has a comment | Integration | `tests/it/test_model_docs_it.py` | `test_every_object_and_column_commented` |
| documentation: Every object comment states its grain | Integration | `tests/it/test_model_docs_it.py` | `test_object_comments_state_grain` |
| documentation: Model columns need no quoting | Integration | `tests/it/test_model_docs_it.py` | `test_columns_need_no_quoting` |
| documentation: The data dictionary matches the deployed comments | Integration | `tests/it/test_model_docs_it.py` | `test_dictionary_matches_deployed_comments` |
| documentation: The example analyses read only the model | Integration | `tests/it/test_model_docs_it.py` | `test_examples_read_only_model` |
| seasons-eras: Every season carries its eras | Integration | `tests/it/test_model_seasons_it.py` | `test_season_eras` |
| seasons-eras: A season without games is still listed | Integration | `tests/it/test_model_seasons_it.py` | `test_season_without_games_listed` |
| seasons-eras: Division labels follow the era | Integration | `tests/it/test_model_seasons_it.py` | `test_division_labels_follow_era` |
| seasons-eras: Weeks take their dates from the calendar when it is stored | Integration | `tests/it/test_model_seasons_it.py` | `test_week_dates_from_calendar_or_games` |
| seasons-eras: The postseason follows the regular season in one week order | Integration | `tests/it/test_model_seasons_it.py` | `test_week_sequence_order` |
| seasons-eras: Only the newest season is current | Integration | `tests/it/test_model_seasons_it.py` | `test_only_newest_season_current` |
| teams-conferences: Membership follows realignment | Integration | `tests/it/test_model_teams_it.py` | `test_membership_follows_realignment` |
| teams-conferences: A stored slice speaks only for the seasons it was requested for | Integration | `tests/it/test_model_teams_it.py` | `test_slice_speaks_only_for_its_seasons` |
| teams-conferences: Conflicting memberships are resolved and flagged | Integration | `tests/it/test_model_teams_it.py` | `test_membership_conflict_flagged` |
| teams-conferences: Tiers follow the era | Integration | `tests/it/test_model_teams_it.py` | `test_tiers_follow_era` |
| teams-conferences: A team without a membership takes its conference from its games | Integration | `tests/it/test_model_teams_it.py` | `test_conference_from_games` |
| teams-conferences: The games decide the classification | Integration | `tests/it/test_model_teams_it.py` | `test_classification_from_games` |
| teams-conferences: Teams that appear only in games are listed | Integration | `tests/it/test_model_teams_it.py` | `test_game_only_teams_listed` |
| teams-conferences: Divisions are kept per season | Integration | `tests/it/test_model_teams_it.py` | `test_divisions_per_season` |
| teams-conferences: Season records count completed games only | Integration | `tests/it/test_model_teams_it.py` | `test_season_records` |
| games: A game stored twice appears once | Integration | `tests/it/test_model_games_it.py` | `test_duplicate_game_appears_once` |
| games: Results name the winner, and a tie has none | Integration | `tests/it/test_model_games_it.py` | `test_results_winner_and_tie` |
| games: Pregame lines join by game id | Integration | `tests/it/test_model_games_it.py` | `test_pregame_lines_join_by_game` |
| games: Postseason games are typed | Integration | `tests/it/test_model_games_it.py` | `test_postseason_kinds` |
| games: A conference championship game is recognized by its schedule | Integration | `tests/it/test_model_games_it.py` | `test_conference_championship_rule` |
| games: Every game carries its coverage flags | Integration | `tests/it/test_model_games_it.py` | `test_game_coverage_flags` |
| games: Each team sees the game from its side | Integration | `tests/it/test_model_games_it.py` | `test_team_game_sides` |
| games: The head-to-head series comes from stored games | Integration | `tests/it/test_model_games_it.py` | `test_head_to_head_from_stored_games` |
| ratings-rankings: SP+ ratings attach to teams by name | Integration | `tests/it/test_model_ratings_it.py` | `test_sp_team_matching` |
| ratings-rankings: The previous season's final SP+ is the prior | Integration | `tests/it/test_model_ratings_it.py` | `test_sp_prior` |
| ratings-rankings: The current season's SP+ is marked as not final | Integration | `tests/it/test_model_ratings_it.py` | `test_current_sp_not_final` |
| ratings-rankings: Polls are coded and placed in the week order | Integration | `tests/it/test_model_ratings_it.py` | `test_polls_coded_and_ordered` |
| ratings-rankings: An unknown poll is kept and flagged | Integration | `tests/it/test_model_ratings_it.py` | `test_unknown_poll_flagged` |
| ratings-rankings: Final ranks belong to the team-season | Integration | `tests/it/test_model_ratings_it.py` | `test_final_ranks` |
| venues: A venue location is a point with SRID 4326 | Integration | `tests/it/test_model_venues_it.py` | `test_venue_point_srid_4326` |
| venues: A venue without coordinates has no point | Integration | `tests/it/test_model_venues_it.py` | `test_venue_without_coordinates` |
| venues: Great-circle distances are in kilometres | Integration | `tests/it/test_model_venues_it.py` | `test_great_circle_km` |
| venues: Travel distance per team and game | Integration | `tests/it/test_model_venues_it.py` | `test_travel_km` |
| venues: Venue points work with Exasol's spatial functions | Integration | `tests/it/test_model_venues_it.py` | `test_spatial_within` |
| coverage: A known gap is not available | Integration | `tests/it/test_model_coverage_it.py` | `test_known_gap_not_available` |
| coverage: A finished season with full coverage is complete | Integration | `tests/it/test_model_coverage_it.py` | `test_complete_season` |
| coverage: Low measured coverage is partial | Integration | `tests/it/test_model_coverage_it.py` | `test_partial_season` |
| coverage: The current season is in progress | Integration | `tests/it/test_model_coverage_it.py` | `test_current_season_in_progress` |
| coverage: Data that is declared but not stored is on demand | Integration | `tests/it/test_model_coverage_it.py` | `test_declared_not_stored_on_demand` |
| coverage: Planned data that is not loaded yet is missing | Integration | `tests/it/test_model_coverage_it.py` | `test_planned_not_loaded_missing` |
| coverage: Elo coverage starts in 1890 | Integration | `tests/it/test_model_coverage_it.py` | `test_elo_coverage_from_1890` |
| coverage: The dataset list names what the model does not store | Integration | `tests/it/test_model_coverage_it.py` | `test_dataset_catalog` |
| as-of: As-of points match the refresh arguments | Integration | `tests/it/test_model_as_of_it.py` | `test_points_match_refresh_arguments` |
| as-of: The current point is the newest week with a completed game | Integration | `tests/it/test_model_as_of_it.py` | `test_current_as_of` |
| as-of: Results after the point are hidden | Integration | `tests/it/test_model_as_of_it.py` | `test_results_after_point_hidden` |
| as-of: Records count only games up to the point | Integration | `tests/it/test_model_as_of_it.py` | `test_records_up_to_point` |
| as-of: A line is visible only once it was known | Integration | `tests/it/test_model_as_of_it.py` | `test_line_visibility` |
| as-of: The current season's SP+ is visible only from the point when it was fetched | Integration | `tests/it/test_model_as_of_it.py` | `test_sp_visibility` |
| as-of: Polls become visible when they are released | Integration | `tests/it/test_model_as_of_it.py` | `test_poll_visibility` |
| as-of: Team Elo uses only completed games | Integration | `tests/it/test_model_as_of_it.py` | `test_team_elo_as_of` |
| as-of: No fact after the point changes an as-of answer | Integration | `tests/it/test_model_as_of_it.py` | `test_leak_free_invariant` |
| refresh-step: The refresh runs the model step after the fetch | Integration | `tests/it/test_model_refresh_step_it.py` | `test_refresh_runs_model_step` |
| refresh-step: A failed error check stops the chain | Integration | `tests/it/test_model_refresh_step_it.py` | `test_error_check_stops_chain` |
| refresh-step: Warnings do not stop the chain | Integration | `tests/it/test_model_refresh_step_it.py` | `test_warnings_do_not_stop_chain` |
| refresh-step: The checks run for any point outside a refresh | Integration | `tests/it/test_model_refresh_step_it.py` | `test_checks_outside_refresh` |
| refresh-step: A renamed power conference is an error for its season | Integration | `tests/it/test_model_refresh_step_it.py` | `test_renamed_power_conference` |
| refresh-step: An unknown point is rejected | Integration | `tests/it/test_model_refresh_step_it.py` | `test_unknown_point_rejected` |
| refresh-step: The checks can be printed without the step | Integration (CLI + DB) | `tests/it/test_model_refresh_step_it.py` | `test_model_check_command` |

Supporting unit tests (pure computation, no I/O) back the integration tests. They do not replace them:
- parser, lint and render: `tests/unit/test_sqlobjects.py`;
- deploy order, rollback, orphans and read-back with a fake connection: `tests/unit/test_model_deploy.py`;
- seeds against the docs, the catalog and the load plan: `tests/unit/test_model_seeds.py`;
- the dictionary: `tests/unit/test_model_dictionary.py`;
- the league: `tests/unit/test_league.py`;
- the bundle: `tests/unit/test_lua_bundle.py` (extended);
- the namespace and renderer: `test_namespace.py`, `test_meta_ddl.py`;
- CLI: `tests/cli/test_model_cli.py`.

### Manual Testing

All commands run from the repo root against Exasol Personal local (`TARGET=local`) after `make deploy`, unless the row names AWS. None spends a CFBD call.

| Feature | Command | Expected Output |
|---------|---------|-----------------|
| model/deploy | `make model`, then again; `make model TARGET=aws` | Report: 5 tables, 1 function, 18 views, comments 100 %; the second run reports the same objects and drops nothing |
| model/documentation | `make model-docs && git diff --stat docs/model-dictionary.md`; `SELECT OBJECT_NAME, OBJECT_COMMENT FROM EXA_ALL_OBJECTS WHERE ROOT_NAME = 'CFB_MODEL' ORDER BY 1` | No diff; every object commented, each comment starting with "One row per" (the function excepted) |
| model/seasons-eras | `SELECT SEASON, DIVISION_ERA_CODE, POSTSEASON_ERA_CODE, CFP_TEAMS, TIES_POSSIBLE, GAME_COUNT FROM CFB_MODEL.SEASON WHERE SEASON IN (1869, 1871, 1950, 1985, 2013, 2024, 2026) ORDER BY 1` | One row each; 1871 with 0 games; 2024 `CFP_12`, 12 |
| model/teams-conferences | `SELECT SEASON, CONFERENCE_NAME, CONFERENCE_TIER, IS_POWER_CONFERENCE, CONFERENCE_SOURCE FROM CFB_MODEL.TEAM_SEASON WHERE SCHOOL = 'Texas' AND SEASON BETWEEN 2022 AND 2026 ORDER BY 1` | Big 12 through 2023 (`P5`), SEC from 2024 (`P4`), source `AFFILIATION` |
| model/games | `SELECT SEASON, COUNT(*), SUM(CASE WHEN IS_CONF_CHAMPIONSHIP THEN 1 ELSE 0 END) FROM CFB_MODEL.GAME WHERE INVOLVES_FBS AND SEASON BETWEEN 2014 AND 2025 GROUP BY 1 ORDER BY 1`; run `sql/model/examples/head_to_head.sql` for Michigan and Ohio State | Championship-game counts per season that match the known counts (Q7); the all-time series with the last and next meeting, 0 calls |
| model/ratings-rankings | `SELECT POLL_CODE, MIN(SEASON), COUNT(DISTINCT SEASON) FROM CFB_MODEL.RANKING GROUP BY 1` | `AP`, `COACHES`, `CFP` from 2014 (stored); no `OTHER` after the seed fixes |
| model/venues | `SELECT VENUE_NAME, ST_X(GEO_POINT), ST_Y(GEO_POINT) FROM CFB_MODEL.VENUE WHERE HAS_LOCATION LIMIT 5`; `SELECT AVG(TRAVEL_KM) FROM CFB_MODEL.TEAM_GAME WHERE SEASON = 2024 AND SITE = 'AWAY'` | Longitude/latitude pairs; a plausible average away trip in km |
| model/coverage | `SELECT DATASET_CODE, TRUST_LEVEL, COUNT(*) FROM CFB_MODEL.SEASON_COVERAGE GROUP BY 1, 2 ORDER BY 1, 2` | `GAMES` `COMPLETE` for stored final seasons, `NOT_AVAILABLE` for 1871, `IN_PROGRESS` for 2026; `SP_PLUS` `ON_DEMAND` 1970–2012 |
| model/as-of | `SELECT * FROM CFB_MODEL.CURRENT_AS_OF`; `SELECT SCHOOL, WINS, LOSSES, ELO_AS_OF, AP_RANK, CFP_RANK FROM CFB_MODEL.TEAM_SEASON_AS_OF WHERE AS_OF_SEASON = 2024 AND AS_OF_WEEK_SEQ = 9 AND CLASSIFICATION = 'fbs' ORDER BY AP_RANK NULLS LAST LIMIT 10` | The current week; the 2024 records after week 9 and the AP poll released after week 9 |
| model/refresh-step | `make model-check SEASON=2024 WEEK=9`; `EXECUTE SCRIPT CFB_INFRA.REFRESH_MODEL(NULL, 2024, 9, 'regular')`; `SELECT CHECK_NAME, SEVERITY, CHECK_STATUS, OBSERVED FROM CFB_META.MODEL_CHECK_LOG ORDER BY CHECKED_AT DESC LIMIT 20` | Every check listed; no failed ERROR check; the script returns, and the log holds its rows with no run id |

### Checklist

| Step | Command | Expected |
|------|---------|----------|
| Generate | `make generate && git diff --exit-code -- vs/catalog sql/core` | Exit 0; catalog and mirrors current |
| Build | `uv run python -m vs.bundle && uv run python -m loader.bundle` | Exit 0; `build/sql/` holds the adapter, fetch, loader and `model_refresh_model.sql` |
| Model lint | `uv run python -m scripts.model deploy --lint-only` | Exit 0; "lint ok" |
| Dictionary | `make model-docs && git diff --exit-code docs/model-dictionary.md` | Exit 0 |
| Test | `make test` | 0 failures (unit + CLI, no DB) |
| Integration | `make test-it` | 0 failures; mock and replay only; 0 requests to `api.collegefootballdata.com` |
| Lint | `make lint` | 0 ruff errors, 0 mypy errors (`vs`, `loader`, `model`) |
| Format | `uv run ruff format --check .` | No changes |
| Coverage | `make coverage` | ≥ 90 % line coverage for `vs/cfbd_vs`, `vs/gen`, `loader`, `model` and `vs/host/sqlobjects.py` |
| Deploy | `make deploy && make deploy TARGET=aws` | Exit 0 both; model comments 100 %; a second run drops nothing |
| Layering | `git grep -n -i -e 'CFBD_VS' -e '{VS_NAME}' -- sql/model` | No match |
| Checks | `make model-check TARGET=aws SEASON=<current> WEEK=<current>` | No failed ERROR check |
| Zero calls | `make quota TARGET=aws` before and after group 7 | `/info` used calls unchanged |
| No data in git | `git ls-files '*.parquet' '*.cfbd.json'` | Empty |
