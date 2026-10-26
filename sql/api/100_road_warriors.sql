CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.ROAD_WARRIORS (
  AS_OF_SEASON COMMENT IS 'Season of the as-of point',
  AS_OF_WEEK_SEQ COMMENT IS 'Week sequence of the as-of point',
  TEAM_ID COMMENT IS 'FBS team id',
  SCHOOL COMMENT IS 'School in the point season',
  CONFERENCE_NAME COMMENT IS 'Conference in the point season',
  TRIPS COMMENT IS 'Count of completed away or neutral games with known distance through the point',
  KM_TRAVELED COMMENT IS 'Sum of great-circle trip distances in kilometres',
  LONGEST_TRIP_KM COMMENT IS 'Great-circle distance of the longest counted trip',
  LONGEST_TRIP_GAME_ID COMMENT IS 'Game id of the longest trip; earlier kickoff wins a tie',
  LONGEST_TRIP_TO COMMENT IS 'City of the longest trip, or venue name when city is unknown',
  TRAVEL_RANK COMMENT IS 'Rank by total kilometres descending at this point'
) AS
WITH legs AS (
  SELECT p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", s."TEAM_ID", s."SCHOOL",
         s."CONFERENCE_NAME", t."GAME_ID", t."START_AT", t."KM_FROM_HOME",
         COALESCE(t."VENUE_CITY", t."VENUE_NAME") AS "DESTINATION",
         ROW_NUMBER() OVER (
           PARTITION BY p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", s."TEAM_ID"
           ORDER BY t."KM_FROM_HOME" DESC, t."START_AT", t."GAME_ID") AS "TRIP_POS"
    FROM {MODEL_SCHEMA}."AS_OF_POINT" p
    JOIN {MODEL_SCHEMA}."TEAM_SEASON" s
      ON s."SEASON" = p."AS_OF_SEASON" AND s."CLASSIFICATION" = 'fbs'
    JOIN {API_SCHEMA}."TEAM_TRAVEL" t
      ON t."SEASON" = p."AS_OF_SEASON" AND t."TEAM_ID" = s."TEAM_ID"
     AND t."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" AND t."HAS_RESULT"
     AND t."SITE" <> 'HOME' AND t."KM_FROM_HOME" IS NOT NULL
), totals AS (
  SELECT "AS_OF_SEASON", "AS_OF_WEEK_SEQ", "TEAM_ID", "SCHOOL", "CONFERENCE_NAME",
         COUNT(*) AS "TRIPS", SUM("KM_FROM_HOME") AS "KM_TRAVELED",
         MAX("KM_FROM_HOME") AS "LONGEST_TRIP_KM",
         MAX(CASE WHEN "TRIP_POS" = 1 THEN "GAME_ID" END) AS "LONGEST_TRIP_GAME_ID",
         MAX(CASE WHEN "TRIP_POS" = 1 THEN "DESTINATION" END) AS "LONGEST_TRIP_TO"
    FROM legs
   GROUP BY "AS_OF_SEASON", "AS_OF_WEEK_SEQ", "TEAM_ID", "SCHOOL", "CONFERENCE_NAME"
)
SELECT x.*, RANK() OVER (
         PARTITION BY x."AS_OF_SEASON", x."AS_OF_WEEK_SEQ"
         ORDER BY x."KM_TRAVELED" DESC) AS "TRAVEL_RANK"
  FROM totals x
COMMENT IS 'One row per point and FBS team with a completed trip of known distance in that season; distance uses today''s home venue.';
