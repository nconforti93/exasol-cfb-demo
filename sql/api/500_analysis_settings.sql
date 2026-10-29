CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.ANALYSIS_SETTINGS (
  SETTING_NAME COMMENT IS 'Name of a serving setting',
  SETTING_NUMBER COMMENT IS 'Numeric value used by the app for this setting',
  SETTING_NOTE COMMENT IS 'Origin and meaning of the setting'
) AS
SELECT p."PARAM_KEY", p."PARAM_VALUE", p."PARAM_NOTE"
  FROM {SIM_SCHEMA}."SIM_PARAMS" p
 WHERE p."PARAM_KEY" IN ('MOVE_THRESHOLD', 'MIN_BRANCH_SAMPLE')
UNION ALL
SELECT 'MIN_EFFECTIVE_SAMPLE', CAST(1000 AS DECIMAL(12,4)),
       'A what-if below this matching-season count is marked low sample'
UNION ALL
SELECT 'MAX_CONDITIONS', CAST(16 AS DECIMAL(12,4)),
       'Maximum distinct game outcomes in one what-if'
UNION ALL
SELECT 'ALL_TIME_FROM_SEASON', CAST(1978 AS DECIMAL(12,4)),
       'First season of the line-or-Elo all-time upset ranking'
COMMENT IS 'One row per app analysis setting: two engine parameters and three serving contract constants.';
