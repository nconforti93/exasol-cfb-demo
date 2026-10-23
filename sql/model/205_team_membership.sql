-- Conference memberships per team and season (model/teams-conferences), games-free. TEAM_SEASON and
-- TEAM_SEASON_AS_OF both read it, so the membership rules exist once and the as-of view reads GAME only
-- once (through GAME_AS_OF; decision [33]: a view read twice in one query lost its season filter).
-- Every affiliation spell is expanded only over the seasons its slice requested (P_YEAR, else P_MIN_YEAR
-- to P_MAX_YEAR; an open spell of a request without a year runs to the year of its fetch), with a 300-row
-- VALUES list as the season generator (no SYS.DUAL dependency). Per team and season the newest fetch wins
-- (REQ_AT, then the later slice key); within it the later START_YEAR (then the later row) wins, and spells
-- of different conferences set HAS_MEMBERSHIP_CONFLICT.
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.TEAM_MEMBERSHIP (
  TEAM_ID                 COMMENT IS 'CFBD team id (TEAM.TEAM_ID); with SEASON it identifies the row',
  SEASON                  COMMENT IS 'Season the membership speaks for: inside the spell and inside the seasons its slice requested',
  SCHOOL                  COMMENT IS 'CFBD school name as the membership reports it',
  CONFERENCE_NAME         COMMENT IS 'Conference of the winning spell (CFBD name)',
  CONFERENCE_DIVISION     COMMENT IS 'Division within the conference of the winning spell; NULL when the conference had no divisions',
  CLASSIFICATION          COMMENT IS 'Classification code of the winning spell as CFBD reports it (fbs, fcs, ii or iii); it may be the conference''s code of today, so TEAM_SEASON prefers the team''s games',
  HAS_MEMBERSHIP_CONFLICT COMMENT IS 'TRUE when the newest membership fetch holds spells of different conferences for the season; the spell with the later start year won (MODEL_CHECKS: MEMBERSHIP_CONFLICT)',
  FETCHED_AT              COMMENT IS 'When the winning spell was fetched (REQ_AT, UTC)'
) AS
WITH requests AS (
  SELECT a."TEAM_ID", a."TEAM", a."CONFERENCE", a."CONFERENCE_DIVISION", a."CLASSIFICATION",
         a."START_YEAR", a."END_YEAR", a."REQ_AT", a."REQ_SLICE", a."ROW_NO",
         COALESCE(a."P_YEAR", a."P_MIN_YEAR", 1869) AS "ASKED_FROM",
         COALESCE(a."P_YEAR", a."P_MAX_YEAR", EXTRACT(YEAR FROM a."REQ_AT")) AS "ASKED_TO"
    FROM {CORE_SCHEMA}."CONFERENCES_AFFILIATIONS" a
   WHERE a."TEAM_ID" IS NOT NULL
),
spells AS (
  SELECT r.*,
         GREATEST(COALESCE(r."START_YEAR", r."ASKED_FROM"), r."ASKED_FROM") AS "FROM_SEASON",
         LEAST(COALESCE(r."END_YEAR", r."ASKED_TO"), r."ASKED_TO") AS "TO_SEASON"
    FROM requests r
),
numbers AS (
  SELECT u."N" + 10 * t."N" + 100 * h."N" AS "N"
    FROM (VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9)) AS u("N")
   CROSS JOIN (VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9)) AS t("N")
   CROSS JOIN (VALUES (0), (1), (2)) AS h("N")
),
spell_seasons AS (
  SELECT CAST(1869 + n."N" AS DECIMAL(4,0)) AS "SEASON", s.*
    FROM spells s
    JOIN numbers n
      ON 1869 + n."N" BETWEEN s."FROM_SEASON" AND s."TO_SEASON"
),
newest_fetch AS (
  SELECT s.*,
         DENSE_RANK() OVER (
           PARTITION BY s."TEAM_ID", s."SEASON"
           ORDER BY s."REQ_AT" DESC, s."REQ_SLICE" DESC) AS "FETCH_NO"
    FROM spell_seasons s
),
memberships AS (
  SELECT f.*,
         ROW_NUMBER() OVER (
           PARTITION BY f."TEAM_ID", f."SEASON"
           ORDER BY f."START_YEAR" DESC NULLS LAST, f."ROW_NO" DESC) AS "SPELL_NO",
         MIN(f."CONFERENCE") OVER (PARTITION BY f."TEAM_ID", f."SEASON") AS "LOW_NAME",
         MAX(f."CONFERENCE") OVER (PARTITION BY f."TEAM_ID", f."SEASON") AS "HIGH_NAME"
    FROM newest_fetch f
   WHERE f."FETCH_NO" = 1
)
SELECT m."TEAM_ID",
       m."SEASON",
       m."TEAM",
       m."CONFERENCE",
       m."CONFERENCE_DIVISION",
       m."CLASSIFICATION",
       CASE WHEN m."LOW_NAME" <> m."HIGH_NAME" THEN TRUE ELSE FALSE END,
       m."REQ_AT"
  FROM memberships m
 WHERE m."SPELL_NO" = 1
COMMENT IS 'One row per team and season with a stored conference membership whose slice speaks for the season (CFB_CORE.CONFERENCES_AFFILIATIONS): the conference, division and classification of the winning spell, and whether the newest fetch held spells of different conferences. A spell speaks only for the seasons its request asked for, so the static range slice never claims a season after a move. No games are read: the conference of a team without a membership, and the classification the model uses, are in TEAM_SEASON.';
