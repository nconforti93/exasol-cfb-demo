# Plan: add-cfb-analytical-model

## Summary

Roadmap Phase 3: build `CFB_MODEL`, the era-aware analytical model over the stored `CFB_CORE` data. When it is done, Phases 4–6 can write every analysis without touching `CFB_CORE`, and the Phase 4 engine finds every data input of its contract in two as-of views.

- **Views only.** Dimensions, facts, coverage and as-of helpers are views over the mirrors. Seven small reference tables hold curated knowledge that CFBD does not provide: eras, division labels, conference tiers, conference championship seasons, polls, the dataset catalog and classification transitions. Nothing needs a rebuild after a backfill, a refresh or a restore.
- **Era-aware.**
  - Every season carries its division, postseason, champion and overtime era.
  - Every team-season carries its conference, division, classification (with the name used at the time) and power tier: AQ, P5, P4, G5, G6.
  - Conference championship games are detected inside each conference's seeded championship seasons.
  - `SEASON_COVERAGE` states per season and stored dataset whether the data is complete, partial, in progress, missing, on demand or not available; `DATASET` describes the datasets that are never stored.
- **Leak-free as-of helpers.** `AS_OF_POINT`, `CURRENT_AS_OF`, `GAME_AS_OF`, `TEAM_SEASON_AS_OF` and `RANKING_AS_OF` answer "the state as of season S, week W" with one horizon: everything known before the next week's first kickoff.
  - Event facts (results, Elo, polls) are visible once they happen. Values that CFBD overwrites during a season (current SP+, pregame lines, the current polls) are visible from their fetch time.
  - Games whose participants depend on results (conference championships, bowls, CFP rounds) are not rows until their participants are known.
  - A test proves the invariant: no fact after the point changes an as-of answer.
- **Engine-ready.** Real values that the score model or the engine reads are fixed-scale `DECIMAL`, per the S3 finding. The model provides the data inputs of the engine contract and computes no engine value.
- **Geography.** Venues carry a `GEOMETRY(4326)` point, because Exasol has no `GEOGRAPHY` type. Distances come from a great-circle SQL function.
- **Self-describing.** Every object and column is commented, with the grain first. Names are unquoted upper-case identifiers that are not reserved, a data dictionary is generated, and four example analyses prove the done-when criterion.
- **One command.** `make model` (also part of `make deploy`) deploys the model atomically from `sql/model/` under the loader's run lock and costs zero API calls.
- **Refresh step.** The `MODEL` refresh step checks the fresh week and stops the chain before predictions or simulations run on broken inputs.

Everything is verified at zero CFBD calls: the synthetic league in the `TEST_` namespace, a scaled synthetic league for performance, then the AWS data from Phase 2's backfill, read only.

## Design

### Context

The mission promises "a general model over the stored data, aware of eras: conference membership and classification per season, coverage flags per fact, venue GEOGRAPHY, an 'as of week' view for every page". The roadmap's done-when criterion is "an analysis can be written without touching `CFB_CORE`". Phase 2 (`add-cfb-persistence-refresh`), the foundation (`add-cfbd-vs-foundation`) and the engine spikes (`add-engine-spikes`) provide what this phase builds on:

- **The stored data.** The `demo` load profile stores, at about 217 calls once:
  - `GAMES` 1869– (per-game Elo from 1890), `METRICS_WP_PREGAME` and `RATINGS_SP` from 2013, `RANKINGS_POLLS_RANKS` from 2014, `CALENDAR` from 2013;
  - `CONFERENCES_AFFILIATIONS` as one range slice for 1869–2025 plus season slices from 2026;
  - `TEAMS`, `VENUES` and `CONFERENCES`.

  Nothing else is stored (decision [1]).
- **Mirrors and slices.** Each `CFB_CORE` mirror has the virtual table's columns, including the `P_*` parameter echoes and `REQ_*` request metadata (`REQ_SLICE`, `REQ_AT` in UTC).
  - The current season's `weekly` slices are re-fetched by every `REFRESH_WEEK` and stay non-final until the following 15 February.
  - Earlier seasons are final once a load on or after that date re-fetched them.
  - `CFB_META.LOAD_PLAN` and `CFB_META.LOAD_SLICES` say which slice of which season is planned, stored, final or a gap.
  - Once `PROMOTE()` lands (Phase 2 backlog), on-demand slices that overlap stored ones can be stored too, so every slice-scoped source can hold a natural key twice.
- **Prerequisite P1 is in the catalog.** `RANKINGS_POLLS_RANKS` carries the parent keys `SEASON`, `SEASON_TYPE`, `WEEK` and `POLL`, and the poll position `POLLS_NO` (`vs/catalog/catalog.json`, `catalog_hash` `b6389f65…`, read 2026-10-22). So regular week 1 and postseason week 1 polls are distinct. The parent's `RANKINGS_POLLS.IS_FINAL` is not stored (Phase 2 [27]).
- **Column types in the catalog.** Points, Elo, ranks and ids are `DECIMAL(18,0)`. Spreads, win probabilities and SP+ values are `DOUBLE`.
- **The refresh chain** `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(season, week, season_type)` runs registered steps as `EXECUTE SCRIPT <script>(run_id, season, week, season_type)` from step 100 on (`REFRESH_STEPS`, `loader/load-plan.yaml`). It commits its step row when a step starts (`loader/refresh-week`, logging).
- **The engine contract is settled** (D19, D20, `specs/architecture.md` § Engine contract, `docs/spikes/s3-python-engine-throughput.md`):
  - one SET call per batch over `MODEL_ROWS` of kinds `GAME` (remaining games: home, away, neutral, conference flag, μ, σ, total; final conference games with their margin, for head-to-head), `TEAM` (team, conference, tier, rating, strength of schedule, records so far, conference records, conference point differential, capped point differential) and `PARAM`;
  - real values fixed-scale `DECIMAL`, never `DOUBLE` (`CAST('-0.6' AS DOUBLE)` is 1 ulp off);
  - the engine simulates the conference championship games and the whole bracket itself.
- **Other consumers:**
  - Phase 4's score model computes ratings in SQL from games, with SP+ as a prior, and backtests the 2024/2025 CFP fields.
  - Phase 5 needs pregame win probability and Elo per game for all-time upsets, the all-time series for head-to-head, frozen weekly predictions, and week-over-week movement.
  - Phase 6 needs an as-of week picker. Phase 7's chat needs a self-describing model.

The forces:

- **Era and coverage.** 157 seasons span six division structures, seven postseason formats, ties before 1996, conference championship games from 1992 that came and went per conference, and power-conference sets that changed in 1998, 2014 and 2024. It is open whether CFBD's game-level classification is historic or today's code (Q6). Data starts at different seasons per dataset, and the demo stores only part of it.
- **Leakage.** Results and polls carry week labels. Current-season SP+, lines and polls are overwritten by each refresh, so the stored value is the latest one. Historic spreads are closing lines, known only on game day. The stored schedule of a finished season names the participants of games that were decided by earlier results: conference championship games, bowls and every CFP round.
- **Exasol** (checked with the `exasol-database` and `exasol-udfs` skills; see Key Interfaces):
  - no table-valued functions usable in `FROM`, no `COMMENT ON VIEW`, no `GEOGRAPHY` type;
  - `GEOMETRY(srid)` with planar spatial functions;
  - `CREATE [OR REPLACE] [FORCE] VIEW`;
  - "never use quoted identifiers in DDL statements";
  - transactional DDL (probe 1.1b).
- **Process.** No DB session during planning. Probes, the performance smoke and integration tests run only in "local free" windows, and AWS only after "AWS free". Zero CFBD calls throughout.

**Goals**
- `CFB_MODEL` with these objects (Key Interfaces):
  - dimensions `SEASON`, `SEASON_WEEK`, `TEAM`, `CONFERENCE`, `VENUE`, `TEAM_SEASON`;
  - facts `GAME`, `CONFERENCE_CHAMPIONSHIP_GAME`, `TEAM_GAME`, `RATING_SP`, `RANKING`;
  - coverage `SEASON_COVERAGE`;
  - as-of `AS_OF_POINT`, `CURRENT_AS_OF`, `GAME_AS_OF`, `TEAM_SEASON_AS_OF`, `RANKING_AS_OF`;
  - checks `MODEL_CHECKS`;
  - reference tables `ERA`, `CLASSIFICATION_LABEL`, `CONFERENCE_TIER`, `CONFERENCE_CHAMPIONSHIP`, `POLL`, `DATASET`, `CLASSIFICATION_TRANSITION`, and the function `GREAT_CIRCLE_KM`.
- Era awareness per season (eras, `CFP_TEAMS`, `TIES_POSSIBLE`) and per team-season (membership by season with source and conflict flags, tier, classification with source, division label with the retroactive flag).
- Coverage flags per game, and a trust level per season and stored dataset that tells "not stored" (with the virtual-schema table to use) from "does not exist".
- As-of helpers addressed exactly like `REFRESH_WEEK(season, week, season_type)`, with one horizon, result-dependent games hidden until their participants are known, and an invariance test.
- Every data input of the engine contract, in fixed-scale `DECIMAL` where it is real-valued.
- Venue `GEOMETRY(4326)` points and `GREAT_CIRCLE_KM`.
- 100 % comment coverage with grain sentences, unquoted upper-case names that are not reserved, a generated dictionary, and four example analyses that read only `CFB_MODEL`.
- An idempotent, atomic, linted deploy under the loader's run lock: `make model`, part of `make deploy`. A generic SQL-object deployer (`vs/host/sqlobjects.py`) that Phases 4–5 reuse for `CFB_SIM`, `CFB_PRED` and `CFB_API`.
- Refresh step 100 `MODEL` (`CFB_INFRA.REFRESH_MODEL`): checks, a log, and failing on errors. `make model-check` for any point.

**Non-Goals**
- Engine values: team and game indices, ratings computed from games, μ, σ, totals, strength of schedule, capped point differentials, the Elo win-probability formula and upset magnitude. They belong to Phase 4. The model provides their inputs and coverage flags.
- `CFB_SIM`, `CFB_PRED` and `CFB_API` objects, and grants to `CFB_APP` or `CFB_MCP`. Apps and the chat read `CFB_API` only (Phase 5).
- Player, coach, recruit, transfer, draft, play, drive, box-score, full betting-line, SRS, FPI and weather facts. They are not stored in the demo profile. `DATASET` lists them as `ON_DEMAND` (or `UNAVAILABLE`) with their virtual-schema tables ([1]). Pass-through views over `CFBD_VS` are not added, so the model can never trigger a call.
- In-season snapshot history: SP+ and lines per week of the current season. It is not needed to stay leak-free ([7]). Phase 5's frozen predictions keep the inputs they used.
- Travel distances per team and game, historic stadium moves, a conference-season view, conference championship games of lower divisions, and BCS title-game detection.
- The playoff selection and seeding rules (2024 champion byes vs straight seeding from 2025) and standings-based champions of conferences without a championship game: Phase 4's selection configuration.
- As-of points inside the postseason finer than CFBD's postseason weeks (open question Q3).
- Changing Phase 2's loader behavior. This plan registers its step in `loader/load-plan.yaml`, and only the materialization fallback (if it is ever triggered) adds a post-hook call to two host wrappers.

### Decision

The model is a directory of SQL files (`sql/model/`, one object per file) deployed in one transaction by a generic SQL-object deployer. Reference tables are replaced with their seed rows on every deploy. Every derived object is a view over `CFB_CORE`, the loader's plan views and other model objects; every slice-scoped source is deduplicated by its natural key, newest fetch first.

The as-of helpers are parameter-dimension views: `AS_OF_POINT` is joined to the season's facts, rows of games whose participants are not known yet are left out, and each remaining column is masked by an event-time or fetch-time visibility rule with one horizon, `CUTOFF_AT`. A filter on the `AS_OF_*` columns selects the point. A thin Lua step script evaluates the declarative checks view inside the weekly refresh. Tests run on a synthetic league whose rows the foundation's own flattener produces from API-shaped bodies, and performance is smoke-tested early on a scaled copy of it.

#### Architecture

