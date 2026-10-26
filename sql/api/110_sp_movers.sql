CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.SP_MOVERS (
  SEASON COMMENT IS 'Season of the SP+ rating',
  TEAM_ID COMMENT IS 'CFBD team id',
  SCHOOL COMMENT IS 'School in the season',
  CONFERENCE_NAME COMMENT IS 'Conference in the season',
  SP_RATING COMMENT IS 'SP+ rating as last fetched',
  SP_PRIOR_RATING COMMENT IS 'Prior season SP+ rating as last fetched',
  SP_DELTA COMMENT IS 'SP_RATING minus SP_PRIOR_RATING',
  RISE_RANK COMMENT IS 'Position by SP+ improvement descending, team id tie break',
  FALL_RANK COMMENT IS 'Position by SP+ change ascending, team id tie break'
) AS
SELECT s."SEASON", s."TEAM_ID", s."SCHOOL", s."CONFERENCE_NAME",
       s."SP_RATING", s."SP_PRIOR_RATING",
       s."SP_RATING" - s."SP_PRIOR_RATING" AS "SP_DELTA",
       ROW_NUMBER() OVER (
         PARTITION BY s."SEASON" ORDER BY s."SP_RATING" - s."SP_PRIOR_RATING" DESC,
         s."TEAM_ID") AS "RISE_RANK",
       ROW_NUMBER() OVER (
         PARTITION BY s."SEASON" ORDER BY s."SP_RATING" - s."SP_PRIOR_RATING",
         s."TEAM_ID") AS "FALL_RANK"
  FROM {MODEL_SCHEMA}."TEAM_SEASON" s
 WHERE s."CLASSIFICATION" = 'fbs' AND s."SP_RATING" IS NOT NULL
   AND s."SP_PRIOR_RATING" IS NOT NULL
COMMENT IS 'One row per FBS team-season with current and prior SP+ ratings. Ratings of the current season change with each refresh.';
