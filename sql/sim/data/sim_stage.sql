-- Data table: the engine's tagged output, split into the tables below by SQL and emptied at the end of
-- a run (sim-engine/engine-contract). SIM_ENGINE EMITS exactly these columns, in this order. Numeric
-- columns that do not apply to a row kind carry 0 or FALSE; only OUTCOMES and INFO carry NULL.
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.SIM_STAGE (
  RUN_ID        DECIMAL(18,0) NOT NULL COMMENT IS 'The run that produced the row',
  BATCH_ID      DECIMAL(9,0) NOT NULL COMMENT IS 'Engine call that produced the row, 0 upwards',
  ROW_KIND      CHAR(1) ASCII NOT NULL COMMENT IS 'T playoff team, P outcome string, W win total, C conference champion, G home wins of a game, L leverage count, B batch info',
  SIM_ID        DECIMAL(18,0) COMMENT IS 'T and P rows: simulated season, batch id times sims per batch plus its position in the batch',
  TEAM_ID       DECIMAL(18,0) COMMENT IS 'T, W, C and L rows: CFBD team id',
  GAME_IDX      DECIMAL(5,0) COMMENT IS 'G and L rows: GAME_IDX of the remaining game',
  SEED          DECIMAL(2,0) COMMENT IS 'T rows: playoff seed 1 to 12',
  BYE           BOOLEAN COMMENT IS 'T rows: TRUE for seeds 1 to 4',
  REACHED_ROUND DECIMAL(1,0) COMMENT IS 'T rows: furthest round reached, 1 first round, 2 quarterfinal, 3 semifinal, 4 final',
  CHAMPION      BOOLEAN COMMENT IS 'T rows: TRUE for the national champion',
  WINS          DECIMAL(2,0) COMMENT IS 'W rows: regular-season win total',
  N             DECIMAL(18,0) COMMENT IS 'W, C, G and L rows: number of simulated seasons counted',
  OUTCOMES      VARCHAR(2000) ASCII COMMENT IS 'P rows: one character per remaining game in GAME_IDX order, 1 home win, 0 away win',
  INFO          VARCHAR(2000) UTF8 COMMENT IS 'B rows: vm, node, model digest, batch checksum, numpy and Python versions, engine milliseconds'
) COMMENT IS 'One row per engine output row of the run that is being stored: the staging area between SIM_ENGINE and the output tables, empty between runs.';
