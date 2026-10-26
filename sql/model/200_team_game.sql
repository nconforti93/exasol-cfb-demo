-- The game from each participating team's side (model/games): one row per game and named team, built
-- from GAME (which already holds the deduplicated newest fetch), so a game stored more than once still
-- gives two rows. A side without a team id has no row (a placeholder, decision [30]), so a game with
-- one named team gives one row whose opponent columns are NULL. GAME is read once and crossed with
-- a two-row VALUES list (1 = home, 2 = away; a SELECT without FROM would add a SYS.DUAL dependency):
-- a season filter reaches the scan, and there is no join to a prior season. A UNION ALL of two GAME
-- branches read it twice, and Exasol then evaluated the championship detection under it over every
-- season (task 3.7: 3x slower for one season). Real values keep GAME's fixed scale; the cast rounds
-- (probe 1.1i), the negation and the complement stay inside DECIMAL(6,2) and DECIMAL(5,4).
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.TEAM_GAME (
  GAME_ID                COMMENT IS 'CFBD game id (GAME.GAME_ID); with TEAM_ID it identifies the row',
  SEASON                 COMMENT IS 'Season of the game',
  SEASON_TYPE            COMMENT IS 'CFBD season type of the game: regular or postseason',
  WEEK                   COMMENT IS 'Week as CFBD labels it within the season type',
  WEEK_SEQ               COMMENT IS 'Week sequence (SEASON_WEEK.WEEK_SEQ): regular week n = n, postseason week n = 100 + n; NULL for other season types',
  START_AT               COMMENT IS 'Kickoff in UTC as stored; historic games often carry a date with a nominal time',
  TEAM_ID                COMMENT IS 'CFBD team id of the team whose side the row shows; never NULL',
  TEAM_NAME              COMMENT IS 'CFBD school name of the team',
  TEAM_CONFERENCE        COMMENT IS 'Conference recorded on the game for the team (CFBD name); NULL for independents of early eras',
  TEAM_CLASSIFICATION    COMMENT IS 'Classification of the team in the game: fbs, fcs, ii or iii (GAME.HOME_CLASSIFICATION or AWAY_CLASSIFICATION)',
  OPPONENT_ID            COMMENT IS 'CFBD team id of the other team; NULL when CFBD lists the game with one team only',
  OPPONENT_NAME          COMMENT IS 'CFBD school name of the other team',
  OPPONENT_CONFERENCE    COMMENT IS 'Conference recorded on the game for the other team (CFBD name)',
  OPPONENT_CLASSIFICATION COMMENT IS 'Classification of the other team in the game: fbs, fcs, ii or iii',
  IS_HOME_TEAM           COMMENT IS 'TRUE when CFBD lists the team as the home (first-listed) team, also at a neutral site, where SITE is NEUTRAL for both teams; FALSE for the away team',
  SITE                   COMMENT IS 'Where the team played: HOME, AWAY, or NEUTRAL for both teams of a neutral-site game',
  HAS_RESULT             COMMENT IS 'Coverage flag: TRUE when the game is completed with both scores (GAME.HAS_RESULT)',
  GAME_RESULT            COMMENT IS 'Result for the team: W (won), L (lost) or T (tie); NULL without a result',
  POINTS_FOR             COMMENT IS 'Points of the team; NULL without a result',
  POINTS_AGAINST         COMMENT IS 'Points of the opponent; NULL without a result',
  MARGIN                 COMMENT IS 'Points for minus points against, DECIMAL(9,0), from the team''s side (the home team''s GAME.MARGIN, or its negation for the away team); NULL without a result',
  IS_CONFERENCE_GAME     COMMENT IS 'CFBD conferenceGame of the game (GAME.IS_CONFERENCE_GAME); conference records leave out conference championship games',
  IS_CONF_CHAMPIONSHIP   COMMENT IS 'TRUE when the game is a detected conference championship game (GAME.IS_CONF_CHAMPIONSHIP)',
  POSTSEASON_KIND        COMMENT IS 'NULL outside the postseason; else CFP (College Football Playoff), BOWL (any other postseason game with an FBS team) or NON_FBS (postseason game without an FBS team)',
  CFP_ROUND              COMMENT IS 'CFP games only: CFBD playoff round code, for example firstRound, quarterfinal, semifinal or championship',
  PARTICIPANTS_RULE      COMMENT IS 'When the teams of the game were known (GAME.PARTICIPANTS_RULE): CONF_CHAMPIONSHIP, SELECTION, PREVIOUS_ROUND or FIXED',
  TEAM_PREGAME_ELO       COMMENT IS 'CFBD pregame Elo of the team (integer); CFBD fits Elo on all seasons, so historic values may embed later methodology',
  OPPONENT_PREGAME_ELO   COMMENT IS 'CFBD pregame Elo of the opponent (integer)',
  TEAM_POSTGAME_ELO      COMMENT IS 'CFBD postgame Elo of the team (integer); set for completed games',
  TEAM_SPREAD            COMMENT IS 'Pregame spread from the team''s side, DECIMAL(6,2): the stored spread for the home team and its negation for the away team (negative when the team is favored); the cast rounds, and an exact binary tie may land one last place off. NULL without a line',
  TEAM_PREGAME_WIN_PROB  COMMENT IS 'Pregame win probability of the team, DECIMAL(5,4) (0 to 1): GAME.HOME_PREGAME_WIN_PROB for the home team, one minus it for the away team; the cast rounds, and an exact binary tie may land one last place off. NULL without a line',
  HAS_PREGAME_WP         COMMENT IS 'Coverage flag: TRUE when a pregame line is stored for the game (CFBD has them from 2013)'
) AS
SELECT g.GAME_ID,
       g.SEASON,
       g.SEASON_TYPE,
       g.WEEK,
       g.WEEK_SEQ,
       g.START_AT,
       CASE WHEN x.SIDE = 1 THEN g.HOME_TEAM_ID ELSE g.AWAY_TEAM_ID END,
       CASE WHEN x.SIDE = 1 THEN g.HOME_TEAM ELSE g.AWAY_TEAM END,
       CASE WHEN x.SIDE = 1 THEN g.HOME_CONFERENCE ELSE g.AWAY_CONFERENCE END,
       CASE WHEN x.SIDE = 1 THEN g.HOME_CLASSIFICATION ELSE g.AWAY_CLASSIFICATION END,
       CASE WHEN x.SIDE = 1 THEN g.AWAY_TEAM_ID ELSE g.HOME_TEAM_ID END,
       CASE WHEN x.SIDE = 1 THEN g.AWAY_TEAM ELSE g.HOME_TEAM END,
       CASE WHEN x.SIDE = 1 THEN g.AWAY_CONFERENCE ELSE g.HOME_CONFERENCE END,
       CASE WHEN x.SIDE = 1 THEN g.AWAY_CLASSIFICATION ELSE g.HOME_CLASSIFICATION END,
       x.SIDE = 1,
       CASE WHEN g.IS_NEUTRAL_SITE THEN 'NEUTRAL' WHEN x.SIDE = 1 THEN 'HOME' ELSE 'AWAY' END,
       g.HAS_RESULT,
       CASE WHEN g.HAS_RESULT
            THEN CASE WHEN g.IS_TIE THEN 'T'
                      WHEN g.WINNER_TEAM_ID = CASE WHEN x.SIDE = 1 THEN g.HOME_TEAM_ID ELSE g.AWAY_TEAM_ID END THEN 'W'
                      ELSE 'L' END
       END,
       CASE WHEN x.SIDE = 1 THEN g.HOME_POINTS ELSE g.AWAY_POINTS END,
       CASE WHEN x.SIDE = 1 THEN g.AWAY_POINTS ELSE g.HOME_POINTS END,
       CAST(CASE WHEN x.SIDE = 1 THEN g.MARGIN ELSE -g.MARGIN END AS DECIMAL(9,0)),
       g.IS_CONFERENCE_GAME,
       g.IS_CONF_CHAMPIONSHIP,
       g.POSTSEASON_KIND,
       g.CFP_ROUND,
       g.PARTICIPANTS_RULE,
       CASE WHEN x.SIDE = 1 THEN g.HOME_PREGAME_ELO ELSE g.AWAY_PREGAME_ELO END,
       CASE WHEN x.SIDE = 1 THEN g.AWAY_PREGAME_ELO ELSE g.HOME_PREGAME_ELO END,
       CASE WHEN x.SIDE = 1 THEN g.HOME_POSTGAME_ELO ELSE g.AWAY_POSTGAME_ELO END,
       CAST(CASE WHEN x.SIDE = 1 THEN g.PREGAME_SPREAD ELSE -g.PREGAME_SPREAD END AS DECIMAL(6,2)),
       CAST(CASE WHEN x.SIDE = 1 THEN g.HOME_PREGAME_WIN_PROB ELSE 1 - g.HOME_PREGAME_WIN_PROB END
            AS DECIMAL(5,4)),
       g.HAS_PREGAME_WP
  FROM {MODEL_SCHEMA}.GAME g
 CROSS JOIN (VALUES (1), (2)) AS x(SIDE)
 WHERE (x.SIDE = 1 AND g.HOME_TEAM_ID IS NOT NULL)
    OR (x.SIDE = 2 AND g.AWAY_TEAM_ID IS NOT NULL)
COMMENT IS 'One row per game and named participating team, from that team''s side: the game''s keys and week, the team and its opponent with conference and classification, the site (HOME, AWAY or NEUTRAL), the result (W, L or T), points for and against and the margin, the game''s conference, championship and postseason flags, pregame Elo of both teams, and the pregame spread and win probability from the team''s side as fixed-scale DECIMAL. A game gives two rows; a side that CFBD lists without a team id gives none, so a placeholder game has no row. The shape for records, head-to-head series and upset analyses; earlier seasons are fully known. For a state as of a week use GAME_AS_OF.';
