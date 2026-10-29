-- Precomputed branch counts make each remaining game's playoff leverage cheap to read.
CREATE OR REPLACE FORCE VIEW {SIM_SCHEMA}.ROOTING_GUIDE (
  RUN_ID COMMENT IS 'DONE run that simulated the game',
  TEAM_ID COMMENT IS 'Team whose playoff chances are being compared',
  GAME_ID COMMENT IS 'CFBD game id of the remaining game',
  SEASON COMMENT IS 'Season of the run',
  WEEK_SEQ COMMENT IS 'Week sequence of the remaining game',
  IS_NEXT_WEEK COMMENT IS 'Whether the game is in the week after the run point',
  HOME_TEAM_ID COMMENT IS 'Home team id',
  HOME_SCHOOL COMMENT IS 'Home school name',
  AWAY_TEAM_ID COMMENT IS 'Away team id',
  AWAY_SCHOOL COMMENT IS 'Away school name',
  ROOT_FOR_TEAM_ID COMMENT IS 'Team to root for based on greater conditional playoff probability',
  ROOT_FOR_SCHOOL COMMENT IS 'School to root for based on greater conditional playoff probability',
  P_IF_HOME_WIN COMMENT IS 'Playoff probability when the home team wins',
  P_IF_AWAY_WIN COMMENT IS 'Playoff probability when the away team wins',
  LEVERAGE COMMENT IS 'Absolute difference between the two conditional playoff probabilities',
  GUIDE_RANK COMMENT IS 'Game rank by leverage for this run and team',
  NEXT_WEEK_RANK COMMENT IS 'Next-week game rank by leverage; NULL for later games',
  LOW_SAMPLE COMMENT IS 'Either outcome branch has fewer than MIN_BRANCH_SAMPLE seasons'
) AS
WITH chances AS (
  SELECT l."RUN_ID", l."TEAM_ID", g."GAME_ID", r."AS_OF_SEASON" AS "SEASON",
         g."WEEK_SEQ", g."IS_NEXT_WEEK", g."HOME_TEAM_ID", h."SCHOOL" AS "HOME_SCHOOL",
         g."AWAY_TEAM_ID", a."SCHOOL" AS "AWAY_SCHOOL",
         CAST(l."N_HOME_IN_CFP" AS DOUBLE) / NULLIF(l."N_HOME", 0) AS "P_IF_HOME_WIN",
         CAST(l."N_AWAY_IN_CFP" AS DOUBLE) / NULLIF(l."N_AWAY", 0) AS "P_IF_AWAY_WIN",
         l."N_HOME" < m."PARAM_VALUE" OR l."N_AWAY" < m."PARAM_VALUE" AS "LOW_SAMPLE"
    FROM {SIM_SCHEMA}.LEVERAGE l
    JOIN {SIM_SCHEMA}.SIM_RUN r ON r."RUN_ID" = l."RUN_ID" AND r."RUN_STATUS" = 'DONE'
    JOIN {SIM_SCHEMA}.SIM_RUN_GAME g
      ON g."RUN_ID" = l."RUN_ID" AND g."GAME_IDX" = l."GAME_IDX"
     AND g."GAME_ROLE" = 'REMAINING'
    JOIN {SIM_SCHEMA}.SIM_RUN_TEAM h
      ON h."RUN_ID" = g."RUN_ID" AND h."TEAM_ID" = g."HOME_TEAM_ID"
    JOIN {SIM_SCHEMA}.SIM_RUN_TEAM a
      ON a."RUN_ID" = g."RUN_ID" AND a."TEAM_ID" = g."AWAY_TEAM_ID"
    JOIN {SIM_SCHEMA}.SIM_PARAMS m ON m."PARAM_KEY" = 'MIN_BRANCH_SAMPLE'
), ranked AS (
  SELECT c.*, ABS(c."P_IF_HOME_WIN" - c."P_IF_AWAY_WIN") AS "LEVERAGE"
    FROM chances c
)
SELECT x."RUN_ID", x."TEAM_ID", x."GAME_ID", x."SEASON", x."WEEK_SEQ",
       x."IS_NEXT_WEEK", x."HOME_TEAM_ID", x."HOME_SCHOOL", x."AWAY_TEAM_ID",
       x."AWAY_SCHOOL",
       CASE WHEN x."P_IF_HOME_WIN" > x."P_IF_AWAY_WIN" THEN x."HOME_TEAM_ID"
            WHEN x."P_IF_AWAY_WIN" > x."P_IF_HOME_WIN" THEN x."AWAY_TEAM_ID" END,
       CASE WHEN x."P_IF_HOME_WIN" > x."P_IF_AWAY_WIN" THEN x."HOME_SCHOOL"
            WHEN x."P_IF_AWAY_WIN" > x."P_IF_HOME_WIN" THEN x."AWAY_SCHOOL" END,
       x."P_IF_HOME_WIN", x."P_IF_AWAY_WIN", x."LEVERAGE",
       ROW_NUMBER() OVER (PARTITION BY x."RUN_ID", x."TEAM_ID"
                          ORDER BY x."LEVERAGE" DESC NULLS LAST, x."GAME_ID"),
       CASE WHEN x."IS_NEXT_WEEK" THEN
         ROW_NUMBER() OVER (PARTITION BY x."RUN_ID", x."TEAM_ID", x."IS_NEXT_WEEK"
                            ORDER BY x."LEVERAGE" DESC NULLS LAST, x."GAME_ID") END,
       x."LOW_SAMPLE"
  FROM ranked x
COMMENT IS 'One row per DONE run, team with leverage counts and remaining game: precomputed conditional playoff probabilities, leverage and ranking. A tie has no rooting side.';
