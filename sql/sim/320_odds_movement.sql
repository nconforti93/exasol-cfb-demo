-- Odds from shown runs, compared with the preceding shown run of the season.
CREATE OR REPLACE FORCE VIEW {SIM_SCHEMA}.ODDS_MOVEMENT (
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
SELECT p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", p."AS_OF_LABEL", p."RUN_ID",
       o."TEAM_ID", o."SCHOOL", o."CONFERENCE", o."TIER", o."ODDS_RANK",
       o."P_IN_CFP", o."P_BYE", o."P_QUARTERFINAL", o."P_SEMIFINAL", o."P_FINAL",
       o."P_CHAMPION", o."P_CONF_CHAMPION", o."EXPECTED_WINS", o."MEAN_SEED",
       p."PREV_AS_OF_LABEL", prev."P_IN_CFP", o."P_IN_CFP" - prev."P_IN_CFP",
       o."P_CHAMPION" - prev."P_CHAMPION", prev."ODDS_RANK" - o."ODDS_RANK",
       CASE WHEN prev."RUN_ID" IS NULL THEN 'NEW'
            WHEN o."P_IN_CFP" - prev."P_IN_CFP" >= m."PARAM_VALUE" THEN 'UP'
            WHEN prev."P_IN_CFP" - o."P_IN_CFP" >= m."PARAM_VALUE" THEN 'DOWN'
            ELSE 'SAME' END
  FROM {SIM_SCHEMA}.POINT_RUN p
  JOIN {SIM_SCHEMA}.SIM_TEAM_ODDS o ON o."RUN_ID" = p."RUN_ID"
  LEFT JOIN {SIM_SCHEMA}.SIM_TEAM_ODDS prev
    ON prev."RUN_ID" = p."PREV_RUN_ID" AND prev."TEAM_ID" = o."TEAM_ID"
  JOIN {SIM_SCHEMA}.SIM_PARAMS m ON m."PARAM_KEY" = 'MOVE_THRESHOLD'
COMMENT IS 'One row per shown run and eligible team, with change against the preceding shown run in the same season. NEW has no previous team odds.';
