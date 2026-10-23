-- Data table: the input snapshot of a run, games (sim-engine/sim-runs). REMAINING rows: PATH_POS = GAME_IDX + 1,
-- MU to HOME_WIN_PROB from the score model, N_HOME_WINS after the engine; PLAYED_CONF rows: MARGIN.
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.SIM_RUN_GAME (
  RUN_ID        DECIMAL(18,0) NOT NULL COMMENT IS 'The run this snapshot row belongs to',
  GAME_ROLE     VARCHAR(12) UTF8 NOT NULL COMMENT IS 'REMAINING (simulated) or PLAYED_CONF (a completed conference game kept for head-to-head)',
  GAME_IDX      DECIMAL(5,0) NOT NULL COMMENT IS 'Position of the game among the rows of its role, 0 upwards, by kickoff then game id',
  PATH_POS      DECIMAL(5,0) COMMENT IS 'REMAINING only: 1-based position of the game in the outcome string of SIM_PATH, GAME_IDX + 1',
  GAME_ID       DECIMAL(18,0) NOT NULL COMMENT IS 'CFBD game id',
  WEEK_SEQ      DECIMAL(3,0) COMMENT IS 'Sequence number of the game week within the season',
  START_AT      TIMESTAMP COMMENT IS 'Kickoff, UTC',
  IS_NEXT_WEEK  BOOLEAN COMMENT IS 'TRUE for a game of the week right after the as-of point',
  HOME_TEAM_ID  DECIMAL(18,0) NOT NULL COMMENT IS 'Team id of the home team',
  AWAY_TEAM_ID  DECIMAL(18,0) NOT NULL COMMENT IS 'Team id of the away team',
  HOME_IDX      DECIMAL(5,0) NOT NULL COMMENT IS 'TEAM_IDX of the home team in SIM_RUN_TEAM',
  AWAY_IDX      DECIMAL(5,0) NOT NULL COMMENT IS 'TEAM_IDX of the away team in SIM_RUN_TEAM',
  IS_NEUTRAL_SITE BOOLEAN COMMENT IS 'TRUE when the game is played at a neutral site',
  CONF_GAME     BOOLEAN COMMENT IS 'TRUE for a conference game between two teams of one conference',
  MU            DECIMAL(7,2) COMMENT IS 'REMAINING only: expected home margin in points from the score model',
  SIGMA         DECIMAL(5,2) COMMENT IS 'REMAINING only: standard deviation of the home margin in points',
  TEAM_SIGMA    DECIMAL(5,2) COMMENT IS 'REMAINING only: scale of the shared team-strength offsets in points',
  HOME_WIN_PROB DECIMAL(5,4) COMMENT IS 'REMAINING only: the score model probability that the home team wins',
  MARGIN        DECIMAL(4,0) COMMENT IS 'PLAYED_CONF only: final home minus away margin',
  N_HOME_WINS   DECIMAL(18,0) COMMENT IS 'REMAINING only: simulated seasons in which the home team won, set when the engine has run'
) COMMENT IS 'One row per run and game of the input snapshot: the remaining games with their score model numbers and the completed conference games kept for head-to-head.';
