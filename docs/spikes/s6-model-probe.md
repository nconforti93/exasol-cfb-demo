# S6 model platform probe: findings

Task 1.1 of `add-cfb-analytical-model`. Probe script: `docs/spikes/s6_model_probe.py`. Raw results: `cache/probe/s6-model-probe.json` (gitignored).

- **Runtime:** Exasol Personal local, `2026.2.0-nano.3`, 1 node, 127.0.0.1:8563, through `vs/host/target.py` `resolve('local')`, as `sys`. pyexasol 2.4.1.
- **Run:** 2026-10-22. Every item (a)-(i) was run, the whole script twice end to end after fixes, and no item ended negative.
- **Safety:**
  - Objects: only schemas named `PROBE_MP_*` (`A`, `B`, `BC`, `C`, `D`, `D2`, `E`, `EM`, `F`, `F2`, `G`, `H`, `I`). No user, connection, virtual schema or BucketFS object. The cleanup drops only schemas whose name starts with `PROBE_MP_`, so another agent's `PROBE_LD_*` schemas are never touched.
  - Afterwards nothing named `PROBE%` is left on the target.
  - Untouched: `TEST_*`, `CFB_*`, `CFBD_VS`, `CFB_DIAG`, `VECTOR_DEMO`, `VEC_SMOKE`. The only access outside `PROBE_MP_*` was a metadata read of the catalog (`EXA_SCHEMAS`, `EXA_ALL_OBJECTS`) while choosing the dependency test, which showed that local `CFB_META` still has the pre-Phase-2 shape (two tables, no views).
  - Zero CFBD calls, no AWS.
- **Re-run:** `uv run python docs/spikes/s6_model_probe.py [--only a,b] [--keep] [--target aws]`. Exit code 3 when the gate (a or b) is negative.
  - `--target aws` (task 7.1) was not run. Note that no item is strictly read-only: every item creates `PROBE_MP_*` schemas and tables. Items c, d, e, f, g and i touch nothing shared; b, f and h write DDL/DML inside their own schemas. 7.1 should decide which to run under the "deploy and query only" etiquette.
- **Gate: passed.** (a) and (b) are positive, so no fallback is applied. (c) fixes the `GEO_POINT` expression (below).
- **Not run here:** Q6 (decision-log, `GAME` classification of App State 2007 and Liberty 2017). It needs stored games on a target and is read-only at zero calls; it is not part of 1.1's items.

## Summary

Each row: result → the fallback the plan adopts if it is negative (none is), and what the later tasks do with it.

