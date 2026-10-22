-- BACKFILL_QUEUE: the slices a backfill may fetch now, in fetch order; one row per slice of
-- LOAD_SLICES that IS_PENDING (not a gap, not quarantined, missing or finalizable), without the
-- tables that have orphans (LOAD_ORPHANS). All profiles are listed: a run takes PROFILE_RANK <= its
-- profile's rank (demo 1, full 2, so full includes demo) and, with from_season, SEASON IS NULL OR
-- SEASON <= from_season. Filtering keeps the relative order.
-- QUEUE_ORDER: slices that failed before last (fetched alone, BATCH_ALONE), then profile rank,
-- priority, newest season first with seasonless slices (SEASON NULL) first, table, entry, key.
-- A batch is the longest run of consecutive rows of one ENTRY_ID, so one WHERE clause expresses it:
-- FIXED_FILTER AND ("P_YEAR" IN (<SEASON>, ...) for `season`; "P_MIN_YEAR" = SEASON_FROM AND
-- "P_MAX_YEAR" = SEASON_TO for `season_range`; nothing more for `none`).
-- LAST_FAILED_RUN_ID lets a run skip slices that already failed in the same run (one failure per run).
-- Rendered by vs/host/metaddl.py (render_loader_view).

CREATE OR REPLACE VIEW "{META_SCHEMA}"."BACKFILL_QUEUE" AS
SELECT
  ROW_NUMBER() OVER (
    ORDER BY
      CASE WHEN S."FAILURES" > 0 THEN 1 ELSE 0 END,
      S."PROFILE_RANK",
      S."PRIORITY",
      S."SEASON" DESC NULLS FIRST,
      S."TABLE_NAME",
      S."ENTRY_ID",
      S."SLICE_KEY"
  ) AS "QUEUE_ORDER",
  S."ENTRY_ID" AS "ENTRY_ID",
  S."TABLE_NAME" AS "TABLE_NAME",
  S."PROFILE_NAME" AS "PROFILE_NAME",
  S."PROFILE_RANK" AS "PROFILE_RANK",
  S."PRIORITY" AS "PRIORITY",
  S."SLICING" AS "SLICING",
  S."REFRESH_CLASS" AS "REFRESH_CLASS",
  S."SEASON" AS "SEASON",
  S."SLICE_KEY" AS "SLICE_KEY",
  P."SEASON_FROM" AS "SEASON_FROM",
  P."SEASON_TO" AS "SEASON_TO",
  P."FIXED_FILTER" AS "FIXED_FILTER",
  S."FINAL_IF_LOADED_NOW" AS "FINAL_IF_LOADED_NOW",
  S."IS_STORED" AS "IS_STORED",
  S."FAILURES" AS "FAILURES",
  S."LAST_FAILED_RUN_ID" AS "LAST_FAILED_RUN_ID",
  CASE WHEN S."FAILURES" > 0 THEN TRUE ELSE FALSE END AS "BATCH_ALONE"
FROM "{META_SCHEMA}"."LOAD_SLICES" S
INNER JOIN "{META_SCHEMA}"."LOAD_PLAN" P
  ON P."ENTRY_ID" = S."ENTRY_ID"
WHERE S."IS_PENDING"
  AND NOT EXISTS (
    SELECT 1
    FROM "{META_SCHEMA}"."LOAD_ORPHANS" O
    WHERE O."TABLE_NAME" = S."TABLE_NAME"
  )
