-- Data table: layout A of decision [47], one outcome string per simulated season.
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.SIM_PATH (
  RUN_ID   DECIMAL(18,0) NOT NULL COMMENT IS 'The run that simulated the season',
  SIM_ID   DECIMAL(18,0) NOT NULL COMMENT IS 'Simulated season within the run',
  OUTCOMES VARCHAR(2000) ASCII COMMENT IS 'One character per remaining game at PATH_POS of SIM_RUN_GAME: 1 home win, 0 away win; NULL when no game remains'
) COMMENT IS 'One row per run and simulated season: the results of every remaining game as one string, which a what-if filters with SUBSTR.';
