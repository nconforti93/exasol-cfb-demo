CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.VIEW_A (
  K COMMENT IS 'Key of the row',
  V COMMENT IS 'Value of the row, upper case'
) AS
SELECT "K", UPPER("V") FROM {MODEL_SCHEMA}."VIEW_B"
COMMENT IS 'One row per key, reading the view of a later file.';
