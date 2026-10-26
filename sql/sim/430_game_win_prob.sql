-- Stored pregame line where present, else the fitted Elo logistic probability.
CREATE OR REPLACE FORCE VIEW {SIM_SCHEMA}.GAME_WIN_PROB (
  GAME_ID COMMENT IS 'Completed CFBD game id',
  SEASON COMMENT IS 'Game season',
  SEASON_TYPE COMMENT IS 'Regular or postseason',
  WEEK COMMENT IS 'Game week within the season type',
  WEEK_SEQ COMMENT IS 'Game week sequence',
  HOME_TEAM_ID COMMENT IS 'Home team id',
  AWAY_TEAM_ID COMMENT IS 'Away team id',
  WINNER_TEAM_ID COMMENT IS 'Winning team id',
  LOSER_TEAM_ID COMMENT IS 'Losing team id',
  PROB_SOURCE COMMENT IS 'LINE if a stored pregame line exists, else ELO',
  HOME_PREGAME_WIN_PROB COMMENT IS 'Probability of a home win from the line or fitted Elo',
  WINNER_PREGAME_WIN_PROB COMMENT IS 'Probability assigned to the eventual winner before kickoff',
  UPSET_MAGNITUDE COMMENT IS 'One minus WINNER_PREGAME_WIN_PROB'
) AS
WITH probabilities AS (
  SELECT g."GAME_ID", g."SEASON", g."SEASON_TYPE", g."WEEK", g."WEEK_SEQ",
         g."HOME_TEAM_ID", g."AWAY_TEAM_ID", g."WINNER_TEAM_ID", g."LOSER_TEAM_ID",
         CASE WHEN g."HOME_PREGAME_WIN_PROB" IS NOT NULL THEN 'LINE' ELSE 'ELO' END
           AS "PROB_SOURCE",
         COALESCE(CAST(g."HOME_PREGAME_WIN_PROB" AS DOUBLE),
                  1.0 / (1.0 + POWER(10.0,
                    -(g."HOME_PREGAME_ELO" - g."AWAY_PREGAME_ELO"
                      + CASE WHEN g."IS_NEUTRAL_SITE" THEN 0 ELSE p."ELO_PROB_HFA" END)
                    / p."ELO_PROB_SCALE"))) AS "HOME_PROB"
    FROM {MODEL_SCHEMA}.GAME g
   CROSS JOIN {SIM_SCHEMA}.SCORE_PARAMS p
   WHERE g."HAS_RESULT" AND g."WINNER_TEAM_ID" IS NOT NULL AND g."INVOLVES_FBS"
     AND (g."HOME_PREGAME_WIN_PROB" IS NOT NULL
          OR (g."HOME_PREGAME_ELO" IS NOT NULL AND g."AWAY_PREGAME_ELO" IS NOT NULL))
)
SELECT p."GAME_ID", p."SEASON", p."SEASON_TYPE", p."WEEK", p."WEEK_SEQ",
       p."HOME_TEAM_ID", p."AWAY_TEAM_ID", p."WINNER_TEAM_ID", p."LOSER_TEAM_ID",
       p."PROB_SOURCE", p."HOME_PROB",
       CASE WHEN p."WINNER_TEAM_ID" = p."HOME_TEAM_ID" THEN p."HOME_PROB"
            ELSE 1.0 - p."HOME_PROB" END,
       CASE WHEN p."WINNER_TEAM_ID" = p."HOME_TEAM_ID" THEN 1.0 - p."HOME_PROB"
            ELSE p."HOME_PROB" END
  FROM probabilities p
COMMENT IS 'One row per completed game with a winner, an FBS team and a pregame probability: the stored line where available, else fitted Elo. The upset ranking lives in the serving layer.';
