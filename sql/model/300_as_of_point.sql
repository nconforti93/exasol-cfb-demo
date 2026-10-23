-- As-of points (model/as-of): the preseason point and every week of SEASON_WEEK, per season. SEASON_WEEK
-- is read once and crossed with a two-row VALUES list; the second copy of each season's first week becomes
-- the preseason point (week 0, season type regular, sequence 0). Every window partitions by the season, so
-- a season filter reaches SEASON_WEEK's scans (decision [33]); each window step is its own CTE.
-- CUTOFF_AT is the next week's FIRST_GAME_AT, the first kickoff of its games (decision [39]).
-- IS_CURRENT: in the newest season with stored games (the raw CFB_CORE.GAMES, one column scan, as SEASON
-- does) the latest week that is done while every earlier week of the season is done too, else the
-- preseason point. The prefix condition keeps the point in the regular season when CFBD labels November
-- lower-division playoff games as postseason weeks 13 and 14 (sequences 113 and 114 sort after postseason
-- week 1; decision [38]): they are done before the regular season is.
-- IS_SEASON_COMPLETE needs every week done (IS_DONE: all games completed, or fetched a day after the
-- week), not every game completed: real seasons keep a few cancelled games.
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.AS_OF_POINT (
  AS_OF_SEASON          COMMENT IS 'Season of the point; with AS_OF_WEEK_SEQ it identifies the row',
  AS_OF_WEEK            COMMENT IS 'Week of the point as REFRESH_WEEK(season, week, season_type) receives it: the CFBD week within AS_OF_SEASON_TYPE; 0 for the preseason point',
  AS_OF_SEASON_TYPE     COMMENT IS 'Season type of the point as REFRESH_WEEK receives it: regular or postseason; regular for the preseason point',
  AS_OF_WEEK_SEQ        COMMENT IS 'Week sequence of the point: 0 preseason, regular week n = n, postseason week n = 100 + n (SEASON_WEEK.WEEK_SEQ). The point is everything known after the games of that week and before CUTOFF_AT',
  AS_OF_LABEL           COMMENT IS 'Name of the point for pages and the chat, for example 2026, after week 9; 2026, preseason; 2024, after postseason week 1',
  PREVIOUS_WEEK_SEQ     COMMENT IS 'Week sequence of the season''s previous point (0 for the first week); NULL for the preseason point',
  NEXT_WEEK_SEQ         COMMENT IS 'Week sequence of the season''s next week, whose games are NEXT in GAME_AS_OF; NULL at the last point',
  LAST_REGULAR_WEEK_SEQ COMMENT IS 'Week sequence of the season''s last regular-season week. From this point on, bowls and the first CFP round are rows of GAME_AS_OF (their participants are selected)',
  CUTOFF_AT             COMMENT IS 'Information horizon of the point in UTC: the first kickoff of the next week (SEASON_WEEK.FIRST_GAME_AT). Values that CFBD overwrites (current-season SP+, next-week lines, polls) are visible at the point only when fetched before it. NULL at the last point',
  IS_SEASON_COMPLETE    COMMENT IS 'Point metadata: TRUE at the season''s last point when every week of the season is done (SEASON_WEEK.IS_DONE). It describes the point, not the world at the point, and changes when later games complete, so the three as-of views do not carry it',
  IS_CURRENT            COMMENT IS 'Point metadata: TRUE for exactly one point, the default of pages and the chat (CURRENT_AS_OF): in the newest season with stored games, the latest week that is done while every earlier week of the season is done too; the preseason point before that. A week whose Thursday game is played while its Saturday games are not stays open'
) AS
WITH weeks AS (
  SELECT w."SEASON", w."SEASON_TYPE", w."WEEK", w."WEEK_SEQ", w."FIRST_GAME_AT",
         CASE WHEN w."IS_DONE" THEN 1 ELSE 0 END AS "DONE",
         MIN(w."WEEK_SEQ") OVER (PARTITION BY w."SEASON") AS "FIRST_SEQ"
    FROM {MODEL_SCHEMA}."SEASON_WEEK" w
),
points AS (
  SELECT k."SEASON",
         x."IS_WEEK",
         CASE WHEN x."IS_WEEK" = 1 THEN k."SEASON_TYPE" ELSE 'regular' END AS "SEASON_TYPE",
         CASE WHEN x."IS_WEEK" = 1 THEN k."WEEK" ELSE 0 END AS "WEEK",
         CASE WHEN x."IS_WEEK" = 1 THEN k."WEEK_SEQ" ELSE 0 END AS "WEEK_SEQ",
         CASE WHEN x."IS_WEEK" = 1 THEN k."FIRST_GAME_AT" END AS "FIRST_GAME_AT",
         CASE WHEN x."IS_WEEK" = 1 THEN k."DONE" ELSE 1 END AS "DONE"
    FROM weeks k
   CROSS JOIN (VALUES (0), (1)) AS x("IS_WEEK")
   WHERE x."IS_WEEK" = 1 OR k."WEEK_SEQ" = k."FIRST_SEQ"
),
ordered AS (
  SELECT p.*,
         LAG(p."WEEK_SEQ") OVER (PARTITION BY p."SEASON" ORDER BY p."WEEK_SEQ") AS "PREVIOUS_SEQ",
         LEAD(p."WEEK_SEQ") OVER (PARTITION BY p."SEASON" ORDER BY p."WEEK_SEQ") AS "NEXT_SEQ",
         LEAD(p."FIRST_GAME_AT") OVER (PARTITION BY p."SEASON" ORDER BY p."WEEK_SEQ") AS "CUTOFF",
         MAX(CASE WHEN p."IS_WEEK" = 1 AND p."SEASON_TYPE" = 'regular' THEN p."WEEK_SEQ" END)
           OVER (PARTITION BY p."SEASON") AS "LAST_REGULAR_SEQ",
         MAX(p."WEEK_SEQ") OVER (PARTITION BY p."SEASON") AS "LAST_SEQ",
         MIN(p."DONE") OVER (PARTITION BY p."SEASON") AS "ALL_DONE",
         MIN(p."DONE") OVER (PARTITION BY p."SEASON" ORDER BY p."WEEK_SEQ"
                             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS "DONE_SO_FAR"
    FROM points p
),
reached AS (
  SELECT o.*,
         MAX(CASE WHEN o."DONE_SO_FAR" = 1 THEN o."WEEK_SEQ" END)
           OVER (PARTITION BY o."SEASON") AS "REACHED_SEQ"
    FROM ordered o
),
newest AS (
  SELECT MAX(r."SEASON") AS "SEASON"
    FROM {CORE_SCHEMA}."GAMES" r
)
SELECT o."SEASON",
       CAST(o."WEEK" AS DECIMAL(3,0)),
       o."SEASON_TYPE",
       CAST(o."WEEK_SEQ" AS DECIMAL(3,0)),
       CAST(CASE WHEN o."IS_WEEK" = 0 THEN CAST(o."SEASON" AS VARCHAR(4)) || ', preseason'
                 WHEN o."SEASON_TYPE" = 'regular'
                 THEN CAST(o."SEASON" AS VARCHAR(4)) || ', after week ' || CAST(o."WEEK" AS VARCHAR(3))
                 ELSE CAST(o."SEASON" AS VARCHAR(4)) || ', after postseason week '
                      || CAST(o."WEEK" AS VARCHAR(3)) END AS VARCHAR(40) UTF8),
       CAST(o."PREVIOUS_SEQ" AS DECIMAL(3,0)),
       CAST(o."NEXT_SEQ" AS DECIMAL(3,0)),
       CAST(o."LAST_REGULAR_SEQ" AS DECIMAL(3,0)),
       o."CUTOFF",
       CASE WHEN o."WEEK_SEQ" = o."LAST_SEQ" AND o."ALL_DONE" = 1 THEN TRUE ELSE FALSE END,
       CASE WHEN o."SEASON" = n."SEASON" AND o."WEEK_SEQ" = o."REACHED_SEQ" THEN TRUE ELSE FALSE END
  FROM reached o
 CROSS JOIN newest n
COMMENT IS 'One row per season and as-of point: the preseason point (sequence 0) and every week of SEASON_WEEK. A point is everything known after the games of its week and before the first kickoff of the next week (CUTOFF_AT), and it is addressed exactly like REFRESH_WEEK(season, week, season_type): AS_OF_SEASON, AS_OF_WEEK and AS_OF_SEASON_TYPE. Join or filter GAME_AS_OF, TEAM_SEASON_AS_OF and RANKING_AS_OF on AS_OF_SEASON and AS_OF_WEEK_SEQ. IS_SEASON_COMPLETE and IS_CURRENT describe the point and change as later games complete; CURRENT_AS_OF is the current point.';
