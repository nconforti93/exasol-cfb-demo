CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.PLAYOFF_ODDS (
  AS_OF_SEASON COMMENT IS 'Season of the shown run',
  AS_OF_WEEK_SEQ COMMENT IS 'Week sequence of the shown run',
  AS_OF_LABEL COMMENT IS 'Display label of the shown run',
  RUN_ID COMMENT IS 'Shown DONE run id',
  TEAM_ID COMMENT IS 'CFBD team id',
  SCHOOL COMMENT IS 'School name',
  CONFERENCE COMMENT IS 'Conference name; NULL for an independent',
  TIER COMMENT IS 'P4, G6 or IND',
  ODDS_RANK COMMENT IS 'Rank by playoff odds in this run, 1 = best',
  P_IN_CFP COMMENT IS 'Probability of making the playoff',
  P_BYE COMMENT IS 'Probability of a first-round bye',
  P_QUARTERFINAL COMMENT IS 'Probability of reaching the quarterfinals',
  P_SEMIFINAL COMMENT IS 'Probability of reaching the semifinals',
  P_FINAL COMMENT IS 'Probability of reaching the final',
  P_CHAMPION COMMENT IS 'Probability of winning the championship',
  P_CONF_CHAMPION COMMENT IS 'Probability of winning the conference; NULL where no title is crowned',
  EXPECTED_WINS COMMENT IS 'Mean regular-season wins',
  MEAN_SEED COMMENT IS 'Mean seed among playoff seasons',
  PREV_AS_OF_LABEL COMMENT IS 'Display label of the preceding point with a run',
  PREV_P_IN_CFP COMMENT IS 'Previous shown probability of making the playoff',
  DELTA_P_IN_CFP COMMENT IS 'Current minus previous playoff probability',
  DELTA_P_CHAMPION COMMENT IS 'Current minus previous title probability',
  RANK_CHANGE COMMENT IS 'Previous rank minus current rank; positive means the team rose',
  MOVE COMMENT IS 'NEW without prior team odds, UP or DOWN at the configured threshold, else SAME'
) AS
SELECT s."AS_OF_SEASON", s."AS_OF_WEEK_SEQ", s."AS_OF_LABEL", s."RUN_ID", s."TEAM_ID",
       s."SCHOOL", s."CONFERENCE", s."TIER", s."ODDS_RANK", s."P_IN_CFP",
       s."P_BYE", s."P_QUARTERFINAL", s."P_SEMIFINAL", s."P_FINAL", s."P_CHAMPION",
       s."P_CONF_CHAMPION", s."EXPECTED_WINS", s."MEAN_SEED", s."PREV_AS_OF_LABEL", s."PREV_P_IN_CFP",
       s."DELTA_P_IN_CFP", s."DELTA_P_CHAMPION", s."RANK_CHANGE", s."MOVE"
  FROM {SIM_SCHEMA}."ODDS_MOVEMENT" s
COMMENT IS 'One row per shown simulation run and eligible team: playoff odds and movement from the prior shown run.';
