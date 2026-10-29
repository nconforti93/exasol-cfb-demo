CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.WHATIF_GAMES (
  RUN_ID COMMENT IS 'Current what-if run',
  GAME_ID COMMENT IS 'CFBD id of a remaining game in that run',
  PATH_POS COMMENT IS 'One-based character position of this game in WHATIF_OUTCOMES.OUTCOMES',
  SEASON COMMENT IS 'Season simulated by the run',
  WEEK_SEQ COMMENT IS 'Week sequence of the remaining game',
  IS_NEXT_WEEK COMMENT IS 'Whether the game is in the week immediately after the run point',
  START_AT COMMENT IS 'Kickoff in UTC',
  HOME_TEAM_ID COMMENT IS 'Home team id in the run snapshot',
  HOME_SCHOOL COMMENT IS 'Home school name in the run snapshot',
  AWAY_TEAM_ID COMMENT IS 'Away team id in the run snapshot',
  AWAY_SCHOOL COMMENT IS 'Away school name in the run snapshot',
  HOME_WIN_PROB COMMENT IS 'Score-model probability of a home win at the run point',
  N_HOME_WINS COMMENT IS 'Simulated seasons in which the home team won'
) AS
SELECT r."RUN_ID", g."GAME_ID", g."PATH_POS", r."AS_OF_SEASON", g."WEEK_SEQ",
       g."IS_NEXT_WEEK", g."START_AT", g."HOME_TEAM_ID", h."SCHOOL",
       g."AWAY_TEAM_ID", a."SCHOOL", g."HOME_WIN_PROB", g."N_HOME_WINS"
  FROM {SIM_SCHEMA}."CURRENT_RUN" r
  JOIN {SIM_SCHEMA}."SIM_RUN_GAME" g ON g."RUN_ID" = r."RUN_ID"
  JOIN {SIM_SCHEMA}."SIM_RUN_TEAM" h
    ON h."RUN_ID" = g."RUN_ID" AND h."TEAM_ID" = g."HOME_TEAM_ID"
  JOIN {SIM_SCHEMA}."SIM_RUN_TEAM" a
    ON a."RUN_ID" = g."RUN_ID" AND a."TEAM_ID" = g."AWAY_TEAM_ID"
 WHERE g."GAME_ROLE" = 'REMAINING'
COMMENT IS 'One row per remaining game of the current what-if run: outcome position, teams and win chance.';
