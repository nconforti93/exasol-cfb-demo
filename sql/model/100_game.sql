-- Materialized (decision [40]): a table with the columns the view had, filled by the INSERT below. Every
-- `make model` recreates it, and CFB_INFRA.REFRESH_MODEL rebuilds it (TRUNCATE + the INSERT, file order)
-- after every load: refresh step 100, and the post-hook of `make backfill` / `make restore`.
-- The game fact (model/games): one row per CFBD game id of CFB_CORE.GAMES, newest fetch first
-- (REQ_AT, then the later slice key, then the later row). The pregame line of METRICS_WP_PREGAME is
-- deduplicated the same way and joined by game id; venues by venue id. Both partitions start with
-- SEASON (a game id belongs to one season) so that a season filter reaches the scans (probe 1.1g).
-- Engine-facing real values are cast once to fixed-scale DECIMAL; the cast rounds (probe 1.1i).
-- CFP_ROUND_SEQ ranks the season's CFP rounds by their first kickoff. Placeholders (no teams) are
-- FIXED whatever their kind (decision [30]).
CREATE OR REPLACE TABLE {MODEL_SCHEMA}.GAME (
  GAME_ID                DECIMAL(18,0)      COMMENT IS 'CFBD game id; unique in this table (a game stored more than once appears once, from its newest fetch)',
  SEASON                 DECIMAL(18,0)      COMMENT IS 'Season of the game',
  SEASON_TYPE            VARCHAR(2000) UTF8 COMMENT IS 'CFBD season type: regular or postseason; any other type has no week (WEEK_SEQ NULL), which MODEL_CHECKS reports',
  WEEK                   DECIMAL(18,0)      COMMENT IS 'Week as CFBD labels it within the season type',
  WEEK_SEQ               DECIMAL(3,0)       COMMENT IS 'Week sequence (SEASON_WEEK.WEEK_SEQ): regular week n = n, postseason week n = 100 + n; NULL for other season types',
  START_AT               TIMESTAMP(3)       COMMENT IS 'Kickoff in UTC as stored; historic games often carry a date with a nominal time',
  IS_START_TIME_TBD      BOOLEAN            COMMENT IS 'TRUE when CFBD says the kickoff time is not yet set (start time TBD)',
  FETCHED_AT             TIMESTAMP(3)       COMMENT IS 'When the game row shown was fetched from CFBD (REQ_AT, UTC)',
  HOME_TEAM_ID           DECIMAL(18,0)      COMMENT IS 'CFBD team id of the home (first-listed) team; NULL while CFBD lists the game without teams',
  HOME_TEAM              VARCHAR(2000) UTF8 COMMENT IS 'CFBD school name of the home team',
  HOME_CONFERENCE        VARCHAR(2000) UTF8 COMMENT IS 'Conference recorded on the game for the home team (CFBD name); NULL for independents of early eras and placeholders',
  HOME_CLASSIFICATION    VARCHAR(2000) UTF8 COMMENT IS 'Classification of the home team: fbs, fcs, ii or iii. The CLASSIFICATION_TRANSITION row of the team and season if there is one, else the code CFBD records on the game',
  AWAY_TEAM_ID           DECIMAL(18,0)      COMMENT IS 'CFBD team id of the away team; NULL while CFBD lists the game without teams',
  AWAY_TEAM              VARCHAR(2000) UTF8 COMMENT IS 'CFBD school name of the away team',
  AWAY_CONFERENCE        VARCHAR(2000) UTF8 COMMENT IS 'Conference recorded on the game for the away team (CFBD name)',
  AWAY_CLASSIFICATION    VARCHAR(2000) UTF8 COMMENT IS 'Classification of the away team: fbs, fcs, ii or iii, as for HOME_CLASSIFICATION',
  HAS_TEAMS              BOOLEAN            COMMENT IS 'TRUE when both team ids are set; FALSE for a placeholder that CFBD lists before naming its teams (no TEAM_GAME rows, PARTICIPANTS_RULE FIXED)',
  VENUE_ID               DECIMAL(18,0)      COMMENT IS 'CFBD venue id (VENUE.VENUE_ID)',
  VENUE_NAME             VARCHAR(2000) UTF8 COMMENT IS 'Venue name as recorded on the game',
  IS_NEUTRAL_SITE        BOOLEAN            COMMENT IS 'CFBD neutralSite: TRUE when neither team plays at home (engine input neutral)',
  IS_CONFERENCE_GAME     BOOLEAN            COMMENT IS 'CFBD conferenceGame: TRUE when the game counts as a conference game (engine input conf_game). Non-counting games between members, such as Army-Navy in 2024, are FALSE',
  IS_COMPLETED           BOOLEAN            COMMENT IS 'TRUE once CFBD reports the game as completed',
  HOME_POINTS            DECIMAL(18,0)      COMMENT IS 'Points of the home team; NULL unless the game is completed',
  AWAY_POINTS            DECIMAL(18,0)      COMMENT IS 'Points of the away team; NULL unless the game is completed',
  MARGIN                 DECIMAL(9,0)       COMMENT IS 'Home minus away points; NULL without a result',
  TOTAL_POINTS           DECIMAL(9,0)       COMMENT IS 'Home plus away points; NULL without a result',
  WINNER_TEAM_ID         DECIMAL(18,0)      COMMENT IS 'Team id of the winner; NULL without a result or after a tie',
  LOSER_TEAM_ID          DECIMAL(18,0)      COMMENT IS 'Team id of the loser; NULL without a result or after a tie',
  IS_TIE                 BOOLEAN            COMMENT IS 'TRUE when a completed game ended with equal points (possible at the FBS level before 1996), FALSE for any other result, NULL without a result',
  HAS_RESULT             BOOLEAN            COMMENT IS 'Coverage flag: TRUE when the game is completed with both scores',
  HOME_PREGAME_ELO       DECIMAL(18,0)      COMMENT IS 'CFBD pregame Elo of the home team (integer). CFBD fits its Elo model on all seasons, so historic values may embed later methodology; that is not a fact leak',
  AWAY_PREGAME_ELO       DECIMAL(18,0)      COMMENT IS 'CFBD pregame Elo of the away team (integer)',
  HOME_POSTGAME_ELO      DECIMAL(18,0)      COMMENT IS 'CFBD postgame Elo of the home team (integer); set for completed games',
  AWAY_POSTGAME_ELO      DECIMAL(18,0)      COMMENT IS 'CFBD postgame Elo of the away team (integer); set for completed games',
  HAS_ELO                BOOLEAN            COMMENT IS 'Coverage flag: TRUE when both pregame Elo values are stored (CFBD has Elo from 1890)',
  HOME_POSTGAME_WIN_PROB DOUBLE             COMMENT IS 'CFBD postgame win probability of the home team, DOUBLE as CFBD reports it (fitted on all seasons, like Elo); not an engine input',
  AWAY_POSTGAME_WIN_PROB DOUBLE             COMMENT IS 'CFBD postgame win probability of the away team, DOUBLE as CFBD reports it',
  ATTENDANCE             DECIMAL(18,0)      COMMENT IS 'Attendance as CFBD reports it',
  EXCITEMENT_INDEX       DOUBLE             COMMENT IS 'CFBD excitement index, DOUBLE as CFBD reports it',
  PREGAME_SPREAD         DECIMAL(6,2)       COMMENT IS 'Pregame spread from METRICS_WP_PREGAME, from the home team''s side as CFBD reports it (negative when the home team is favored), DECIMAL(6,2); the cast rounds, and an exact binary tie may land one last place off. Historic spreads are closing lines, known only on game day. NULL without a line',
  HOME_PREGAME_WIN_PROB  DECIMAL(5,4)       COMMENT IS 'Pregame win probability of the home team from METRICS_WP_PREGAME, DECIMAL(5,4) (0 to 1); the cast rounds, and an exact binary tie may land one last place off. NULL without a line',
  PREGAME_WP_FETCHED_AT  TIMESTAMP(3)       COMMENT IS 'When the pregame line shown was fetched (REQ_AT, UTC); the as-of views compare it with the cutoff',
  HAS_PREGAME_WP         BOOLEAN            COMMENT IS 'Coverage flag: TRUE when a pregame line is stored for the game (CFBD has them from 2013 and omits games without a spread)',
  POSTSEASON_KIND        VARCHAR(7) ASCII   COMMENT IS 'NULL outside the postseason; else CFP (College Football Playoff: CFBD playoff competition cfp), BOWL (any other postseason game with an FBS team) or NON_FBS (postseason game without an FBS team: lower-division playoffs and bowls)',
  CFP_ROUND              VARCHAR(2000) UTF8 COMMENT IS 'CFP games only: CFBD playoff round code, for example firstRound, quarterfinal, semifinal or championship',
  CFP_ROUND_NAME         VARCHAR(2000) UTF8 COMMENT IS 'CFP games only: CFBD name of the round, for example Quarterfinal',
  CFP_ROUND_SEQ          DECIMAL(18,0)      COMMENT IS 'CFP games only: 1 for the season''s first CFP round, 2 for the next and so on, ordered by each round''s first kickoff',
  CFP_HOME_SEED          DECIMAL(18,0)      COMMENT IS 'CFP games only: playoff seed of the home team',
  CFP_AWAY_SEED          DECIMAL(18,0)      COMMENT IS 'CFP games only: playoff seed of the away team',
  BOWL_NAME              VARCHAR(2000) UTF8 COMMENT IS 'Bowl of a CFP or BOWL game: the playoff bowl name of a CFP game (NULL for a campus game), the CFBD notes of a BOWL game; NULL otherwise',
  IS_CONF_CHAMPIONSHIP   BOOLEAN            COMMENT IS 'TRUE for a conference championship game detected by CONFERENCE_CHAMPIONSHIP_GAME (CFBD does not mark them)',
  PARTICIPANTS_RULE      VARCHAR(17) ASCII  COMMENT IS 'When the teams of the game were known (model/as-of hides the game until then): CONF_CHAMPIONSHIP (detected championship games; from the point before their week), SELECTION (BOWL games and the first CFP round; from the last regular week), PREVIOUS_ROUND (later CFP rounds and NON_FBS games; from their own week) or FIXED (every other game, placeholders included; always)',
  NOTES                  VARCHAR(2000) UTF8 COMMENT IS 'CFBD notes of the game as stored, for example a bowl name or a championship title; free text',
  HAS_LINE_SCORES        BOOLEAN            COMMENT IS 'Coverage flag: TRUE when both teams have points per period stored',
  HAS_VENUE_LOCATION     BOOLEAN            COMMENT IS 'Coverage flag: TRUE when the game''s venue has both coordinates in CFB_CORE.VENUES',
  INVOLVES_FBS           BOOLEAN            COMMENT IS 'Coverage flag: TRUE when at least one team is classified fbs (HOME_CLASSIFICATION, AWAY_CLASSIFICATION)',
  IS_FBS_GAME            BOOLEAN            COMMENT IS 'Coverage flag: TRUE when both teams are classified fbs',
  PLAYS_ACCESS           VARCHAR(13) ASCII  COMMENT IS 'Plays are never stored: ON_DEMAND in seasons in which CFBD has plays (DATASET PLAYS, from 2001; fetch them for one game through CFBD_VS.PLAYS) or NOT_AVAILABLE before'
) COMMENT IS 'One row per game of CFB_CORE.GAMES (1869 on), deduplicated by game id with the newest fetch winning: keys and week sequence, teams with the conference and classification of the game, site and conference flags, the result (completed games only), CFBD Elo, the pregame line from 2013 as fixed-scale DECIMAL, the postseason kind and CFP round, the detected conference championship flag, PARTICIPANTS_RULE (when the teams were known, which the as-of views use to hide result-dependent games) and coverage flags that say which facts the row has. CFBD fits Elo and win probabilities on all seasons, so historic values may embed later methodology. Earlier seasons are fully known; for a state as of a week use GAME_AS_OF. Materialized: rebuilt by CFB_INFRA.REFRESH_MODEL after every load, so it is current as of the last load or refresh.';