```
 sql/model/NNN_<object>.sql  (one object each; comments inline; {MODEL_SCHEMA} {CORE_SCHEMA} {META_SCHEMA})
        │  vs/host/sqlobjects.py: parse ─▶ lint (comments, grain, DDL names, reserved words, schema refs) ─▶ render
        ▼
 make model / make deploy [TARGET=local|aws] ─▶ model/deploy.py ─▶ take the loader run lock ─▶ one transaction:
     CREATE SCHEMA IF NOT EXISTS ; reference tables (CREATE OR REPLACE + INSERT) ; GREAT_CIRCLE_KM ;
     views (CREATE OR REPLACE FORCE VIEW, file order) ; DROP orphans ; compile every view ;
     read back EXA_ALL_OBJECTS/EXA_ALL_COLUMNS comments ─▶ COMMIT
     + CFB_INFRA.REFRESH_MODEL (Lua, build/sql/model_refresh_model.sql)   + CFB_META.MODEL_CHECK_LOG (meta DDL)

 CFB_CORE mirrors ──┐      CFB_META.LOAD_PLAN / LOAD_SLICES (loader views; plan, stored, final, gap)
                    ▼                    │
 CFB_MODEL  reference tables: ERA CLASSIFICATION_LABEL CONFERENCE_TIER CONFERENCE_CHAMPIONSHIP POLL DATASET
            CLASSIFICATION_TRANSITION                                        function: GREAT_CIRCLE_KM
   CONFERENCE_CHAMPIONSHIP_GAME ─▶ GAME ─▶ SEASON_WEEK ─▶ SEASON          VENUE
   GAME ─▶ TEAM, TEAM_GAME ;  SEASON_WEEK ─▶ RANKING ;  TEAM ─▶ RATING_SP ;  CONFERENCE
   TEAM_GAME, RATING_SP, RANKING ─▶ TEAM_SEASON
   AS_OF_POINT ─▶ CURRENT_AS_OF, GAME_AS_OF, RANKING_AS_OF ─▶ TEAM_SEASON_AS_OF
   SEASON_COVERAGE (DATASET × SEASON × LOAD_SLICES × measured shares)      MODEL_CHECKS (all of the above)
        │
        ├─▶ engine inputs (GAME_AS_OF, TEAM_SEASON_AS_OF) and analyses (Phases 4–5: CFB_SIM, CFB_PRED) ─▶ CFB_API ─▶ app, MCP chat
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
| Views over the mirrors, no materialized model tables | `sql/model/0xx–5xx` | The model is always consistent with `CFB_CORE` after a backfill, a refresh or a restore, with no rebuild order to get wrong. All history is about 250k games, which is small for Exasol. A scaled-league smoke test checks that early; a view is materialized only per the fallback below |
| Newest fetch wins per natural key | every slice-scoped source: games, lines, SP+, rankings, calendar, teams, venues, conferences | Overlapping or promoted slices never duplicate a grain. Duplicates are reported as a warning, not an error, because the views already resolve them |
| Reference tables seeded from git | `ERA`, `CLASSIFICATION_LABEL`, `CONFERENCE_TIER`, `CONFERENCE_CHAMPIONSHIP`, `POLL`, `DATASET`, `CLASSIFICATION_TRANSITION` | Era knowledge that CFBD does not provide is reviewed in pull requests and replaced on every deploy, never edited in the database |
| Parameter dimension joined to facts ("parameterized view") | `AS_OF_POINT` × facts in the `*_AS_OF` views | Exasol has no table function usable in `FROM`. A filter on the point's columns selects the point and is applied before the join (probe 1.1g) |
| One horizon, event time vs fetch time | the `*_AS_OF` views | Week labels decide when results, Elo and polls become visible, and `REQ_AT < CUTOFF_AT` decides it for values that CFBD overwrites. A slice fetched before the cutoff can only hold values released before it. Leak-free without snapshot history tables |
| Result-dependent games are not rows before their participants are known | `GAME.PARTICIPANTS_RULE`, `GAME_AS_OF` | The stored schedule of a finished season names championship, bowl and playoff participants. Leaving the rows out hides teams, existence and pregame Elo at once, and the engine simulates those games anyway |
| As-of views bounded to the point's season | `GAME_AS_OF`, `TEAM_SEASON_AS_OF`, `RANKING_AS_OF` | An unfiltered query costs points × one season, never every point × all of history (≈ 2,700 × 250k rows). Earlier seasons are fully known and come from `GAME` |
| Each slice speaks only for its request | `TEAM_SEASON` (`P_YEAR`, `P_MIN_YEAR`–`P_MAX_YEAR`), `RATING_SP.IS_FINAL` | The static range slice of memberships cannot claim seasons it was not asked for, and the newest fetch wins where slices overlap |
| Coverage = declared × planned × stored × measured | `SEASON_COVERAGE`, `DATASET` | Trust is explainable, and "not stored, fetch it on demand" is distinguished from "does not exist" |
| Lint before deploy, compile and read back after | `vs/host/sqlobjects.py` | Comment coverage, grain, DDL names, reserved words and layering are checked before any statement. `FORCE` views remove file-order fragility; compiling every view and reading the comments back proves what the database holds |
| Deploy in one transaction under the run lock, drop orphans | `vs/host/sqlobjects.py`, `model/deploy.py` | A failed deploy changes nothing, no refresh conflicts with a deploy, and stale objects never mislead the chat |
| Declarative checks, thin Lua | `MODEL_CHECKS`, `model/lua/refresh_model.lua` | Checks are testable without Lua. Lua only resolves the point, logs, commits and fails. It is the same split as Phase 2's views and scripts |
| Synthetic league through the real flattener | `tests/synthetic/league.py`, `tests/synthetic/scaled_league.py` | Rows are exactly what the virtual schema would store. Direct inserts keep tests fast, the same bodies serve as replay recordings for the refresh test, and a scaled copy measures performance before the as-of views depend on the base views |
| Example analyses as acceptance tests | `sql/model/examples/` | The done-when criterion becomes executable, and the examples double as the analysis builder's starting point |

#### Key Interfaces

**Objects of `CFB_MODEL`** (file `sql/model/NNN_<object>.sql`. The comment of every table and view starts with "One row per <grain>".)

| File | Object | Kind | Grain | Reads |
|---|---|---|---|---|
| 010 | `ERA` | reference table | era kind and season range | seed |
| 011 | `CLASSIFICATION_LABEL` | reference table | division era and CFBD classification | seed |
| 012 | `CONFERENCE_TIER` | reference table | conference name (NULL = every other FBS conference) and season range | seed |
| 013 | `CONFERENCE_CHAMPIONSHIP` | reference table | conference name and season range in which it held a championship game | seed |
| 014 | `POLL` | reference table | CFBD poll name | seed |
| 015 | `DATASET` | reference table | CFBD dataset | seed |
| 016 | `CLASSIFICATION_TRANSITION` | reference table | team (CFBD school name) and season range whose classification differs from CFBD's recorded code | seed (empty unless Q6 says otherwise) |
| 020 | `GREAT_CIRCLE_KM` | function | one value per call | — |
| 090 | `CONFERENCE_CHAMPIONSHIP_GAME` | view | detected conference championship game | `GAMES`, `CONFERENCE_CHAMPIONSHIP`, `CONFERENCE_TIER` (power-conference championship week, X1) |
| 100 | `GAME` | view | game (deduplicated by game id) | `GAMES`, `METRICS_WP_PREGAME`, `VENUES`, `CONFERENCE_CHAMPIONSHIP_GAME`, `CLASSIFICATION_TRANSITION`, `DATASET` (`PLAYS_ACCESS`) |
| 110 | `SEASON_WEEK` | view | season, season type and week | `GAME`, `CALENDAR` |
| 120 | `SEASON` | view | season from 1869 to the newest season with games | `ERA`, `GAME`, `GAMES` (newest season) |
| 130 | `VENUE` | view | venue | `VENUES` |
| 140 | `TEAM` | view | team, listed or seen in games | `TEAMS`, `GAME` |
| 150 | `CONFERENCE` | view | conference | `CONFERENCES` |
| 160 | `RATING_SP` | view | season and SP+-rated team | `RATINGS_SP`, `TEAM`, `LOAD_SLICES` |
| 170 | `RANKING` | view | poll, season, season type, week and ranked team | `RANKINGS_POLLS_RANKS`, `POLL`, `SEASON_WEEK` |
| 200 | `TEAM_GAME` | view | game and named participating team | `GAME` |
| 210 | `TEAM_SEASON` | view | team and season with a game or a membership | `CONFERENCES_AFFILIATIONS`, `TEAM_GAME`, `GAME`, `TEAM`, `CONFERENCE_TIER`, `CLASSIFICATION_LABEL`, `CLASSIFICATION_TRANSITION`, `ERA`, `RATING_SP`, `RANKING` |
| 300 | `AS_OF_POINT` | view | season and as-of point (preseason + each week) | `SEASON_WEEK`, `SEASON` |
| 310 | `CURRENT_AS_OF` | view | the current point (one row) | `AS_OF_POINT` |
| 320 | `GAME_AS_OF` | view | as-of point and game of its season that is visible at the point | `AS_OF_POINT`, `GAME` |
| 330 | `RANKING_AS_OF` | view | as-of point, poll and ranked team | `AS_OF_POINT`, `RANKING` |
| 340 | `TEAM_SEASON_AS_OF` | view | as-of point and team-season of its season | `AS_OF_POINT`, `TEAM_SEASON`, `GAME_AS_OF`, `RANKING_AS_OF`, `RATING_SP` |
| 400 | `SEASON_COVERAGE` | view | season and stored season-scoped dataset | `DATASET`, `SEASON`, `GAME`, `TEAM_SEASON`, `RATING_SP`, `LOAD_PLAN`, `LOAD_SLICES` |
| 500 | `MODEL_CHECKS` | view | check and season (and week for week checks) | the model |

Seven tables, one function, 18 views. The columns and rules are in the specs: `model/seasons-eras`, `model/teams-conferences`, `model/games`, `model/ratings-rankings`, `model/venues`, `model/coverage`, `model/as-of` and `model/refresh-step`. Outside the model:
- `CFB_META.MODEL_CHECK_LOG` (meta DDL);
- `CFB_INFRA.REFRESH_MODEL` (Lua);
- `sql/model/examples/*.sql`.

**SQL file conventions** (enforced by the lint, `model/deploy` and `model/documentation`)

```sql
-- sql/model/320_game_as_of.sql: DDL names unquoted UPPER_CASE (skill: never quote identifiers in DDL);
-- identifiers in the query body double-quoted (skill rule); names never reserved
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.GAME_AS_OF (
  AS_OF_SEASON      COMMENT IS 'Season of the as-of point; join or filter with AS_OF_POINT',
  AS_OF_WEEK_SEQ    COMMENT IS 'Week sequence of the point: 0 preseason, regular week n = n, postseason week n = 100 + n',
  GAME_ID           COMMENT IS 'CFBD game id (GAME.GAME_ID)',
  PARTICIPANTS_RULE COMMENT IS 'When the teams were known: FIXED, CONF_CHAMPIONSHIP, SELECTION or PREVIOUS_ROUND (GAME.PARTICIPANTS_RULE)',
  GAME_STATUS_AS_OF COMMENT IS 'PLAYED, NOT_PLAYED, NEXT or REMAINING at the point',
  MARGIN            COMMENT IS 'Home minus away points; NULL unless PLAYED at the point',
  HOME_PREGAME_ELO  COMMENT IS 'CFBD pregame Elo of the home team; NULL for games after the next week',
  PREGAME_SPREAD    COMMENT IS 'Home-side spread as CFBD reports it, DECIMAL(6,2); NULL unless the game is played by the point or is a next-week game whose line was fetched before CUTOFF_AT'
  -- … every column commented
) AS
SELECT p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", g."GAME_ID", g."PARTICIPANTS_RULE",
       CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" AND g."IS_COMPLETED" THEN 'PLAYED'
            WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" THEN 'NOT_PLAYED'
            WHEN g."WEEK_SEQ" = p."NEXT_WEEK_SEQ" THEN 'NEXT' ELSE 'REMAINING' END,
       CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" THEN g."MARGIN" END,
       CASE WHEN g."WEEK_SEQ" <= COALESCE(p."NEXT_WEEK_SEQ", p."AS_OF_WEEK_SEQ") THEN g."HOME_PREGAME_ELO" END,
       CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ"
              OR (g."WEEK_SEQ" = p."NEXT_WEEK_SEQ" AND g."PREGAME_WP_FETCHED_AT" < p."CUTOFF_AT")
            THEN g."PREGAME_SPREAD" END
  FROM {MODEL_SCHEMA}."AS_OF_POINT" p
  JOIN {MODEL_SCHEMA}."GAME" g ON g."SEASON" = p."AS_OF_SEASON"
 WHERE g."PARTICIPANTS_RULE" = 'FIXED'
    OR (g."PARTICIPANTS_RULE" = 'CONF_CHAMPIONSHIP' AND g."WEEK_SEQ" <= COALESCE(p."NEXT_WEEK_SEQ", p."AS_OF_WEEK_SEQ"))
    OR (g."PARTICIPANTS_RULE" = 'SELECTION'         AND p."AS_OF_WEEK_SEQ" >= p."LAST_REGULAR_WEEK_SEQ")
    OR (g."PARTICIPANTS_RULE" = 'PREVIOUS_ROUND'    AND g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ")
COMMENT IS 'One row per as-of point and game of the point''s season whose teams are known at the point. Values not known at the point are NULL (event time for results, Elo and polls; fetch time for lines). Earlier seasons are fully known: use GAME.';
```

- Placeholders are `{MODEL_SCHEMA}`, `{CORE_SCHEMA}`, `{META_SCHEMA}` and `{INFRA_SCHEMA}`. They render to the namespace's UPPER_CASE schema names; Phase 2's renderer gains `{MODEL_SCHEMA}`.
- Statements end with `;` at the end of a line. The function body ends with a line holding only `/`.
- DDL object and column names are unquoted, upper case (`^[A-Z][A-Z0-9_]*$`) and not reserved, so every name works unquoted. Identifiers in query bodies are double-quoted.
- Allowed references:
  - `{MODEL_SCHEMA}.*`;
  - `{CORE_SCHEMA}.*`;
  - `{META_SCHEMA}.LOAD_PLAN`, `LOAD_PLAN_GAPS` and `LOAD_SLICES`.

  Anything else is a lint error, in particular `CFBD_VS` or `{VS_NAME}`.
- Reference tables:

  ```sql
  CREATE OR REPLACE TABLE {MODEL_SCHEMA}.ERA (
    ERA_KIND VARCHAR(32) UTF8 NOT NULL COMMENT IS 'DIVISION, POSTSEASON, CHAMPION or OVERTIME', …)
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
- Engine-facing real values: `CAST(<DOUBLE> AS DECIMAL(6,2))` for spreads and SP+ ratings, `DECIMAL(5,4)` for win probabilities (probe 1.1i: the cast rounds; fallback `ROUND(x, s)` first).
- Deduplication: `QUALIFY ROW_NUMBER() OVER (PARTITION BY <natural key> ORDER BY "REQ_AT" DESC, "REQ_SLICE" DESC, "ROW_NO" DESC) = 1`; for rankings the order adds `"POLLS_NO" DESC` before `"ROW_NO"`.
- Team-history windows partition by team and season; `SP_PRIOR_RATING` is a join on `SEASON = S - 1`, not a `LAG`, so a season filter pushes down.

**As-of semantics** (full rules in `model/as-of`)

| Concept | Definition |
|---|---|
| Point | (season, `WEEK_SEQ`): everything known after the games of that week and before the next week's first kickoff (`CUTOFF_AT`). 0 = preseason; regular week n = n; postseason week n = 100 + n. One horizon for every fact |
| Refresh mapping | `REFRESH_WEEK(S, W, T)` ↔ `AS_OF_POINT` row (`AS_OF_SEASON` = S, `AS_OF_WEEK` = W, `AS_OF_SEASON_TYPE` = T); NULL type = `regular`, as Phase 2 passes it |
| `CUTOFF_AT` | first kickoff of the next week, from `SEASON_WEEK.FIRST_GAME_AT` (calendar `FIRST_GAME_START` if stored, else the games); NULL after the last week |
| Event time | results ≤ P; pregame Elo ≤ next week; polls with `VISIBLE_AT_WEEK_SEQ` ≤ P; the previous season's SP+ |
| Fetch time | current-season SP+, next-week lines and polls are visible if `REQ_AT` < `CUTOFF_AT` (at the last point of a complete season, final SP+ is visible) |
| Result-dependent games | `PARTICIPANTS_RULE`: `CONF_CHAMPIONSHIP` rows from the point before their week; `SELECTION` (FBS bowls, first CFP round) from `LAST_REGULAR_WEEK_SEQ`; `PREVIOUS_ROUND` (later CFP rounds, lower-division postseason) from their own week; `FIXED` always |
| Current point | in the current season, the newest week with `SEASON_WEEK.IS_DONE` (all games completed, or the games were last fetched at least a day after the week ended); the preseason point before that |
| Point metadata | `IS_CURRENT`, `IS_SEASON_COMPLETE` live only in `AS_OF_POINT` and `CURRENT_AS_OF`; the invariant covers the three as-of views |

**Engine contract mapping** (`specs/architecture.md` § Engine contract; S3 input shape). The model provides exactly the data inputs; every computed engine value is Phase 4's.

| Engine input | Source in `CFB_MODEL` at point P | Owner |
|---|---|---|
| `GAME` rows to simulate | `GAME_AS_OF` with `GAME_STATUS_AS_OF` `NEXT` or `REMAINING`, `HAS_TEAMS`, not `IS_CONF_CHAMPIONSHIP`, `POSTSEASON_KIND` NULL (the engine simulates championships and the bracket) | model rows, Phase 4 filter |
| `game_idx`, `home_idx`, `away_idx` | `GAME_ID`, `HOME_TEAM_ID`, `AWAY_TEAM_ID` | Phase 4 indexes |
| `neutral` | `GAME_AS_OF.IS_NEUTRAL_SITE` | model |
| `conf_game` | `GAME_AS_OF.IS_CONFERENCE_GAME` (CFBD `conferenceGame`; `CONFERENCE_GAME_MISMATCH` warns about flags across conferences, which the engine rejects) | model |
| `mu`, `sigma`, `total` | score model over `PLAYED` rows of `GAME_AS_OF`, `TEAM_SEASON_AS_OF.SP_PRIOR_RATING`, the previous season's `RATING_SP` parts, `IS_NEUTRAL_SITE` | Phase 4 |
| final conference games with `margin` (head-to-head) | `GAME_AS_OF` `PLAYED`, `IS_CONFERENCE_GAME`, not `IS_CONF_CHAMPIONSHIP`, `MARGIN` | model |
| `TEAM` rows: `team` | `TEAM_SEASON_AS_OF` with `CLASSIFICATION` `fbs`: `TEAM_ID`, `SCHOOL` | model; Phase 4 indexes |
| `conf`, `conf_tier` (P4 / G6 / IND) | `CONFERENCE_NAME`, `CONFERENCE_TIER` (`P4`, `G6`, `INDEPENDENT`) | model; Phase 4 maps `INDEPENDENT` to IND |
| `rating` | ratings from games with the SP+ prior (`SP_PRIOR_RATING`; `ELO_AS_OF` where SP+ is missing) | Phase 4 |
| `sos` | from Phase 4 ratings over the full schedule in `GAME_AS_OF` | Phase 4 |
| `wins`, `losses` so far (all games, FCS included) | `WINS`, `LOSSES` | model |
| `conf_wins`, `conf_losses` | `CONF_WINS`, `CONF_LOSSES` (conference games without championship games) | model |
| `conf_pd` | `CONF_POINT_DIFF` | model |
| `capped_pd` | `MARGIN` of the team's `PLAYED` games, capped by the engine parameter `margin_cap` (the example uses its default, 24) | Phase 4 |
| `PARAM` rows | — | Phase 4 |
| Fixed-scale `DECIMAL` | spreads `DECIMAL(6,2)`, win probabilities `DECIMAL(5,4)`, SP+ ratings `DECIMAL(6,2)`; points, Elo, ranks integers | model |

No model column exists only for the engine, and the model computes no engine value. Removed because neither the engine nor a v1 page uses them: `TEAM_GAME.TRAVEL_KM` (the engine has a constant `hfa`) and `CONFERENCE_SEASON` (the engine ranks standings per sim from `TEAM` rows; members and divisions are a `GROUP BY` over `TEAM_SEASON`).

**Reference seeds** (contents in the specs; verified on AWS in 7.2)
- `ERA`: `model/seasons-eras` (4 kinds, 19 rows).
- `CLASSIFICATION_LABEL`: 6 division eras × 4 codes (`fbs`, `fcs`, `ii`, `iii`).
- `CONFERENCE_TIER`:
  - named rows for AQ 1998–2013 (ACC, Big East to 2012, American Athletic 2013, Big Ten, Big 12, Pac-10 to 2010, Pac-12 from 2011, SEC), P5 2014–2023 and P4 2024–;
  - `FBS Independents` → `INDEPENDENT`;
  - NULL-name rows for the other FBS conferences: `NON_AQ` 1998–2013, `G5` 2014–2023, `G6` 2024–.
- `CONFERENCE_CHAMPIONSHIP` (12 rows, CFBD names): SEC 1992–, Western Athletic 1996–1998, Big 12 1996–2010 and 2017–, Mid-American 1997–, ACC 2005–, Conference USA 2005–, Big Ten 2011–, Pac-12 2011–2023, Mountain West 2013–, American Athletic 2015–, Sun Belt 2018–.
- `POLL`: `model/ratings-rankings` (6 rows; `LABEL_LEAD_WEEKS` AP 1, Coaches 1, CFP 0; `FINAL_BEFORE_POSTSEASON` TRUE for CFP only).
- `DATASET`: 25 rows transcribed from `docs/cfbd-data-availability.md`, with `COVERAGE_POPULATION` and `COMPLETE_SHARE` 0.95. Endpoints and virtual tables come from `vs/catalog/catalog.json`, and access classes from D15.
- `CLASSIFICATION_TRANSITION`: empty, unless Q6 shows that CFBD's game codes are today's codes (then the reviewed list from 1978 on).

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
3. `INSERT INTO MODEL_CHECK_LOG SELECT … FROM MODEL_CHECKS WHERE SEASON = :season AND (WEEK_SEQ IS NULL OR WEEK_SEQ = :seq)`, then `COMMIT`. The refresh has committed its step row before the step starts (`loader/refresh-week`), so the nested `COMMIT` ends no open refresh state (probe 1.1h confirms the survival of the logged rows).
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
def deploy(connection, schema: str, objects, *, extra_statements=(), lock: RunLock) -> DeployReport
    # takes the loader run lock, one transaction, FORCE views, orphans dropped, every view compiled, comments read back
```

**Materialization fallback** (decision [2]; used only if 3.7, 4.3 or 7.3 measures a view more than 2× over its target and restructuring the view did not fix it):
- The view becomes a table of the same name, columns and comments, filled from `sql/model/materialize/<object>.sql` (its former query).
- It is rebuilt (`TRUNCATE` + `INSERT … SELECT`, one transaction) by every `make model`, by `CFB_INFRA.REFRESH_MODEL` before it evaluates the checks, and by the post-hook `model.materialize.rebuild(target)` that `make backfill` and `make restore` call after a successful run (Phase 2's host wrappers gain one call each).
- Each rebuild records the newest load time in `CFB_META.LOAD_SLICES` that it saw. `MODEL_CHECKS` gains `MATERIALIZED_STALE` (ERROR) for a table built before the newest stored slice, which catches a `BACKFILL` run directly through `EXECUTE SCRIPT`.
- The decision, the numbers and the rebuild cost go to the decision log and `docs/model-verification.md`.

**Namespace and targets.** `Namespace.model_schema` = `{prefix}CFB_MODEL`. All commands take `TARGET=local|aws` through the foundation's `vs/host/target.py`.

| Make target | Does |
|---|---|
| `make model [TARGET=…] [ARGS="--prefix TEST_"]` | lint, render, deploy the model, the step script and the check-log table under the run lock; print the report |
| `make deploy` | virtual schema → loader → model |
| `make model-check SEASON=y WEEK=w [SEASON_TYPE=postseason] [TARGET=…]` | print `MODEL_CHECKS` for that point (no log, 0 calls) |
| `make model-docs` | regenerate `docs/model-dictionary.md` from `sql/model/` (no DB) |
| `make model-perf [TARGET=…] [ARGS="--prefix PERF_"]` | seed the scaled league into the `PERF_` namespace if needed, deploy the model there, time the Requirements queries (3 warm-ups, 5 runs, query cache off), print median and max, drop the namespace with `--drop` |

**Synthetic league** (`tests/synthetic/league.py`; the facts the specs use)

| Season | Facts |
|---|---|
| 1871 | gap, no games |
| 1885 | Ashford vs Marston (not in the team list) at Old Grounds (no coordinates); no Elo |
| 1950 | Ashford 7 – Brookvale 7; Elo on every game; week-5 Brookvale vs Lowell never completed |
| 1985 | Ashford (`fbs`) vs Lowell (`fcs`, no membership, Big Sky on its games); SEC: two conference games in week 13, one in week 14 |
| 2000 | one game (BCS era) |
| 2013 | 25 completed FBS-vs-FBS games, 24 with lines, plus one FBS-vs-FCS game without a line; SP+; Fairhaven `fcs` on its games, `fbs` Mountain West membership |
| 2014 | 25 completed FBS-vs-FBS games (the named ones included), 20 with lines; Northam's conflicting spells; Mountain West divisions; Brookvale beats Ashford; Mountain West week 15: Eastport vs Fairhaven with notes "Mountain West Championship", plus a makeup Mountain West game in the same week |
| 2023 | Carrow in the Pac-12 (SP+ 20.0); Desert Bowl Carrow vs Eastport; American Athletic: two conference games in week 13, Ivel vs Jessup alone in week 14, Jessup vs Kenning in week 15 (both American Athletic, not flagged as a conference game) |
| 2024 | Carrow in the SEC. SEC: Ashford–Brookvale and Carrow–Quinton in week 14, Ashford vs Carrow alone in week 15 (notes "SEC Championship"). Pac-12: two conference games in week 13, one in week 14. Postseason week 1: CFP first round Kingsley (8) vs Northam (9); quarterfinal Ashford (1) vs Kingsley (8), Sugar Bowl, Neutral Dome, Kingsley wins; Harbor Bowl Brookvale vs Eastport; FCS playoff game Lowell vs Pemberton. AP weeks 1–16 + postseason 1, committee weeks 10–15, "Mystery Poll". Ashford: regular season 10–2 (8 SEC games, 7–1; byes in weeks 9 and 11), SEC title, quarterfinal loss: 11–3; final AP 5, final committee 1; week-8 postgame Elo 1702, preseason Elo 1650; Ashford Field week 14 vs Brookvale 31–24, spread -7.5, home WP 0.70. SP+ rows "Ashford St." and "nationalAverages" |
| 2025 | complete; final SP+; committee weeks 10–14 plus the selection ranking labeled postseason week 1; regular weeks 1–15. Championship week 14: SEC Quinton vs Vance (two SEC games in week 13), American Athletic Ivel vs Kenning (two in week 13); week 15: Marlow vs Jessup, a flagged American Athletic game alone in its week (the 2026 Army–Navy shape, X1) |
| 2026 | weeks 1–9 completed, 10–15 scheduled; week 10 opens Thursday 2026-10-29 23:30 UTC; Ashford W W L W W W L W W; Dunmore moves to the Mountain West; Brookvale vs Ashford week 12; SEC championship game in week 15 listed without teams; lines for weeks 10–11; rankings with AP weeks 1–10 and a committee ranking labeled week 10 |
| every stored season from 1998 | a member in every power conference of `CONFERENCE_TIER`, through the range slice's spells: Fillmore (ACC), Granton (Big Ten), Hollis (Big 12), Ashford and Brookvale (SEC), Ivel (Big East to 2012, American Athletic from 2013), Carrow and Dunmore (Pac-10 1978–2010, Pac-12 from 2011); the 2026 season slice repeats the 2026 memberships |

- Fetch times: seasons up to 2025 at 2026-10-20 10:00 UTC; 2026 at 2026-10-25 12:00 UTC, except the 2026 rankings slice at Tuesday 2026-10-27 23:00 UTC. Week 9 of 2026 opens at 2026-10-22 23:30 and week 10 at 2026-10-29 23:30. `NOW_OVERRIDE` is 2026-10-28 12:00 UTC.
- No ERROR check fails on the base league in any season.
- Variants:
  - `mutate_after(point)`: changes every fact that is not visible at the point (the list in the invariance scenario), including swapped participants of the SEC championship game and the CFP quarterfinal, an added bowl game, and calendar rows after the next week; it never changes the next week's first kickoff;
  - `with_unaffiliated_fbs_team` (Quarry), `with_incomplete_week9_game`, `with_renamed_conference`;
  - `with_duplicate_rows`: the 2026 week-3 game Carrow vs Dunmore and its line in a second, later games slice; Ashford's 2026 SP+ in a later team slice; the 2026 AP poll of week 9 in a later week slice. The extra slices are registered under keys outside the load plan; only view-level tests use this variant, and they run no loader step, so Phase 2's orphan guard is not involved;
  - `with_thursday_result`: the 2026 games re-fetched at 2026-10-30 12:00 UTC with week 10's Thursday game completed;
  - `with_nonfinal_prior`: the 2025 SP+ slice registered as not final.

**Scaled league** (`tests/synthetic/scaled_league.py`): the league's season shapes replicated to about 250,000 games over 1869–2026 (about 2,000 games per modern season across all divisions, conference championship games and postseason weeks included), lines and SP+ from 2013, polls from 2014, memberships from the range and season slices. Deterministic, zero calls, rows through the same flattener, loaded into the `PERF_` namespace only.

**Exasol claims checked against the `exasol-database` and `exasol-udfs` skills and the documentation**

| Claim | Evidence | Result |
|---|---|---|
| `CREATE OR REPLACE [FORCE] VIEW v (C COMMENT IS '…', …) AS … COMMENT IS '…'` | `exasol-grammar.md` `create_view_statement` (`"CREATE" ["OR" "REPLACE"] ["FORCE"] "VIEW" view`), `…2`, `…3` | Grammar confirmed. `FORCE` semantics (creation over a missing object, stored column comments, validity once the object exists): probe 1.1f |
| No `COMMENT ON VIEW` | `comment_object_statement` lists COLUMN, SCHEMA, FUNCTION, SCRIPT, USER, ROLE, CONNECTION, CONSUMER GROUP; `comment_table_statement` is `COMMENT ON [TABLE] table` | Not in the grammar for views. Whether a view is accepted as `table` is probe 1.1a; the design does not need it, because inline comments are replaced atomically with the view |
| Never quote identifiers in DDL | `exasol-sql.md` "Never use quoted identifiers in DDL statements"; unquoted identifiers are upper-cased | DDL names are unquoted upper case, enforced by the lint; query bodies quote every identifier |
| `CREATE OR REPLACE TABLE` with column and table `COMMENT IS` | `create_table_statement_1–3`, `column_definition_2` | Confirmed |
| `CREATE FUNCTION f (p IN DOUBLE, …) RETURN DOUBLE IS v DOUBLE; BEGIN …; RETURN …; END f; /` | `create_function_statement_1–6` (at least one variable, at least one statement before `RETURN`) | Confirmed; `COMMENT ON FUNCTION` is in `comment_object_statement` |
| `GEOMETRY(srid)`; no `GEOGRAPHY` type | `exasol-sql.md` data types; docs example `geo GEOMETRY(4326)` | Confirmed by the type list |
| `ST_X`, `ST_Y`, `ST_WITHIN`, `ST_DISTANCE`, `ST_SETSRID`, `ST_TRANSFORM` exist; `ST_SRID` does not | `exasol-grammar-functions.md` `spatial_1–3` | Confirmed. The SRID is checked through the column type |
| Units of `ST_DISTANCE` on SRID 4326 | docs: "Minimal distance between two geospatial objects", no unit | Unverified (probe 1.1c). The model does not rely on it (`GREAT_CIRCLE_KM`) |
| `EXA_SPATIAL_REF_SYS` (`SRID`, `AUTH_NAME`, `AUTH_SRID`, `SRTEXT`, `PROJ4TEXT`) | docs | Columns confirmed; contents probe 1.1c |
| `DECIMAL(p,s)`, p 1–36; `CAST(DOUBLE AS DECIMAL(p,s))` rounds to the scale | `exasol-sql.md` data types; rounding not stated | Silent on rounding: probe 1.1i, fallback `ROUND(x, s)` before the cast |
| Comments readable: `EXA_ALL_OBJECTS.OBJECT_COMMENT`, `EXA_ALL_COLUMNS.COLUMN_COMMENT` / `COLUMN_TYPE` / `COLUMN_OBJECT_TYPE` | docs | Confirmed |
| View dependencies: `EXA_ALL_DEPENDENCIES` (`REFERENCED_OBJECT_SCHEMA`, direct only, `REFERENCE_TYPE` `VIEW`) | docs | Confirmed. The layering test walks the graph |
| DDL is transactional | docs transaction example rolls back a `CREATE TABLE` (implied, not stated) | Probe 1.1b |
| A season predicate on a `UNION ALL` view reaches each branch before its windows | not stated in the skills | Probe 1.1g (`MODEL_CHECKS` shape) |
| `QUALIFY`, window functions, `LISTAGG`, `RADIANS`, `SIN`, `COS`, `ASIN`, `SQRT`, `POWER`, `LEAST` | `analytics-qualify.md`, `exasol-grammar-functions.md` | Confirmed |
| Lua step: nested `EXECUTE SCRIPT`, `pquery`, `error()`, output without `WITH OUTPUT` discarded | `exasol-udfs` `lua-execute-scripts.md` (nesting up to 255, `pquery` returns `success, result`) | Confirmed. A `COMMIT` in the nested script ends the caller's transaction too: harmless because the refresh commits before each step; probe 1.1h |
| Reserved words | every planned object and column name checked against `vs/gen/exasol_reserved_keywords.txt` (2026.1.0, 468 words) | None reserved. `RESULT`, `STATE`, `SOURCE`, `LEVEL`, `VALUE`, `NEXT` and `CHECK` are avoided (`GAME_RESULT`, `STATE_CODE`, `CONFERENCE_SOURCE`, `NEXT_WEEK_SEQ`, `CHECK_NAME`) |
| Mirror columns `RANK`, `POINTS`, `YEAR_`, `STATE_` | catalog: the generator renames only reserved words | Confirmed. The model renames them to `POLL_RANK`, `POLL_POINTS`, `SEASON`, `STATE_CODE` |

### Consequences

| Decision | Alternatives Considered | Rationale |
|----------|------------------------|-----------|
| Every derived object is a view; a measured, early-tested fallback with rebuild hooks ([2]) | Materialize `TEAM_SEASON` / `GAME` / as-of snapshots, rebuilt by a `REFRESH_STEPS` step | Always consistent after backfill, refresh and restore. A materialized table would be rebuilt by deploy, refresh, backfill and restore hooks and guarded by a staleness check, and the scaled-league smoke decides before the as-of views depend on it |
| One horizon: event time and fetch time against `CUTOFF_AT` ([4], [7], [8]) | "Sunday after the week" for polls; snapshot history; week labels only | One definition everywhere, which frozen predictions need. The live demo shows a Tuesday committee ranking at once. Leak-free without history |
| Result-dependent games hidden until their participants are known ([21]) | Always show the schedule; rows with NULL teams | The stored schedule of a finished season leaks championship and playoff participants. No rows means teams, existence and pregame Elo cannot leak, and the engine simulates those games |
| As-of addressed by `WEEK_SEQ` (0, n, 100 + n) ([5]) | Dates or timestamps; calendar-ordinal sequence | Matches `REFRESH_WEEK` exactly and works for seasons without a stored calendar. Postseason granularity is the known limit (Q3) |
| As-of views bounded to the point's season ([6]) | All earlier seasons inside each point | Prevents a 750M-row cross join from one careless chat query; earlier seasons are fully known anyway |
| The previous season's SP+, final or not, is the prior ([7], [24]) | Require a final slice | It is in the past at every point, so it never leaks; requiring finality would null every prior when no backfill ran after 15 February. A warning reports it |
| Classification: transition seed, then games, then memberships ([9]) | Games only; affiliation spell first | One code path for both Q6 outcomes: Q6 changes only seed rows. Spell codes may be the conference's code of today (the WAC) |
| Tiers as a reviewed reference table keyed by CFBD names, with an ERROR check ([10]) | Derive "power" from rankings; hard-code in Phase 4 | Power status is an era rule, not data. A renamed conference would silently break auto-bids, and the check stops the refresh instead |
| Championship games detected inside seeded championship seasons ([11]), up to the season's power-conference championship week ([32]) | Schedule shape alone; `NOTES` text | The seed removes false positives of conferences without a game; the conference-game flag skips Army–Navy-type games, and the week limit skips a flagged one after the championship weekend (2026); notes cross-check as warnings. Verified 1992–2025 (Q7) |
| Fixed-scale `DECIMAL` for engine-facing values ([22]) | `DOUBLE` as CFBD reports; Phase 4 casts | The engine contract requires it, and casting once in the model keeps every consumer bit-identical |
| Newest fetch wins for every slice-scoped source; duplicates warn ([23]) | Deduplicate games only; duplicates as an error | Promoted on-demand slices will overlap stored ones; the views resolve it, so a duplicate must not stop the refresh |
| `GEOMETRY(4326)` and `GREAT_CIRCLE_KM`; no travel distance ([12]) | `GEOGRAPHY` (not in Exasol); `ST_DISTANCE` after `ST_TRANSFORM`; `TRAVEL_KM` | The only spatial type, correct distances. No consumer reads travel |
| Inline comments, grain-first rule, unquoted upper-case names, compile and read-back ([13]) | `COMMENT ON` statements after creation; quoted names; free-form comments | Inline comments are atomic with the view; the skill forbids quoted DDL names; the grain rule makes the chat's job deterministic |
| Generic one-transaction SQL-object deployer with `FORCE` views, orphan drop and run lock ([14]) | Per-phase ad-hoc scripts; `CREATE` without drop; deploying any time | Phases 4–5 reuse it. Atomic deploys, no stale views, no file-order fragility, no conflict with a refresh |
| Model reads `CFB_META.LOAD_PLAN` / `LOAD_PLAN_GAPS` / `LOAD_SLICES` ([16]) | Derive "stored" from mirror rows; copy load state into the model | Empty and gap slices have no rows, and finality lives only in the loader's views. One reader per contract |
| `MODEL` step = checks, not a rebuild ([15]) | No model step; a rebuild step | Catches upstream renames before sims run and puts "model" on the timeline. The views need no rebuild |
| League rows from API-shaped bodies through the foundation flattener, inserted directly; a scaled copy for performance ([17]) | Hand-written mirror rows; staging every slice through the virtual schema | Faithful rows without per-test virtual-schema cost. One generator-shape test still stages `fake.py` through the virtual schema |
| The model provides the engine's data inputs, Phase 4 computes the rest ([31]) | Engine-shaped `MODEL_ROWS` in the model; capped differentials in the model | Keeps parameters (`margin_cap`, `hfa`) with the engine that owns them, and keeps the model general |
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
- `loader/refresh-week`: registered steps, `(run_id, season, week, season_type)`, the timeline, the step-row commit before a step;
- `loader/load-plan`: `LOAD_PLAN`, `LOAD_SLICES`, finality, gaps, the orphan guard;
- `loader/run-logs`: the run lock that the model deploy joins.

It adds one `refresh_steps` entry to `loader/load-plan.yaml`. The foundation specs are not changed.

## Requirements

| Requirement | Details |
|-------------|---------|
| Zero CFBD calls | No model object references `CFBD_VS`: lint plus the dependency test (`model/deploy`). Integration tests use the synthetic league, the mock API and replay; the performance smoke uses the scaled league. The harness guard against `CFBD_API` stays. Live verification only reads `CFB_CORE` and the model; `/info` used calls (free) are unchanged across group K |
| Shared DB etiquette | Probe 1.1, the performance smoke and `make test-it` only in "local free" windows. AWS `default`: deploy and query only, after "AWS free". No stop, start or resize. The `PERF_` namespace is dropped after each smoke. No DB session during planning |
| Layering | The model reads `CFB_CORE.*`, `CFB_META.{LOAD_PLAN, LOAD_PLAN_GAPS, LOAD_SLICES}` and itself. Analyses (Phases 4–5) read only `CFB_MODEL` and their own schemas. No grants to `CFB_APP` or `CFB_MCP` |
| Leak-free as-of | One horizon (`CUTOFF_AT`); result-dependent games hidden by `PARTICIPANTS_RULE`. The invariant test (`model/as-of`) passes for 2024 week 9 and for 2026 week 5 (same test, second point) |
| Engine inputs | Every data input of the engine contract is a column of `GAME_AS_OF` or `TEAM_SEASON_AS_OF` (Engine contract mapping); real values are fixed-scale `DECIMAL` |
| Self-description | 100 % object and column comments, read back after every deploy. Grain sentence first; ≤ 2,000 characters; unquoted upper-case names that are not reserved; dictionary current |
| Exasol SQL | DDL names unquoted upper case; identifiers in query bodies double-quoted (skill rules). Every claim used is listed in Key Interfaces, and the unverified ones go through probe 1.1 |
| Atomic, idempotent deploy | One transaction (probe 1.1b, or its recorded fallback), under the loader's run lock. Every view compiled after creation. A second deploy yields identical objects, comments and reference rows |
| UTC | All model timestamps are UTC as stored (foundation flattener). `CHECKED_AT` uses Phase 2's UTC expression |
| Ownership | `sys` owns the real model (foundation [20]); `CFB_TEST_OWNER` the test and `PERF_` namespaces |
| No CFBD data in git | The leagues are synthetic (fictional teams; real conference names only as reference keys). Seeds hold curated public knowledge (conference names and season ranges; school names in `CLASSIFICATION_TRANSITION` if Q6 needs them). `docs/model-verification.md` holds derived numbers only. The dictionary and the examples hold no data |
| Runtime code | Host Python 3.12 with pyexasol 2.x and PyYAML; Lua as built into Exasol scripting; no new dependencies |
| Performance (measured; fallback rule) | Warm, query cache off: `TEAM_SEASON_AS_OF` and `GAME_AS_OF` for one point < 1 s; `SELECT SEASON, COUNT(*) FROM GAME GROUP BY 1` < 2 s; full `SEASON_COVERAGE` < 3 s; `MODEL_CHECKS` for one season < 3 s; each example < 3 s; `REFRESH_MODEL` < 5 s. Measured on the scaled league locally before group F (3.7) and on the full model (4.3), then on AWS and local after restore (7.3, 7.4). A view more than 2× over its target is first restructured; only if that fails is it materialized per Key Interfaces § Materialization fallback, with the decision logged |

## Dependencies

- **`add-cfbd-vs-foundation`** (in flight):
  - all groups through I, before this plan's group B;
  - its group J task 9.4 (S1 findings applied, catalog final), before group K; re-check P1 and the column types against the final `catalog_hash` in 7.1;
  - P1, the `RANKINGS_POLLS_RANKS` parent keys: present in the current catalog (`b6389f65…`), including `SEASON_TYPE` and `POLLS_NO`;
  - P2, `vs/host/target.py`: present, used by `scripts/model.py`.
- **`add-cfb-persistence-refresh`** (reviewed, waiting for the foundation). This plan needs, before its group B:
  - its 3.1 (meta renderer with placeholders), 3.2 (`LOAD_SLICES`), 3.3 (loader deploy), 4.1 (`loader/bundle.py`), 4.3 (step registry in `refresh_week.lua`), the run lock of `loader/run-logs`, and 6.1/6.2 (harness: `deploy_plan`, `set_now`, `refresh`, step test scripts, calendar fake);
  - its group I (docs), before this plan's group J;
  - its group J (AWS demo backfill, snapshot `aws-demo-<date>`), before group K.

  This plan's task 2.1 calls the model deploy from `scripts/deploy.py` after the loader deploy.
- **`add-engine-spikes`** (S3 and S4 done; D19 and D20 are in `docs/roadmap.md`):
  - it owns the exclusive benchmark windows while any remain;
  - `specs/architecture.md` § Engine contract is the input contract the model serves (Engine contract mapping);
  - this plan edits `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md` only in group J, re-reading each file first.
- **Tools.** None new: `uv`, pyexasol 2.4.1, PyYAML.
- **User prerequisites:** U1, the "local free" and "AWS free" signals; U2, Phase 2's AWS snapshot available under `~/.cfb-demo/snapshots/`. No `CONFIRM`: nothing in this plan spends calls.

## Migration

No data migration: `CFB_MODEL` is new and holds no data of its own.
- `make deploy` and `make model` create `CFB_MODEL`, `CFB_META.MODEL_CHECK_LOG` and `CFB_INFRA.REFRESH_MODEL`.
- `loader/load-plan.yaml`'s `refresh_steps` changes from `[]` to the `MODEL` entry. The next `make deploy` registers it, and the next `REFRESH_WEEK` runs it.
- `vs/host/namespace.py` gains `model_schema`. Phase 2's placeholder renderer gains `{MODEL_SCHEMA}`.
- `tests/it/harness.py`:
  - `drop_namespace` and `leftovers` cover `{prefix}CFB_MODEL` and the `PERF_` namespace;
  - new helpers `deploy_model`, `seed_league`, `seed_scaled_league`, `write_league_recordings`, `hold_run_lock`;
  - `reset_state` leaves the model's reference tables alone (they are in `CFB_MODEL`, which it never truncates);
  - a test plan variant `tests/it/fixtures/load-plan.model.yaml`.
- `.gitignore`: no change (`build/` is already ignored).

## Implementation Tasks

Tasks are TDD per `/speq:code-guardrails`. Host code writes its unit tests first. Each model-object task writes its integration test file first (failing), then the SQL files, until the tests pass. Lua has no unit tier.

1. **Probe, namespace, parser, leagues, seeds (no DB except 1.1)**
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
     - (d) `CREATE FUNCTION` per Key Interfaces: results and NULL handling. Fallback: inline the haversine in the examples and keep no function.
     - (e) `EXA_ALL_DEPENDENCIES` for view → view → table, view → `CFB_META` view, and view → function.
     - (f) `CREATE OR REPLACE FORCE VIEW` over a missing object: it is created, its declared column comments are stored, a query on it fails until the object exists and works afterwards; a plain `CREATE OR REPLACE VIEW` over a missing object fails. The `STATUS` of a view after its referenced table is replaced. Fallback: plain views plus a lint rule that a view references only objects of earlier files.
     - (g) A filter on the point columns of a view joining a parameter view to a windowed fact view (`QUALIFY`) is applied before the join; a season predicate on a `UNION ALL` view of windowed branches reaches each branch: rows read in the profile. Fallback for the `UNION ALL` case: `REFRESH_MODEL` and `make model-check` query each check branch with the season predicate inlined (the view stays for ad-hoc use).
     - (h) A nested Lua step that inserts, `COMMIT`s and then `error()`s: the committed rows survive the caller's rollback. Reuse Phase 2's probe 1.1b/e findings where they cover it.
     - (i) `CAST(<DOUBLE> AS DECIMAL(6,2))` and `DECIMAL(5,4)` for -7.5, 0.7, 25.149999999, 0.123449999, 0.12345 and -0.6: rounding to the scale, not truncation, and exact read-back in pyexasol. Fallback: `ROUND(x, s)` before the cast.
     - **Gate:** (a) or (b) negative → apply the fallback. (c) fixes the `GEO_POINT` expression. (d), (f), (g) or (i) negative → the fallback above.
   - [ ] 1.2 `Namespace.model_schema`, and `{MODEL_SCHEMA}` in Phase 2's placeholder renderer (`vs/host/metaddl.py`). Unit tests in `tests/unit/test_namespace.py` and `test_meta_ddl.py`.
   - [ ] 1.3 `vs/host/sqlobjects.py`, pure part (`load`, `lint`, `render`) per Key Interfaces:
     - statement splitting (`;`, and the `/` line for functions and scripts);
     - object kind (including `FORCE` views) and name;
     - declared columns with their comments, and the object comment;
     - references (`{X_SCHEMA}."NAME"` and bare `SCHEMA.NAME`), and placeholders.

     Lint rules per `model/deploy` and `model/documentation`: comments, grain, DDL names unquoted, upper case and not reserved (reserved words from `vs/gen/exasol_reserved_keywords.txt`), references, file names, placeholders. Every problem is reported, none stops at the first. Unit tests `tests/unit/test_sqlobjects.py` with fixture files in `tests/unit/fixtures/sqlobjects/`.
   - [ ] 1.4 Synthetic league `tests/synthetic/league.py`:
     - API-shaped response bodies per slice, holding exactly the facts in Key Interfaces and the spec scenarios;
     - mirror rows from the foundation's flattener (`vs.cfbd_vs.flatten`), plus `P_*` echoes and `REQ_*`, with slice keys from Phase 2's templates and fetch times as specified;
     - `LOAD_LOG` registrations (final up to 2025, non-final 2026);
     - replay-recording export for the 2026 weekly slices;
     - the variants of Key Interfaces.

     Unit tests `tests/unit/test_league.py`:
     - rows typed per catalog mirror columns, slice keys equal `build_slice_key`, deterministic output;
     - the scenario counts: 2013 24/25 and 2014 20/25 FBS-vs-FBS lines, Ashford 2024 10–2 in the regular season and 11–3 overall, Ashford 2026 7–2 at week 9;
     - a structural guard that mirrors the ERROR checks: every stored season from 1998 has a membership in every power conference of the tier seed, no natural key is stored twice in the base league, every non-retroactive FBS team-season has a membership or a conference on its games, every game has a week.
   - [ ] 1.5 Reference seeds `sql/model/010_era.sql` … `016_classification_transition.sql`, with comments, per the specs and Key Interfaces. Unit tests `tests/unit/test_model_seeds.py`:
     - the eras of each kind are contiguous and do not overlap;
     - every `CLASSIFICATION_LABEL` era × code exists;
     - the `CONFERENCE_CHAMPIONSHIP` ranges of a conference do not overlap and start in 1992 or later;
     - `CLASSIFICATION_TRANSITION` rows (if any) use the four codes and do not overlap per team;
     - `DATASET` declared seasons and gaps equal `docs/cfbd-data-availability.md` (parsed table), and `COMPLETE_SHARE` is between 0 and 1;
     - every `VS_TABLE` exists in `vs/catalog/catalog.json` and every `CORE_TABLE` in `sql/core/cfb_core_mirrors.sql`;
     - stored datasets equal the `demo` entries of `loader/load-plan.yaml`.
   - [ ] 1.6 Scaled league `tests/synthetic/scaled_league.py` per Key Interfaces. Unit tests `tests/unit/test_scaled_league.py`: deterministic output, about 250,000 games, every season shape of the base league represented, slice keys valid.

2. **Deployer, meta table, dictionary, harness**
   - [ ] 2.1 Deploy part of `vs/host/sqlobjects.py` (`deploy`):
     - take the loader's run lock through Phase 2's helper, refusing while a loader run, snapshot or restore holds it;
     - one transaction per probe 1.1b, or its fallback;
     - create the schema, run statements in file order (`FORCE` views per probe 1.1f, or its fallback), drop orphans limited to the schema, compile every view, read comments back, roll back on any failure;
     - report lines.

     Then:
     - `model/deploy.py` binds `sql/model/`, the step script from `build/sql/model_refresh_model.sql` (skipped with a notice until 4.1) and the namespace.
     - `scripts/model.py` has the subcommands `deploy`, `check` (prints `MODEL_CHECKS` for a point), `docs` and `perf`, all through `vs/host/target.py`. Make targets `model`, `model-check`, `model-docs` and `model-perf`.
     - `scripts/deploy.py` calls the model deploy after the loader deploy.

     Unit tests `tests/unit/test_model_deploy.py` use a fake connection: lock first, statement order, rollback on failure, orphan drop, compile failure, the comment read-back failure. CLI tests `tests/cli/test_model_cli.py`: lint errors listed with nothing sent, `--prefix`, exit codes.
   - [ ] 2.2 `sql/meta/model_check_log.sql`, registered in `vs/host/metaddl.py`; unit test.
   - [ ] 2.3 `model/dictionary.py` and `make model-docs` → `docs/model-dictionary.md` (grain, columns, comments per object, in file order). Unit test `tests/unit/test_model_dictionary.py`: the committed file equals the generated one.
   - [ ] 2.4 Harness (`tests/it/harness.py`, `tests/it/conftest.py`):
     - model and `PERF_` schemas in `drop_namespace` and `leftovers`;
     - `deploy_model()`;
     - `seed_league(variant=None)`: direct inserts into the mirrors, `LOAD_LOG`, `NOW_OVERRIDE` 2026-10-28 12:00 UTC;
     - `seed_scaled_league()` into the `PERF_` namespace;
     - `write_league_recordings(...)`;
     - `hold_run_lock()`: a `RUNNING` loader run in a live session, released on exit;
     - module conventions: read-only feature modules seed the league once (marker `model_readonly`, no per-test truncation); `tests/it/test_model_variants_it.py` and `tests/it/test_model_refresh_step_it.py` re-seed before every test, so no test order leaks state;
     - the plan variant `tests/it/fixtures/load-plan.model.yaml`: demo-like entries with the league's seasons and gaps, step 100 `TEST_CFB_INFRA.REFRESH_MODEL`, step 200 `TEST_STEP_RECORD`.
   - [ ] 2.5 Deploy integration tests on fixture model directories (`tests/it/fixtures/model_sql/{valid,lint_errors,db_error,orphan,later_file}`), in `tests/it/test_model_harness_it.py`: lint stops before any statement, a view referencing a later file deploys, a failing statement rolls back, the orphan is dropped, a held run lock refuses the deploy, the model never reads the virtual schema (dependency walk + mock hits + `FETCH_LOG`).

3. **Model objects** (each task: integration test file first, then `sql/model/` files; the scenarios it makes pass are listed in Verification)
   - [ ] 3.1 `090_conference_championship_game.sql`, `100_game.sql`, `110_season_week.sql`, `120_season.sql` [expert]:
     - deduplication of games, lines and calendar weeks (newest fetch wins);
     - `WEEK_SEQ`, teams and `HAS_TEAMS`, classification with `CLASSIFICATION_TRANSITION`, results, `IS_NEUTRAL_SITE`, `IS_CONFERENCE_GAME`, the line join with `DECIMAL` casts (probe 1.1i);
     - postseason kinds and `CFP_ROUND_SEQ`; the championship rule in its own view (`model/games`); `PARTICIPANTS_RULE`;
     - coverage flags (`HAS_VENUE_LOCATION` from `CFB_CORE.VENUES`), `PLAYS_ACCESS`;
     - week dates from the calendar or the games, `FIRST_GAME_AT`, `IS_DONE`;
     - seasons from 1869 with eras and counts.

     Tests: `tests/it/test_model_games_it.py` (game parts), `tests/it/test_model_seasons_it.py`.
   - [ ] 3.2 `020_great_circle_km.sql`, `130_venue.sql`: the point per probe 1.1c, the function, venue deduplication. Tests: `tests/it/test_model_venues_it.py` (point, no coordinates, kilometres, `ST_WITHIN`).
   - [ ] 3.3 `140_team.sql`, `150_conference.sql` (read `GAME`): teams including game-only teams, team deduplication, conferences. Tests: the game-only team test and `CURRENT_CONFERENCE` in `tests/it/test_model_teams_it.py`.
   - [ ] 3.4 `200_team_game.sql` (reads `GAME`): sides, results, flags, team-side `DECIMAL` spread and win probability, no row for a placeholder side. Tests: the team-game, placeholder and head-to-head tests (the head-to-head example file is written here and reused in 4.2).
   - [ ] 3.5 `170_ranking.sql` (reads `SEASON_WEEK`): deduplication with the `POLLS_NO` tie-break, poll codes, the label rule and the final-before-postseason rule, `IS_FINAL_POLL`, `FETCHED_AT`. Tests: the ranking parts of `tests/it/test_model_ratings_it.py`.
   - [ ] 3.6 `160_rating_sp.sql` (reads `TEAM`): deduplication, the national-averages row left out, name matching, `DECIMAL` ratings, `IS_FINAL` from `LOAD_SLICES`. Tests: the SP+ parts of `tests/it/test_model_ratings_it.py`.
   - [ ] 3.7 Performance smoke on the scaled league (needs 1.6, 3.1, 3.3, 3.4 and a "local free" window): `make model-perf ARGS="--prefix PERF_"` times `SELECT SEASON, COUNT(*) FROM GAME GROUP BY 1`, `GAME` and `TEAM_GAME` for one season, `CONFERENCE_CHAMPIONSHIP_GAME` in full, `SEASON_WEEK` in full, and a prototype of the as-of join (one point of `SEASON_WEEK` × `GAME` with the participants filter) against the Requirements targets. Numbers go to `docs/spikes/s6-model-probe.md` § Performance smoke.
     - **Gate:** a target missed by more than 2× → restructure the view first (split windows, filter by season earlier). If that fails, materialize it per Key Interfaces § Materialization fallback, log the decision, and add task 3.7b (rebuild hooks, `MATERIALIZED_STALE`, tests) before group F.
   - [ ] 3.8 `210_team_season.sql` [expert]:
     - membership expansion scoped to each slice's request, newest-slice precedence, conflict resolution and flag;
     - conference from the `FIXED` games when there is no membership;
     - classification: transition seed, then the `FIXED` games, then the membership;
     - tier lookup including the NULL-name rows;
     - division labels and the retroactive flag;
     - season records, conference records on `IS_CONFERENCE_GAME` without championship games, `CONF_POINT_DIFF`;
     - Elo first and last; SP+, the prior (join on `SEASON = S - 1`) and `SP_PRIOR_IS_FINAL`; final ranks from `IS_FINAL_POLL`.

     Q6 may run earlier than 7.2 if stored 2007 and 2017 games exist on any target (read-only, 0 calls); its outcome changes only the rows of `CLASSIFICATION_TRANSITION`. Tests: `tests/it/test_model_teams_it.py`, the division-label test in `test_model_seasons_it.py`, the prior and final-rank tests in `test_model_ratings_it.py`, and the transition and non-final-prior tests in `test_model_variants_it.py`.
   - [ ] 3.9 `300_as_of_point.sql` … `340_team_season_as_of.sql`, per `model/as-of` [expert]:
     - points, `LAST_REGULAR_WEEK_SEQ`, cutoffs and the current point from `IS_DONE`;
     - the participants filter, status and masking in `GAME_AS_OF`;
     - the latest visible week per poll by the label rule or the fetch time;
     - records, remaining and next games over `GAME_AS_OF`, Elo, ranks and SP+ at the point.

     Tests: `tests/it/test_model_as_of_it.py`; in `tests/it/test_model_variants_it.py` the partly played week (`with_thursday_result`) and the leak-free invariant for 2024 week 9 and 2026 week 5 (`league.mutate_after`, then `MINUS` both ways empty) with its positive control.
   - [ ] 3.10 `400_season_coverage.sql`: stored datasets only, declared × planned × stored × measured, the FBS-vs-FBS population for lines, `COMPLETE_SHARE` per dataset, the trust-level order, and the notes. Tests: `tests/it/test_model_coverage_it.py`.
   - [ ] 3.11 `500_model_checks.sql`: the 15 checks of `model/refresh-step`, each filtered by season before it aggregates (probe 1.1g), with `DETAIL` listing up to 10 offenders. Tests: the view part of `tests/it/test_model_refresh_step_it.py` (renamed power conference), the check assertions used by 3.1–3.8, and `test_base_league_passes_error_checks` (every season of the base league).

4. **Refresh step, examples, performance**
   - [ ] 4.1 `model/lua/refresh_model.lua` per Key Interfaces, every statement through `pquery`:
     - `loader/bundle.py` gains a model entry (no Lua logic) that writes `build/sql/model_refresh_model.sql`;
     - the model deploy installs it in `{INFRA_SCHEMA}`;
     - `loader/load-plan.yaml` registers step 100 `MODEL`.

     Test first: `tests/it/test_model_refresh_step_it.py` (chain order, error stops the chain, warnings pass, outside a refresh, unknown point, `make model-check`). Bundle unit tests: no bare `query(`, no `ALTER VIRTUAL SCHEMA`.
   - [ ] 4.2 Example analyses `sql/model/examples/{as_of_engine_inputs,all_time_upsets,head_to_head,cfp_field_inputs}.sql`, with a header comment (question, consumer phase, objects read). `as_of_engine_inputs.sql` follows the Engine contract mapping; `cfp_field_inputs.sql` reads the winners of `CONFERENCE_CHAMPIONSHIP_GAME` and the final committee ranking. Tests `tests/it/test_model_docs_it.py`: comments, grain, names, dictionary vs database, examples with expected league answers (2024 week 9 engine inputs hold no championship or postseason game) and the dependency walk. Final-model tests in `tests/it/test_model_deploy_it.py`: install everything, a second deploy, the full deploy, generator shapes from `fake.py` for four seasons.
   - [ ] 4.3 Performance on the full model (needs 3.9–3.11 and a "local free" window): `make model-perf ARGS="--prefix PERF_"` for every Requirements target, including `TEAM_SEASON_AS_OF` and `GAME_AS_OF` for one point, `MODEL_CHECKS` for one season and `REFRESH_MODEL`. Same gate as 3.7; numbers appended to `docs/spikes/s6-model-probe.md`.

5. **Quality gate**
   - [ ] 5.1 Lint, coverage and the integration suite:
     - `make lint` is clean (`mypy` `files` += `model`, `vs/host/sqlobjects.py`);
     - `make coverage` reaches ≥ 90 % line coverage for `model` and `vs/host/sqlobjects.py` (Makefile: add `coverage report --include="model/*,vs/host/sqlobjects.py"`);
     - `make test-it` is green, including the foundation and loader suites;
     - `make model-docs && git diff --exit-code docs/model-dictionary.md`.

6. **Documentation** (after Phase 2's group I. Re-read each file first, and only append to decision tables.)
   - [ ] 6.1 `docs/user-guide.md` § "The analytical model":
     - the layout and the object list with grain;
     - as-of points, the one horizon, the `REFRESH_WEEK` mapping and the hidden result-dependent games, with examples;
     - the engine inputs and their `DECIMAL` scales;
     - the SP+ prior: the previous season as last fetched, and why a backfill after 15 February makes it final (`SP_PRIOR_NOT_FINAL`); CFBD's methodology drift in historic Elo, win probability and SP+;
     - trust levels and on-demand datasets (which virtual-schema table, with required filters);
     - `GEOMETRY(4326)` and `GREAT_CIRCLE_KM`;
     - `make model`, `make model-check`, `make model-docs`, `make model-perf`;
     - the examples, and the `MODEL` refresh step and its log.
   - [ ] 6.2 `specs/architecture.md`:
     - The `CFB_MODEL` component: views only, the reference tables, reads from `CFB_CORE` + `CFB_META.{LOAD_PLAN, LOAD_PLAN_GAPS, LOAD_SLICES}`, `GEOMETRY(4326)` instead of `GEOGRAPHY`, as-of semantics, refresh step 100.
     - Replace the aspirational dimension and fact list with the stored-only model, and name the on-demand datasets.
     - § Engine contract: one line that `GAME_AS_OF` and `TEAM_SEASON_AS_OF` provide the data inputs (the mapping lives in the user guide).
     - The schema table: `CFB_MODEL` "Reads from"; `CFB_META.MODEL_CHECK_LOG`; `CFB_INFRA.REFRESH_MODEL`.
     - Open question: as-of granularity inside the postseason (Q3).
   - [ ] 6.3 `docs/roadmap.md`:
     - decisions from the decision-log entries marked `Promotes to ADR: yes`, numbered after the last D-number present;
     - Phase 3 bullets: "venue `GEOMETRY(4326)`", the done-when proof by example analyses;
     - Phase 4 notes: the CFP seeding rules of 2024 and 2025 and standings-based champions are its selection configuration;
     - backlog: in-season snapshot history (SP+ and lines per week), travel distances per team and game, lower-division championship games;
     - the Phase 5 note: head-to-head from stored games (decision [18]);
     - mark Phase 3 progress.
   - [ ] 6.4 `CLAUDE.md` dense bullets:
     - the model is views only, never reads `CFBD_VS`, and lint rules apply (comments, grain, unquoted upper-case names);
     - `make model`, `make model-check`, `make model-docs`, `make model-perf`;
     - the as-of rule (one horizon, event time vs fetch time, result-dependent games hidden);
     - `GEOMETRY(4326)` + `GREAT_CIRCLE_KM`; engine-facing values are `DECIMAL`;
     - `MODEL_CHECKS` errors stop the refresh.

     `specs/mission.md`:
     - § Commands: `make model`, `make model-check`, `make model-docs`, `make model-perf`;
     - Core Capability 5: "venue `GEOMETRY(4326)` point (Exasol has no `GEOGRAPHY` type)";
     - Glossary "As-of (season, week)": "everything known after that week's games and before the next week's first kickoff".

7. **Live verification (last; zero CFBD calls; AWS `default` after "AWS free", local in a "local free" window)**
   - [ ] 7.1 Gates and deploy:
     - gates: Phase 2's group J is done (AWS demo backfill + snapshot), and the foundation's 9.4 is done; P1 and the column types re-checked against the final `catalog_hash`;
     - record `make quota TARGET=aws` (`/info` used calls, free) and `SELECT MAX(LOGGED_AT) FROM CFB_META.FETCH_LOG`;
     - `make deploy TARGET=aws`;
     - re-run probe 1.1 (a, c, e, f, i) with `--target aws`;
     - the deploy report shows 100 % comments.
   - [ ] 7.2 Checks and assumptions on AWS:
     - `make model-check TARGET=aws` for `CURRENT_AS_OF`;
     - `SELECT CHECK_NAME, SEASON, OBSERVED, DETAIL FROM CFB_MODEL.MODEL_CHECKS WHERE CHECK_STATUS = 'FAIL'` over all seasons;
     - verify Q1–Q13 (decision log) with the queries listed there; Q6 decides the rows of `CLASSIFICATION_TRANSITION`, Q7 the championship rule;
     - fix the reference seeds where they differ (conference names, poll names, label leads, tier names, championship seasons, transitions, `COMPLETE_SHARE`) and redeploy (0 calls);
     - record every outcome.
   - [ ] 7.3 Performance and examples:
     - timings per the Requirements row (query cache off, 3 warm-ups, 5 timed runs, median and max) on AWS;
     - run the four examples and keep only derived numbers (row counts, seconds);
     - apply the fallback rule if a target is missed by more than 2×. If it is, log the decision and add a follow-up task; do not change the design inside this task.
   - [ ] 7.4 Local:
     - in a "local free" window, `make restore TARGET=local NAME=<Phase 2 AWS snapshot> CONFIRM=yes` (if not yet restored), then `make deploy TARGET=local`;
     - repeat 7.2's failed-check summary and 7.3's timings;
     - `EXECUTE SCRIPT CFB_INFRA.REFRESH_MODEL(NULL, <current season>, <current week>, 'regular')`.
   - [ ] 7.5 Zero-call proof and write-up:
     - `make quota TARGET=aws`: used calls equal 7.1's value, and `FETCH_LOG` has no row after 7.1's maximum;
     - `docs/model-verification.md`, derived numbers only: trust-level counts per dataset, check results, assumption outcomes, championship-game counts per season, timings, the AWS and local versions.

## Parallelization

| Parallel Group | Tasks |
|----------------|-------|
| Group A | 1.1, 1.2, 1.3, 1.4, 1.5, 1.6 |
| Group B | 2.1, 2.2, 2.3, 2.4 |
| Group C | 2.5, 3.1, 3.2 |
| Group D | 3.3, 3.4, 3.5 |
| Group E | 3.6, 3.7 |
| Group F | 3.8 |
| Group G | 3.9, 3.10, 3.11 |
| Group H | 4.1, 4.2, 4.3 |
| Group I | 5.1 |
| Group J | 6.1, 6.2, 6.3, 6.4 |
| Group K (sequential, gated) | 7.1 → 7.2 → 7.3 → 7.4 → 7.5 |

Sequential dependencies (each group's tasks read only objects of earlier groups):
- Group A → B → C → D → E → F → G → H → I → J → K.
- Before group B: the foundation through group I, and Phase 2 tasks 3.1, 3.2, 3.3, 4.1, 4.3, 6.1, 6.2 and its run lock.
- 1.1 needs a "local free" window. 2.1 needs 1.1(b, f), 1.2 and 1.3. 2.4 needs 1.4, 1.6 and Phase 2's 6.1. 2.5 needs 2.1 and 2.4.
- 3.x need 1.5, 2.1 and 2.4. 3.1 needs 1.1(i). 3.2 needs 1.1(c, d).
- 3.3, 3.4 and 3.5 read `GAME` or `SEASON_WEEK`: they need 3.1.
- 3.6 reads `TEAM`: it needs 3.3. 3.7 needs 1.6, 3.1, 3.3, 3.4 and a "local free" window.
- 3.8 needs 3.4, 3.5, 3.6 and the outcome of 3.7 (and 3.7b if it was added).
- 3.9 needs 3.1, 3.5, 3.6 and 3.8. 3.10 needs 3.1, 3.6 and 3.8. 3.11 needs 3.1–3.8 and 1.1(g).
- 4.1 needs 3.9, 3.11 and Phase 2's 4.1 and 4.3. 4.2 needs 3.1–3.11. 4.3 needs 3.9–3.11 and a "local free" window.
- Group J needs Phase 2's group I.
- Group K needs green groups A–J, Phase 2's group J, the foundation's 9.4, and U1–U2.

## Dead Code Removal

None. This plan adds new objects only, and every helper it touches (`tests/it/harness.py`, `vs/host/metaddl.py`, `loader/bundle.py`) stays in use.

## Verification

### Scenario Coverage

| Scenario | Test Type | Test Location | Test Name |
|----------|-----------|---------------|-----------|
| deploy: One command installs the whole model | Integration | `tests/it/test_model_deploy_it.py` | `test_make_model_installs_every_object` |
| deploy: A second deploy changes nothing | Integration | `tests/it/test_model_deploy_it.py` | `test_second_deploy_changes_nothing` |
| deploy: The full deploy includes the model | Integration | `tests/it/test_model_deploy_it.py` | `test_full_deploy_includes_model` |
| deploy: A file that breaks a lint rule stops the deploy before any statement | Integration (CLI + DB) | `tests/cli/test_model_cli.py`, `tests/it/test_model_harness_it.py` | `test_lint_errors_are_listed_and_nothing_is_sent`, `test_lint_problems_are_listed_one_per_rule_and_nothing_is_sent` |
| deploy: A view may reference a view of a later file | Integration | `tests/it/test_model_harness_it.py` | `test_a_view_in_an_earlier_file_may_read_a_later_one` |
| deploy: A failing statement rolls the whole deploy back | Integration | `tests/it/test_model_harness_it.py` | `test_a_view_that_only_fails_at_its_first_query_fails_the_deploy_and_changes_nothing` |
| deploy: An object that no file defines any more is dropped | Integration | `tests/it/test_model_harness_it.py` | `test_objects_that_no_file_defines_are_dropped` |
| deploy: A deploy is refused while a loader run holds the lock | Integration | `tests/it/test_model_harness_it.py` | `test_a_deploy_is_refused_while_a_loader_run_holds_the_lock` |
| deploy: The model accepts any data the virtual schema can store | Integration | `tests/it/test_model_deploy_it.py` | `test_model_over_generated_shapes` |
| deploy: The model never reads the virtual schema | Integration | `tests/it/test_model_harness_it.py` | `test_the_model_never_reads_the_virtual_schema` |
| documentation: Every object and column has a comment | Integration | `tests/it/test_model_docs_it.py` | `test_every_object_and_column_commented` |
| documentation: Every object comment states its grain | Integration | `tests/it/test_model_docs_it.py` | `test_object_comments_state_grain` |
| documentation: Model columns need no quoting | Integration | `tests/it/test_model_docs_it.py` | `test_columns_need_no_quoting` |
| documentation: The data dictionary matches the deployed comments | Integration | `tests/it/test_model_docs_it.py` | `test_dictionary_matches_deployed_comments` |
| documentation: The example analyses read only the model | Integration | `tests/it/test_model_docs_it.py` | `test_examples_read_only_model` |
| seasons-eras: Early seasons carry their eras | Integration | `tests/it/test_model_seasons_it.py` | `test_early_season_eras` |
| seasons-eras: Modern seasons carry their eras | Integration | `tests/it/test_model_seasons_it.py` | `test_modern_season_eras` |
| seasons-eras: A season without games is still listed | Integration | `tests/it/test_model_seasons_it.py` | `test_season_without_games_listed` |
| seasons-eras: Division labels follow the era | Integration | `tests/it/test_model_seasons_it.py` | `test_division_labels_follow_era` |
| seasons-eras: Weeks take their dates from the calendar when it is stored | Integration | `tests/it/test_model_seasons_it.py` | `test_week_dates_from_calendar_or_games` |
| seasons-eras: The postseason follows the regular season in one week order | Integration | `tests/it/test_model_seasons_it.py` | `test_week_sequence_order` |
| seasons-eras: A week is done when its games are played or its time has passed | Integration | `tests/it/test_model_seasons_it.py` | `test_week_done` |
| seasons-eras: Only the newest season is current | Integration | `tests/it/test_model_seasons_it.py` | `test_only_newest_season_current` |
| teams-conferences: Membership follows realignment | Integration | `tests/it/test_model_teams_it.py` | `test_membership_follows_realignment` |
| teams-conferences: A stored slice speaks only for the seasons it was requested for | Integration | `tests/it/test_model_teams_it.py` | `test_slice_speaks_only_for_its_seasons` |
| teams-conferences: Conflicting memberships are resolved and flagged | Integration | `tests/it/test_model_teams_it.py` | `test_membership_conflict_flagged` |
| teams-conferences: Power tiers follow the era | Integration | `tests/it/test_model_teams_it.py` | `test_power_tiers_follow_era` |
| teams-conferences: Other FBS conferences take the default tier of the era | Integration | `tests/it/test_model_teams_it.py` | `test_default_tiers_follow_era` |
| teams-conferences: A team without a membership takes its conference from its games | Integration | `tests/it/test_model_teams_it.py` | `test_conference_from_games` |
| teams-conferences: The games decide the classification when no transition is seeded | Integration | `tests/it/test_model_teams_it.py` | `test_classification_from_games` |
| teams-conferences: A seeded transition overrides the recorded classification | Integration | `tests/it/test_model_variants_it.py` | `test_classification_from_transition_seed` |
| teams-conferences: Teams that appear only in games are listed | Integration | `tests/it/test_model_teams_it.py` | `test_game_only_teams_listed` |
| teams-conferences: Divisions are kept per season | Integration | `tests/it/test_model_teams_it.py` | `test_divisions_per_season` |
| teams-conferences: Season records count completed games, and conference records leave out the championship game | Integration | `tests/it/test_model_teams_it.py` | `test_season_records` |
| games: A game stored twice appears once | Integration | `tests/it/test_model_variants_it.py` | `test_duplicate_game_appears_once` |
| games: Results name the winner and the loser | Integration | `tests/it/test_model_games_it.py` | `test_results_winner_and_loser` |
| games: A tie has no winner | Integration | `tests/it/test_model_games_it.py` | `test_tie_has_no_winner` |
| games: Pregame lines join by game id | Integration | `tests/it/test_model_games_it.py` | `test_pregame_lines_join_by_game` |
| games: Postseason games are typed | Integration | `tests/it/test_model_games_it.py` | `test_postseason_kinds` |
| games: Postseason games without an FBS team wait for their own week | Integration | `tests/it/test_model_games_it.py` | `test_non_fbs_postseason_waits` |
| games: A conference championship game is recognized within its conference's championship seasons | Integration | `tests/it/test_model_games_it.py` | `test_conference_championship_rule` |
| games: A same-conference game after the championship game is not mistaken for it | Integration | `tests/it/test_model_games_it.py` | `test_game_after_championship_not_marked` |
| games: A conference game after the power conferences' championship week is not taken for a championship game | Integration | `tests/it/test_model_games_it.py` | `test_conference_game_after_power_championship_week_not_marked` |
| games: A championship game the rule misses is reported | Integration | `tests/it/test_model_games_it.py` | `test_missed_championship_reported` |
| games: A game without named teams is a placeholder | Integration | `tests/it/test_model_games_it.py` | `test_placeholder_game` |
| games: Every game carries its coverage flags | Integration | `tests/it/test_model_games_it.py` | `test_game_coverage_flags` |
| games: Each team sees the game from its side | Integration | `tests/it/test_model_games_it.py` | `test_team_game_sides` |
| games: The head-to-head series comes from stored games | Integration | `tests/it/test_model_games_it.py` | `test_head_to_head_from_stored_games` |
| ratings-rankings: SP+ ratings attach to teams by name | Integration | `tests/it/test_model_ratings_it.py` | `test_sp_team_matching` |
| ratings-rankings: A rating or a ranking stored twice appears once | Integration | `tests/it/test_model_variants_it.py` | `test_duplicate_ratings_and_rankings` |
| ratings-rankings: The previous season's SP+ is the prior | Integration | `tests/it/test_model_ratings_it.py` | `test_sp_prior` |
| ratings-rankings: A prior that is not final is used and reported | Integration | `tests/it/test_model_variants_it.py` | `test_nonfinal_prior_reported` |
| ratings-rankings: The current season's SP+ is marked as not final | Integration | `tests/it/test_model_ratings_it.py` | `test_current_sp_not_final` |
| ratings-rankings: Polls are coded and placed in the week order | Integration | `tests/it/test_model_ratings_it.py` | `test_polls_coded_and_ordered` |
| ratings-rankings: An unknown poll is kept and flagged | Integration | `tests/it/test_model_ratings_it.py` | `test_unknown_poll_flagged` |
| ratings-rankings: A committee ranking labeled postseason is visible after the regular season | Integration | `tests/it/test_model_ratings_it.py` | `test_postseason_committee_ranking` |
| ratings-rankings: Final ranks belong to the team-season | Integration | `tests/it/test_model_ratings_it.py` | `test_final_ranks` |
| venues: A venue location is a point with SRID 4326 | Integration | `tests/it/test_model_venues_it.py` | `test_venue_point_srid_4326` |
| venues: A venue without coordinates has no point | Integration | `tests/it/test_model_venues_it.py` | `test_venue_without_coordinates` |
| venues: Great-circle distances are in kilometres | Integration | `tests/it/test_model_venues_it.py` | `test_great_circle_km` |
| venues: Venue points work with Exasol's spatial functions | Integration | `tests/it/test_model_venues_it.py` | `test_spatial_within` |
| coverage: A known gap is not available | Integration | `tests/it/test_model_coverage_it.py` | `test_known_gap_not_available` |
| coverage: A finished season with full coverage is complete | Integration | `tests/it/test_model_coverage_it.py` | `test_complete_season` |
| coverage: Low measured coverage is partial | Integration | `tests/it/test_model_coverage_it.py` | `test_partial_season` |
| coverage: The current season is in progress | Integration | `tests/it/test_model_coverage_it.py` | `test_current_season_in_progress` |
| coverage: Stored data for seasons outside the load plan is on demand | Integration | `tests/it/test_model_coverage_it.py` | `test_unplanned_seasons_on_demand` |
| coverage: Datasets that are never stored have no season rows | Integration | `tests/it/test_model_coverage_it.py` | `test_unstored_datasets_have_no_season_rows` |
| coverage: Planned data that is not loaded yet is missing | Integration | `tests/it/test_model_coverage_it.py` | `test_planned_not_loaded_missing` |
| coverage: Elo coverage starts in 1890 | Integration | `tests/it/test_model_coverage_it.py` | `test_elo_coverage_from_1890` |
| coverage: The dataset list names what the model does not store | Integration | `tests/it/test_model_coverage_it.py` | `test_dataset_catalog` |
| as-of: As-of points match the refresh arguments | Integration | `tests/it/test_model_as_of_it.py` | `test_points_match_refresh_arguments` |
| as-of: The current point is the newest finished week | Integration | `tests/it/test_model_as_of_it.py` | `test_current_as_of` |
| as-of: A partly played week does not move the current point | Integration | `tests/it/test_model_variants_it.py` | `test_current_as_of_ignores_partly_played_week` |
| as-of: Results after the point are hidden | Integration | `tests/it/test_model_as_of_it.py` | `test_results_after_point_hidden` |
| as-of: Records count only games up to the point | Integration | `tests/it/test_model_as_of_it.py` | `test_records_up_to_point` |
| as-of: Games whose participants depend on results are hidden until they are known | Integration | `tests/it/test_model_as_of_it.py` | `test_result_dependent_games_hidden` |
| as-of: Remaining games count only games with known participants | Integration | `tests/it/test_model_as_of_it.py` | `test_remaining_games_known_participants` |
| as-of: A line is visible only once it was known | Integration | `tests/it/test_model_as_of_it.py` | `test_line_visibility` |
| as-of: The current season's SP+ is visible only from the point when it was fetched | Integration | `tests/it/test_model_as_of_it.py` | `test_sp_visibility` |
| as-of: Polls become visible when they are released | Integration | `tests/it/test_model_as_of_it.py` | `test_poll_visibility` |
| as-of: A ranking fetched before the next kickoff is visible at that point | Integration | `tests/it/test_model_as_of_it.py` | `test_ranking_fetched_before_cutoff` |
| as-of: Team Elo uses only completed games | Integration | `tests/it/test_model_as_of_it.py` | `test_team_elo_as_of` |
| as-of: Engine inputs are fixed-scale decimals | Integration | `tests/it/test_model_as_of_it.py` | `test_engine_inputs_are_decimals` |
| as-of: No fact after the point changes an as-of answer | Integration | `tests/it/test_model_variants_it.py` | `test_leak_free_invariant` |
| refresh-step: The refresh runs the model step after the fetch | Integration | `tests/it/test_model_refresh_step_it.py` | `test_refresh_runs_model_step` |
| refresh-step: A failed error check stops the chain | Integration | `tests/it/test_model_refresh_step_it.py` | `test_error_check_stops_chain` |
| refresh-step: Warnings do not stop the chain | Integration | `tests/it/test_model_refresh_step_it.py` | `test_warnings_do_not_stop_chain` |
| refresh-step: The checks run for any point outside a refresh | Integration | `tests/it/test_model_refresh_step_it.py` | `test_checks_outside_refresh` |
| refresh-step: A renamed power conference is an error for its season | Integration | `tests/it/test_model_refresh_step_it.py` | `test_renamed_power_conference` |
| refresh-step: An unknown point is rejected | Integration | `tests/it/test_model_refresh_step_it.py` | `test_unknown_point_rejected` |
| refresh-step: The checks can be printed without the step | Integration (CLI + DB) | `tests/it/test_model_refresh_step_it.py` | `test_model_check_command` |

Supporting tests back the integration tests. They do not replace them:
- unit (pure computation, no I/O):
  - parser, lint and render: `tests/unit/test_sqlobjects.py`;
  - deploy order, lock, rollback, orphans, compile and read-back with a fake connection: `tests/unit/test_model_deploy.py`;
  - seeds against the docs, the catalog and the load plan: `tests/unit/test_model_seeds.py`;
  - the dictionary: `tests/unit/test_model_dictionary.py`;
  - the leagues, including the structural guard that mirrors the ERROR checks: `tests/unit/test_league.py`, `tests/unit/test_scaled_league.py`;
  - the bundle: `tests/unit/test_lua_bundle.py` (extended);
  - the namespace and renderer: `test_namespace.py`, `test_meta_ddl.py`;
  - CLI: `tests/cli/test_model_cli.py`;
- integration: `test_base_league_passes_error_checks` in `tests/it/test_model_refresh_step_it.py` (every season of the base league, no failed ERROR check).

### Manual Testing

All commands run from the repo root against Exasol Personal local (`TARGET=local`) after `make deploy`, unless the row names AWS. None spends a CFBD call.

| Feature | Command | Expected Output |
|---------|---------|-----------------|
| model/deploy | `make model`, then again; `make model TARGET=aws` | Report: 7 tables, 1 function, 18 views, comments 100 %; the second run reports the same objects and drops nothing |
| model/documentation | `make model-docs && git diff --stat docs/model-dictionary.md`; `SELECT OBJECT_NAME, OBJECT_COMMENT FROM EXA_ALL_OBJECTS WHERE ROOT_NAME = 'CFB_MODEL' ORDER BY 1` | No diff; every object commented, each comment starting with "One row per" (the function excepted) |
| model/seasons-eras | `SELECT SEASON, DIVISION_ERA_CODE, POSTSEASON_ERA_CODE, CFP_TEAMS, TIES_POSSIBLE, GAME_COUNT FROM CFB_MODEL.SEASON WHERE SEASON IN (1869, 1871, 1950, 1985, 2013, 2024, 2026) ORDER BY 1` | One row each; 1871 with 0 games; 2024 `CFP_12`, 12 |
| model/teams-conferences | `SELECT SEASON, CONFERENCE_NAME, CONFERENCE_TIER, IS_POWER_CONFERENCE, CONFERENCE_SOURCE, CLASSIFICATION_SOURCE FROM CFB_MODEL.TEAM_SEASON WHERE SCHOOL = 'Texas' AND SEASON BETWEEN 2022 AND 2026 ORDER BY 1` | Big 12 through 2023 (`P5`), SEC from 2024 (`P4`), source `AFFILIATION` |
| model/games | `SELECT C.SEASON, C.CONFERENCE_NAME, COUNT(*) FROM CFB_MODEL.CONFERENCE_CHAMPIONSHIP_GAME C WHERE C.SEASON BETWEEN 1992 AND 2025 GROUP BY 1, 2 ORDER BY 1, 2`; run `sql/model/examples/head_to_head.sql` for Michigan and Ohio State | One game per conference and season of the `CONFERENCE_CHAMPIONSHIP` seed (Q7; 2024: 9); the all-time series with the last and next meeting, 0 calls |
| model/ratings-rankings | `SELECT POLL_CODE, MIN(SEASON), COUNT(DISTINCT SEASON), SUM(CASE WHEN IS_FINAL_POLL THEN 1 ELSE 0 END) FROM CFB_MODEL.RANKING GROUP BY 1` | `AP`, `COACHES`, `CFP` from 2014 (stored), final polls per finished season; no `OTHER` after the seed fixes |
| model/venues | `SELECT VENUE_NAME, ST_X(GEO_POINT), ST_Y(GEO_POINT) FROM CFB_MODEL.VENUE WHERE HAS_LOCATION LIMIT 5`; `SELECT CFB_MODEL.GREAT_CIRCLE_KM(a.LATITUDE, a.LONGITUDE, b.LATITUDE, b.LONGITUDE) FROM CFB_MODEL.VENUE a, CFB_MODEL.VENUE b WHERE a.VENUE_NAME = 'Michigan Stadium' AND b.VENUE_NAME = 'Ohio Stadium'` | Longitude/latitude pairs; a plausible distance in km (about 260) |
| model/coverage | `SELECT DATASET_CODE, TRUST_LEVEL, COUNT(*) FROM CFB_MODEL.SEASON_COVERAGE GROUP BY 1, 2 ORDER BY 1, 2` | `GAMES` `COMPLETE` for stored final seasons, `NOT_AVAILABLE` for 1871, `IN_PROGRESS` for 2026; `SP_PLUS` `ON_DEMAND` 1970–2012; no `PLAYS` or `WEATHER` rows |
| model/as-of | `SELECT * FROM CFB_MODEL.CURRENT_AS_OF`; `SELECT SCHOOL, WINS, LOSSES, CONF_WINS, CONF_POINT_DIFF, ELO_AS_OF, AP_RANK, CFP_RANK FROM CFB_MODEL.TEAM_SEASON_AS_OF WHERE AS_OF_SEASON = 2024 AND AS_OF_WEEK_SEQ = 9 AND CLASSIFICATION = 'fbs' ORDER BY AP_RANK NULLS LAST LIMIT 10`; `SELECT COUNT(*) FROM CFB_MODEL.GAME_AS_OF WHERE AS_OF_SEASON = 2024 AND AS_OF_WEEK_SEQ = 9 AND (IS_CONF_CHAMPIONSHIP OR POSTSEASON_KIND IS NOT NULL)` | The newest finished week; the 2024 records after week 9 and the AP poll released after week 9; 0 result-dependent games at week 9 |
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
| Performance | `make model-perf ARGS="--prefix PERF_ --drop"` (local, "local free" window) | Every target met, or a logged decision per the fallback rule |
| Lint | `make lint` | 0 ruff errors, 0 mypy errors (`vs`, `loader`, `model`) |
| Format | `uv run ruff format --check .` | No changes |
| Coverage | `make coverage` | ≥ 90 % line coverage for `vs/cfbd_vs`, `vs/gen`, `loader`, `model` and `vs/host/sqlobjects.py` |
| Deploy | `make deploy && make deploy TARGET=aws` | Exit 0 both; model comments 100 %; a second run drops nothing |
| Layering | `git grep -n -i -e 'CFBD_VS' -e '{VS_NAME}' -- sql/model` | No match |
| Checks | `make model-check TARGET=aws SEASON=<current> WEEK=<current>` | No failed ERROR check |
| Zero calls | `make quota TARGET=aws` before and after group K | `/info` used calls unchanged |
| No data in git | `git ls-files '*.parquet' '*.cfbd.json'` | Empty |