| Item | Result | Fallback if negative | What the plan does with it |
|---|---|---|---|
| (a) Comments | **Pass.** Inline comments on tables, views and their columns read back exactly from `EXA_ALL_OBJECTS.OBJECT_COMMENT` and `EXA_ALL_COLUMNS.COLUMN_COMMENT`, including `''` quotes, newline, tab, `{x}`, `%s` and non-ASCII. **`COMMENT ON TABLE <view>`, `COMMENT ON <view>` and `COMMENT ON COLUMN <view>.<col>` all fail** ("table PROBE_MP_A.V not found"); `COMMENT ON COLUMN <table>.<col>` works. **Maximum length 2000 characters** for each of view, view column, table and table column comments ("comment string too long, maximum allowed characters: 2000") | Comments by the deploy after creation | Inline comments only: decision [13] holds (not in the grammar for views, and in practice rejected). 1.3 lint: a comment over 2000 characters is an error. 2.3 dictionary can rely on `EXA_ALL_*` for read-back |
| (b) Transactional DDL | **Pass.** With autocommit off, `CREATE OR REPLACE` table, view and function, `DROP VIEW`, `COMMENT ON FUNCTION`, `INSERT`, `CREATE TABLE` and `CREATE SCHEMA` + table were all undone by `ROLLBACK`: rows, structure, table, view and column comments, view text, function value and comment, the dropped view and the new schema all came back exactly (before = after, in the same session and a second one). Another session reading the table and calling the function during the open transaction saw the old state without blocking, and saw none of the uncommitted objects. A failing statement (`CREATE VIEW` over a missing table) leaves the transaction usable. A view over a table created in the same transaction compiled in it; the objects became visible to others only at `COMMIT` | `{prefix}CFB_MODEL_NEXT` first | 2.1: one transaction for the whole deploy, `ROLLBACK` on the first failure (a failed statement does not abort the transaction by itself). No `_NEXT` schema |
| (c) `GEOMETRY(4326)` | **Pass.** See "Spatial" below. `GEO_POINT` = `CASE WHEN <lon> IS NULL OR <lat> IS NULL THEN NULL ELSE CAST('POINT (' \|\| <lon> \|\| ' ' \|\| <lat> \|\| ')' AS GEOMETRY(4326)) END`. **The NULL guard is required.** `ST_DISTANCE` on 4326 is **planar, in degrees** | If geodesic: keep `GREAT_CIRCLE_KM` (not needed) | 3.2 uses the guarded expression. Q10 is resolved: planar. The function stays the only km distance |
| (d) `GREAT_CIRCLE_KM` | **Pass.** The Key Interfaces text compiles as written (`IN` modes, `RETURN DOUBLE IS … BEGIN … END`). It accepts the statement with a trailing `/` line, without it, and with only the `;`. Values equal Python's haversine within 5.5e-12 km (Dallas to New York 2205.7345 km; antipodes 20015.1144; pole to pole; across the antimeridian; a rounding edge near the antipode, where `LEAST(1, …)` matters). Any NULL argument gives NULL; `DECIMAL` and integer arguments work. A view in another schema calls it by its qualified name. `COMMENT ON FUNCTION` is read back from `EXA_ALL_FUNCTIONS.FUNCTION_COMMENT` | Inline the haversine in the examples | 3.2 as planned. 1.3 `split`: the `/` line is optional; strip it or send it, both work |
| (e) Dependencies | **Pass.** `EXA_ALL_DEPENDENCIES` lists view → view, view → table, view → a view in another schema (the `CFB_META` stand-in `PROBE_MP_EM.META_V`), and **view → function** (`REFERENCED_OBJECT_TYPE = FUNCTION`). `EXA_DBA_DEPENDENCIES_RECURSIVE` adds `DEPENDENCY_LEVEL` (V5 → V3/V4 level 1, F/T/META_V level 2, MT level 3). A `FORCE` view over a missing object has one row with an empty referenced name | Parse references from view text only | 1.3's reference parser can be cross-checked against `EXA_ALL_DEPENDENCIES` in 2.5, functions included |
| (f) `FORCE` views | **Pass, with three deploy rules.** See "FORCE views" below. Plain `CREATE OR REPLACE VIEW` over a missing table fails ("object … not found"); `FORCE` creates it | Plain views plus an order lint | The `FORCE` design stays. 2.1 must **compile every view** after creating it (`SELECT 1 FROM <view> WHERE 1 = 0`) and read the columns and comments only afterwards |
| (g) Filters reach the scans | **Pass.** On 120,000 fact rows (30 seasons, a season holds 4,000): a season filter on a dedupe-and-window view read 4,000 rows; a filter on the point columns of `AS_OF_POINT` × windowed view read 4,000 (unfiltered 120,000; 0.19 s → 0.03 s); a season filter on a three-branch `UNION ALL` of windowed branches read 4,000 in every fact scan (unfiltered 120,000; 0.63 s → 0.10 s). Results equal the hand-inlined queries. One caveat, below | `REFRESH_MODEL` and `make model-check` inline the season per branch | **No fallback for the `UNION ALL`.** 3.11 may keep the view and filter it by season from outside. 3.9 point views work as designed. Caveat for the prior-season join: see "Pushdown" |
| (h) Nested Lua step | **Pass.** Inner script: insert, `COMMIT`, insert, `error()`. Under autocommit off, with the outer script catching the failure through `pquery`, rows 1 (outer, inserted before the call) and 2 (inner, before its `COMMIT`) survived the caller's `ROLLBACK`; 3 (after the inner `COMMIT`) and 4 (outer, after the failure) did not. The wrapper recipe (insert `STARTED`, `COMMIT`, `pquery` the step, on failure `ROLLBACK`, insert `FAILED`, `COMMIT`, `error(msg, 0)`; the step inserts check rows, `COMMIT`s and `error()`s) left both run rows and the check-log row, with autocommit on and off. Consistent with Phase 2's finding that an inner script shares the outer transaction | Log after the run only | 4.1 as planned. The step's `COMMIT` also commits the caller's earlier work, so the refresh must commit its own state before the step (it does) |
| (i) `DOUBLE` → `DECIMAL` | **Pass: the cast rounds, it does not truncate.** `25.149999999` → `25.15` (6,2); `0.123449999` → `0.1234` and `0.12345` → `0.1235` (5,4); `-7.5`, `0.7`, `-0.6` are exact. A cast outside the range **raises** (`numeric value out of range`), for example `CAST(25.15 AS DECIMAL(5,4))`; a `WHERE` or `CASE` guard in the same select prevents it. Tie caveat below. pyexasol read-back: see "Read-back" | `ROUND(x, s)` first: does not help, see below | 3.x views cast as planned, no `ROUND` first. Record the tie caveat in the dictionary comment of the spread and probability columns |

