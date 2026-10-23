-- Engine team inputs as of a point (sim-engine/engine-contract, § Engine inputs): the roster of a run,
-- one row per FBS team-season plus every non-FBS team that plays a game the engine simulates. The snapshot
-- numbers the rows (TEAM_IDX by TEAM_ID) and the engine receives these columns as its TEAM rows.
-- SOS and CAPPED_PD are read from GAME_AS_OF in one pass (a row per side). Filter with literal
-- AS_OF_SEASON and AS_OF_WEEK_SEQ values; a join to CURRENT_AS_OF is not pushed down.
CREATE OR REPLACE FORCE VIEW {SIM_SCHEMA}.ENGINE_TEAM_AS_OF (
  AS_OF_SEASON    COMMENT IS 'Season of the as-of point; with AS_OF_WEEK_SEQ and TEAM_ID it identifies the row',
  AS_OF_WEEK_SEQ  COMMENT IS 'Week sequence of the point: 0 preseason, regular week n = n, postseason week n = 100 + n',
  TEAM_ID         COMMENT IS 'CFBD team id (engine TEAM_ID)',
  SCHOOL          COMMENT IS 'CFBD school name',
  CONFERENCE      COMMENT IS 'Engine conference of a P4 or G6 team (CFBD name); NULL for FBS independents and non-FBS teams',
  TIER            COMMENT IS 'Engine tier: P4, G6, IND (FBS independent) or NON_FBS',
  HAS_CCG         COMMENT IS 'TRUE when the team''s conference holds a championship game in the season (CONFERENCE_CHAMPIONSHIP covers it)',
  IS_ELIGIBLE     COMMENT IS 'TRUE for an FBS team with at least one game in the season: it can be selected for the playoff',
  RATING          COMMENT IS 'Team rating in points against an average FBS team (TEAM_RATING_AS_OF.RATING)',
  RATING_SOURCE   COMMENT IS 'How the rating was set: SP_ELO, ELO, SP or DEFAULT',
  SOS             COMMENT IS 'Strength of schedule: mean rating of the opponents of the team''s regular-season games before the selection day, played and remaining, championship games excluded',
  WINS            COMMENT IS 'Games won up to the point',
  LOSSES          COMMENT IS 'Games lost up to the point',
  CONF_WINS       COMMENT IS 'Conference games won up to the point, championship games excluded',
  CONF_LOSSES     COMMENT IS 'Conference games lost up to the point, championship games excluded',
  CONF_PD         COMMENT IS 'Point differential of the conference games that CONF_WINS and CONF_LOSSES count',
  CAPPED_PD       COMMENT IS 'Sum of the team''s margins in its played regular-season games, each capped at plus or minus MARGIN_CAP'
) AS
WITH games AS (
  SELECT g."AS_OF_SEASON", g."AS_OF_WEEK_SEQ", g."GAME_STATUS_AS_OF" AS "STATUS", g."HOME_TEAM_ID", g."AWAY_TEAM_ID",
         g."MARGIN", g."INVOLVES_FBS",
         CASE WHEN g."START_AT" < COALESCE(sr."SELECTION_AT", TIMESTAMP '9999-12-31 00:00:00') THEN 1 ELSE 0 END AS "BEFORE_SELECTION"
    FROM {MODEL_SCHEMA}."GAME_AS_OF" g
    LEFT JOIN {SIM_SCHEMA}.SEASON_RULES sr
      ON sr."SEASON" = g."AS_OF_SEASON"
   WHERE g."SEASON_TYPE" = 'regular'
     AND NOT g."IS_CONF_CHAMPIONSHIP"
     AND g."HOME_TEAM_ID" IS NOT NULL
     AND g."AWAY_TEAM_ID" IS NOT NULL
     AND g."GAME_STATUS_AS_OF" IN ('PLAYED', 'NEXT', 'REMAINING')
),
sides AS (
  SELECT g."AS_OF_SEASON", g."AS_OF_WEEK_SEQ", g."STATUS", g."INVOLVES_FBS", g."BEFORE_SELECTION",
         CASE WHEN x."SIDE" = 1 THEN g."HOME_TEAM_ID" ELSE g."AWAY_TEAM_ID" END AS "TEAM_ID",
         CASE WHEN x."SIDE" = 1 THEN g."AWAY_TEAM_ID" ELSE g."HOME_TEAM_ID" END AS "OPPONENT_ID",
         CASE WHEN x."SIDE" = 1 THEN g."MARGIN" ELSE -g."MARGIN" END AS "TEAM_MARGIN"
    FROM games g
   CROSS JOIN (VALUES (1), (2)) AS x("SIDE")
),
opponents AS (
  SELECT s."AS_OF_SEASON", s."AS_OF_WEEK_SEQ", s."TEAM_ID", s."STATUS", s."INVOLVES_FBS", s."BEFORE_SELECTION",
         s."TEAM_MARGIN", COALESCE(o."RATING", p."NON_FBS_RATING") AS "OPPONENT_RATING"
    FROM sides s
   CROSS JOIN {SIM_SCHEMA}.SCORE_PARAMS p
    LEFT JOIN {SIM_SCHEMA}.TEAM_RATING_AS_OF o
      ON o."AS_OF_SEASON" = s."AS_OF_SEASON"
     AND o."AS_OF_WEEK_SEQ" = s."AS_OF_WEEK_SEQ"
     AND o."TEAM_ID" = s."OPPONENT_ID"
),
schedule AS (
  SELECT q."AS_OF_SEASON", q."AS_OF_WEEK_SEQ", q."TEAM_ID",
         AVG(CASE WHEN q."BEFORE_SELECTION" = 1 THEN q."OPPONENT_RATING" END) AS "SOS",
         SUM(CASE WHEN q."STATUS" = 'PLAYED' AND q."TEAM_MARGIN" IS NOT NULL
                  THEN GREATEST(-p."MARGIN_CAP", LEAST(p."MARGIN_CAP", q."TEAM_MARGIN")) ELSE 0 END) AS "CAPPED_PD",
         MAX(CASE WHEN q."STATUS" IN ('NEXT', 'REMAINING') AND q."BEFORE_SELECTION" = 1 AND q."INVOLVES_FBS"
                  THEN 1 ELSE 0 END) AS "PLAYS_SIMULATED_GAME"
    FROM opponents q
   CROSS JOIN {SIM_SCHEMA}.SCORE_PARAMS p
   GROUP BY q."AS_OF_SEASON", q."AS_OF_WEEK_SEQ", q."TEAM_ID"
)
SELECT t."AS_OF_SEASON",
       t."AS_OF_WEEK_SEQ",
       t."TEAM_ID",
       t."SCHOOL",
       t."CONFERENCE",
       t."TIER",
       CASE WHEN cc."CONFERENCE_NAME" IS NOT NULL THEN TRUE ELSE FALSE END,
       CASE WHEN t."TIER" <> 'NON_FBS' AND t."GAMES_PLAYED" + t."GAMES_REMAINING" > 0 THEN TRUE ELSE FALSE END,
       t."RATING",
       t."RATING_SOURCE",
       CAST(COALESCE(sc."SOS", p."NON_FBS_RATING") AS DECIMAL(6,2)),
       CAST(t."WINS" AS DECIMAL(2,0)),
       CAST(t."LOSSES" AS DECIMAL(2,0)),
       CAST(t."CONF_WINS" AS DECIMAL(2,0)),
       CAST(t."CONF_LOSSES" AS DECIMAL(2,0)),
       CAST(t."CONF_POINT_DIFF" AS DECIMAL(5,0)),
       CAST(COALESCE(sc."CAPPED_PD", 0) AS DECIMAL(5,0))
  FROM {SIM_SCHEMA}.TEAM_RATING_AS_OF t
 CROSS JOIN {SIM_SCHEMA}.SCORE_PARAMS p
  LEFT JOIN schedule sc
    ON sc."AS_OF_SEASON" = t."AS_OF_SEASON"
   AND sc."AS_OF_WEEK_SEQ" = t."AS_OF_WEEK_SEQ"
   AND sc."TEAM_ID" = t."TEAM_ID"
  LEFT JOIN {MODEL_SCHEMA}."CONFERENCE_CHAMPIONSHIP" cc
    ON cc."CONFERENCE_NAME" = t."CONFERENCE"
   AND t."AS_OF_SEASON" BETWEEN cc."FIRST_SEASON" AND COALESCE(cc."LAST_SEASON", t."AS_OF_SEASON")
 WHERE t."TIER" <> 'NON_FBS' OR COALESCE(sc."PLAYS_SIMULATED_GAME", 0) = 1
COMMENT IS 'One row per as-of point and roster team of the point''s season: every FBS team-season plus the non-FBS teams that play a game the engine simulates (a regular-season game that is NEXT or REMAINING, has an FBS team and starts before the selection day). Carries the engine''s TEAM columns: conference, tier, championship game, eligibility, rating, strength of schedule, records, conference point differential and the capped point differential. The snapshot assigns TEAM_IDX by TEAM_ID. Filter with literal AS_OF_SEASON and AS_OF_WEEK_SEQ.';
