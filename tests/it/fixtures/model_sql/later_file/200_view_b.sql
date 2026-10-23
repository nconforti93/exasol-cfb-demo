CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.VIEW_B (
  K COMMENT IS 'Key of the row',
  V COMMENT IS 'Value of the row'
) AS
SELECT "K", "V" FROM {MODEL_SCHEMA}."BASE" WHERE "V" IS NOT NULL
COMMENT IS 'One row per key that has a value, reading the table of a later file.';