## Spatial (c)

- **Point text from numbers:**
  - Plain `||` of the two `DOUBLE` columns works: Exasol prints 15 significant digits (`POINT (-97.1234567890123 33.4567890123456)`), and small values in exponent form (`9.99999999999999e-8`) parse. Round trip through `ST_X`/`ST_Y` over 9 coordinates (including 0, 1e-7, the poles' edge and 179.999999999): worst absolute error 9.3e-23, far inside 1e-9.
  - `TRIM(TO_CHAR(x, '9999990.0000000000000'))` also round-trips (error 0) but is not needed. **Use plain `||`.**
  - **`||` treats NULL as the empty string.** `'POINT (' || NULL || ' ' || 1 || ')'` is `POINT ( 1)`, and `CAST('POINT ( 1)' AS GEOMETRY(4326))` raises "ParseException: Expected number but encountered ')'", which would fail every query over `VENUE`. The `CASE WHEN … IS NULL` guard returns NULL instead.
- **Column type:**
  - `CAST(… AS GEOMETRY(4326))` in a view, in `CREATE TABLE … AS` and inserted into a `GEOMETRY(4326)` column all show `GEOMETRY(4326)` in `EXA_ALL_COLUMNS`.
  - `ST_SETSRID(CAST(… AS GEOMETRY), 4326)` also shows `GEOMETRY(4326)`. `CAST(… AS GEOMETRY)` alone shows `GEOMETRY` (SRID 0). So the typed cast is needed.
- **`ST_DISTANCE`:** one degree of longitude at latitude 33 gives `1.0`; Dallas to New York gives `24.13325053` = the planar distance in degrees (haversine says 2205.7 km). After `ST_TRANSFORM(…, 5070)` it gives 2,196,123.6 (metres, 0.4% from the haversine, as expected from the projection). Q10 is true.
- **`ST_WITHIN` with a 4326 polygon:** works with `CAST('POLYGON (…)' AS GEOMETRY(4326))`, with a plain string literal (it takes the other operand's SRID), and with `ST_SETSRID(CAST(… AS GEOMETRY), 4326)`. A polygon cast to unlabelled `GEOMETRY` fails: "Operands of function ST_WITHIN have different SRIDs: 4326 and 0".
- **`EXA_SPATIAL_REF_SYS`:** has 3,869 rows, including 4326 (`EPSG`, `+proj=longlat +datum=WGS84`) and 5070 (`NAD83 / Conus Albers`, `+proj=aea …`). `ST_TRANSFORM(point4326, 5070)` of (-97, 33) gives x = -92867.34, y = 1103782.59.
- `ST_ASTEXT` and `ST_SRID` do not exist on this version (not callable); read points back with `ST_X` and `ST_Y`.

## `FORCE` views (f)

Observed on `2026.2.0-nano.3`:
- **Creation over a missing table:** `CREATE OR REPLACE FORCE VIEW v (A COMMENT IS 'a', B COMMENT IS 'b') AS SELECT A, B FROM missing COMMENT IS 'v'` succeeds. The view comment is stored at once (`EXA_ALL_OBJECTS`, `EXA_ALL_VIEWS`). **`EXA_ALL_COLUMNS` has no rows for the view** until it has compiled, so the declared column comments are not visible yet. `FORCE` over an existing table behaves the same: created, no column rows until the first access.
- **Query while the object is missing:** fails with "view FV is invalid: object PROBE_MP_F.LATER not found", also `… WHERE 1 = 0`.
- **After the table is created, with no re-creation of the view:** the first query works. **`EXA_ALL_COLUMNS` then lists the columns with their types and the declared comments** (`comment a`, `comment b`). `SELECT 1 FROM v WHERE 1 = 0` compiles the same way (columns and comments appear, no rows read). A plain view over an existing table has its columns visible at creation.
- **Order independence:** view A over view B over a missing table, created in the order A, B, table, answers a query on A.
- **`STATUS`** (`EXA_ALL_COLUMNS`, NULL when fine):
  - After `CREATE OR REPLACE TABLE` with the same columns, the view's columns are `OUTDATED`; the next query works and the status returns to NULL, comments intact.
  - After a replacement that drops a column the view uses, the status stays `OUTDATED` and the query fails ("view FV is invalid: object B not found"); restoring the column makes it work again.
  - `DROP TABLE` of a table a view uses is allowed; the view becomes `OUTDATED`, fails, and works again when the table is recreated.
- **Mismatches surface only at compile time:** a declared list of 3 columns over a 2-column table, or 1 declared over a 2-column select, is created without error and fails at the first query ("object C not found" / "invalid number of column names specified"). A reference to a column that does not exist also fails only at the first query.
- **Inside one transaction:** views created before their table, with the table created later in the same transaction and one view compiled in it, committed cleanly; the compiled view's column comments were visible after `COMMIT`, the uncompiled view's only after a compile query from another session.
- **Rules for 2.1:**
  1. Deploy in the planned single transaction, then compile every view in file order with `SELECT 1 FROM <view> WHERE 1 = 0`; a failure there is the deploy error, and the transaction rolls back.
  2. Verify comments and column lists only after the compile step.
  3. A deploy that replaces reference tables leaves dependent views `OUTDATED`, so the compile step must run for every view, not only the new ones.
  4. 1.3 lint should compare the declared column list with the select list when it can; the database does not.

## Pushdown (g)

Layout: `F` (120,000 rows = 30 seasons × 2,000 games × 2 `REQ_AT` versions), a `QUALIFY ROW_NUMBER()` dedupe view, a team-season window view over it, a 450-row `POINTS` table, and views as in the plan. Profile (`EXA_USER_PROFILE_LAST_DAY`, `OUT_ROWS` of the scans of `F`; query cache off):

| Query | Rows read from `F` | Season holds |
|---|---|---|
| window view, no filter | 120,000 | 4,000 |
| window view `WHERE SEASON = 2010` | 4,000 | 4,000 |
| `AS_OF_POINT` × window view, no filter | 120,000 | |
| same, `WHERE POINT_SEASON = 2010 AND POINT_WEEK = 5` | 4,000 (join output 670 rows) | 4,000 |
| `UNION ALL` of 3 windowed branches, no filter | 120,000 per scan (2 scans: the optimizer shares the common dedupe subselect) | |
| same, `WHERE SEASON = 2010` | 4,000 per scan | 4,000 |
| self-join of an aggregated view on `b.SEASON = a.SEASON - 1`, `WHERE a.SEASON = 2010` | **120,000** | 8,000 for seasons 2009 and 2010 |
| same with `SEASON IN (2009, 2010)` on both sides | 8,000 and 4,000 | |

- The filtered results equal the hand-inlined queries (for example 670 rows, sum 281 at the point 2010 week 5).
- **Caveat:** a filter on `a.SEASON` does **not** reach the other side of `b.SEASON = a.SEASON - 1`, because the optimizer does not derive `b.SEASON = 2009`. The plan's claim that `SP_PRIOR_RATING` as a join on `SEASON = S - 1` lets the season filter push down holds for the `a` side only. For the small ratings table this is irrelevant (a full scan of a few thousand rows). Any large windowed or aggregated view joined to its prior season must carry an explicit `SEASON IN (S, S - 1)` predicate on both sides, or accept a full scan.

## Read-back and numerics (i)

- **pyexasol 2.4.1 default read-back of `DECIMAL` is a Python `str`, with trailing zeros dropped** (`DECIMAL(6,2)` -7.50 arrives as `'-7.5'`, `DECIMAL(9,3)` 1 as `'1'`), because `fetch_mapper` is `None` by default. With `fetch_mapper=pyexasol.exasol_mapper` the same cells arrive as `Decimal` (numerically equal, `Decimal('0.12')`, `Decimal('0.1234')`). `DOUBLE` arrives as `float`. Tests that compare engine inputs (3.x integration tests, 4.1, 5.x harness) should connect with the mapper, or compare `Decimal(str(value))`.
- **Ties:** for inputs that are exactly a tie in the decimal text (for example `1.005`, `8.075`), neither `CAST` nor `ROUND` always agrees with half-away rounding of the decimal text, because the `DOUBLE` is binary. Sweeping every 3-decimal tie k+0.5 over 5,000 values for scale 2 and every 5-decimal tie over 10,000 values for scale 4:
  - `CAST` differs from the text rule for 286 (scale 2) and 573 (scale 4) values, by exactly one last place (0.01, 0.0001);
  - `ROUND(x, s)` (and `CAST(ROUND(x, s) AS DECIMAL)`) differs for a different set, 250 and 538 values.
  - So `ROUND` first is not a fix, and the plan keeps the plain `CAST`. Real inputs are not exact ties of this kind in practice (CFBD spreads have one decimal, ratings and probabilities have many), so the effect is at most one last place on a rare tie. Record it once in the dictionary text.
- **Range:** `DECIMAL(6,2)` holds ±9999.99 and `DECIMAL(5,4)` ±9.9999. A larger value raises `numeric value out of range` (it never wraps or truncates), also inside a view, where it fails every query that evaluates the row. Win probabilities (0 to 1), spreads and SP+ ratings are far inside the ranges; a corrupt source value would make the view fail loudly, and 3.11's checks cannot see it because the query itself fails. A `CASE WHEN ABS(x) < … THEN CAST(…)` guard avoids the error if that is ever wanted.
- A `DECIMAL` view column keeps its declared type (`DECIMAL(6,2)`, `DECIMAL(5,4)`) in `EXA_ALL_COLUMNS`.

## Nested step (h)

| Scenario | Script result | Rows after the script and the caller's `ROLLBACK` |
|---|---|---|
| outer catches the inner failure with `pquery`, autocommit off | ok | 1, 2 (visible to another session before the rollback too) |
| outer uses `query()`, inner fails, autocommit off | error, `inner boom caught in script …` | 1, 2 |
| outer catches with `pquery`, autocommit on | ok | 1, 2, 3, 4 |
| outer uses `query()`, autocommit on | error | 1, 2, 3 (the failing script commits its open work under autocommit, as in Phase 2's probe) |
| refresh wrapper (`STARTED`, step logs and `COMMIT`s then `error()`, wrapper `ROLLBACK`, `FAILED`, `COMMIT`, `error(msg, 0)`), both modes | error, message of the step | `STARTED`, `FAILED` and the one check-log row, in both modes |

## What changes in the plan

- **2.1 `deploy`:** add the compile pass (`SELECT 1 FROM <view> WHERE 1 = 0`, in file order, in the deploy transaction) and verify comments and column lists after it; `ROLLBACK` on any failure; recompile every view whenever reference tables are replaced. Optionally cross-check references against `EXA_ALL_DEPENDENCIES`.
- **3.1 and the windowed views:** no change. The dedupe `QUALIFY` windows and `PARTITION BY SEASON` windows accept a season predicate from outside. The prior-season join must not rely on filter propagation for large views (caveat above; no 3.1 view is affected).
- **3.2:** `VENUE.GEO_POINT` takes the `CASE … IS NULL` guard; `GREAT_CIRCLE_KM` unchanged. Document `ST_DISTANCE` as planar degrees.
- **3.11 `MODEL_CHECKS`:** the `UNION ALL` season pushdown works, so no fallback and no inlining in `REFRESH_MODEL` or `make model-check`. Keep the per-branch season filter anyway for the ad-hoc case of a branch that joins a non-seasoned table (not probed).
- **1.3 lint:** comments over 2000 characters, and everything in the existing plan. **2.3:** read comments from `EXA_ALL_OBJECTS` / `EXA_ALL_COLUMNS` / `EXA_ALL_FUNCTIONS` after the compile pass.
- **Tests and harness:** open pyexasol with `fetch_mapper=pyexasol.exasol_mapper` where `DECIMAL` values are compared.
