-- Materialized (decision [40]): a table with the columns the view had, filled by the INSERT below. Every
-- `make model` recreates it, and CFB_INFRA.REFRESH_MODEL rebuilds it (TRUNCATE + the INSERT, file order)
-- after every load: refresh step 100, and the post-hook of `make backfill` / `make restore`.
-- SP+ ratings (model/ratings-rankings): one row per season and team name of CFB_CORE.RATINGS_SP. The newest
-- fetch of a season and team name wins (REQ_AT, then the later slice key, then the later row), so a promoted
-- team slice or an overlapping season slice never doubles a team. CFBD's national-averages row (team
-- 'nationalAverages') is not a team and is left out before anything else. CFBD identifies a team only by
-- name: TEAM_ID is the team of the stored team list (CFB_CORE.TEAMS, newest fetch per team id) whose SCHOOL
-- equals the name exactly (if two listed teams carried one name, the lower id is taken). A name without a
-- match is kept with TEAM_ID NULL and IS_TEAM_MATCHED FALSE. The team list is read directly, not through
-- TEAM: TEAM reads every stored game, and TEAM_SEASON, which joins this view, reads GAME already (decision
-- [33]: GAME at most once per query).
-- Overall, offense, defense and special-teams ratings are cast once to DECIMAL(6,2), because the score model
-- reads them; the parts stay DOUBLE as CFBD reports them. IS_FINAL is the load log's verdict on the slice the
-- row came from (CFB_META.LOAD_SLICES, the planned slices only: a slice outside the plan counts as not final).
CREATE OR REPLACE TABLE {MODEL_SCHEMA}.RATING_SP (
  SEASON                    DECIMAL(4,0)       COMMENT IS 'Season the rating belongs to (CFBD year). It includes that season''s games, so as a prior for season S use the rating of season S - 1 (TEAM_SEASON.SP_PRIOR_RATING does). CFBD may compute historic seasons with a later methodology than the current one',
  SCHOOL                    VARCHAR(2000) UTF8 COMMENT IS 'Team name exactly as CFBD reports it with the rating; CFBD gives no team id. Matched to the school names of the stored team list',
  TEAM_ID                   DECIMAL(18,0)      COMMENT IS 'CFBD team id of the team of the stored team list whose school name equals SCHOOL (TEAM.SCHOOL of a listed team); NULL when no listed team has that name (IS_TEAM_MATCHED FALSE)',
  IS_TEAM_MATCHED           BOOLEAN            COMMENT IS 'TRUE when SCHOOL equals the name of a team in the stored team list; FALSE keeps the row but without a team id (MODEL_CHECKS: SP_TEAM_UNMATCHED). CFBD''s national-averages row is not kept at all',
  CONFERENCE                VARCHAR(2000) UTF8 COMMENT IS 'Conference as the SP+ row reports it (CFBD name)',
  SP_RATING                 DECIMAL(6,2)       COMMENT IS 'Overall SP+ rating in points above or below an average team, cast to DECIMAL(6,2) (the cast rounds)',
  SP_RANKING                DECIMAL(18,0)      COMMENT IS 'Overall SP+ rank (1 = best) as CFBD reports it',
  OFFENSE_RATING            DECIMAL(6,2)       COMMENT IS 'Offense SP+ rating, DECIMAL(6,2)',
  OFFENSE_RANKING           DECIMAL(18,0)      COMMENT IS 'Offense SP+ rank as CFBD reports it',
  DEFENSE_RATING            DECIMAL(6,2)       COMMENT IS 'Defense SP+ rating, DECIMAL(6,2); CFBD reports a better defense as a lower value',
  DEFENSE_RANKING           DECIMAL(18,0)      COMMENT IS 'Defense SP+ rank as CFBD reports it',
  SPECIAL_TEAMS_RATING      DECIMAL(6,2)       COMMENT IS 'Special-teams SP+ rating, DECIMAL(6,2)',
  OFFENSE_SUCCESS           DOUBLE             COMMENT IS 'Offense success rate as CFBD reports it',
  OFFENSE_EXPLOSIVENESS     DOUBLE             COMMENT IS 'Offense explosiveness as CFBD reports it',
  OFFENSE_RUSHING           DOUBLE             COMMENT IS 'Offense rushing component as CFBD reports it',
  OFFENSE_PASSING           DOUBLE             COMMENT IS 'Offense passing component as CFBD reports it',
  OFFENSE_STANDARD_DOWNS    DOUBLE             COMMENT IS 'Offense standard-downs component as CFBD reports it',
  OFFENSE_PASSING_DOWNS     DOUBLE             COMMENT IS 'Offense passing-downs component as CFBD reports it',
  OFFENSE_PACE              DOUBLE             COMMENT IS 'Offense pace as CFBD reports it',
  OFFENSE_RUN_RATE          DOUBLE             COMMENT IS 'Offense run rate as CFBD reports it',
  DEFENSE_SUCCESS           DOUBLE             COMMENT IS 'Defense success rate as CFBD reports it',
  DEFENSE_EXPLOSIVENESS     DOUBLE             COMMENT IS 'Defense explosiveness as CFBD reports it',
  DEFENSE_RUSHING           DOUBLE             COMMENT IS 'Defense rushing component as CFBD reports it',
  DEFENSE_PASSING           DOUBLE             COMMENT IS 'Defense passing component as CFBD reports it',
  DEFENSE_STANDARD_DOWNS    DOUBLE             COMMENT IS 'Defense standard-downs component as CFBD reports it',
  DEFENSE_PASSING_DOWNS     DOUBLE             COMMENT IS 'Defense passing-downs component as CFBD reports it',
  DEFENSE_HAVOC_TOTAL       DOUBLE             COMMENT IS 'Defense havoc rate, total, as CFBD reports it',
  DEFENSE_HAVOC_FRONT_SEVEN DOUBLE             COMMENT IS 'Defense havoc rate of the front seven as CFBD reports it',
  DEFENSE_HAVOC_DB          DOUBLE             COMMENT IS 'Defense havoc rate of the defensive backs as CFBD reports it',
  STRENGTH_OF_SCHEDULE      DOUBLE             COMMENT IS 'Strength of schedule as CFBD reports it (CFBD field sos)',
  SECOND_ORDER_WINS         DOUBLE             COMMENT IS 'Second-order wins as CFBD reports them',
  IS_FINAL                  BOOLEAN            COMMENT IS 'TRUE when the slice the row came from is registered as final in the load log (CFB_META.LOAD_SLICES.IS_FINAL: a season is final once it was loaded after 15 February of the following year); FALSE for a current season, for a slice that is not final, and for a slice outside the load plan, such as a promoted team slice',
  FETCHED_AT                TIMESTAMP(3)       COMMENT IS 'When the row shown was fetched from CFBD (REQ_AT, UTC); the as-of views compare it with the cutoff'
) COMMENT IS 'One row per season and team name of CFB_CORE.RATINGS_SP (stored from 2013; the newest fetch wins). CFBD''s national-averages row is not a team and is left out. TEAM_ID comes from the exact name match with the stored team list; a name without a match is kept with TEAM_ID NULL (MODEL_CHECKS: SP_TEAM_UNMATCHED). The overall, offense, defense and special-teams ratings are DECIMAL(6,2). A season''s SP+ includes that season''s games and historic seasons may use a later methodology: as a prior for season S use S - 1 (TEAM_SEASON.SP_PRIOR_RATING). The current season''s rows are overwritten by each refresh, so the as-of views show them only from their FETCHED_AT. Materialized: rebuilt by CFB_INFRA.REFRESH_MODEL after every load, so it is current as of the last load or refresh.';

