-- Data table: win-total distribution per team, tallied in the engine (sim-engine/engine-contract).
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.TEAM_WINS (
  RUN_ID  DECIMAL(18,0) NOT NULL COMMENT IS 'The run that simulated the seasons',
  TEAM_ID DECIMAL(18,0) NOT NULL COMMENT IS 'CFBD team id',
  WINS    DECIMAL(2,0) NOT NULL COMMENT IS 'Regular-season win total, championship games excluded',
  N       DECIMAL(18,0) NOT NULL COMMENT IS 'Simulated seasons in which the team ended with that many wins'
) COMMENT IS 'One row per run, team and regular-season win total that occurred: the win distribution of every team.';
