-- Data table: conference titles per team, tallied in the engine (sim-engine/engine-contract).
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.CONF_CHAMP (
  RUN_ID  DECIMAL(18,0) NOT NULL COMMENT IS 'The run that simulated the seasons',
  TEAM_ID DECIMAL(18,0) NOT NULL COMMENT IS 'CFBD team id',
  N       DECIMAL(18,0) NOT NULL COMMENT IS 'Simulated seasons in which the team won its conference'
) COMMENT IS 'One row per run and team that won its conference at least once: the number of simulated seasons with a conference title.';
