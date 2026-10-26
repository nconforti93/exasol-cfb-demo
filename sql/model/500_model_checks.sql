-- Model checks (model/refresh-step): one row per check and season, and per week for WEEK_NOT_COMPLETE.
-- GRID holds every (check, season) and every (WEEK_NOT_COMPLETE, week) row; OFFENDERS holds the offending
-- items (SEASON, WEEK_SEQ, ITEM, WEIGHT); one LEFT JOIN of the two gives every check its row, PASS or
-- FAIL. OBSERVED is the sum of the weights (games, team-seasons, keys, names, weeks), DETAIL the first 10
-- items plus a count of the rest. A season filter reaches every source through the joins on SEASON.
-- Compile time grows with every reference to a view with a deep stack (GAME, TEAM_SEASON, TEAM_GAME), and
-- a second reference to TEAM_SEASON in a semi-join never finished compiling on the real data. So each of
-- them is read in one pass that serves several checks: the pass is crossed with a VALUES list of the
-- checks it serves, each check keeps its own rows, and one GROUP BY forms the items.
-- WEEK_ORDER_MISMATCH (decision [38]): CFBD labels the lower-division playoff games of November 2025 as
-- postseason weeks 13 and 14 (sequences 113 and 114 sort after postseason week 1) and dates some games
-- outside their calendar week (61 lower-division games of 2026 week 1, Army-Navy 2026). As-of answers
-- follow the week order, so the teams involved are reported.
-- CONFERENCE_GAME_MISMATCH counts games with an FBS team only: on the real data CFBD's lower-division
-- memberships disagree with its conference-game flags in about 1,900 games, none of which the engine sees.
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.MODEL_CHECKS (
  CHECK_NAME   COMMENT IS 'Check: GAME_WITHOUT_WEEK, FBS_TEAM_WITHOUT_CONFERENCE, POWER_CONFERENCE_MISSING (ERROR); DUPLICATE_SOURCE_ROW, CONF_CHAMPIONSHIP_MISSING, CONF_CHAMPIONSHIP_NOTES_MISMATCH, CONFERENCE_GAME_MISMATCH, WEEK_NOT_COMPLETE, MEMBERSHIP_CONFLICT, SP_TEAM_UNMATCHED, SP_PRIOR_NOT_FINAL, POLL_UNKNOWN, CONFERENCE_NAME_UNMATCHED, WEEK_ORDER_MISMATCH (WARNING); GAME_TEAM_NOT_IN_TEAM_LIST, VENUE_WITHOUT_LOCATION (INFO)',
  SEVERITY     COMMENT IS 'ERROR (a failure stops the weekly refresh in its MODEL step), WARNING or INFO',
  SEASON       COMMENT IS 'Season the check covers, 1869 to the newest season with stored games; filter on it (the refresh step evaluates one season)',
  WEEK_SEQ     COMMENT IS 'Week sequence for the week check WEEK_NOT_COMPLETE (regular week n = n, postseason week n = 100 + n); NULL for season checks',
  CHECK_STATUS COMMENT IS 'PASS when OBSERVED is 0, else FAIL',
  OBSERVED     COMMENT IS 'Number of offending rows: games, team-seasons, conferences, keys, names, venues or weeks, as the check counts them',
  DETAIL       COMMENT IS 'Up to 10 offending items, then how many more; NULL when the check passes'
) AS
WITH newest AS (
  SELECT MAX(r."SEASON") AS "SEASON"
    FROM {CORE_SCHEMA}."GAMES" r
),
seasons AS (
  SELECT CAST(y."YEAR_NUMBER" AS DECIMAL(4,0)) AS "SEASON",
         CASE WHEN y."YEAR_NUMBER" = n."SEASON" THEN 1 ELSE 0 END AS "IS_NEWEST"
    FROM (VALUES BETWEEN 1869 AND 2999) y ("YEAR_NUMBER")
   CROSS JOIN newest n
   WHERE y."YEAR_NUMBER" <= n."SEASON"
),
weeks AS (
  SELECT w."SEASON", w."SEASON_TYPE", w."WEEK", w."WEEK_SEQ", w."START_AT", w."END_AT", w."DATES_SOURCE",
         MAX(w."START_AT") OVER (PARTITION BY w."SEASON" ORDER BY w."WEEK_SEQ"
                                 ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS "EARLIER_START"
    FROM {MODEL_SCHEMA}."SEASON_WEEK" w
),
calendar AS (
  SELECT k."SEASON", k."SEASON_TYPE", k."WEEK", k."START_DATE", k."END_DATE"
    FROM {CORE_SCHEMA}."CALENDAR" k
 QUALIFY ROW_NUMBER() OVER (
           PARTITION BY k."SEASON", k."SEASON_TYPE", k."WEEK"
           ORDER BY k."REQ_AT" DESC, k."REQ_SLICE" DESC, k."ROW_NO" DESC) = 1
),
grid AS (
  SELECT c."CHECK_NAME", c."SEVERITY", s."SEASON", CAST(NULL AS DECIMAL(3,0)) AS "WEEK_SEQ"
    FROM (VALUES ('GAME_WITHOUT_WEEK', 'ERROR'),
                 ('FBS_TEAM_WITHOUT_CONFERENCE', 'ERROR'),
                 ('POWER_CONFERENCE_MISSING', 'ERROR'),
                 ('DUPLICATE_SOURCE_ROW', 'WARNING'),
                 ('CONF_CHAMPIONSHIP_MISSING', 'WARNING'),
                 ('CONF_CHAMPIONSHIP_NOTES_MISMATCH', 'WARNING'),
                 ('CONFERENCE_GAME_MISMATCH', 'WARNING'),
                 ('MEMBERSHIP_CONFLICT', 'WARNING'),
                 ('SP_TEAM_UNMATCHED', 'WARNING'),
                 ('SP_PRIOR_NOT_FINAL', 'WARNING'),
                 ('POLL_UNKNOWN', 'WARNING'),
                 ('CONFERENCE_NAME_UNMATCHED', 'WARNING'),
                 ('WEEK_ORDER_MISMATCH', 'WARNING'),
                 ('GAME_TEAM_NOT_IN_TEAM_LIST', 'INFO'),
                 ('VENUE_WITHOUT_LOCATION', 'INFO')) AS c ("CHECK_NAME", "SEVERITY")
   CROSS JOIN seasons s
  UNION ALL
  SELECT 'WEEK_NOT_COMPLETE', 'WARNING', w."SEASON", w."WEEK_SEQ"
    FROM weeks w
),
game_pass AS (
  SELECT a."ARM", g."SEASON",
         CASE WHEN a."ARM" = 'WEEK_NOT_COMPLETE' THEN g."WEEK_SEQ" END AS "WEEK_SEQ",
         CASE a."ARM"
           WHEN 'GAME_WITHOUT_WEEK'
           THEN 'game ' || CAST(g."GAME_ID" AS VARCHAR(20)) || ' (season type '
                || COALESCE(g."SEASON_TYPE", 'none') || ')'
           WHEN 'CONF_CHAMPIONSHIP_NOTES_MISMATCH'
           THEN CASE WHEN g."IS_CONF_CHAMPIONSHIP" THEN 'detected, notes differ: ' ELSE 'not detected: ' END
                || g."HOME_TEAM" || ' vs ' || g."AWAY_TEAM" || ' (week ' || CAST(g."WEEK" AS VARCHAR(3))
                || ', ' || SUBSTR(g."NOTES", 1, 60) || ')'
           WHEN 'VENUE_WITHOUT_LOCATION' THEN g."VENUE_NAME"
           WHEN 'WEEK_ORDER_MISMATCH'
           THEN g."SEASON_TYPE" || ' week ' || CAST(g."WEEK" AS VARCHAR(3)) || ': games kick off '
                || CASE WHEN g."START_AT" < k."START_DATE" THEN 'before' ELSE 'after' END
                || ' the week''s calendar dates'
           WHEN 'WEEK_NOT_COMPLETE'
           THEN g."HOME_TEAM" || ' vs ' || g."AWAY_TEAM" || ' (game '
                || CAST(g."GAME_ID" AS VARCHAR(20)) || ')'
         END AS "ITEM_KEY",
         g."HOME_TEAM" || ' vs ' || g."AWAY_TEAM" AS "EXAMPLE"
    FROM {MODEL_SCHEMA}."GAME" g
    LEFT JOIN calendar k
      ON k."SEASON" = g."SEASON" AND k."SEASON_TYPE" = g."SEASON_TYPE" AND k."WEEK" = g."WEEK"
   CROSS JOIN (VALUES ('GAME_WITHOUT_WEEK'), ('CONF_CHAMPIONSHIP_NOTES_MISMATCH'),
                      ('VENUE_WITHOUT_LOCATION'), ('WEEK_ORDER_MISMATCH'), ('WEEK_NOT_COMPLETE'))
         AS a ("ARM")
   WHERE (a."ARM" = 'GAME_WITHOUT_WEEK' AND g."WEEK_SEQ" IS NULL)
      OR (a."ARM" = 'CONF_CHAMPIONSHIP_NOTES_MISMATCH' AND g."NOTES" IS NOT NULL
          AND ((g."IS_CONF_CHAMPIONSHIP" AND UPPER(g."NOTES") NOT LIKE '%CHAMPIONSHIP%')
               OR (NOT g."IS_CONF_CHAMPIONSHIP" AND g."SEASON_TYPE" = 'regular' AND g."INVOLVES_FBS"
                   AND g."HOME_CONFERENCE" = g."AWAY_CONFERENCE"
                   AND UPPER(g."NOTES") LIKE '%CHAMPIONSHIP%')))
      OR (a."ARM" = 'VENUE_WITHOUT_LOCATION' AND g."VENUE_ID" IS NOT NULL AND NOT g."HAS_VENUE_LOCATION")
      OR (a."ARM" = 'WEEK_ORDER_MISMATCH' AND k."SEASON" IS NOT NULL
          AND (g."START_AT" < k."START_DATE" OR g."START_AT" > k."END_DATE"))
      OR (a."ARM" = 'WEEK_NOT_COMPLETE' AND g."WEEK_SEQ" IS NOT NULL AND NOT g."IS_COMPLETED")
),
team_pass AS (
  SELECT a."ARM", t."SEASON",
         CASE WHEN a."ARM" = 'POWER_CONFERENCE_MISSING' THEN ct."CONFERENCE_NAME" ELSE t."SCHOOL" END
           AS "ITEM_KEY",
         MAX(CASE WHEN a."ARM" = 'FBS_TEAM_WITHOUT_CONFERENCE' AND t."CLASSIFICATION" = 'fbs'
                   AND NOT t."IS_CLASSIFICATION_RETROACTIVE" AND t."CONFERENCE_NAME" IS NULL THEN 1
                  WHEN a."ARM" = 'SP_PRIOR_NOT_FINAL' AND t."HAS_SP_PRIOR" AND NOT t."SP_PRIOR_IS_FINAL"
                  THEN 1
                  WHEN a."ARM" = 'POWER_CONFERENCE_MISSING' AND t."CONFERENCE_NAME" = ct."CONFERENCE_NAME"
                  THEN 1
                  ELSE 0 END) AS "HIT"
    FROM {MODEL_SCHEMA}."TEAM_SEASON" t
   CROSS JOIN (VALUES ('FBS_TEAM_WITHOUT_CONFERENCE'), ('SP_PRIOR_NOT_FINAL'), ('POWER_CONFERENCE_MISSING'))
         AS a ("ARM")
    LEFT JOIN {MODEL_SCHEMA}."CONFERENCE_TIER" ct
      ON a."ARM" = 'POWER_CONFERENCE_MISSING'
     AND t."SEASON" BETWEEN ct."FIRST_SEASON" AND COALESCE(ct."LAST_SEASON", t."SEASON")
     AND ct."IS_POWER_CONFERENCE" AND ct."CONFERENCE_NAME" IS NOT NULL
   GROUP BY a."ARM", t."SEASON",
            CASE WHEN a."ARM" = 'POWER_CONFERENCE_MISSING' THEN ct."CONFERENCE_NAME" ELSE t."SCHOOL" END,
            CASE WHEN a."ARM" = 'POWER_CONFERENCE_MISSING' THEN NULL ELSE t."TEAM_ID" END
),
listed AS (
  SELECT DISTINCT t."ID"
    FROM {CORE_SCHEMA}."TEAMS" t
),
side_pass AS (
  SELECT a."ARM", tg."SEASON",
         CASE a."ARM" WHEN 'CONFERENCE_GAME_MISMATCH' THEN CAST(tg."GAME_ID" AS VARCHAR(20))
                      WHEN 'CONFERENCE_NAME_UNMATCHED' THEN tg."TEAM_CONFERENCE"
                      ELSE tg."TEAM_NAME" END AS "ITEM_KEY",
         MIN(tg."TEAM_NAME") AS "FIRST_TEAM",
         MAX(tg."TEAM_NAME") AS "LAST_TEAM",
         MIN(tg."WEEK") AS "GAME_WEEK",
         COUNT(DISTINCT COALESCE(ts."CONFERENCE_NAME", '(none)')) AS "CONFERENCES"
    FROM {MODEL_SCHEMA}."TEAM_GAME" tg
    LEFT JOIN {MODEL_SCHEMA}."TEAM_SEASON" ts
      ON ts."TEAM_ID" = tg."TEAM_ID" AND ts."SEASON" = tg."SEASON"
    LEFT JOIN {MODEL_SCHEMA}."CONFERENCE" c
      ON c."CONFERENCE_NAME" = tg."TEAM_CONFERENCE"
    LEFT JOIN listed l
      ON l."ID" = tg."TEAM_ID"
   CROSS JOIN (VALUES ('CONFERENCE_GAME_MISMATCH'), ('CONFERENCE_NAME_UNMATCHED'),
                      ('GAME_TEAM_NOT_IN_TEAM_LIST')) AS a ("ARM")
   WHERE (a."ARM" = 'CONFERENCE_GAME_MISMATCH' AND tg."IS_CONFERENCE_GAME" AND tg."OPPONENT_ID" IS NOT NULL
          AND (tg."TEAM_CLASSIFICATION" = 'fbs' OR tg."OPPONENT_CLASSIFICATION" = 'fbs'))
      OR (a."ARM" = 'CONFERENCE_NAME_UNMATCHED' AND tg."TEAM_CONFERENCE" IS NOT NULL
          AND c."CONFERENCE_NAME" IS NULL)
      OR (a."ARM" = 'GAME_TEAM_NOT_IN_TEAM_LIST' AND l."ID" IS NULL)
   GROUP BY a."ARM", tg."SEASON",
            CASE a."ARM" WHEN 'CONFERENCE_GAME_MISMATCH' THEN CAST(tg."GAME_ID" AS VARCHAR(20))
                         WHEN 'CONFERENCE_NAME_UNMATCHED' THEN tg."TEAM_CONFERENCE"
                         ELSE tg."TEAM_NAME" END
),
offenders AS (
  SELECT p."ARM" AS "CHECK_NAME", p."SEASON", p."WEEK_SEQ",
         CASE WHEN p."ARM" = 'WEEK_ORDER_MISMATCH'
              THEN p."ITEM_KEY" || ' (' || CAST(COUNT(*) AS VARCHAR(10)) || ' games, for example '
                   || MIN(p."EXAMPLE") || ')'
              ELSE p."ITEM_KEY" END AS "ITEM",
         CASE WHEN p."ARM" = 'VENUE_WITHOUT_LOCATION' THEN 1 ELSE COUNT(*) END AS "WEIGHT"
    FROM game_pass p
   GROUP BY p."ARM", p."SEASON", p."WEEK_SEQ", p."ITEM_KEY"
  UNION ALL
  SELECT t."ARM", t."SEASON", NULL, t."ITEM_KEY", 1
    FROM team_pass t
   WHERE (t."ARM" = 'POWER_CONFERENCE_MISSING' AND t."HIT" = 0 AND t."ITEM_KEY" IS NOT NULL)
      OR (t."ARM" <> 'POWER_CONFERENCE_MISSING' AND t."HIT" = 1)
  UNION ALL
  SELECT d."ARM", d."SEASON", NULL,
         CASE WHEN d."ARM" = 'CONFERENCE_GAME_MISMATCH'
              THEN d."FIRST_TEAM" || ' vs ' || d."LAST_TEAM" || ' (week '
                   || CAST(d."GAME_WEEK" AS VARCHAR(3)) || ')'
              ELSE d."ITEM_KEY" END,
         1
    FROM side_pass d
   WHERE d."ARM" <> 'CONFERENCE_GAME_MISMATCH' OR d."CONFERENCES" > 1
  UNION ALL
  SELECT 'WEEK_ORDER_MISMATCH', w."SEASON", NULL,
         w."SEASON_TYPE" || ' week ' || CAST(w."WEEK" AS VARCHAR(3)) || ' starts '
           || TO_CHAR(w."START_AT", 'YYYY-MM-DD') || ', before a week earlier in the week order',
         1
    FROM weeks w
   WHERE w."START_AT" < w."EARLIER_START"
  UNION ALL
  SELECT 'DUPLICATE_SOURCE_ROW', x."SEASON", NULL,
         x."SOURCE_TABLE" || ': ' || CAST(COUNT(*) AS VARCHAR(10)) || ' keys stored more than once',
         COUNT(*)
    FROM (SELECT g."SEASON", 'GAMES' AS "SOURCE_TABLE"
            FROM {CORE_SCHEMA}."GAMES" g
           GROUP BY g."SEASON", g."ID" HAVING COUNT(*) > 1
          UNION ALL
          SELECT w."SEASON", 'METRICS_WP_PREGAME'
            FROM {CORE_SCHEMA}."METRICS_WP_PREGAME" w
           GROUP BY w."SEASON", w."GAME_ID" HAVING COUNT(*) > 1
          UNION ALL
          SELECT r."YEAR_", 'RATINGS_SP'
            FROM {CORE_SCHEMA}."RATINGS_SP" r
           WHERE COALESCE(r."TEAM", '') <> 'nationalAverages'
           GROUP BY r."YEAR_", r."TEAM" HAVING COUNT(*) > 1
          UNION ALL
          SELECT k."SEASON", 'RANKINGS_POLLS_RANKS'
            FROM {CORE_SCHEMA}."RANKINGS_POLLS_RANKS" k
           GROUP BY k."SEASON", k."SEASON_TYPE", k."WEEK", k."POLL", k."TEAM_ID",
                    CASE WHEN k."TEAM_ID" IS NULL THEN k."SCHOOL" END
          HAVING COUNT(*) > 1
          UNION ALL
          SELECT c."SEASON", 'CALENDAR'
            FROM {CORE_SCHEMA}."CALENDAR" c
           GROUP BY c."SEASON", c."SEASON_TYPE", c."WEEK" HAVING COUNT(*) > 1) x
   GROUP BY x."SEASON", x."SOURCE_TABLE"
  UNION ALL
  SELECT 'CONF_CHAMPIONSHIP_MISSING', s."SEASON", NULL, cc."CONFERENCE_NAME", 1
    FROM seasons s
    JOIN {MODEL_SCHEMA}."CONFERENCE_CHAMPIONSHIP" cc
      ON s."SEASON" BETWEEN cc."FIRST_SEASON" AND COALESCE(cc."LAST_SEASON", s."SEASON")
    LEFT JOIN (SELECT d."SEASON", d."CONFERENCE_NAME"
                 FROM {MODEL_SCHEMA}."CONFERENCE_CHAMPIONSHIP_GAME" d
                GROUP BY d."SEASON", d."CONFERENCE_NAME") d
      ON d."SEASON" = s."SEASON" AND d."CONFERENCE_NAME" = cc."CONFERENCE_NAME"
    LEFT JOIN (SELECT g."SEASON" FROM {CORE_SCHEMA}."GAMES" g GROUP BY g."SEASON") gs
      ON gs."SEASON" = s."SEASON"
   WHERE d."SEASON" IS NULL AND s."IS_NEWEST" = 0 AND gs."SEASON" IS NOT NULL
  UNION ALL
  SELECT 'MEMBERSHIP_CONFLICT', m."SEASON", NULL, m."SCHOOL", 1
    FROM {MODEL_SCHEMA}."TEAM_MEMBERSHIP" m
   WHERE m."HAS_MEMBERSHIP_CONFLICT"
  UNION ALL
  SELECT 'SP_TEAM_UNMATCHED', r."SEASON", NULL, r."SCHOOL", 1
    FROM {MODEL_SCHEMA}."RATING_SP" r
   WHERE NOT r."IS_TEAM_MATCHED"
  UNION ALL
  SELECT 'POLL_UNKNOWN', k."SEASON", NULL, k."POLL", 1
    FROM {CORE_SCHEMA}."RANKINGS_POLLS_RANKS" k
    LEFT JOIN {MODEL_SCHEMA}."POLL" p
      ON p."POLL_NAME" = k."POLL"
   WHERE p."POLL_NAME" IS NULL
   GROUP BY k."SEASON", k."POLL"
),
items AS (
  SELECT g."CHECK_NAME", g."SEVERITY", g."SEASON", g."WEEK_SEQ",
         SUBSTR(o."ITEM", 1, 150) AS "ITEM", o."WEIGHT",
         ROW_NUMBER() OVER (PARTITION BY g."CHECK_NAME", g."SEASON", g."WEEK_SEQ" ORDER BY o."ITEM")
           AS "ITEM_NO"
    FROM grid g
    LEFT JOIN offenders o
      ON o."CHECK_NAME" = g."CHECK_NAME" AND o."SEASON" = g."SEASON"
     AND COALESCE(o."WEEK_SEQ", -1) = COALESCE(g."WEEK_SEQ", -1)
)
SELECT i."CHECK_NAME",
       i."SEVERITY",
       i."SEASON",
       i."WEEK_SEQ",
       CASE WHEN SUM(COALESCE(i."WEIGHT", 0)) > 0 THEN 'FAIL' ELSE 'PASS' END,
       CAST(SUM(COALESCE(i."WEIGHT", 0)) AS DECIMAL(18,0)),
       CAST(LISTAGG(CASE WHEN i."ITEM_NO" <= 10 THEN i."ITEM" END, '; ')
              WITHIN GROUP (ORDER BY i."ITEM_NO")
            || CASE WHEN COUNT(i."ITEM") > 10
                    THEN '; and ' || CAST(COUNT(i."ITEM") - 10 AS VARCHAR(10)) || ' more' END
            AS VARCHAR(2000) UTF8)
  FROM items i
 GROUP BY i."CHECK_NAME", i."SEVERITY", i."SEASON", i."WEEK_SEQ"
COMMENT IS 'One row per check and season, and per check and week for the week check WEEK_NOT_COMPLETE, from 1869 to the newest season with stored games: severity, CHECK_STATUS (PASS or FAIL), the number of offending rows (OBSERVED) and up to 10 of them (DETAIL). ERROR checks (a game without a week, an FBS team-season without a conference, a power conference of CONFERENCE_TIER without team-seasons) stop the weekly refresh in its MODEL step; WARNING and INFO checks are logged. Filter on SEASON, and on WEEK_SEQ NULL or the point''s week, as CFB_INFRA.REFRESH_MODEL and make model-check do.';
