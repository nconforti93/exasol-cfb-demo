-- Data table: the engine parameters of a run (sim-engine/engine-contract), the engine's lower-case keys.
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.SIM_RUN_PARAM (
  RUN_ID      DECIMAL(18,0) NOT NULL COMMENT IS 'The run this parameter belongs to',
  PARAM_KEY   VARCHAR(40) ASCII NOT NULL COMMENT IS 'Engine parameter name, lower case, for example hfa or game_sigma',
  PARAM_VALUE DECIMAL(12,4) NOT NULL COMMENT IS 'Value the engine received'
) COMMENT IS 'One row per run and engine parameter: the constants of the score model, the resume weights and the selection rules as the engine received them.';
