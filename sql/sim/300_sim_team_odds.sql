-- The playoff odds of a run (sim-engine/sim-runs): one row per DONE run and eligible roster team, every
-- probability a share of the run's N_SIMS simulated seasons. Counts come from SIM_TEAM (the 12 playoff
-- teams of each season), TEAM_WINS and CONF_CHAMP; a team that never made the field has zeros. A
-- conference crowns a champion when it has at least min_conference_teams eligible teams in the run's
-- roster (SIM_RUN_PARAM); P_CONF_CHAMPION is NULL for a team without one. Filter on RUN_ID.
CREATE OR REPLACE FORCE VIEW {SIM_SCHEMA}.SIM_TEAM_ODDS (
  RUN_ID          COMMENT IS 'The DONE run the odds come from',
  AS_OF_SEASON    COMMENT IS 'Season of the run''s as-of point',
  AS_OF_WEEK_SEQ  COMMENT IS 'Week sequence of the run''s as-of point',
  AS_OF_LABEL     COMMENT IS 'Display label of the run''s as-of point, for example 2026, after week 7',
  TEAM_ID         COMMENT IS 'CFBD team id',
  SCHOOL          COMMENT IS 'School name',
  CONFERENCE      COMMENT IS 'Conference name; NULL for an independent',
  TIER            COMMENT IS 'P4, G6 or IND',
  ODDS_RANK       COMMENT IS 'Rank within the run by P_IN_CFP, then P_CHAMPION, then TEAM_ID, 1 = best',
  P_IN_CFP        COMMENT IS 'Share of the simulated seasons in which the team made the 12-team playoff',
  P_BYE           COMMENT IS 'Share of the simulated seasons in which the team was seeded 1 to 4 (a first-round bye)',
  P_QUARTERFINAL  COMMENT IS 'Share of the simulated seasons in which the team reached the quarterfinals (a bye counts)',
  P_SEMIFINAL     COMMENT IS 'Share of the simulated seasons in which the team reached the semifinals',
  P_FINAL         COMMENT IS 'Share of the simulated seasons in which the team reached the national championship game',
  P_CHAMPION      COMMENT IS 'Share of the simulated seasons in which the team won the national championship',
  P_CONF_CHAMPION COMMENT IS 'Share of the simulated seasons in which the team won its conference; NULL when its conference crowns no champion (independents, conferences below min_conference_teams eligible teams)',
  EXPECTED_WINS   COMMENT IS 'Mean regular-season wins over the simulated seasons, wins so far included',
  MEAN_SEED       COMMENT IS 'Mean playoff seed over the seasons in which the team made the field; NULL when it never did'
) AS
WITH runs AS (
  SELECT r."RUN_ID", r."AS_OF_SEASON", r."AS_OF_WEEK_SEQ", r."AS_OF_LABEL", r."N_SIMS"
    FROM {SIM_SCHEMA}.SIM_RUN r
   WHERE r."RUN_STATUS" = 'DONE'
),
playoff AS (
  SELECT s."RUN_ID", s."TEAM_ID", COUNT(*) AS "N_IN_CFP",
         SUM(CASE WHEN s."BYE" THEN 1 ELSE 0 END) AS "N_BYE",
         SUM(CASE WHEN s."REACHED_ROUND" >= 2 THEN 1 ELSE 0 END) AS "N_QUARTERFINAL",
         SUM(CASE WHEN s."REACHED_ROUND" >= 3 THEN 1 ELSE 0 END) AS "N_SEMIFINAL",
         SUM(CASE WHEN s."REACHED_ROUND" >= 4 THEN 1 ELSE 0 END) AS "N_FINAL",
         SUM(CASE WHEN s."CHAMPION" THEN 1 ELSE 0 END) AS "N_CHAMPION",
         SUM(s."SEED") AS "SEED_SUM"
    FROM {SIM_SCHEMA}.SIM_TEAM s
   GROUP BY s."RUN_ID", s."TEAM_ID"
),
win_totals AS (
  SELECT w."RUN_ID", w."TEAM_ID", SUM(w."WINS" * w."N") AS "WIN_SUM"
    FROM {SIM_SCHEMA}.TEAM_WINS w
   GROUP BY w."RUN_ID", w."TEAM_ID"
),
titles AS (
  SELECT c."RUN_ID", c."TEAM_ID", SUM(c."N") AS "N_CONF_CHAMPION"
    FROM {SIM_SCHEMA}.CONF_CHAMP c
   GROUP BY c."RUN_ID", c."TEAM_ID"
),
crowning AS (
  SELECT t."RUN_ID", t."CONFERENCE"
    FROM {SIM_SCHEMA}.SIM_RUN_TEAM t
    JOIN {SIM_SCHEMA}.SIM_RUN_PARAM m
      ON m."RUN_ID" = t."RUN_ID"
     AND m."PARAM_KEY" = 'min_conference_teams'
   WHERE t."IS_ELIGIBLE"
     AND t."CONFERENCE" IS NOT NULL
   GROUP BY t."RUN_ID", t."CONFERENCE", m."PARAM_VALUE"
  HAVING COUNT(*) >= m."PARAM_VALUE"
),
odds AS (
  SELECT u."RUN_ID", u."AS_OF_SEASON", u."AS_OF_WEEK_SEQ", u."AS_OF_LABEL",
         t."TEAM_ID", t."SCHOOL", t."CONFERENCE", t."TIER",
         CAST(COALESCE(p."N_IN_CFP", 0) AS DOUBLE) / u."N_SIMS" AS "P_IN_CFP",
         CAST(COALESCE(p."N_BYE", 0) AS DOUBLE) / u."N_SIMS" AS "P_BYE",
         CAST(COALESCE(p."N_QUARTERFINAL", 0) AS DOUBLE) / u."N_SIMS" AS "P_QUARTERFINAL",
         CAST(COALESCE(p."N_SEMIFINAL", 0) AS DOUBLE) / u."N_SIMS" AS "P_SEMIFINAL",
         CAST(COALESCE(p."N_FINAL", 0) AS DOUBLE) / u."N_SIMS" AS "P_FINAL",
         CAST(COALESCE(p."N_CHAMPION", 0) AS DOUBLE) / u."N_SIMS" AS "P_CHAMPION",
         CASE WHEN k."CONFERENCE" IS NOT NULL
              THEN CAST(COALESCE(c."N_CONF_CHAMPION", 0) AS DOUBLE) / u."N_SIMS" END AS "P_CONF_CHAMPION",
         CAST(CAST(w."WIN_SUM" AS DOUBLE) / u."N_SIMS" AS DECIMAL(5,2)) AS "EXPECTED_WINS",
         CAST(CAST(p."SEED_SUM" AS DOUBLE) / p."N_IN_CFP" AS DECIMAL(5,2)) AS "MEAN_SEED"
    FROM runs u
    JOIN {SIM_SCHEMA}.SIM_RUN_TEAM t
      ON t."RUN_ID" = u."RUN_ID"
     AND t."IS_ELIGIBLE"
    LEFT JOIN playoff p
      ON p."RUN_ID" = t."RUN_ID" AND p."TEAM_ID" = t."TEAM_ID"
    LEFT JOIN win_totals w
      ON w."RUN_ID" = t."RUN_ID" AND w."TEAM_ID" = t."TEAM_ID"
    LEFT JOIN titles c
      ON c."RUN_ID" = t."RUN_ID" AND c."TEAM_ID" = t."TEAM_ID"
    LEFT JOIN crowning k
      ON k."RUN_ID" = t."RUN_ID" AND k."CONFERENCE" = t."CONFERENCE"
)
SELECT o."RUN_ID", o."AS_OF_SEASON", o."AS_OF_WEEK_SEQ", o."AS_OF_LABEL", o."TEAM_ID", o."SCHOOL",
       o."CONFERENCE", o."TIER",
       ROW_NUMBER() OVER (PARTITION BY o."RUN_ID"
                          ORDER BY o."P_IN_CFP" DESC, o."P_CHAMPION" DESC, o."TEAM_ID"),
       o."P_IN_CFP", o."P_BYE", o."P_QUARTERFINAL", o."P_SEMIFINAL", o."P_FINAL", o."P_CHAMPION",
       o."P_CONF_CHAMPION", o."EXPECTED_WINS", o."MEAN_SEED"
  FROM odds o
COMMENT IS 'One row per DONE run and eligible roster team: the probabilities of making the playoff, a bye, each round and the title, of winning the conference, the expected regular-season wins and the mean seed, as shares of the run''s simulated seasons, ranked within the run. Filter on RUN_ID.';
