-- Data table: the playoff field of every simulated season, sparse (sim-engine/sim-runs).
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.SIM_TEAM (
  RUN_ID        DECIMAL(18,0) NOT NULL COMMENT IS 'The run that simulated the season',
  SIM_ID        DECIMAL(18,0) NOT NULL COMMENT IS 'Simulated season within the run',
  TEAM_ID       DECIMAL(18,0) NOT NULL COMMENT IS 'CFBD team id of a team in the playoff field',
  SEED          DECIMAL(2,0) NOT NULL COMMENT IS 'Playoff seed 1 to 12',
  BYE           BOOLEAN NOT NULL COMMENT IS 'TRUE for seeds 1 to 4, which skip the first round',
  REACHED_ROUND DECIMAL(1,0) NOT NULL COMMENT IS 'Furthest round reached: 1 first round, 2 quarterfinal, 3 semifinal, 4 final',
  CHAMPION      BOOLEAN NOT NULL COMMENT IS 'TRUE when the team won the national championship'
) COMMENT IS 'One row per run, simulated season and playoff team: twelve rows per season; teams that miss the field have no row.';
