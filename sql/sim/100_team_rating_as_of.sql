-- Team ratings as of a point (sim-engine/score-model): one row per team-season of TEAM_SEASON_AS_OF. A
-- rating blends the previous season's SP+ with the team's current Elo; the first rule that applies sets
-- RATING_SOURCE. Everything is read from CFB_MODEL's as-of views, so filter this view with literal
-- AS_OF_SEASON and AS_OF_WEEK_SEQ values (a join to CURRENT_AS_OF is not pushed down). The engine's
-- team labels (conference, P4/G6/IND/NON_FBS tier) and the records it needs are carried along, so every
-- consumer reads TEAM_SEASON_AS_OF once. The previous season's offense and defense SP+ come from RATING_SP.
CREATE OR REPLACE FORCE VIEW {SIM_SCHEMA}.TEAM_RATING_AS_OF (
  AS_OF_SEASON      COMMENT IS 'Season of the as-of point (AS_OF_POINT.AS_OF_SEASON); with AS_OF_WEEK_SEQ and TEAM_ID it identifies the row',
  AS_OF_WEEK_SEQ    COMMENT IS 'Week sequence of the point: 0 preseason, regular week n = n, postseason week n = 100 + n',
  TEAM_ID           COMMENT IS 'CFBD team id',
  SCHOOL            COMMENT IS 'CFBD school name (TEAM_SEASON_AS_OF.SCHOOL)',
  CLASSIFICATION    COMMENT IS 'Classification in the season: fbs, fcs, ii or iii; NULL when unknown (treated as not FBS)',
  CONFERENCE        COMMENT IS 'Engine conference: the season conference of a P4 or G6 team; NULL for FBS independents and for teams that are not FBS',
  TIER              COMMENT IS 'Engine tier: P4 or G6 (an FBS team of a conference, by power status), IND (an FBS independent) or NON_FBS',
  SP_PRIOR_RATING   COMMENT IS 'SP+ rating of the previous season (TEAM_SEASON_AS_OF.SP_PRIOR_RATING), in points; NULL when the team had none',
  SP_PRIOR_OFFENSE  COMMENT IS 'Offense SP+ rating of the previous season, in points; NULL when the team had none',
  SP_PRIOR_DEFENSE  COMMENT IS 'Defense SP+ rating of the previous season, in points (a lower value is a better defense); NULL when the team had none',
  ELO_AS_OF         COMMENT IS 'Elo of the team at the point (TEAM_SEASON_AS_OF.ELO_AS_OF); NULL when it has none',
  RATING            COMMENT IS 'Team rating in points against an average FBS team: B_SP x prior SP+ + B_ELO x (Elo - ELO_CENTER) with both, else the one-feature or default rule named by RATING_SOURCE',
  RATING_SOURCE     COMMENT IS 'Rule that set RATING: SP_ELO (prior SP+ and Elo), ELO (Elo only), SP (prior SP+ only) or DEFAULT (FBS_DEFAULT_RATING for an FBS team, else NON_FBS_RATING)',
  GAMES_PLAYED      COMMENT IS 'Games played up to the point (TEAM_SEASON_AS_OF.GAMES_PLAYED)',
  GAMES_REMAINING   COMMENT IS 'Games NEXT or REMAINING at the point (TEAM_SEASON_AS_OF.GAMES_REMAINING)',
  WINS              COMMENT IS 'Games won up to the point (TEAM_SEASON_AS_OF.WINS)',
  LOSSES            COMMENT IS 'Games lost up to the point (TEAM_SEASON_AS_OF.LOSSES)',
  CONF_WINS         COMMENT IS 'Conference games won up to the point, championship games excluded',
  CONF_LOSSES       COMMENT IS 'Conference games lost up to the point, championship games excluded',
  CONF_POINT_DIFF   COMMENT IS 'Points for minus against in the conference games that CONF_WINS and CONF_LOSSES count'
) AS
WITH previous_sp AS (
  SELECT r."SEASON", r."TEAM_ID", MAX(r."OFFENSE_RATING") AS "OFFENSE_RATING", MAX(r."DEFENSE_RATING") AS "DEFENSE_RATING"
    FROM {MODEL_SCHEMA}."RATING_SP" r
   WHERE r."TEAM_ID" IS NOT NULL
   GROUP BY r."SEASON", r."TEAM_ID"
),
labelled AS (
  SELECT t.*,
         CASE WHEN t."CLASSIFICATION" IS NULL OR t."CLASSIFICATION" <> 'fbs' THEN 'NON_FBS'
              WHEN t."CONFERENCE_TIER" = 'INDEPENDENT' OR t."CONFERENCE_NAME" IS NULL THEN 'IND'
              WHEN t."IS_POWER_CONFERENCE" THEN 'P4'
              ELSE 'G6' END AS "ENGINE_TIER"
    FROM {MODEL_SCHEMA}."TEAM_SEASON_AS_OF" t
)
SELECT t."AS_OF_SEASON",
       t."AS_OF_WEEK_SEQ",
       t."TEAM_ID",
       t."SCHOOL",
       t."CLASSIFICATION",
       CAST(CASE WHEN t."ENGINE_TIER" IN ('P4', 'G6') THEN t."CONFERENCE_NAME" END AS VARCHAR(100) UTF8),
       CAST(t."ENGINE_TIER" AS VARCHAR(8) ASCII),
       t."SP_PRIOR_RATING",
       pr."OFFENSE_RATING",
       pr."DEFENSE_RATING",
       t."ELO_AS_OF",
       CAST(CASE WHEN t."SP_PRIOR_RATING" IS NOT NULL AND t."ELO_AS_OF" IS NOT NULL
                 THEN p."B_SP" * t."SP_PRIOR_RATING" + p."B_ELO" * (t."ELO_AS_OF" - p."ELO_CENTER")
                 WHEN t."ELO_AS_OF" IS NOT NULL
                 THEN p."B_ELO_ONLY" * (t."ELO_AS_OF" - p."ELO_CENTER")
                 WHEN t."SP_PRIOR_RATING" IS NOT NULL
                 THEN p."B_SP_ONLY" * t."SP_PRIOR_RATING"
                 WHEN t."ENGINE_TIER" = 'NON_FBS' THEN p."NON_FBS_RATING"
                 ELSE p."FBS_DEFAULT_RATING" END AS DECIMAL(6,2)),
       CAST(CASE WHEN t."SP_PRIOR_RATING" IS NOT NULL AND t."ELO_AS_OF" IS NOT NULL THEN 'SP_ELO'
                 WHEN t."ELO_AS_OF" IS NOT NULL THEN 'ELO'
                 WHEN t."SP_PRIOR_RATING" IS NOT NULL THEN 'SP'
                 ELSE 'DEFAULT' END AS VARCHAR(8) ASCII),
       t."GAMES_PLAYED",
       t."GAMES_REMAINING",
       t."WINS",
       t."LOSSES",
       t."CONF_WINS",
       t."CONF_LOSSES",
       t."CONF_POINT_DIFF"
  FROM labelled t
 CROSS JOIN {SIM_SCHEMA}.SCORE_PARAMS p
  LEFT JOIN previous_sp pr
    ON pr."TEAM_ID" = t."TEAM_ID"
   AND pr."SEASON" = t."AS_OF_SEASON" - 1
COMMENT IS 'One row per as-of point and team-season of the point''s season (TEAM_SEASON_AS_OF), with its rating in points against an average FBS team: the previous season''s SP+ blended with the current Elo, one-feature and default rules when one is missing. Carries the engine''s conference and tier labels, the previous season''s offense and defense SP+ (for the predicted total) and the records the engine inputs need. Filter with literal AS_OF_SEASON and AS_OF_WEEK_SEQ.';
