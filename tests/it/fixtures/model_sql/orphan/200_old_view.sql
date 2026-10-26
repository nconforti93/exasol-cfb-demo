CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.OLD_VIEW (
  K COMMENT IS 'Key of the row'
) AS
SELECT "K" FROM {MODEL_SCHEMA}."KEPT"
COMMENT IS 'One row per key of the kept table; the view that a later model no longer defines.';
