CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.KEYED_VIEW (
  K COMMENT IS 'Key of the row',
  DOUBLE_K COMMENT IS 'Twice the key'
) AS
SELECT "K", {MODEL_SCHEMA}.DOUBLE_IT("K") FROM {MODEL_SCHEMA}."KEYED"
COMMENT IS 'One row per key of the table, with its doubled value.';
