-- The current as-of point (model/as-of): the one row of AS_OF_POINT with IS_CURRENT, read with the newest
-- season's filter, so only that season's weeks are evaluated.
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.CURRENT_AS_OF (
  AS_OF_SEASON          COMMENT IS 'Season of the current point: the newest season with stored games',
  AS_OF_WEEK            COMMENT IS 'Week of the current point as REFRESH_WEEK receives it; 0 before any week of the season is done',
  AS_OF_SEASON_TYPE     COMMENT IS 'Season type of the current point as REFRESH_WEEK receives it: regular or postseason',
  AS_OF_WEEK_SEQ        COMMENT IS 'Week sequence of the current point: 0 preseason, regular week n = n, postseason week n = 100 + n',
  AS_OF_LABEL           COMMENT IS 'Name of the current point, for example 2026, after week 9',
  PREVIOUS_WEEK_SEQ     COMMENT IS 'Week sequence of the previous point (0 for the first week); NULL for the preseason point',
  NEXT_WEEK_SEQ         COMMENT IS 'Week sequence of the next week, whose games are NEXT in GAME_AS_OF; NULL at the last point',
  LAST_REGULAR_WEEK_SEQ COMMENT IS 'Week sequence of the season''s last regular-season week',
  CUTOFF_AT             COMMENT IS 'Information horizon of the current point in UTC: the first kickoff of the next week; NULL at the last point',
  IS_SEASON_COMPLETE    COMMENT IS 'TRUE when the current point is the last point of a season whose weeks are all done',
  IS_CURRENT            COMMENT IS 'Always TRUE (AS_OF_POINT.IS_CURRENT)'
) AS
SELECT p."AS_OF_SEASON", p."AS_OF_WEEK", p."AS_OF_SEASON_TYPE", p."AS_OF_WEEK_SEQ", p."AS_OF_LABEL",
       p."PREVIOUS_WEEK_SEQ", p."NEXT_WEEK_SEQ", p."LAST_REGULAR_WEEK_SEQ", p."CUTOFF_AT",
       p."IS_SEASON_COMPLETE", p."IS_CURRENT"
  FROM {MODEL_SCHEMA}."AS_OF_POINT" p
 WHERE p."IS_CURRENT"
COMMENT IS 'One row per current as-of point, so exactly one row: in the newest season with stored games, the latest week that is done while every earlier week of the season is done too (SEASON_WEEK.IS_DONE), else that season''s preseason point. The default point of pages and the chat; any other point is a row of AS_OF_POINT. Same columns as AS_OF_POINT.';
