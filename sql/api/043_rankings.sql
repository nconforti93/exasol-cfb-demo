CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.RANKINGS (
  SEASON COMMENT IS 'Season of the poll',
  SEASON_TYPE COMMENT IS 'Season type as CFBD labels the poll: regular or postseason',
  WEEK COMMENT IS 'Week as CFBD labels the poll within the season type. The label can run ahead of the games it reflects: an AP poll labeled week 10 is released after the games of week 9',
  WEEK_SEQ COMMENT IS 'Week sequence of the label: regular week n = n, postseason week n = 100 + n, so regular week 1 and postseason week 1 stay separate; NULL for another season type',
  POLL_NAME COMMENT IS 'CFBD poll name as it appears in the rankings data',
  POLL_CODE COMMENT IS 'Code from the POLL seed: AP, COACHES, CFP, FCS_COACHES, D2_COACHES or D3_COACHES; OTHER for a poll name that is not in the seed (MODEL_CHECKS: POLL_UNKNOWN)',
  POLL_RANK COMMENT IS 'Position in the poll (1 = best); tied teams share a position as CFBD reports it',
  TEAM_ID COMMENT IS 'CFBD team id of the ranked team (TEAM.TEAM_ID)',
  SCHOOL COMMENT IS 'School name as the poll reports it',
  CONFERENCE COMMENT IS 'Conference of the team as the poll reports it (CFBD name)',
  FIRST_PLACE_VOTES COMMENT IS 'First-place votes as CFBD reports them; NULL for polls without votes, such as the committee',
  POINTS COMMENT IS 'Poll points as CFBD reports them',
  VISIBLE_AT_WEEK_SEQ COMMENT IS 'Week sequence from which the ranking is known by the label rule: the label''s WEEK_SEQ minus the poll''s label lead for regular labels (0 = before the first game), the label''s own WEEK_SEQ for postseason labels, and the last regular week of the season for a poll whose final ranking comes before the postseason (the committee''s selection ranking). The as-of views also show a ranking fetched before the point''s cutoff; historic seasons use the label rule alone. NULL when WEEK_SEQ is NULL',
  IS_FINAL_POLL COMMENT IS 'Derived, as the stored ranks do not carry CFBD''s isFinal: TRUE for postseason-labeled polls released after the postseason, and for the committee''s last stored ranking of a season whose regular-season weeks are all done (SEASON_WEEK.IS_DONE); FALSE for a season in progress',
  FETCHED_AT COMMENT IS 'When the row shown was fetched from CFBD (REQ_AT, UTC); the as-of views compare it with the cutoff'
) AS
SELECT m."SEASON",
       m."SEASON_TYPE",
       m."WEEK",
       m."WEEK_SEQ",
       m."POLL_NAME",
       m."POLL_CODE",
       m."POLL_RANK",
       m."TEAM_ID",
       m."SCHOOL",
       m."CONFERENCE",
       m."FIRST_PLACE_VOTES",
       m."POINTS",
       m."VISIBLE_AT_WEEK_SEQ",
       m."IS_FINAL_POLL",
       m."FETCHED_AT"
  FROM {MODEL_SCHEMA}."RANKING" m
COMMENT IS 'One row per poll, season, season type, week and ranked team of CFB_CORE. Stored history as it is now; filter by season and week for a point.';
