CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.CONFERENCE_STRENGTH (
  AS_OF_SEASON COMMENT IS 'Season of the as-of point',
  AS_OF_WEEK_SEQ COMMENT IS 'Week sequence of the as-of point',
  CONFERENCE_NAME COMMENT IS 'Conference of the FBS team-seasons',
  CONFERENCE_TIER COMMENT IS 'Tier of the conference in this season',
  TEAM_COUNT COMMENT IS 'Number of FBS members',
  MEAN_SP_RATING COMMENT IS 'Mean SP+ of members with a rating',
  MEAN_SP_PRIOR_RATING COMMENT IS 'Mean prior SP+ of members with a prior rating',
  NONCONF_GAMES COMMENT IS 'Completed nonconference games against FBS opponents through the point, counted from each member side',
  NONCONF_WINS COMMENT IS 'Wins among those nonconference games',
  NONCONF_LOSSES COMMENT IS 'Losses among those nonconference games',
  AP_RANKED_TEAMS COMMENT IS 'Members in the latest AP poll visible at the point',
  STRENGTH_RANK COMMENT IS 'Rank by mean SP+ descending at this point; NULL when SP+ is unknown'
) AS
WITH latest_ap AS (
  SELECT p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", r."TEAM_ID",
         ROW_NUMBER() OVER (
           PARTITION BY p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", r."TEAM_ID"
           ORDER BY r."VISIBLE_AT_WEEK_SEQ" DESC, r."WEEK_SEQ" DESC) AS "RN"
    FROM {MODEL_SCHEMA}."AS_OF_POINT" p
    JOIN {MODEL_SCHEMA}."RANKING" r
      ON r."SEASON" = p."AS_OF_SEASON" AND r."POLL_CODE" = 'AP'
     AND r."VISIBLE_AT_WEEK_SEQ" <= p."AS_OF_WEEK_SEQ"
), members AS (
  SELECT p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", s."CONFERENCE_NAME",
         s."CONFERENCE_TIER", s."TEAM_ID", s."SP_RATING", s."SP_PRIOR_RATING",
         CASE WHEN a."TEAM_ID" IS NOT NULL THEN 1 ELSE 0 END AS "IS_AP_RANKED"
    FROM {MODEL_SCHEMA}."AS_OF_POINT" p
    JOIN {MODEL_SCHEMA}."TEAM_SEASON" s
      ON s."SEASON" = p."AS_OF_SEASON" AND s."CLASSIFICATION" = 'fbs'
    LEFT JOIN latest_ap a
      ON a."AS_OF_SEASON" = p."AS_OF_SEASON"
     AND a."AS_OF_WEEK_SEQ" = p."AS_OF_WEEK_SEQ"
     AND a."TEAM_ID" = s."TEAM_ID" AND a."RN" = 1
), games AS (
  SELECT p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", tg."TEAM_ID",
         COUNT(*) AS "GAMES",
         SUM(CASE WHEN tg."GAME_RESULT" = 'W' THEN 1 ELSE 0 END) AS "WINS",
         SUM(CASE WHEN tg."GAME_RESULT" = 'L' THEN 1 ELSE 0 END) AS "LOSSES"
    FROM {MODEL_SCHEMA}."AS_OF_POINT" p
    JOIN {MODEL_SCHEMA}."TEAM_GAME" tg
      ON tg."SEASON" = p."AS_OF_SEASON" AND tg."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ"
     AND tg."HAS_RESULT" AND tg."OPPONENT_CLASSIFICATION" = 'fbs'
     AND NOT tg."IS_CONFERENCE_GAME"
   GROUP BY p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", tg."TEAM_ID"
), totals AS (
  SELECT m."AS_OF_SEASON", m."AS_OF_WEEK_SEQ", m."CONFERENCE_NAME",
         MAX(m."CONFERENCE_TIER") AS "CONFERENCE_TIER", COUNT(*) AS "TEAM_COUNT",
         AVG(m."SP_RATING") AS "MEAN_SP_RATING",
         AVG(m."SP_PRIOR_RATING") AS "MEAN_SP_PRIOR_RATING",
         SUM(COALESCE(g."GAMES", 0)) AS "NONCONF_GAMES",
         SUM(COALESCE(g."WINS", 0)) AS "NONCONF_WINS",
         SUM(COALESCE(g."LOSSES", 0)) AS "NONCONF_LOSSES",
         SUM(m."IS_AP_RANKED") AS "AP_RANKED_TEAMS"
    FROM members m
    LEFT JOIN games g
      ON g."AS_OF_SEASON" = m."AS_OF_SEASON"
     AND g."AS_OF_WEEK_SEQ" = m."AS_OF_WEEK_SEQ" AND g."TEAM_ID" = m."TEAM_ID"
   GROUP BY m."AS_OF_SEASON", m."AS_OF_WEEK_SEQ", m."CONFERENCE_NAME"
)
SELECT x.*, CASE WHEN x."MEAN_SP_RATING" IS NOT NULL THEN
         RANK() OVER (PARTITION BY x."AS_OF_SEASON", x."AS_OF_WEEK_SEQ"
                      ORDER BY x."MEAN_SP_RATING" DESC NULLS LAST) END AS "STRENGTH_RANK"
  FROM totals x
COMMENT IS 'One row per point and conference with FBS members in that season. Games count through the point; SP+ reflects its last stored fetch.';
