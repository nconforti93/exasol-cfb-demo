-- Engine game inputs as of a point (sim-engine/engine-contract, § Engine inputs): the REMAINING games the
-- engine simulates, with the score model's MU and TEAM_SIGMA, and the PLAYED_CONF games it needs for
-- head-to-head tiebreaks. The snapshot assigns GAME_IDX by START_AT, GAME_ID. Both teams of every row are
-- on the roster ENGINE_TEAM_AS_OF. Filter with literal AS_OF_SEASON and AS_OF_WEEK_SEQ values.
CREATE OR REPLACE FORCE VIEW {SIM_SCHEMA}.ENGINE_GAME_AS_OF (
  AS_OF_SEASON    COMMENT IS 'Season of the as-of point; with AS_OF_WEEK_SEQ, GAME_ROLE and GAME_ID it identifies the row',
  AS_OF_WEEK_SEQ  COMMENT IS 'Week sequence of the point: 0 preseason, regular week n = n, postseason week n = 100 + n',
  GAME_ROLE       COMMENT IS 'REMAINING (simulated) or PLAYED_CONF (a completed conference game kept for head-to-head)',
  GAME_ID         COMMENT IS 'CFBD game id',
  WEEK_SEQ        COMMENT IS 'Week sequence of the game',
  START_AT        COMMENT IS 'Kickoff in UTC',
  IS_NEXT_WEEK    COMMENT IS 'TRUE for a game of the week right after the point (REMAINING rows only; FALSE for PLAYED_CONF)',
  HOME_TEAM_ID    COMMENT IS 'Team id of the home team',
  AWAY_TEAM_ID    COMMENT IS 'Team id of the away team',
  IS_NEUTRAL_SITE COMMENT IS 'TRUE when the game is played at a neutral site',
  CONF_GAME       COMMENT IS 'TRUE for a conference game between two teams of one engine conference',
  MU              COMMENT IS 'REMAINING only: expected margin, home minus away, in points (GAME_SCORE_AS_OF.MU); NULL for PLAYED_CONF',
  SIGMA           COMMENT IS 'REMAINING only: standard deviation of the margin in points; NULL for PLAYED_CONF',
  TEAM_SIGMA      COMMENT IS 'REMAINING only: scale in points of the shared team-strength offsets; NULL for PLAYED_CONF',
  HOME_WIN_PROB   COMMENT IS 'REMAINING only: probability that the home team wins; NULL for PLAYED_CONF',
  MARGIN          COMMENT IS 'PLAYED_CONF only: final margin, home minus away, in points; NULL for REMAINING'
) AS
WITH remaining AS (
  SELECT s."AS_OF_SEASON", s."AS_OF_WEEK_SEQ", 'REMAINING' AS "GAME_ROLE", s."GAME_ID", s."WEEK_SEQ", s."START_AT",
         CASE WHEN s."GAME_STATUS_AS_OF" = 'NEXT' THEN TRUE ELSE FALSE END AS "IS_NEXT_WEEK", s."HOME_TEAM_ID", s."AWAY_TEAM_ID", s."IS_NEUTRAL_SITE",
         CASE WHEN s."IS_CONFERENCE_GAME" AND s."HOME_CONFERENCE" IS NOT NULL
                   AND s."HOME_CONFERENCE" = s."AWAY_CONFERENCE" THEN TRUE ELSE FALSE END AS "CONF_GAME",
         s."MU", s."SIGMA", s."TEAM_SIGMA", s."HOME_WIN_PROB", CAST(NULL AS DECIMAL(4,0)) AS "MARGIN"
    FROM {SIM_SCHEMA}.GAME_SCORE_AS_OF s
    LEFT JOIN {SIM_SCHEMA}.SEASON_RULES sr
      ON sr."SEASON" = s."AS_OF_SEASON"
   WHERE s."SEASON_TYPE" = 'regular'
     AND NOT s."IS_CONF_CHAMPIONSHIP"
     AND s."START_AT" < COALESCE(sr."SELECTION_AT", TIMESTAMP '9999-12-31 00:00:00')
),
played AS (
  SELECT g."AS_OF_SEASON", g."AS_OF_WEEK_SEQ", g."GAME_ID", g."WEEK_SEQ", g."START_AT", g."HOME_TEAM_ID",
         g."AWAY_TEAM_ID", g."IS_NEUTRAL_SITE", g."MARGIN"
    FROM {MODEL_SCHEMA}."GAME_AS_OF" g
   WHERE g."SEASON_TYPE" = 'regular'
     AND g."GAME_STATUS_AS_OF" = 'PLAYED'
     AND g."IS_CONFERENCE_GAME"
     AND NOT g."IS_CONF_CHAMPIONSHIP"
     AND g."MARGIN" IS NOT NULL
     AND g."HOME_TEAM_ID" IS NOT NULL
     AND g."AWAY_TEAM_ID" IS NOT NULL
),
sides AS (
  SELECT g."AS_OF_SEASON", g."AS_OF_WEEK_SEQ", g."GAME_ID", x."SIDE",
         CASE WHEN x."SIDE" = 1 THEN g."HOME_TEAM_ID" ELSE g."AWAY_TEAM_ID" END AS "TEAM_ID"
    FROM played g
   CROSS JOIN (VALUES (1), (2)) AS x("SIDE")
),
conferences AS (
  SELECT s."AS_OF_SEASON", s."AS_OF_WEEK_SEQ", s."GAME_ID",
         MAX(CASE WHEN s."SIDE" = 1 THEN r."CONFERENCE" END) AS "HOME_CONFERENCE",
         MAX(CASE WHEN s."SIDE" = 2 THEN r."CONFERENCE" END) AS "AWAY_CONFERENCE"
    FROM sides s
    LEFT JOIN {SIM_SCHEMA}.TEAM_RATING_AS_OF r
      ON r."AS_OF_SEASON" = s."AS_OF_SEASON"
     AND r."AS_OF_WEEK_SEQ" = s."AS_OF_WEEK_SEQ"
     AND r."TEAM_ID" = s."TEAM_ID"
   GROUP BY s."AS_OF_SEASON", s."AS_OF_WEEK_SEQ", s."GAME_ID"
),
played_conference AS (
  SELECT p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", 'PLAYED_CONF' AS "GAME_ROLE", p."GAME_ID", p."WEEK_SEQ", p."START_AT",
         FALSE AS "IS_NEXT_WEEK", p."HOME_TEAM_ID", p."AWAY_TEAM_ID", p."IS_NEUTRAL_SITE", TRUE AS "CONF_GAME",
         CAST(NULL AS DECIMAL(7,2)) AS "MU", CAST(NULL AS DECIMAL(5,2)) AS "SIGMA",
         CAST(NULL AS DECIMAL(5,2)) AS "TEAM_SIGMA", CAST(NULL AS DECIMAL(5,4)) AS "HOME_WIN_PROB",
         CAST(p."MARGIN" AS DECIMAL(4,0)) AS "MARGIN"
    FROM played p
    JOIN conferences c
      ON c."AS_OF_SEASON" = p."AS_OF_SEASON"
     AND c."AS_OF_WEEK_SEQ" = p."AS_OF_WEEK_SEQ"
     AND c."GAME_ID" = p."GAME_ID"
   WHERE c."HOME_CONFERENCE" IS NOT NULL
     AND c."HOME_CONFERENCE" = c."AWAY_CONFERENCE"
)
SELECT r.* FROM remaining r
UNION ALL
SELECT c.* FROM played_conference c
COMMENT IS 'One row per as-of point and engine game of the point''s season. REMAINING: a regular-season game that GAME_SCORE_AS_OF shows (NEXT or REMAINING, both teams known, an FBS team), is no conference championship game and starts before the selection day (SEASON_RULES.SELECTION_AT), with the score model''s MU, SIGMA, TEAM_SIGMA and HOME_WIN_PROB. PLAYED_CONF: a completed regular-season conference game between two teams of one engine conference, with its final MARGIN, kept for head-to-head tiebreaks. The snapshot assigns GAME_IDX by START_AT, GAME_ID. Filter with literal AS_OF_SEASON and AS_OF_WEEK_SEQ.';
