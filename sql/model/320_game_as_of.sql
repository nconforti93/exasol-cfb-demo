-- Games as of a point (model/as-of): AS_OF_POINT joined to GAME on the season (decision [6]: only the
-- point's season), each read once. A game is a row only once its participants are known
-- (GAME.PARTICIPANTS_RULE, decision [21]); a game without a week (WEEK_SEQ NULL, which MODEL_CHECKS
-- reports as GAME_WITHOUT_WEEK) cannot be placed relative to a point and is left out. Each value is masked
-- by one horizon: results and postgame values up to the point's week, pregame Elo up to the next week,
-- pregame lines up to the point's week or, for the next week, when fetched before CUTOFF_AT (decision [4]).
-- Schedule facts of a visible game (teams, kickoff, site, flags, kinds) are shown as last fetched.
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.GAME_AS_OF (
  AS_OF_SEASON         COMMENT IS 'Season of the as-of point (AS_OF_POINT.AS_OF_SEASON); filter or join on it with AS_OF_WEEK_SEQ',
  AS_OF_WEEK_SEQ       COMMENT IS 'Week sequence of the point: 0 preseason, regular week n = n, postseason week n = 100 + n',
  CUTOFF_AT            COMMENT IS 'Information horizon of the point (AS_OF_POINT.CUTOFF_AT): the first kickoff of the next week, UTC; NULL at the last point',
  GAME_ID              COMMENT IS 'CFBD game id (GAME.GAME_ID); with the point it identifies the row',
  SEASON_TYPE          COMMENT IS 'CFBD season type of the game: regular or postseason',
  WEEK                 COMMENT IS 'Week as CFBD labels it within the season type',
  WEEK_SEQ             COMMENT IS 'Week sequence of the game: regular week n = n, postseason week n = 100 + n',
  START_AT             COMMENT IS 'Kickoff in UTC as last fetched',
  HOME_TEAM_ID         COMMENT IS 'CFBD team id of the home (first-listed) team; NULL for a placeholder that CFBD lists before naming its teams',
  HOME_TEAM            COMMENT IS 'CFBD school name of the home team',
  HOME_CONFERENCE      COMMENT IS 'Conference recorded on the game for the home team (CFBD name)',
  HOME_CLASSIFICATION  COMMENT IS 'Classification of the home team on the game: fbs, fcs, ii or iii (GAME.HOME_CLASSIFICATION)',
  AWAY_TEAM_ID         COMMENT IS 'CFBD team id of the away team; NULL for a placeholder',
  AWAY_TEAM            COMMENT IS 'CFBD school name of the away team',
  AWAY_CONFERENCE      COMMENT IS 'Conference recorded on the game for the away team (CFBD name)',
  AWAY_CLASSIFICATION  COMMENT IS 'Classification of the away team on the game: fbs, fcs, ii or iii',
  HAS_TEAMS            COMMENT IS 'TRUE when both team ids are set; the engine simulates only games with teams',
  INVOLVES_FBS         COMMENT IS 'TRUE when at least one team is classified fbs on the game (GAME.INVOLVES_FBS)',
  IS_FBS_GAME          COMMENT IS 'TRUE when both teams are classified fbs on the game (GAME.IS_FBS_GAME)',
  VENUE_ID             COMMENT IS 'CFBD venue id (VENUE.VENUE_ID)',
  VENUE_NAME           COMMENT IS 'Venue name as recorded on the game',
  IS_NEUTRAL_SITE      COMMENT IS 'CFBD neutralSite: TRUE when neither team plays at home (engine input neutral)',
  IS_CONFERENCE_GAME   COMMENT IS 'CFBD conferenceGame (engine input conf_game); conference records leave out conference championship games',
  IS_CONF_CHAMPIONSHIP COMMENT IS 'TRUE for a detected conference championship game (GAME.IS_CONF_CHAMPIONSHIP); a row from the point before its week on',
  POSTSEASON_KIND      COMMENT IS 'NULL outside the postseason; else CFP, BOWL (another postseason game with an FBS team) or NON_FBS (postseason game without an FBS team)',
  CFP_ROUND            COMMENT IS 'CFP games only: CFBD playoff round code, for example firstRound, quarterfinal, semifinal or championship',
  PARTICIPANTS_RULE    COMMENT IS 'When the teams were known, which decides from which point the game is a row: FIXED (always), CONF_CHAMPIONSHIP (from the point before its week), SELECTION (bowls and the first CFP round, from LAST_REGULAR_WEEK_SEQ) or PREVIOUS_ROUND (later CFP rounds and NON_FBS postseason games, from their own week)',
  GAME_STATUS_AS_OF    COMMENT IS 'Status at the point: PLAYED (week at most the point''s and completed), NOT_PLAYED (week at most the point''s, not completed), NEXT (in the next week) or REMAINING (later). The engine simulates NEXT and REMAINING games',
  HOME_POINTS          COMMENT IS 'Points of the home team; NULL unless the game is PLAYED with a result',
  AWAY_POINTS          COMMENT IS 'Points of the away team; NULL unless the game is PLAYED with a result',
  MARGIN               COMMENT IS 'Home minus away points (engine input margin of final conference games); NULL unless the game is PLAYED with a result',
  WINNER_TEAM_ID       COMMENT IS 'Team id of the winner; NULL unless the game is PLAYED with a result, and after a tie',
  IS_TIE               COMMENT IS 'TRUE for a PLAYED game that ended with equal points, FALSE for any other PLAYED result; NULL otherwise',
  HOME_PREGAME_ELO     COMMENT IS 'CFBD pregame Elo of the home team (integer); NULL for games after the next week, whose pregame Elo encodes later results',
  AWAY_PREGAME_ELO     COMMENT IS 'CFBD pregame Elo of the away team (integer); NULL for games after the next week',
  HOME_POSTGAME_ELO    COMMENT IS 'CFBD postgame Elo of the home team (integer); NULL unless the game''s week is at most the point''s',
  AWAY_POSTGAME_ELO    COMMENT IS 'CFBD postgame Elo of the away team (integer); NULL unless the game''s week is at most the point''s',
  PREGAME_SPREAD       COMMENT IS 'Pregame spread from the home side as CFBD reports it, DECIMAL(6,2) (GAME.PREGAME_SPREAD); NULL unless the game''s week is at most the point''s, or it is a NEXT game whose line was fetched before CUTOFF_AT. Historic lines are closing lines, known on game day',
  HOME_PREGAME_WIN_PROB COMMENT IS 'Pregame win probability of the home team, DECIMAL(5,4) (GAME.HOME_PREGAME_WIN_PROB); visible exactly when PREGAME_SPREAD is'
) AS
SELECT p."AS_OF_SEASON",
       p."AS_OF_WEEK_SEQ",
       p."CUTOFF_AT",
       g."GAME_ID",
       g."SEASON_TYPE",
       g."WEEK",
       g."WEEK_SEQ",
       g."START_AT",
       g."HOME_TEAM_ID",
       g."HOME_TEAM",
       g."HOME_CONFERENCE",
       g."HOME_CLASSIFICATION",
       g."AWAY_TEAM_ID",
       g."AWAY_TEAM",
       g."AWAY_CONFERENCE",
       g."AWAY_CLASSIFICATION",
       g."HAS_TEAMS",
       g."INVOLVES_FBS",
       g."IS_FBS_GAME",
       g."VENUE_ID",
       g."VENUE_NAME",
       g."IS_NEUTRAL_SITE",
       g."IS_CONFERENCE_GAME",
       g."IS_CONF_CHAMPIONSHIP",
       g."POSTSEASON_KIND",
       g."CFP_ROUND",
       g."PARTICIPANTS_RULE",
       CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" AND g."IS_COMPLETED" THEN 'PLAYED'
            WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" THEN 'NOT_PLAYED'
            WHEN g."WEEK_SEQ" = p."NEXT_WEEK_SEQ" THEN 'NEXT'
            ELSE 'REMAINING' END,
       CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" THEN g."HOME_POINTS" END,
       CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" THEN g."AWAY_POINTS" END,
       CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" THEN g."MARGIN" END,
       CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" THEN g."WINNER_TEAM_ID" END,
       CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" THEN g."IS_TIE" END,
       CASE WHEN g."WEEK_SEQ" <= COALESCE(p."NEXT_WEEK_SEQ", p."AS_OF_WEEK_SEQ")
            THEN g."HOME_PREGAME_ELO" END,
       CASE WHEN g."WEEK_SEQ" <= COALESCE(p."NEXT_WEEK_SEQ", p."AS_OF_WEEK_SEQ")
            THEN g."AWAY_PREGAME_ELO" END,
       CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" THEN g."HOME_POSTGAME_ELO" END,
       CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" THEN g."AWAY_POSTGAME_ELO" END,
       CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ"
              OR (g."WEEK_SEQ" = p."NEXT_WEEK_SEQ" AND g."PREGAME_WP_FETCHED_AT" < p."CUTOFF_AT")
            THEN g."PREGAME_SPREAD" END,
       CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ"
              OR (g."WEEK_SEQ" = p."NEXT_WEEK_SEQ" AND g."PREGAME_WP_FETCHED_AT" < p."CUTOFF_AT")
            THEN g."HOME_PREGAME_WIN_PROB" END
  FROM {MODEL_SCHEMA}."AS_OF_POINT" p
  JOIN {MODEL_SCHEMA}."GAME" g
    ON g."SEASON" = p."AS_OF_SEASON"
 WHERE g."WEEK_SEQ" IS NOT NULL
   AND (g."PARTICIPANTS_RULE" = 'FIXED'
        OR (g."PARTICIPANTS_RULE" = 'CONF_CHAMPIONSHIP'
            AND g."WEEK_SEQ" <= COALESCE(p."NEXT_WEEK_SEQ", p."AS_OF_WEEK_SEQ"))
        OR (g."PARTICIPANTS_RULE" = 'SELECTION' AND p."AS_OF_WEEK_SEQ" >= p."LAST_REGULAR_WEEK_SEQ")
        OR (g."PARTICIPANTS_RULE" = 'PREVIOUS_ROUND' AND g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ"))
COMMENT IS 'One row per as-of point and game of the point''s season whose teams are known at the point (PARTICIPANTS_RULE: conference championship games from the point before their week, bowls and the first CFP round from the last regular week, later rounds and lower-division postseason games from their own week). Values not known at the point are NULL: results and postgame Elo after the point''s week, pregame Elo after the next week, lines after the point''s week unless a next-week line was fetched before CUTOFF_AT. GAME_STATUS_AS_OF says PLAYED, NOT_PLAYED, NEXT or REMAINING. The engine''s game inputs (neutral site, conference flag, final conference-game margins, the games to simulate) come from here. Earlier seasons are fully known: use GAME.';
