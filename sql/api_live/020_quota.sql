CREATE OR REPLACE FORCE VIEW {API_LIVE_SCHEMA}.QUOTA (
  TIER_NAME COMMENT IS 'CFBD key tier reported by /info',
  MONTHLY_LIMIT COMMENT IS 'Monthly call limit of the key',
  USED_CALLS COMMENT IS 'Calls spent by the key this period',
  REMAINING_CALLS COMMENT IS 'Calls left on the key this period',
  RESET_AT COMMENT IS 'UTC time when the monthly quota resets',
  PERIOD_START COMMENT IS 'UTC start of the current quota period',
  MONTHLY_CALL_BUDGET COMMENT IS 'Project limit configured for this target',
  REFRESH_RESERVE COMMENT IS 'Calls reserved for weekly refreshes',
  PROJECT_BUDGET COMMENT IS 'Lesser of the CFBD limit and project limit',
  REFRESH_SPENDABLE COMMENT IS 'Calls that a refresh may spend now',
  BACKFILL_SPENDABLE COMMENT IS 'Calls a backfill may spend after the refresh reserve',
  BACKFILL_CALLS COMMENT IS 'Logged live backfill HTTP attempts this period',
  REFRESH_CALLS COMMENT IS 'Logged live refresh HTTP attempts this period',
  OTHER_CALLS COMMENT IS 'Used calls not attributed to logged backfill or refresh',
  DEMO_CALLS_TO_COMPLETE COMMENT IS 'Pending demo slices estimated as calls',
  FULL_CALLS_TO_COMPLETE COMMENT IS 'Pending demo and full slices estimated as calls'
) AS
SELECT q."TIER_NAME", q."MONTHLY_LIMIT", q."USED_CALLS", q."REMAINING_CALLS",
       q."RESET_AT", q."PERIOD_START", q."MONTHLY_CALL_BUDGET", q."REFRESH_RESERVE",
       q."PROJECT_BUDGET", q."REFRESH_SPENDABLE", q."BACKFILL_SPENDABLE",
       q."BACKFILL_CALLS", q."REFRESH_CALLS", q."OTHER_CALLS",
       q."DEMO_CALLS_TO_COMPLETE", q."FULL_CALLS_TO_COMPLETE"
  FROM {META_SCHEMA}."QUOTA" q
COMMENT IS 'One row per key quota read from CFBD /info through CFB_META.QUOTA. This network read costs zero CFBD calls.';
