CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.ROOTING_GUIDE (
  RUN_ID COMMENT IS 'DONE run that simulated the game',
  TEAM_ID COMMENT IS 'Team whose playoff chances are being compared',
  GAME_ID COMMENT IS 'CFBD game id of the remaining game',
  SEASON COMMENT IS 'Season of the run',
  WEEK_SEQ COMMENT IS 'Week sequence of the remaining game',
  IS_NEXT_WEEK COMMENT IS 'Whether the game is in the week after the run point',
  HOME_TEAM_ID COMMENT IS 'Home team id',
  HOME_SCHOOL COMMENT IS 'Home school name',
  AWAY_TEAM_ID COMMENT IS 'Away team id',
  AWAY_SCHOOL COMMENT IS 'Away school name',
  ROOT_FOR_TEAM_ID COMMENT IS 'Team to root for based on greater conditional playoff probability',
  ROOT_FOR_SCHOOL COMMENT IS 'School to root for based on greater conditional playoff probability',
  P_IF_HOME_WIN COMMENT IS 'Playoff probability when the home team wins',
  P_IF_AWAY_WIN COMMENT IS 'Playoff probability when the away team wins',
  LEVERAGE COMMENT IS 'Absolute difference between the two conditional playoff probabilities',
  GUIDE_RANK COMMENT IS 'Game rank by leverage for this run and team',
  NEXT_WEEK_RANK COMMENT IS 'Next-week game rank by leverage; NULL for later games',
  LOW_SAMPLE COMMENT IS 'Either outcome branch has fewer than MIN_BRANCH_SAMPLE seasons'
) AS
SELECT s."RUN_ID", s."TEAM_ID", s."GAME_ID", s."SEASON", s."WEEK_SEQ",
       s."IS_NEXT_WEEK", s."HOME_TEAM_ID", s."HOME_SCHOOL", s."AWAY_TEAM_ID", s."AWAY_SCHOOL",
       s."ROOT_FOR_TEAM_ID", s."ROOT_FOR_SCHOOL", s."P_IF_HOME_WIN", s."P_IF_AWAY_WIN", s."LEVERAGE",
       s."GUIDE_RANK", s."NEXT_WEEK_RANK", s."LOW_SAMPLE"
  FROM {SIM_SCHEMA}."ROOTING_GUIDE" s
COMMENT IS 'One row per DONE run, team and remaining game: the precomputed playoff rooting guide.';