INSERT INTO {MODEL_SCHEMA}.GAME
WITH games AS (
  SELECT g.*
    FROM {CORE_SCHEMA}."GAMES" g
 QUALIFY ROW_NUMBER() OVER (
           PARTITION BY g."SEASON", g."ID"
           ORDER BY g."REQ_AT" DESC, g."REQ_SLICE" DESC, g."ROW_NO" DESC) = 1
),
lines AS (
  SELECT w."SEASON", w."GAME_ID", w."SPREAD", w."HOME_WIN_PROBABILITY", w."REQ_AT"
    FROM {CORE_SCHEMA}."METRICS_WP_PREGAME" w
 QUALIFY ROW_NUMBER() OVER (
           PARTITION BY w."SEASON", w."GAME_ID"
           ORDER BY w."REQ_AT" DESC, w."REQ_SLICE" DESC, w."ROW_NO" DESC) = 1
),
venues AS (
  SELECT v."ID", v."LATITUDE", v."LONGITUDE"
    FROM {CORE_SCHEMA}."VENUES" v
 QUALIFY ROW_NUMBER() OVER (
           PARTITION BY v."ID"
           ORDER BY v."REQ_AT" DESC, v."REQ_SLICE" DESC, v."ROW_NO" DESC) = 1
),
classified AS (
  SELECT g.*,
         COALESCE(th."CLASSIFICATION", g."HOME_CLASSIFICATION") AS "HOME_CLASS",
         COALESCE(ta."CLASSIFICATION", g."AWAY_CLASSIFICATION") AS "AWAY_CLASS"
    FROM games g
    LEFT JOIN {MODEL_SCHEMA}."CLASSIFICATION_TRANSITION" th
      ON th."TEAM_NAME" = g."HOME_TEAM"
     AND g."SEASON" BETWEEN th."FIRST_SEASON" AND th."LAST_SEASON"
    LEFT JOIN {MODEL_SCHEMA}."CLASSIFICATION_TRANSITION" ta
      ON ta."TEAM_NAME" = g."AWAY_TEAM"
     AND g."SEASON" BETWEEN ta."FIRST_SEASON" AND ta."LAST_SEASON"
),
kinds AS (
  SELECT c.*,
         c."HOME_ID" IS NOT NULL AND c."AWAY_ID" IS NOT NULL AS "TEAMS_KNOWN",
         CASE WHEN c."HOME_CLASS" = 'fbs' OR c."AWAY_CLASS" = 'fbs' THEN TRUE ELSE FALSE END
           AS "WITH_FBS",
         CASE WHEN c."SEASON_TYPE" = 'postseason'
              THEN CASE WHEN c."PLAYOFF_COMPETITION" = 'cfp' THEN 'CFP'
                        WHEN c."HOME_CLASS" = 'fbs' OR c."AWAY_CLASS" = 'fbs' THEN 'BOWL'
                        ELSE 'NON_FBS' END END AS "KIND",
         CASE WHEN c."COMPLETED" AND c."HOME_POINTS" IS NOT NULL AND c."AWAY_POINTS" IS NOT NULL
              THEN TRUE ELSE FALSE END AS "RESULT_KNOWN"
    FROM classified c
),
rounds AS (
  SELECT k.*,
         MIN(k."START_DATE") OVER (PARTITION BY k."SEASON", k."KIND", k."PLAYOFF_ROUND")
           AS "ROUND_FIRST_AT"
    FROM kinds k
),
sequenced AS (
  SELECT r.*,
         CASE WHEN r."KIND" = 'CFP'
              THEN DENSE_RANK() OVER (
                     PARTITION BY r."SEASON", r."KIND"
                     ORDER BY r."ROUND_FIRST_AT", r."PLAYOFF_ROUND") END AS "ROUND_SEQ"
    FROM rounds r
)
SELECT s."ID",
       s."SEASON",
       s."SEASON_TYPE",
       s."WEEK",
       CAST(CASE s."SEASON_TYPE" WHEN 'regular' THEN s."WEEK" WHEN 'postseason' THEN 100 + s."WEEK" END
            AS DECIMAL(3,0)),
       s."START_DATE",
       COALESCE(s."START_TIME_TBD", FALSE),
       s."REQ_AT",
       s."HOME_ID",
       s."HOME_TEAM",
       s."HOME_CONFERENCE",
       s."HOME_CLASS",
       s."AWAY_ID",
       s."AWAY_TEAM",
       s."AWAY_CONFERENCE",
       s."AWAY_CLASS",
       s."TEAMS_KNOWN",
       s."VENUE_ID",
       s."VENUE",
       COALESCE(s."NEUTRAL_SITE", FALSE),
       COALESCE(s."CONFERENCE_GAME", FALSE),
       COALESCE(s."COMPLETED", FALSE),
       CASE WHEN s."RESULT_KNOWN" THEN s."HOME_POINTS" END,
       CASE WHEN s."RESULT_KNOWN" THEN s."AWAY_POINTS" END,
       CAST(CASE WHEN s."RESULT_KNOWN" THEN s."HOME_POINTS" - s."AWAY_POINTS" END AS DECIMAL(9,0)),
       CAST(CASE WHEN s."RESULT_KNOWN" THEN s."HOME_POINTS" + s."AWAY_POINTS" END AS DECIMAL(9,0)),
       CASE WHEN s."RESULT_KNOWN" AND s."HOME_POINTS" > s."AWAY_POINTS" THEN s."HOME_ID"
            WHEN s."RESULT_KNOWN" AND s."AWAY_POINTS" > s."HOME_POINTS" THEN s."AWAY_ID" END,
       CASE WHEN s."RESULT_KNOWN" AND s."HOME_POINTS" > s."AWAY_POINTS" THEN s."AWAY_ID"
            WHEN s."RESULT_KNOWN" AND s."AWAY_POINTS" > s."HOME_POINTS" THEN s."HOME_ID" END,
       CASE WHEN s."RESULT_KNOWN" THEN s."HOME_POINTS" = s."AWAY_POINTS" END,
       s."RESULT_KNOWN",
       s."HOME_PREGAME_ELO",
       s."AWAY_PREGAME_ELO",
       s."HOME_POSTGAME_ELO",
       s."AWAY_POSTGAME_ELO",
       CASE WHEN s."HOME_PREGAME_ELO" IS NOT NULL AND s."AWAY_PREGAME_ELO" IS NOT NULL
            THEN TRUE ELSE FALSE END,
       s."HOME_POSTGAME_WIN_PROBABILITY",
       s."AWAY_POSTGAME_WIN_PROBABILITY",
       s."ATTENDANCE",
       s."EXCITEMENT_INDEX",
       CAST(l."SPREAD" AS DECIMAL(6,2)),
       CAST(l."HOME_WIN_PROBABILITY" AS DECIMAL(5,4)),
       l."REQ_AT",
       CASE WHEN l."GAME_ID" IS NOT NULL THEN TRUE ELSE FALSE END,
       s."KIND",
       CASE WHEN s."KIND" = 'CFP' THEN s."PLAYOFF_ROUND" END,
       CASE WHEN s."KIND" = 'CFP' THEN s."PLAYOFF_ROUND_NAME" END,
       s."ROUND_SEQ",
       CASE WHEN s."KIND" = 'CFP' THEN s."PLAYOFF_HOME_SEED" END,
       CASE WHEN s."KIND" = 'CFP' THEN s."PLAYOFF_AWAY_SEED" END,
       CASE s."KIND" WHEN 'CFP' THEN s."PLAYOFF_BOWL_NAME" WHEN 'BOWL' THEN s."NOTES" END,
       CASE WHEN cc."GAME_ID" IS NOT NULL THEN TRUE ELSE FALSE END,
       CASE WHEN NOT s."TEAMS_KNOWN" THEN 'FIXED'
            WHEN cc."GAME_ID" IS NOT NULL THEN 'CONF_CHAMPIONSHIP'
            WHEN s."KIND" = 'BOWL' THEN 'SELECTION'
            WHEN s."KIND" = 'CFP' AND s."ROUND_SEQ" = 1 THEN 'SELECTION'
            WHEN s."KIND" IN ('CFP', 'NON_FBS') THEN 'PREVIOUS_ROUND'
            ELSE 'FIXED' END,
       s."NOTES",
       CASE WHEN s."HOME_LINE_SCORES" IS NOT NULL AND s."HOME_LINE_SCORES" <> '[]'
             AND s."AWAY_LINE_SCORES" IS NOT NULL AND s."AWAY_LINE_SCORES" <> '[]'
            THEN TRUE ELSE FALSE END,
       CASE WHEN v."LATITUDE" IS NOT NULL AND v."LONGITUDE" IS NOT NULL THEN TRUE ELSE FALSE END,
       s."WITH_FBS",
       CASE WHEN s."HOME_CLASS" = 'fbs' AND s."AWAY_CLASS" = 'fbs' THEN TRUE ELSE FALSE END,
       CASE WHEN s."SEASON" >= p."FIRST_SEASON"
             AND (p."LAST_SEASON" IS NULL OR s."SEASON" <= p."LAST_SEASON")
            THEN 'ON_DEMAND' ELSE 'NOT_AVAILABLE' END
  FROM sequenced s
  LEFT JOIN lines l
    ON l."SEASON" = s."SEASON" AND l."GAME_ID" = s."ID"
  LEFT JOIN venues v
    ON v."ID" = s."VENUE_ID"
  LEFT JOIN {MODEL_SCHEMA}."CONFERENCE_CHAMPIONSHIP_GAME" cc
    ON cc."SEASON" = s."SEASON" AND cc."GAME_ID" = s."ID"
  CROSS JOIN (SELECT MIN(d."FIRST_SEASON") AS "FIRST_SEASON", MAX(d."LAST_SEASON") AS "LAST_SEASON"
                FROM {MODEL_SCHEMA}."DATASET" d
               WHERE d."DATASET_CODE" = 'PLAYS') p;
