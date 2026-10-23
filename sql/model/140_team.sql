-- Team dimension (model/teams-conferences): one row per team id, from the stored team list and from the
-- games. Both sources are joined with a FULL OUTER JOIN on the team id: a team in the list keeps its
-- listed name, a team seen only in games (historic programs) takes the name of its most recent game.
-- The team list is deduplicated by team id, newest fetch first (REQ_AT, then the later slice key, then
-- the later row). Games come from GAME, which is already deduplicated; FIRST_SEASON and LAST_SEASON are
-- window values over every game side of the team, taken on the row of its most recent game
-- (season, kickoff, game id; a missing kickoff counts as the oldest).
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.TEAM (
  TEAM_ID                COMMENT IS 'CFBD team id',
  SCHOOL                 COMMENT IS 'CFBD school name: the name in the stored team list, else the name on the team''s most recent stored game. SP+ ratings and games identify teams by this name',
  MASCOT                 COMMENT IS 'Mascot as the team list reports it; NULL for a team that is not in the list',
  ABBREVIATION           COMMENT IS 'Abbreviation as the team list reports it; NULL for a team that is not in the list',
  ALTERNATE_NAMES        COMMENT IS 'Alternate names as the team list reports them (a JSON array as text); NULL for a team that is not in the list',
  CURRENT_CONFERENCE     COMMENT IS 'Conference as of the last load of the team list, CFBD name. Not a historical value: use TEAM_SEASON for the conference of any past season. NULL for a team that is not in the list',
  CURRENT_DIVISION       COMMENT IS 'Division as of the last load of the team list; NULL when none or when the team is not in the list. Use TEAM_SEASON for past seasons',
  CURRENT_CLASSIFICATION COMMENT IS 'Classification (fbs, fcs, ii or iii) as of the last load of the team list; NULL for a team that is not in the list. Use TEAM_SEASON for the classification of a past season',
  HOME_VENUE_ID          COMMENT IS 'CFBD id of the team''s current home venue (VENUE.VENUE_ID); NULL for a team that is not in the list',
  IS_IN_TEAM_LIST        COMMENT IS 'TRUE when the team is in the stored team list (CFB_CORE.TEAMS); FALSE for a team that appears only in stored games',
  FIRST_SEASON           COMMENT IS 'First season with a stored game of the team; NULL when no stored game has it',
  LAST_SEASON            COMMENT IS 'Last season with a stored game of the team; NULL when no stored game has it',
  LISTED_AT              COMMENT IS 'When the team list row shown was fetched (REQ_AT, UTC); NULL for a team that is not in the list'
) AS
WITH listed AS (
  SELECT t."ID", t."SCHOOL", t."MASCOT", t."ABBREVIATION", t."ALTERNATE_NAMES", t."CONFERENCE",
         t."DIVISION", t."CLASSIFICATION", t."LOCATION_ID", t."REQ_AT"
    FROM {CORE_SCHEMA}."TEAMS" t
 QUALIFY ROW_NUMBER() OVER (
           PARTITION BY t."ID"
           ORDER BY t."REQ_AT" DESC, t."REQ_SLICE" DESC, t."ROW_NO" DESC) = 1
),
sides AS (
  SELECT g."SEASON", g."START_AT", g."GAME_ID", g."HOME_TEAM_ID" AS "TEAM_ID", g."HOME_TEAM" AS "SCHOOL"
    FROM {MODEL_SCHEMA}."GAME" g
   WHERE g."HOME_TEAM_ID" IS NOT NULL
  UNION ALL
  SELECT g."SEASON", g."START_AT", g."GAME_ID", g."AWAY_TEAM_ID", g."AWAY_TEAM"
    FROM {MODEL_SCHEMA}."GAME" g
   WHERE g."AWAY_TEAM_ID" IS NOT NULL
),
seen AS (
  SELECT s."TEAM_ID", s."SCHOOL",
         MIN(s."SEASON") OVER (PARTITION BY s."TEAM_ID") AS "FIRST_SEASON",
         MAX(s."SEASON") OVER (PARTITION BY s."TEAM_ID") AS "LAST_SEASON"
    FROM sides s
 QUALIFY ROW_NUMBER() OVER (
           PARTITION BY s."TEAM_ID"
           ORDER BY s."SEASON" DESC, s."START_AT" DESC NULLS LAST, s."GAME_ID" DESC) = 1
)
SELECT COALESCE(l."ID", s."TEAM_ID"),
       COALESCE(l."SCHOOL", s."SCHOOL"),
       l."MASCOT",
       l."ABBREVIATION",
       l."ALTERNATE_NAMES",
       l."CONFERENCE",
       l."DIVISION",
       l."CLASSIFICATION",
       l."LOCATION_ID",
       l."ID" IS NOT NULL,
       CAST(s."FIRST_SEASON" AS DECIMAL(4,0)),
       CAST(s."LAST_SEASON" AS DECIMAL(4,0)),
       l."REQ_AT"
  FROM listed l
  FULL OUTER JOIN seen s
    ON l."ID" = s."TEAM_ID"
COMMENT IS 'One row per team: every team of the stored team list (CFB_CORE.TEAMS, newest fetch wins) plus every team that appears only in stored games, such as historic programs (IS_IN_TEAM_LIST FALSE, named by its most recent game). CURRENT_* values are as of the last load of the team list; for any past season use TEAM_SEASON.';
