-- Game predictions as of a point (sim-engine/score-model): one row per game of the point's season that
-- GAME_AS_OF shows as NEXT or REMAINING, with both teams known and at least one FBS team. The one formula
-- of the score model: the engine receives MU and TEAM_SIGMA, the predictions page shows the same MU, SIGMA
-- and HOME_WIN_PROB. Both teams' ratings are read in one join (a row per side, then pivoted back).
-- h = HORIZON_WEEKS = WEEK_SEQ - AS_OF_WEEK_SEQ, at least 1. Filter with literal AS_OF_SEASON and
-- AS_OF_WEEK_SEQ values; a join to CURRENT_AS_OF is not pushed down.
CREATE OR REPLACE FORCE VIEW {SIM_SCHEMA}.GAME_SCORE_AS_OF (
  AS_OF_SEASON         COMMENT IS 'Season of the as-of point; with AS_OF_WEEK_SEQ and GAME_ID it identifies the row',
  AS_OF_WEEK_SEQ       COMMENT IS 'Week sequence of the point: 0 preseason, regular week n = n, postseason week n = 100 + n',
  GAME_ID              COMMENT IS 'CFBD game id',
  SEASON_TYPE          COMMENT IS 'CFBD season type of the game: regular or postseason',
  WEEK                 COMMENT IS 'Week of the game as CFBD labels it within the season type',
  WEEK_SEQ             COMMENT IS 'Week sequence of the game: regular week n = n, postseason week n = 100 + n',
  START_AT             COMMENT IS 'Kickoff in UTC as last fetched',
  GAME_STATUS_AS_OF    COMMENT IS 'NEXT (a game of the week right after the point) or REMAINING (a later game)',
  HORIZON_WEEKS        COMMENT IS 'Weeks from the point to the game, WEEK_SEQ - AS_OF_WEEK_SEQ: 1 for a NEXT game',
  HOME_TEAM_ID         COMMENT IS 'CFBD team id of the home team',
  HOME_TEAM            COMMENT IS 'CFBD school name of the home team',
  AWAY_TEAM_ID         COMMENT IS 'CFBD team id of the away team',
  AWAY_TEAM            COMMENT IS 'CFBD school name of the away team',
  HOME_CONFERENCE      COMMENT IS 'Engine conference of the home team (TEAM_RATING_AS_OF.CONFERENCE); NULL for an independent or a team that is not FBS',
  AWAY_CONFERENCE      COMMENT IS 'Engine conference of the away team; NULL for an independent or a team that is not FBS',
  IS_NEUTRAL_SITE      COMMENT IS 'TRUE when neither team plays at home: no home-field advantage',
  IS_CONFERENCE_GAME   COMMENT IS 'CFBD conferenceGame flag of the game',
  IS_CONF_CHAMPIONSHIP COMMENT IS 'TRUE for a conference championship game',
  HOME_RATING          COMMENT IS 'Rating of the home team in points (TEAM_RATING_AS_OF.RATING)',
  AWAY_RATING          COMMENT IS 'Rating of the away team in points',
  HOME_RATING_SOURCE   COMMENT IS 'How the home rating was set: SP_ELO, ELO, SP or DEFAULT',
  AWAY_RATING_SOURCE   COMMENT IS 'How the away rating was set: SP_ELO, ELO, SP or DEFAULT',
  MU                   COMMENT IS 'Expected margin, home minus away, in points: HFA off neutral sites plus home rating minus away rating',
  SIGMA                COMMENT IS 'Standard deviation of the margin in points: SIGMA_1 one week ahead, growing with the square root of the weeks beyond the first',
  TEAM_SIGMA           COMMENT IS 'Scale in points of the team-strength offsets that the engine shares across a team''s games: TAU x sqrt(h - 1) / sqrt(2); 0 one week ahead',
  TOTAL                COMMENT IS 'Predicted total points of both teams: from both teams'' previous-season offense and defense SP+, else the default total',
  HOME_POINTS_PRED     COMMENT IS 'Predicted points of the home team, (TOTAL + MU) / 2, at least 0',
  AWAY_POINTS_PRED     COMMENT IS 'Predicted points of the away team, (TOTAL - MU) / 2, at least 0',
  HOME_WIN_PROB        COMMENT IS 'Probability that the home team wins: the normal margin rounded to whole points, a tie decided by an overtime coin flip'
) AS
WITH games AS (
  SELECT g."AS_OF_SEASON", g."AS_OF_WEEK_SEQ", g."GAME_ID", g."SEASON_TYPE", g."WEEK", g."WEEK_SEQ", g."START_AT",
         g."GAME_STATUS_AS_OF", g."HOME_TEAM_ID", g."HOME_TEAM", g."AWAY_TEAM_ID", g."AWAY_TEAM",
         g."IS_NEUTRAL_SITE", g."IS_CONFERENCE_GAME", g."IS_CONF_CHAMPIONSHIP"
    FROM {MODEL_SCHEMA}."GAME_AS_OF" g
   WHERE g."GAME_STATUS_AS_OF" IN ('NEXT', 'REMAINING')
     AND g."HOME_TEAM_ID" IS NOT NULL
     AND g."AWAY_TEAM_ID" IS NOT NULL
     AND g."INVOLVES_FBS"
),
sides AS (
  SELECT g."AS_OF_SEASON", g."AS_OF_WEEK_SEQ", g."GAME_ID", x."SIDE",
         CASE WHEN x."SIDE" = 1 THEN g."HOME_TEAM_ID" ELSE g."AWAY_TEAM_ID" END AS "TEAM_ID"
    FROM games g
   CROSS JOIN (VALUES (1), (2)) AS x("SIDE")
),
rated AS (
  SELECT s."AS_OF_SEASON", s."AS_OF_WEEK_SEQ", s."GAME_ID", s."SIDE",
         r."CONFERENCE", r."SP_PRIOR_OFFENSE", r."SP_PRIOR_DEFENSE",
         COALESCE(r."RATING", p."NON_FBS_RATING") AS "RATING",
         COALESCE(r."RATING_SOURCE", 'DEFAULT') AS "RATING_SOURCE"
    FROM sides s
   CROSS JOIN {SIM_SCHEMA}.SCORE_PARAMS p
    LEFT JOIN {SIM_SCHEMA}.TEAM_RATING_AS_OF r
      ON r."AS_OF_SEASON" = s."AS_OF_SEASON"
     AND r."AS_OF_WEEK_SEQ" = s."AS_OF_WEEK_SEQ"
     AND r."TEAM_ID" = s."TEAM_ID"
),
pivoted AS (
  SELECT d."AS_OF_SEASON", d."AS_OF_WEEK_SEQ", d."GAME_ID",
         MAX(CASE WHEN d."SIDE" = 1 THEN d."CONFERENCE" END) AS "HOME_CONFERENCE",
         MAX(CASE WHEN d."SIDE" = 2 THEN d."CONFERENCE" END) AS "AWAY_CONFERENCE",
         MAX(CASE WHEN d."SIDE" = 1 THEN d."RATING" END) AS "HOME_RATING",
         MAX(CASE WHEN d."SIDE" = 2 THEN d."RATING" END) AS "AWAY_RATING",
         MAX(CASE WHEN d."SIDE" = 1 THEN d."RATING_SOURCE" END) AS "HOME_RATING_SOURCE",
         MAX(CASE WHEN d."SIDE" = 2 THEN d."RATING_SOURCE" END) AS "AWAY_RATING_SOURCE",
         MAX(CASE WHEN d."SIDE" = 1 THEN d."SP_PRIOR_OFFENSE" END) AS "HOME_OFFENSE",
         MAX(CASE WHEN d."SIDE" = 1 THEN d."SP_PRIOR_DEFENSE" END) AS "HOME_DEFENSE",
         MAX(CASE WHEN d."SIDE" = 2 THEN d."SP_PRIOR_OFFENSE" END) AS "AWAY_OFFENSE",
         MAX(CASE WHEN d."SIDE" = 2 THEN d."SP_PRIOR_DEFENSE" END) AS "AWAY_DEFENSE"
    FROM rated d
   GROUP BY d."AS_OF_SEASON", d."AS_OF_WEEK_SEQ", d."GAME_ID"
),
scored AS (
  SELECT g.*, v."HOME_CONFERENCE", v."AWAY_CONFERENCE", v."HOME_RATING", v."AWAY_RATING",
         v."HOME_RATING_SOURCE", v."AWAY_RATING_SOURCE",
         g."WEEK_SEQ" - g."AS_OF_WEEK_SEQ" AS "HORIZON_WEEKS",
         CAST(CASE WHEN g."IS_NEUTRAL_SITE" THEN 0 ELSE p."HFA" END + v."HOME_RATING" - v."AWAY_RATING"
              AS DECIMAL(7,2)) AS "MU",
         CAST(SQRT(p."SIGMA_1" * p."SIGMA_1"
                   + p."TAU" * p."TAU" * GREATEST(g."WEEK_SEQ" - g."AS_OF_WEEK_SEQ" - 1, 0)) AS DECIMAL(5,2)) AS "SIGMA",
         CAST(p."TAU" * SQRT(GREATEST(g."WEEK_SEQ" - g."AS_OF_WEEK_SEQ" - 1, 0)) / SQRT(2) AS DECIMAL(5,2)) AS "TEAM_SIGMA",
         CAST(CASE WHEN v."HOME_OFFENSE" IS NOT NULL AND v."HOME_DEFENSE" IS NOT NULL
                    AND v."AWAY_OFFENSE" IS NOT NULL AND v."AWAY_DEFENSE" IS NOT NULL
                   THEN p."TOTAL_INTERCEPT" + p."TOTAL_SLOPE"
                        * (v."HOME_OFFENSE" + v."HOME_DEFENSE" + v."AWAY_OFFENSE" + v."AWAY_DEFENSE")
                   ELSE p."TOTAL_DEFAULT" END AS DECIMAL(5,1)) AS "TOTAL"
    FROM games g
    JOIN pivoted v
      ON v."AS_OF_SEASON" = g."AS_OF_SEASON"
     AND v."AS_OF_WEEK_SEQ" = g."AS_OF_WEEK_SEQ"
     AND v."GAME_ID" = g."GAME_ID"
   CROSS JOIN {SIM_SCHEMA}.SCORE_PARAMS p
)
SELECT s."AS_OF_SEASON", s."AS_OF_WEEK_SEQ", s."GAME_ID", s."SEASON_TYPE", s."WEEK", s."WEEK_SEQ", s."START_AT",
       s."GAME_STATUS_AS_OF", CAST(s."HORIZON_WEEKS" AS DECIMAL(3,0)),
       s."HOME_TEAM_ID", s."HOME_TEAM", s."AWAY_TEAM_ID", s."AWAY_TEAM",
       s."HOME_CONFERENCE", s."AWAY_CONFERENCE",
       s."IS_NEUTRAL_SITE", s."IS_CONFERENCE_GAME", s."IS_CONF_CHAMPIONSHIP",
       s."HOME_RATING", s."AWAY_RATING", s."HOME_RATING_SOURCE", s."AWAY_RATING_SOURCE",
       s."MU", s."SIGMA", s."TEAM_SIGMA", s."TOTAL",
       CAST(GREATEST(0, (s."TOTAL" + s."MU") / 2) AS DECIMAL(5,1)),
       CAST(GREATEST(0, (s."TOTAL" - s."MU") / 2) AS DECIMAL(5,1)),
       CAST(0.5 * ({SIM_SCHEMA}.NORMAL_CDF((s."MU" - 0.5) / s."SIGMA")
                   + {SIM_SCHEMA}.NORMAL_CDF((s."MU" + 0.5) / s."SIGMA")) AS DECIMAL(5,4))
  FROM scored s
COMMENT IS 'One row per as-of point and game of the point''s season that GAME_AS_OF shows as NEXT or REMAINING, with both teams known and at least one FBS team. The score model''s one formula: expected margin MU (home field off neutral sites plus the rating difference), SIGMA growing with the horizon, the shared-offset scale TEAM_SIGMA, the predicted total and points, and HOME_WIN_PROB. Read by the engine inputs, the freeze of predictions and the pages. Filter with literal AS_OF_SEASON and AS_OF_WEEK_SEQ.';