INSERT INTO {MODEL_SCHEMA}.RATING_SP
WITH ratings AS (
  SELECT r."YEAR_", r."TEAM", r."CONFERENCE", r."RATING", r."RANKING", r."OFFENSE_RATING",
         r."OFFENSE_RANKING", r."DEFENSE_RATING", r."DEFENSE_RANKING", r."SPECIAL_TEAMS_RATING",
         r."OFFENSE_SUCCESS", r."OFFENSE_EXPLOSIVENESS", r."OFFENSE_RUSHING", r."OFFENSE_PASSING",
         r."OFFENSE_STANDARD_DOWNS", r."OFFENSE_PASSING_DOWNS", r."OFFENSE_PACE", r."OFFENSE_RUN_RATE",
         r."DEFENSE_SUCCESS", r."DEFENSE_EXPLOSIVENESS", r."DEFENSE_RUSHING", r."DEFENSE_PASSING",
         r."DEFENSE_STANDARD_DOWNS", r."DEFENSE_PASSING_DOWNS", r."DEFENSE_HAVOC_TOTAL",
         r."DEFENSE_HAVOC_FRONT_SEVEN", r."DEFENSE_HAVOC_DB", r."SOS", r."SECOND_ORDER_WINS",
         r."REQ_SLICE", r."REQ_AT"
    FROM {CORE_SCHEMA}."RATINGS_SP" r
   WHERE COALESCE(r."TEAM", '') <> 'nationalAverages'
 QUALIFY ROW_NUMBER() OVER (
           PARTITION BY r."YEAR_", r."TEAM"
           ORDER BY r."REQ_AT" DESC, r."REQ_SLICE" DESC, r."ROW_NO" DESC) = 1
),
listed AS (
  SELECT t."ID", t."SCHOOL"
    FROM {CORE_SCHEMA}."TEAMS" t
 QUALIFY ROW_NUMBER() OVER (
           PARTITION BY t."ID"
           ORDER BY t."REQ_AT" DESC, t."REQ_SLICE" DESC, t."ROW_NO" DESC) = 1
),
by_name AS (
  SELECT l."SCHOOL", l."ID" AS "TEAM_ID"
    FROM listed l
 QUALIFY ROW_NUMBER() OVER (PARTITION BY l."SCHOOL" ORDER BY l."ID") = 1
),
slice_finality AS (
  SELECT s."SLICE_KEY",
         MAX(CASE WHEN s."IS_FINAL" THEN 1 ELSE 0 END) AS "FINAL"
    FROM {META_SCHEMA}."LOAD_SLICES" s
   WHERE s."TABLE_NAME" = 'RATINGS_SP'
   GROUP BY s."SLICE_KEY"
)
SELECT CAST(r."YEAR_" AS DECIMAL(4,0)),
       r."TEAM",
       n."TEAM_ID",
       n."TEAM_ID" IS NOT NULL,
       r."CONFERENCE",
       CAST(r."RATING" AS DECIMAL(6,2)),
       r."RANKING",
       CAST(r."OFFENSE_RATING" AS DECIMAL(6,2)),
       r."OFFENSE_RANKING",
       CAST(r."DEFENSE_RATING" AS DECIMAL(6,2)),
       r."DEFENSE_RANKING",
       CAST(r."SPECIAL_TEAMS_RATING" AS DECIMAL(6,2)),
       r."OFFENSE_SUCCESS",
       r."OFFENSE_EXPLOSIVENESS",
       r."OFFENSE_RUSHING",
       r."OFFENSE_PASSING",
       r."OFFENSE_STANDARD_DOWNS",
       r."OFFENSE_PASSING_DOWNS",
       r."OFFENSE_PACE",
       r."OFFENSE_RUN_RATE",
       r."DEFENSE_SUCCESS",
       r."DEFENSE_EXPLOSIVENESS",
       r."DEFENSE_RUSHING",
       r."DEFENSE_PASSING",
       r."DEFENSE_STANDARD_DOWNS",
       r."DEFENSE_PASSING_DOWNS",
       r."DEFENSE_HAVOC_TOTAL",
       r."DEFENSE_HAVOC_FRONT_SEVEN",
       r."DEFENSE_HAVOC_DB",
       r."SOS",
       r."SECOND_ORDER_WINS",
       CASE WHEN sl."FINAL" = 1 THEN TRUE ELSE FALSE END,
       r."REQ_AT"
  FROM ratings r
  LEFT JOIN by_name n
    ON n."SCHOOL" = r."TEAM"
  LEFT JOIN slice_finality sl
    ON sl."SLICE_KEY" = r."REQ_SLICE";
