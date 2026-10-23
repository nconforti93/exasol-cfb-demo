-- LOAD_ORPHANS: one row per orphan slice, keyed by (TABLE_NAME, SLICE_KEY) (spec loader/load-plan).
-- An orphan is a LOAD_LOG row that a loader run wrote (LOADED_BY 'BACKFILL#<run>', 'REFRESH_WEEK#<run>'
-- or 'LOAD_PLAN_GAP') for a planned table, whose key matches no slice in LOAD_SLICES. It appears after
-- a catalog change, a plan edit or a restore from an older catalog. Re-fetching its table would pay
-- twice, so LOAD_STATUS reports it, BACKFILL_QUEUE drops the table, and the scripts refuse the table
-- until the owner migrates or deletes the orphans.
-- Tables whose API feature is missing are skipped: LOAD_SLICES lists none of their slices then, and
-- their stored slices are not orphans; LOAD_STATUS shows such an entry as blocked by the feature.
-- Rows written by others (the IT harness, a later PROMOTE) are never orphans.
-- Rendered by vs/host/metaddl.py (render_loader_view).

CREATE OR REPLACE VIEW "{META_SCHEMA}"."LOAD_ORPHANS" AS
SELECT
  L."TABLE_NAME" AS "TABLE_NAME",
  L."SLICE_KEY" AS "SLICE_KEY",
  L."IS_FINAL" AS "IS_FINAL",
  L."ROWS_LOADED" AS "ROWS_LOADED",
  L."LOADED_AT" AS "LOADED_AT",
  L."LOADED_BY" AS "LOADED_BY"
FROM "{META_SCHEMA}"."LOAD_LOG" L
WHERE (
    SUBSTR(L."LOADED_BY", 1, 9) = 'BACKFILL#'
    OR SUBSTR(L."LOADED_BY", 1, 13) = 'REFRESH_WEEK#'
    OR L."LOADED_BY" = 'LOAD_PLAN_GAP'
  )
  AND EXISTS (
    SELECT 1
    FROM "{META_SCHEMA}"."LOAD_PLAN" P
    CROSS JOIN "{META_SCHEMA}"."LOADER_SETTINGS" CFG
    WHERE P."TABLE_NAME" = L."TABLE_NAME"
      AND (
        P."GATED_BY" IS NULL
        OR INSTR(',' || CFG."API_FEATURES" || ',', ',' || P."GATED_BY" || ',') > 0
      )
  )
  AND NOT EXISTS (
    SELECT 1
    FROM "{META_SCHEMA}"."LOAD_SLICES" S
    WHERE S."TABLE_NAME" = L."TABLE_NAME"
      AND S."SLICE_KEY" = L."SLICE_KEY"
  )
