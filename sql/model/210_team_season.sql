-- Team-seasons (model/teams-conferences): one row per team id and season with a stored game or a
-- conference membership.
-- Memberships come from TEAM_MEMBERSHIP (the games-free rules: each slice speaks only for the seasons
-- it requested, the newest fetch wins, a later start year wins within it, conflicts are flagged), which
-- TEAM_SEASON_AS_OF shares.
-- Games: TEAM_GAME is read once. Conference and classification from games count only games with FIXED
-- participants (the code on most of them; a tie goes to the latest such game), so a championship or
-- postseason game, whose participants depend on results, never decides them. Records count every
-- completed game; conference records and CONF_POINT_DIFF leave out conference championship games.
-- Both sources are merged by a UNION ALL grouped by team and season, so a season filter reaches each
-- branch; every window sits in its own step and partitions by the season (decision [33]).
-- Classification: CLASSIFICATION_TRANSITION, then the FIXED games, then the membership. The tier is the
-- named CONFERENCE_TIER row, else the era's default row for a conference whose team-seasons with a
-- classification are mostly fbs that season. SP+ comes from RATING_SP (season and SEASON - 1, by team id;
-- unmatched names have no team id and never join), final ranks from RANKING's final polls.
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.TEAM_SEASON (
  TEAM_ID                       COMMENT IS 'CFBD team id (TEAM.TEAM_ID); with SEASON it identifies the row',
  SEASON                        COMMENT IS 'Season',
  SCHOOL                        COMMENT IS 'CFBD school name: the name in the stored team list (TEAM.SCHOOL), else the name on the team''s latest stored game of the season, else the name of its membership',
  CONFERENCE_NAME               COMMENT IS 'Conference in the season (CFBD name): the membership''s conference, else the one recorded on most of the team''s games with FIXED participants that season; NULL when neither exists (CONFERENCE_SOURCE NONE)',
  CONFERENCE_DIVISION           COMMENT IS 'Division within the conference in the season, from the membership; NULL without a membership or when the conference had no divisions',
  CONFERENCE_SOURCE             COMMENT IS 'Where the conference comes from: AFFILIATION (a stored membership whose slice speaks for the season), GAMES (no membership: the conference on most games with FIXED participants; championship and postseason games do not count) or NONE',
  HAS_MEMBERSHIP_CONFLICT       COMMENT IS 'TRUE when the newest membership fetch holds spells of different conferences for the season; the spell with the later start year won (MODEL_CHECKS: MEMBERSHIP_CONFLICT)',
  CONFERENCE_TIER               COMMENT IS 'Tier of the conference in the season from CONFERENCE_TIER: AQ or NON_AQ (1998-2013), P5 or G5 (2014-2023), P4 or G6 (2024 on), INDEPENDENT for FBS Independents (engine input conf_tier). A conference without a named row takes the era''s default tier when most of its team-seasons with a classification are fbs that season. NULL before 1998, for lower-division conferences and without a conference',
  IS_POWER_CONFERENCE           COMMENT IS 'TRUE for the tiers AQ, P5 and P4; FALSE for every other tier and without a tier',
  CLASSIFICATION                COMMENT IS 'CFBD classification code in the season: fbs, fcs, ii or iii, from the first rule that applies (CLASSIFICATION_SOURCE); NULL when none applies',
  CLASSIFICATION_SOURCE         COMMENT IS 'TRANSITION (a CLASSIFICATION_TRANSITION row for SCHOOL and the season), GAMES (the code on most of the team''s games with FIXED participants; a tie goes to the latest such game), AFFILIATION (the membership''s code) or NONE',
  CLASSIFICATION_LABEL          COMMENT IS 'Name of the classification at the time, from CLASSIFICATION_LABEL for the season''s division era, for example Division I-A in 1985 or Major college in 1950; NULL without a classification',
  IS_CLASSIFICATION_RETROACTIVE COMMENT IS 'TRUE when the classification code did not exist in the season (fbs and fcs before 1978, ii and iii before 1973); NULL without a classification',
  GAMES                         COMMENT IS 'Stored games of the team in the season, of any status, postseason included; 0 for a season known only from a membership',
  FIXED_GAMES                   COMMENT IS 'Of GAMES, those with FIXED participants (GAME.PARTICIPANTS_RULE), the games from which CONFERENCE_SOURCE GAMES and CLASSIFICATION_SOURCE GAMES come',
  WINS                          COMMENT IS 'Completed games won, postseason and every opponent included (engine input wins); 0 without completed games',
  LOSSES                        COMMENT IS 'Completed games lost, postseason and every opponent included (engine input losses); 0 without completed games',
  TIES                          COMMENT IS 'Completed games tied (possible at the FBS level before 1996); 0 without completed games',
  CONF_WINS                     COMMENT IS 'Completed conference games won (IS_CONFERENCE_GAME), without conference championship games (engine input conf_wins)',
  CONF_LOSSES                   COMMENT IS 'Completed conference games lost, without conference championship games (engine input conf_losses)',
  CONF_TIES                     COMMENT IS 'Completed conference games tied, without conference championship games',
  POINTS_FOR                    COMMENT IS 'Points scored in completed games; 0 without completed games',
  POINTS_AGAINST                COMMENT IS 'Points allowed in completed games; 0 without completed games',
  CONF_POINT_DIFF               COMMENT IS 'Points for minus points against in the completed conference games that CONF_WINS, CONF_LOSSES and CONF_TIES count, so without championship games (engine input conf_pd); 0 without them',
  FIRST_PREGAME_ELO             COMMENT IS 'CFBD pregame Elo of the team''s first game of the season that has one (by kickoff); NULL without Elo (CFBD has Elo from 1890)',
  LAST_POSTGAME_ELO             COMMENT IS 'CFBD postgame Elo of the team''s last completed game of the season that has one (by kickoff), postseason included',
  SP_RATING                     COMMENT IS 'SP+ rating of the season as last fetched (RATING_SP.SP_RATING), DECIMAL(6,2). It includes the season''s own games, so it is not a prior; NULL without a matched SP+ row',
  SP_PRIOR_RATING               COMMENT IS 'SP+ rating of the previous season as last fetched, final or not (RATING_SP of SEASON - 1), DECIMAL(6,2): the prior of the score model; NULL without one',
  HAS_SP_PRIOR                  COMMENT IS 'TRUE when the team has a matched SP+ row for the previous season',
  SP_PRIOR_IS_FINAL             COMMENT IS 'RATING_SP.IS_FINAL of the prior''s row: FALSE when the previous season''s slice is not final yet (MODEL_CHECKS: SP_PRIOR_NOT_FINAL); NULL without a prior',
  FINAL_AP_RANK                 COMMENT IS 'Rank in the season''s final AP poll (RANKING, IS_FINAL_POLL, the latest one); NULL when unranked or while the season runs',
  FINAL_CFP_RANK                COMMENT IS 'Rank in the committee''s final ranking of the season (RANKING, IS_FINAL_POLL); NULL when unranked and for a season whose regular-season weeks are not all done'
) AS
WITH votes AS (
  SELECT g."TEAM_ID", g."SEASON", g."GAME_ID", g."START_AT", g."TEAM_NAME", g."TEAM_CONFERENCE",
         g."TEAM_CLASSIFICATION", g."HAS_RESULT", g."GAME_RESULT", g."POINTS_FOR",
         g."POINTS_AGAINST", g."MARGIN", g."TEAM_PREGAME_ELO", g."TEAM_POSTGAME_ELO",
         CASE WHEN g."PARTICIPANTS_RULE" = 'FIXED' THEN 1 ELSE 0 END AS "IS_FIXED",
         CASE WHEN g."IS_CONFERENCE_GAME" AND NOT g."IS_CONF_CHAMPIONSHIP" THEN 1 ELSE 0 END
           AS "IN_CONFERENCE_RECORD",
         SUM(CASE WHEN g."PARTICIPANTS_RULE" = 'FIXED' AND g."TEAM_CONFERENCE" IS NOT NULL
                  THEN 1 ELSE 0 END)
           OVER (PARTITION BY g."TEAM_ID", g."SEASON", g."TEAM_CONFERENCE") AS "CONFERENCE_VOTES",
         SUM(CASE WHEN g."PARTICIPANTS_RULE" = 'FIXED' AND g."TEAM_CLASSIFICATION" IS NOT NULL
                  THEN 1 ELSE 0 END)
           OVER (PARTITION BY g."TEAM_ID", g."SEASON", g."TEAM_CLASSIFICATION")
           AS "CLASSIFICATION_VOTES"
    FROM {MODEL_SCHEMA}."TEAM_GAME" g
),
picks AS (
  SELECT v.*,
         ROW_NUMBER() OVER (
           PARTITION BY v."TEAM_ID", v."SEASON"
           ORDER BY CASE WHEN v."IS_FIXED" = 1 AND v."TEAM_CONFERENCE" IS NOT NULL THEN 0 ELSE 1 END,
                    v."CONFERENCE_VOTES" DESC, v."START_AT" DESC NULLS LAST, v."GAME_ID" DESC)
           AS "CONFERENCE_NO",
         ROW_NUMBER() OVER (
           PARTITION BY v."TEAM_ID", v."SEASON"
           ORDER BY CASE WHEN v."IS_FIXED" = 1 AND v."TEAM_CLASSIFICATION" IS NOT NULL THEN 0 ELSE 1 END,
                    v."CLASSIFICATION_VOTES" DESC, v."START_AT" DESC NULLS LAST, v."GAME_ID" DESC)
           AS "CLASSIFICATION_NO",
         ROW_NUMBER() OVER (
           PARTITION BY v."TEAM_ID", v."SEASON"
           ORDER BY CASE WHEN v."TEAM_PREGAME_ELO" IS NULL THEN 1 ELSE 0 END,
                    v."START_AT" NULLS FIRST, v."GAME_ID") AS "FIRST_ELO_NO",
         ROW_NUMBER() OVER (
           PARTITION BY v."TEAM_ID", v."SEASON"
           ORDER BY CASE WHEN v."TEAM_POSTGAME_ELO" IS NULL THEN 1 ELSE 0 END,
                    v."START_AT" DESC NULLS LAST, v."GAME_ID" DESC) AS "LAST_ELO_NO",
         ROW_NUMBER() OVER (
           PARTITION BY v."TEAM_ID", v."SEASON"
           ORDER BY v."START_AT" DESC NULLS LAST, v."GAME_ID" DESC) AS "LATEST_NO"
    FROM votes v
),
game_seasons AS (
  SELECT p."TEAM_ID", p."SEASON",
         COUNT(*) AS "GAMES",
         SUM(p."IS_FIXED") AS "FIXED_GAMES",
         MAX(CASE WHEN p."LATEST_NO" = 1 THEN p."TEAM_NAME" END) AS "GAME_NAME",
         MAX(CASE WHEN p."CONFERENCE_NO" = 1 AND p."IS_FIXED" = 1 THEN p."TEAM_CONFERENCE" END)
           AS "GAME_CONFERENCE",
         MAX(CASE WHEN p."CLASSIFICATION_NO" = 1 AND p."IS_FIXED" = 1
                  THEN p."TEAM_CLASSIFICATION" END) AS "GAME_CLASSIFICATION",
         SUM(CASE WHEN p."GAME_RESULT" = 'W' THEN 1 ELSE 0 END) AS "WINS",
         SUM(CASE WHEN p."GAME_RESULT" = 'L' THEN 1 ELSE 0 END) AS "LOSSES",
         SUM(CASE WHEN p."GAME_RESULT" = 'T' THEN 1 ELSE 0 END) AS "TIES",
         SUM(CASE WHEN p."GAME_RESULT" = 'W' AND p."IN_CONFERENCE_RECORD" = 1 THEN 1 ELSE 0 END)
           AS "CONF_WINS",
         SUM(CASE WHEN p."GAME_RESULT" = 'L' AND p."IN_CONFERENCE_RECORD" = 1 THEN 1 ELSE 0 END)
           AS "CONF_LOSSES",
         SUM(CASE WHEN p."GAME_RESULT" = 'T' AND p."IN_CONFERENCE_RECORD" = 1 THEN 1 ELSE 0 END)
           AS "CONF_TIES",
         SUM(CASE WHEN p."HAS_RESULT" THEN p."POINTS_FOR" ELSE 0 END) AS "POINTS_FOR",
         SUM(CASE WHEN p."HAS_RESULT" THEN p."POINTS_AGAINST" ELSE 0 END) AS "POINTS_AGAINST",
         SUM(CASE WHEN p."HAS_RESULT" AND p."IN_CONFERENCE_RECORD" = 1 THEN p."MARGIN" ELSE 0 END)
           AS "CONF_POINT_DIFF",
         MAX(CASE WHEN p."FIRST_ELO_NO" = 1 THEN p."TEAM_PREGAME_ELO" END) AS "FIRST_PREGAME_ELO",
         MAX(CASE WHEN p."LAST_ELO_NO" = 1 THEN p."TEAM_POSTGAME_ELO" END) AS "LAST_POSTGAME_ELO"
    FROM picks p
   GROUP BY p."TEAM_ID", p."SEASON"
),
facts AS (
  SELECT m."TEAM_ID", m."SEASON",
         1 AS "IS_MEMBER", m."SCHOOL" AS "MEMBER_NAME", m."CONFERENCE_NAME" AS "MEMBER_CONFERENCE",
         m."CONFERENCE_DIVISION" AS "MEMBER_DIVISION", m."CLASSIFICATION" AS "MEMBER_CLASSIFICATION",
         CASE WHEN m."HAS_MEMBERSHIP_CONFLICT" THEN 1 ELSE 0 END AS "MEMBER_CONFLICT",
         NULL AS "GAMES", NULL AS "FIXED_GAMES", NULL AS "GAME_NAME", NULL AS "GAME_CONFERENCE",
         NULL AS "GAME_CLASSIFICATION", NULL AS "WINS", NULL AS "LOSSES", NULL AS "TIES",
         NULL AS "CONF_WINS", NULL AS "CONF_LOSSES", NULL AS "CONF_TIES", NULL AS "POINTS_FOR",
         NULL AS "POINTS_AGAINST", NULL AS "CONF_POINT_DIFF", NULL AS "FIRST_PREGAME_ELO",
         NULL AS "LAST_POSTGAME_ELO"
    FROM {MODEL_SCHEMA}."TEAM_MEMBERSHIP" m
  UNION ALL
  SELECT g."TEAM_ID", CAST(g."SEASON" AS DECIMAL(4,0)),
         0, NULL, NULL, NULL, NULL, NULL,
         g."GAMES", g."FIXED_GAMES", g."GAME_NAME", g."GAME_CONFERENCE", g."GAME_CLASSIFICATION",
         g."WINS", g."LOSSES", g."TIES", g."CONF_WINS", g."CONF_LOSSES", g."CONF_TIES",
         g."POINTS_FOR", g."POINTS_AGAINST", g."CONF_POINT_DIFF", g."FIRST_PREGAME_ELO",
         g."LAST_POSTGAME_ELO"
    FROM game_seasons g
),
merged AS (
  SELECT x."TEAM_ID", x."SEASON",
         MAX(x."IS_MEMBER") AS "IS_MEMBER", MAX(x."MEMBER_NAME") AS "MEMBER_NAME",
         MAX(x."MEMBER_CONFERENCE") AS "MEMBER_CONFERENCE",
         MAX(x."MEMBER_DIVISION") AS "MEMBER_DIVISION",
         MAX(x."MEMBER_CLASSIFICATION") AS "MEMBER_CLASSIFICATION",
         MAX(x."MEMBER_CONFLICT") AS "MEMBER_CONFLICT",
         MAX(x."GAMES") AS "GAMES", MAX(x."FIXED_GAMES") AS "FIXED_GAMES",
         MAX(x."GAME_NAME") AS "GAME_NAME", MAX(x."GAME_CONFERENCE") AS "GAME_CONFERENCE",
         MAX(x."GAME_CLASSIFICATION") AS "GAME_CLASSIFICATION",
         MAX(x."WINS") AS "WINS", MAX(x."LOSSES") AS "LOSSES", MAX(x."TIES") AS "TIES",
         MAX(x."CONF_WINS") AS "CONF_WINS", MAX(x."CONF_LOSSES") AS "CONF_LOSSES",
         MAX(x."CONF_TIES") AS "CONF_TIES", MAX(x."POINTS_FOR") AS "POINTS_FOR",
         MAX(x."POINTS_AGAINST") AS "POINTS_AGAINST", MAX(x."CONF_POINT_DIFF") AS "CONF_POINT_DIFF",
         MAX(x."FIRST_PREGAME_ELO") AS "FIRST_PREGAME_ELO",
         MAX(x."LAST_POSTGAME_ELO") AS "LAST_POSTGAME_ELO"
    FROM facts x
   GROUP BY x."TEAM_ID", x."SEASON"
),
listed AS (
  SELECT t."ID", t."SCHOOL"
    FROM {CORE_SCHEMA}."TEAMS" t
 QUALIFY ROW_NUMBER() OVER (
           PARTITION BY t."ID"
           ORDER BY t."REQ_AT" DESC, t."REQ_SLICE" DESC, t."ROW_NO" DESC) = 1
),
named AS (
  SELECT m.*,
         COALESCE(l."SCHOOL", m."GAME_NAME", m."MEMBER_NAME") AS "SCHOOL",
         CASE WHEN m."IS_MEMBER" = 1 THEN m."MEMBER_CONFERENCE" ELSE m."GAME_CONFERENCE" END
           AS "CONFERENCE_NAME"
    FROM merged m
    LEFT JOIN listed l
      ON l."ID" = m."TEAM_ID"
),
classified AS (
  SELECT n.*,
         CASE WHEN tr."CLASSIFICATION" IS NOT NULL THEN tr."CLASSIFICATION"
              WHEN n."GAME_CLASSIFICATION" IS NOT NULL THEN n."GAME_CLASSIFICATION"
              WHEN n."IS_MEMBER" = 1 THEN n."MEMBER_CLASSIFICATION" END AS "CLASSIFICATION",
         CASE WHEN tr."CLASSIFICATION" IS NOT NULL THEN 'TRANSITION'
              WHEN n."GAME_CLASSIFICATION" IS NOT NULL THEN 'GAMES'
              WHEN n."IS_MEMBER" = 1 AND n."MEMBER_CLASSIFICATION" IS NOT NULL THEN 'AFFILIATION'
              ELSE 'NONE' END AS "CLASSIFICATION_SOURCE"
    FROM named n
    LEFT JOIN {MODEL_SCHEMA}."CLASSIFICATION_TRANSITION" tr
      ON tr."TEAM_NAME" = n."SCHOOL"
     AND n."SEASON" BETWEEN tr."FIRST_SEASON" AND tr."LAST_SEASON"
),
conference_shares AS (
  SELECT c.*,
         SUM(CASE WHEN c."CLASSIFICATION" = 'fbs' THEN 1 ELSE 0 END)
           OVER (PARTITION BY c."SEASON", c."CONFERENCE_NAME") AS "FBS_MEMBERS",
         COUNT(c."CLASSIFICATION") OVER (PARTITION BY c."SEASON", c."CONFERENCE_NAME")
           AS "CLASSIFIED_MEMBERS"
    FROM classified c
),
final_polls AS (
  SELECT k."SEASON", k."TEAM_ID", k."POLL_CODE", k."POLL_RANK"
    FROM {MODEL_SCHEMA}."RANKING" k
   WHERE k."IS_FINAL_POLL" AND k."POLL_CODE" IN ('AP', 'CFP') AND k."TEAM_ID" IS NOT NULL
 QUALIFY ROW_NUMBER() OVER (
           PARTITION BY k."SEASON", k."TEAM_ID", k."POLL_CODE"
           ORDER BY k."WEEK_SEQ" DESC, k."FETCHED_AT" DESC) = 1
),
final_ranks AS (
  SELECT f."SEASON", f."TEAM_ID",
         MAX(CASE WHEN f."POLL_CODE" = 'AP' THEN f."POLL_RANK" END) AS "AP_RANK",
         MAX(CASE WHEN f."POLL_CODE" = 'CFP' THEN f."POLL_RANK" END) AS "CFP_RANK"
    FROM final_polls f
   GROUP BY f."SEASON", f."TEAM_ID"
),
ratings AS (
  SELECT r."SEASON", r."TEAM_ID", r."SP_RATING", r."IS_FINAL"
    FROM {MODEL_SCHEMA}."RATING_SP" r
   WHERE r."TEAM_ID" IS NOT NULL
)
SELECT s."TEAM_ID",
       s."SEASON",
       s."SCHOOL",
       s."CONFERENCE_NAME",
       CASE WHEN s."IS_MEMBER" = 1 THEN s."MEMBER_DIVISION" END,
       CASE WHEN s."IS_MEMBER" = 1 THEN 'AFFILIATION'
            WHEN s."CONFERENCE_NAME" IS NOT NULL THEN 'GAMES'
            ELSE 'NONE' END,
       COALESCE(s."MEMBER_CONFLICT", 0) = 1,
       COALESCE(tn."TIER", td."TIER"),
       COALESCE(tn."IS_POWER_CONFERENCE", td."IS_POWER_CONFERENCE", FALSE),
       s."CLASSIFICATION",
       s."CLASSIFICATION_SOURCE",
       cl."LABEL",
       cl."IS_RETROACTIVE",
       CAST(COALESCE(s."GAMES", 0) AS DECIMAL(4,0)),
       CAST(COALESCE(s."FIXED_GAMES", 0) AS DECIMAL(4,0)),
       CAST(COALESCE(s."WINS", 0) AS DECIMAL(4,0)),
       CAST(COALESCE(s."LOSSES", 0) AS DECIMAL(4,0)),
       CAST(COALESCE(s."TIES", 0) AS DECIMAL(4,0)),
       CAST(COALESCE(s."CONF_WINS", 0) AS DECIMAL(4,0)),
       CAST(COALESCE(s."CONF_LOSSES", 0) AS DECIMAL(4,0)),
       CAST(COALESCE(s."CONF_TIES", 0) AS DECIMAL(4,0)),
       CAST(COALESCE(s."POINTS_FOR", 0) AS DECIMAL(9,0)),
       CAST(COALESCE(s."POINTS_AGAINST", 0) AS DECIMAL(9,0)),
       CAST(COALESCE(s."CONF_POINT_DIFF", 0) AS DECIMAL(9,0)),
       CAST(s."FIRST_PREGAME_ELO" AS DECIMAL(9,0)),
       CAST(s."LAST_POSTGAME_ELO" AS DECIMAL(9,0)),
       sp."SP_RATING",
       pr."SP_RATING",
       pr."TEAM_ID" IS NOT NULL,
       pr."IS_FINAL",
       CAST(fr."AP_RANK" AS DECIMAL(4,0)),
       CAST(fr."CFP_RANK" AS DECIMAL(4,0))
  FROM conference_shares s
  LEFT JOIN {MODEL_SCHEMA}."CONFERENCE_TIER" tn
    ON tn."CONFERENCE_NAME" = s."CONFERENCE_NAME"
   AND s."SEASON" BETWEEN tn."FIRST_SEASON" AND COALESCE(tn."LAST_SEASON", s."SEASON")
  LEFT JOIN {MODEL_SCHEMA}."CONFERENCE_TIER" td
    ON td."CONFERENCE_NAME" IS NULL
   AND s."CONFERENCE_NAME" IS NOT NULL
   AND 2 * s."FBS_MEMBERS" > s."CLASSIFIED_MEMBERS"
   AND s."SEASON" BETWEEN td."FIRST_SEASON" AND COALESCE(td."LAST_SEASON", s."SEASON")
  LEFT JOIN {MODEL_SCHEMA}."ERA" e
    ON e."ERA_KIND" = 'DIVISION'
   AND s."SEASON" BETWEEN e."FIRST_SEASON" AND COALESCE(e."LAST_SEASON", s."SEASON")
  LEFT JOIN {MODEL_SCHEMA}."CLASSIFICATION_LABEL" cl
    ON cl."DIVISION_ERA" = e."ERA_CODE"
   AND cl."CLASSIFICATION" = s."CLASSIFICATION"
  LEFT JOIN ratings sp
    ON sp."TEAM_ID" = s."TEAM_ID"
   AND sp."SEASON" = s."SEASON"
  LEFT JOIN ratings pr
    ON pr."TEAM_ID" = s."TEAM_ID"
   AND pr."SEASON" = s."SEASON" - 1
  LEFT JOIN final_ranks fr
    ON fr."TEAM_ID" = s."TEAM_ID"
   AND fr."SEASON" = s."SEASON"
COMMENT IS 'One row per team and season in which the team has a stored game or a conference membership. Membership spells speak only for the seasons their slice requested, the newest fetch wins, and same-fetch spells of different conferences are flagged. Without a membership the conference comes from the team''s games with FIXED participants. Classification: CLASSIFICATION_TRANSITION, then those games, then the membership, with the division name of the time; the power tier of the era from CONFERENCE_TIER. Season records (all completed games) and conference records without championship games, points and CONF_POINT_DIFF, first pregame and last postgame Elo, SP+ of the season and the prior (SEASON - 1) as fixed-scale DECIMAL, and the final AP and committee ranks. Every column is the whole stored season; for the state as of a week use TEAM_SEASON_AS_OF. A conference season is a GROUP BY over this view.';
