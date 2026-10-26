CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.AS_OF_POINTS (
  AS_OF_SEASON COMMENT IS 'Season of the point; with AS_OF_WEEK_SEQ it identifies the row',
  AS_OF_WEEK COMMENT IS 'Week of the point as REFRESH_WEEK(season, week, season_type) receives it: the CFBD week within AS_OF_SEASON_TYPE; 0 for the preseason point',
  AS_OF_SEASON_TYPE COMMENT IS 'Season type of the point as REFRESH_WEEK receives it: regular or postseason; regular for the preseason point',
  AS_OF_WEEK_SEQ COMMENT IS 'Week sequence of the point: 0 preseason, regular week n = n, postseason week n = 100 + n (SEASON_WEEK.WEEK_SEQ). The point is everything known after the games of that week and before CUTOFF_AT',
  AS_OF_LABEL COMMENT IS 'Name of the point for pages and the chat, for example 2026, after week 9; 2026, preseason; 2024, after postseason week 1',
  PREVIOUS_WEEK_SEQ COMMENT IS 'Week sequence of the season''s previous point (0 for the first week); NULL for the preseason point',
  NEXT_WEEK_SEQ COMMENT IS 'Week sequence of the season''s next week, whose games are NEXT in GAME_AS_OF; NULL at the last point',
  LAST_REGULAR_WEEK_SEQ COMMENT IS 'Week sequence of the season''s last regular-season week. From this point on, bowls and the first CFP round are rows of GAME_AS_OF (their participants are selected)',
  CUTOFF_AT COMMENT IS 'Information horizon of the point in UTC: the first kickoff of the next week (SEASON_WEEK.FIRST_GAME_AT). Values that CFBD overwrites (current-season SP+, next-week lines, polls) are visible at the point only when fetched before it. NULL at the last point',
  IS_SEASON_COMPLETE COMMENT IS 'Point metadata: TRUE at the season''s last point when every week of the season is done (SEASON_WEEK.IS_DONE). It describes the point, not the world at the point, and changes when later games complete, so the three as-of views do not carry it',
  IS_CURRENT COMMENT IS 'Point metadata: TRUE for exactly one point, the default of pages and the chat (CURRENT_AS_OF): in the newest season with stored games, the latest week that is done while every earlier week of the season is done too; the preseason point before that. A week whose Thursday game is played while its Saturday games are not stays open',
  IS_AFTER_CURRENT COMMENT IS 'TRUE when the point is later than the current point',
  RUN_ID COMMENT IS 'Run shown at this point; NULL before the engine',
  HAS_PUBLICATION COMMENT IS 'TRUE when a shown run exists at this point',
  HAS_PREDICTIONS COMMENT IS 'TRUE when frozen predictions exist for the next week',
  HAS_GRADES COMMENT IS 'TRUE when this point has graded predictions'
) AS
SELECT p."AS_OF_SEASON",
       p."AS_OF_WEEK",
       p."AS_OF_SEASON_TYPE",
       p."AS_OF_WEEK_SEQ",
       p."AS_OF_LABEL",
       p."PREVIOUS_WEEK_SEQ",
       p."NEXT_WEEK_SEQ",
       p."LAST_REGULAR_WEEK_SEQ",
       p."CUTOFF_AT",
       p."IS_SEASON_COMPLETE",
       p."IS_CURRENT",
       p."AS_OF_SEASON" > c."AS_OF_SEASON" OR
         (p."AS_OF_SEASON" = c."AS_OF_SEASON" AND p."AS_OF_WEEK_SEQ" > c."AS_OF_WEEK_SEQ"),
       CAST(NULL AS DECIMAL(18,0)), FALSE, FALSE, FALSE
  FROM {MODEL_SCHEMA}."AS_OF_POINT" p
 CROSS JOIN {MODEL_SCHEMA}."CURRENT_AS_OF" c
COMMENT IS 'One row per season and as-of point: point metadata with empty simulation fields until the engine serving slice.';
