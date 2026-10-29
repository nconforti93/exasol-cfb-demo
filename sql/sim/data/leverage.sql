-- Data table: the rooting guide's counts, summed from the engine's tallies (decision [48]).
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.LEVERAGE (
  RUN_ID        DECIMAL(18,0) NOT NULL COMMENT IS 'The run that simulated the seasons',
  TEAM_ID       DECIMAL(18,0) NOT NULL COMMENT IS 'CFBD team id of the team whose playoff chances are counted',
  GAME_IDX      DECIMAL(5,0) NOT NULL COMMENT IS 'GAME_IDX of the remaining game in SIM_RUN_GAME',
  N_HOME        DECIMAL(18,0) NOT NULL COMMENT IS 'Simulated seasons in which the home team won the game',
  N_HOME_IN_CFP DECIMAL(18,0) NOT NULL COMMENT IS 'Of those seasons, the ones in which the team made the playoff field',
  N_AWAY        DECIMAL(18,0) NOT NULL COMMENT IS 'Simulated seasons in which the away team won the game',
  N_AWAY_IN_CFP DECIMAL(18,0) NOT NULL COMMENT IS 'Of those seasons, the ones in which the team made the playoff field'
) COMMENT IS 'One row per run, team that made the field at least once and remaining game: how often the team made the field when the home team won and when the away team won.';
