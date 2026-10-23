-- Passes the lint and the FORCE create; only the compile pass finds that the column does not exist.
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.THIRD_VIEW (
  ID COMMENT IS 'Game id',
  NO_SUCH COMMENT IS 'A column that CFB_CORE.GAMES does not have'
) AS
SELECT "ID", "NO_SUCH_COLUMN" FROM {CORE_SCHEMA}."GAMES"
COMMENT IS 'One row per stored game row, with a column that does not exist.';
