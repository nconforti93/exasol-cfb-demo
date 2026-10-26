-- Coverage per season and stored dataset (model/coverage): declared (DATASET) x planned (LOAD_PLAN,
-- LOAD_SLICES) x stored and final (LOAD_SLICES) x measured (GAME, TEAM_SEASON). Only STORED datasets with
-- seasons get rows (decision [27]); on-demand and unavailable datasets are described by DATASET alone.
-- Seasons run from 1869 to the newest season with stored games (the raw CFB_CORE.GAMES, as SEASON does).
-- A season slice speaks for its season, a range slice for every season of its entry's range; when several
-- slices cover a season, one stored (final) slice is enough. The measured counts read GAME and TEAM_SEASON
-- once each, grouped by season. TRUST_LEVEL is the first rule that applies: NOT_AVAILABLE, ON_DEMAND,
-- MISSING, IN_PROGRESS, PARTIAL, COMPLETE.
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.SEASON_COVERAGE (
  SEASON           COMMENT IS 'Season, 1869 to the newest season with stored games; with DATASET_CODE it identifies the row',
  DATASET_CODE     COMMENT IS 'Stored, season-scoped dataset (DATASET.DATASET_CODE): GAMES, SCORES, ELO, PREGAME_WP, SP_PLUS, POLLS, CALENDAR or CONFERENCE_MEMBERSHIP',
  CORE_TABLE       COMMENT IS 'CFB_CORE mirror that stores the dataset (DATASET.CORE_TABLE)',
  VS_TABLE         COMMENT IS 'CFBD_VS virtual table of the dataset, for seasons that are not stored (DATASET.VS_TABLE)',
  IS_DECLARED      COMMENT IS 'TRUE when CFBD declares data for the season: inside FIRST_SEASON to LAST_SEASON of DATASET and not a declared gap',
  IS_PLANNED       COMMENT IS 'TRUE when an entry of the load plan for CORE_TABLE covers the season (CFB_META.LOAD_PLAN, LOAD_SLICES), gaps included',
  IS_STORED        COMMENT IS 'TRUE when a planned slice that covers the season is stored (CFB_META.LOAD_SLICES.IS_STORED)',
  IS_FINAL         COMMENT IS 'TRUE when a stored slice that covers the season is final (loaded after 15 February of the following year, or static)',
  IS_GAP           COMMENT IS 'TRUE for a known hole: a gap season of DATASET or of the load plan (CFB_META.LOAD_PLAN_GAPS)',
  POPULATION       COMMENT IS 'What the measured share counts (DATASET.COVERAGE_POPULATION): COMPLETED_FBS_GAMES (completed games with an FBS team, or every completed game in a season without one), COMPLETED_FBS_VS_FBS_GAMES or FBS_TEAM_SEASONS; NULL when the dataset is not measured',
  POPULATION_COUNT COMMENT IS 'Size of the population in the season (games from GAME, team-seasons from TEAM_SEASON); NULL when not measured',
  COVERED_COUNT    COMMENT IS 'Members of the population that have the data: games with a result (SCORES), with Elo (ELO) or with a pregame line (PREGAME_WP); team-seasons with an SP+ rating (SP_PLUS) or with a membership-based conference (CONFERENCE_MEMBERSHIP); NULL when not measured',
  MEASURED_SHARE   COMMENT IS 'COVERED_COUNT / POPULATION_COUNT, DECIMAL(5,4); NULL when the dataset is not measured, the season is not stored or the population is empty',
  COMPLETE_SHARE   COMMENT IS 'Share from which a stored, final season counts as COMPLETE (DATASET.COMPLETE_SHARE)',
  TRUST_LEVEL      COMMENT IS 'How far the season''s data can be trusted, the first rule that applies: NOT_AVAILABLE (outside the declared seasons, or a gap), ON_DEMAND (no load-plan entry covers it: query CFBD_VS selectively), MISSING (planned, not loaded yet), IN_PROGRESS (stored, not final), PARTIAL (stored and final, MEASURED_SHARE below COMPLETE_SHARE), COMPLETE (stored and final, share at least COMPLETE_SHARE or not measured)',
  TRUST_NOTE       COMMENT IS 'One sentence that explains TRUST_LEVEL: it names the gap, the virtual-schema table to query, or the measured share'
) AS
WITH newest AS (
  SELECT MAX(r."SEASON") AS "SEASON"
    FROM {CORE_SCHEMA}."GAMES" r
),
seasons AS (
  SELECT CAST(y."YEAR_NUMBER" AS DECIMAL(4,0)) AS "SEASON"
    FROM (VALUES BETWEEN 1869 AND 2999) y ("YEAR_NUMBER")
   CROSS JOIN newest n
   WHERE y."YEAR_NUMBER" <= n."SEASON"
),
datasets AS (
  SELECT d."DATASET_CODE", d."LABEL_TEXT", d."CORE_TABLE", d."VS_TABLE", d."FIRST_SEASON",
         d."LAST_SEASON", d."GAP_SEASONS", d."COVERAGE_POPULATION", d."COMPLETE_SHARE"
    FROM {MODEL_SCHEMA}."DATASET" d
   WHERE d."ACCESS_CLASS" = 'STORED' AND d."FIRST_SEASON" IS NOT NULL
),
slices AS (
  SELECT l."TABLE_NAME", s."SEASON", l."IS_GAP", l."IS_STORED", l."IS_FINAL"
    FROM {META_SCHEMA}."LOAD_SLICES" l
    JOIN {META_SCHEMA}."LOAD_PLAN" p
      ON p."ENTRY_ID" = l."ENTRY_ID"
    JOIN seasons s
      ON s."SEASON" = l."SEASON"
      OR (l."SEASON" IS NULL AND l."SLICING" = 'season_range'
          AND s."SEASON" BETWEEN p."SEASON_FROM" AND p."SEASON_TO")
),
load_state AS (
  SELECT x."TABLE_NAME", x."SEASON",
         MAX(CASE WHEN x."IS_GAP" THEN 1 ELSE 0 END) AS "GAP",
         MAX(CASE WHEN x."IS_STORED" THEN 1 ELSE 0 END) AS "STORED",
         MAX(CASE WHEN x."IS_STORED" AND x."IS_FINAL" THEN 1 ELSE 0 END) AS "FINAL"
    FROM slices x
   GROUP BY x."TABLE_NAME", x."SEASON"
),
game_counts AS (
  SELECT g."SEASON",
         SUM(CASE WHEN g."INVOLVES_FBS" THEN 1 ELSE 0 END) AS "FBS_GAMES",
         SUM(CASE WHEN g."IS_COMPLETED" THEN 1 ELSE 0 END) AS "COMPLETED",
         SUM(CASE WHEN g."IS_COMPLETED" AND g."HAS_RESULT" THEN 1 ELSE 0 END) AS "COMPLETED_RESULT",
         SUM(CASE WHEN g."IS_COMPLETED" AND g."HAS_ELO" THEN 1 ELSE 0 END) AS "COMPLETED_ELO",
         SUM(CASE WHEN g."IS_COMPLETED" AND g."INVOLVES_FBS" THEN 1 ELSE 0 END) AS "FBS_COMPLETED",
         SUM(CASE WHEN g."IS_COMPLETED" AND g."INVOLVES_FBS" AND g."HAS_RESULT" THEN 1 ELSE 0 END)
           AS "FBS_RESULT",
         SUM(CASE WHEN g."IS_COMPLETED" AND g."INVOLVES_FBS" AND g."HAS_ELO" THEN 1 ELSE 0 END)
           AS "FBS_ELO",
         SUM(CASE WHEN g."IS_COMPLETED" AND g."IS_FBS_GAME" THEN 1 ELSE 0 END)
           AS "FBS_FBS_COMPLETED",
         SUM(CASE WHEN g."IS_COMPLETED" AND g."IS_FBS_GAME" AND g."HAS_PREGAME_WP" THEN 1 ELSE 0 END)
           AS "FBS_FBS_LINES"
    FROM {MODEL_SCHEMA}."GAME" g
   GROUP BY g."SEASON"
),
team_counts AS (
  SELECT t."SEASON",
         SUM(CASE WHEN t."CLASSIFICATION" = 'fbs' THEN 1 ELSE 0 END) AS "FBS_TEAMS",
         SUM(CASE WHEN t."CLASSIFICATION" = 'fbs' AND t."SP_RATING" IS NOT NULL THEN 1 ELSE 0 END)
           AS "FBS_SP",
         SUM(CASE WHEN t."CLASSIFICATION" = 'fbs' AND t."CONFERENCE_SOURCE" = 'AFFILIATION'
                  THEN 1 ELSE 0 END) AS "FBS_MEMBERS"
    FROM {MODEL_SCHEMA}."TEAM_SEASON" t
   GROUP BY t."SEASON"
),
grid AS (
  SELECT s."SEASON", d.*,
         CASE WHEN s."SEASON" >= d."FIRST_SEASON"
               AND (d."LAST_SEASON" IS NULL OR s."SEASON" <= d."LAST_SEASON")
               AND INSTR(',' || COALESCE(d."GAP_SEASONS", '') || ',',
                         ',' || CAST(s."SEASON" AS VARCHAR(4)) || ',') = 0
              THEN 1 ELSE 0 END AS "DECLARED",
         CASE WHEN INSTR(',' || COALESCE(d."GAP_SEASONS", '') || ',',
                         ',' || CAST(s."SEASON" AS VARCHAR(4)) || ',') > 0
              THEN 1 ELSE 0 END AS "DECLARED_GAP",
         CASE WHEN ls."TABLE_NAME" IS NOT NULL THEN 1 ELSE 0 END AS "PLANNED",
         COALESCE(ls."GAP", 0) AS "PLANNED_GAP",
         COALESCE(ls."STORED", 0) AS "STORED",
         COALESCE(ls."FINAL", 0) AS "FINAL",
         CASE d."COVERAGE_POPULATION"
              WHEN 'COMPLETED_FBS_GAMES'
              THEN CASE WHEN gc."FBS_GAMES" > 0 THEN gc."FBS_COMPLETED" ELSE gc."COMPLETED" END
              WHEN 'COMPLETED_FBS_VS_FBS_GAMES' THEN gc."FBS_FBS_COMPLETED"
              WHEN 'FBS_TEAM_SEASONS' THEN tc."FBS_TEAMS" END AS "POPULATION_N",
         CASE d."DATASET_CODE"
              WHEN 'SCORES'
              THEN CASE WHEN gc."FBS_GAMES" > 0 THEN gc."FBS_RESULT" ELSE gc."COMPLETED_RESULT" END
              WHEN 'ELO'
              THEN CASE WHEN gc."FBS_GAMES" > 0 THEN gc."FBS_ELO" ELSE gc."COMPLETED_ELO" END
              WHEN 'PREGAME_WP' THEN gc."FBS_FBS_LINES"
              WHEN 'SP_PLUS' THEN tc."FBS_SP"
              WHEN 'CONFERENCE_MEMBERSHIP' THEN tc."FBS_MEMBERS" END AS "COVERED_N",
         CASE WHEN d."COVERAGE_POPULATION" = 'COMPLETED_FBS_GAMES' AND COALESCE(gc."FBS_GAMES", 0) = 0
              THEN 'completed games'
              WHEN d."COVERAGE_POPULATION" = 'COMPLETED_FBS_GAMES' THEN 'completed games with an FBS team'
              WHEN d."COVERAGE_POPULATION" = 'COMPLETED_FBS_VS_FBS_GAMES'
              THEN 'completed FBS-vs-FBS games'
              WHEN d."COVERAGE_POPULATION" = 'FBS_TEAM_SEASONS' THEN 'FBS team-seasons' END
           AS "POPULATION_WORDS"
    FROM seasons s
   CROSS JOIN datasets d
    LEFT JOIN load_state ls
      ON ls."TABLE_NAME" = d."CORE_TABLE" AND ls."SEASON" = s."SEASON"
    LEFT JOIN game_counts gc
      ON gc."SEASON" = s."SEASON"
    LEFT JOIN team_counts tc
      ON tc."SEASON" = s."SEASON"
),
measured AS (
  SELECT r.*,
         CASE WHEN r."COVERAGE_POPULATION" IS NOT NULL AND r."STORED" = 1
               AND COALESCE(r."POPULATION_N", 0) > 0
              THEN CAST(ROUND(COALESCE(r."COVERED_N", 0) / r."POPULATION_N", 4) AS DECIMAL(5,4))
         END AS "SHARE",
         CASE WHEN r."DECLARED" = 0 OR r."PLANNED_GAP" = 1 THEN 'NOT_AVAILABLE'
              WHEN r."PLANNED" = 0 THEN 'ON_DEMAND'
              WHEN r."STORED" = 0 THEN 'MISSING'
              WHEN r."FINAL" = 0 THEN 'IN_PROGRESS' END AS "EARLY_LEVEL"
    FROM grid r
),
leveled AS (
  SELECT m.*,
         COALESCE(m."EARLY_LEVEL",
                  CASE WHEN m."SHARE" < m."COMPLETE_SHARE" THEN 'PARTIAL' ELSE 'COMPLETE' END)
           AS "LEVEL_CODE",
         CASE WHEN m."SHARE" IS NOT NULL
              THEN 'measured share ' || CAST(CAST(m."SHARE" AS DECIMAL(3,2)) AS VARCHAR(10)) || ' ('
                   || CAST(COALESCE(m."COVERED_N", 0) AS VARCHAR(10)) || ' of '
                   || CAST(m."POPULATION_N" AS VARCHAR(10)) || ' ' || m."POPULATION_WORDS" || ')'
         END AS "SHARE_TEXT"
    FROM measured m
)
SELECT v."SEASON",
       v."DATASET_CODE",
       v."CORE_TABLE",
       v."VS_TABLE",
       v."DECLARED" = 1,
       v."PLANNED" = 1,
       v."STORED" = 1,
       v."FINAL" = 1,
       v."DECLARED_GAP" = 1 OR v."PLANNED_GAP" = 1,
       v."COVERAGE_POPULATION",
       CAST(CASE WHEN v."COVERAGE_POPULATION" IS NOT NULL THEN COALESCE(v."POPULATION_N", 0) END
            AS DECIMAL(9,0)),
       CAST(CASE WHEN v."COVERAGE_POPULATION" IS NOT NULL THEN COALESCE(v."COVERED_N", 0) END
            AS DECIMAL(9,0)),
       v."SHARE",
       v."COMPLETE_SHARE",
       v."LEVEL_CODE",
       CAST(CASE v."LEVEL_CODE"
              WHEN 'NOT_AVAILABLE'
              THEN CASE WHEN v."DECLARED_GAP" = 1 OR v."PLANNED_GAP" = 1
                        THEN CAST(v."SEASON" AS VARCHAR(4)) || ' is a known gap without data in CFBD ('
                             || v."LABEL_TEXT" || ')'
                        WHEN v."SEASON" < v."FIRST_SEASON"
                        THEN 'CFBD has ' || v."LABEL_TEXT" || ' from '
                             || CAST(v."FIRST_SEASON" AS VARCHAR(4)) || ' on, not for '
                             || CAST(v."SEASON" AS VARCHAR(4))
                        ELSE 'CFBD has ' || v."LABEL_TEXT" || ' up to '
                             || CAST(v."LAST_SEASON" AS VARCHAR(4)) || ' only' END
              WHEN 'ON_DEMAND'
              THEN 'Not stored for ' || CAST(v."SEASON" AS VARCHAR(4))
                   || ': query it selectively through CFBD_VS.' || v."VS_TABLE"
                   || ' with the required filters'
              WHEN 'MISSING'
              THEN 'Planned but not loaded yet: the backfill has not reached '
                   || CAST(v."SEASON" AS VARCHAR(4))
              WHEN 'IN_PROGRESS'
              THEN 'Stored and not final: the season is refreshed until a load after 15 February makes it final'
                   || CASE WHEN v."SHARE_TEXT" IS NOT NULL THEN '; ' || v."SHARE_TEXT" || ' so far' END
              WHEN 'PARTIAL'
              THEN 'Stored and final, but the ' || v."SHARE_TEXT" || ' is below '
                   || CAST(v."COMPLETE_SHARE" AS VARCHAR(10))
              ELSE 'Stored and final; '
                   || COALESCE(v."SHARE_TEXT", 'coverage is not measured for this dataset') END
            AS VARCHAR(400) UTF8)
  FROM leveled v
COMMENT IS 'One row per season (1869 to the newest season with stored games) and stored, season-scoped dataset: whether CFBD declares the data, whether the load plan covers the season, whether it is stored and final, the measured share for SCORES, ELO, PREGAME_WP (completed FBS-vs-FBS games), SP_PLUS and CONFERENCE_MEMBERSHIP, and a TRUST_LEVEL with a one-sentence TRUST_NOTE (NOT_AVAILABLE, ON_DEMAND with the CFBD_VS table to query, MISSING, IN_PROGRESS, PARTIAL, COMPLETE). Datasets that are never stored have no rows here: DATASET is their whole coverage statement.';
