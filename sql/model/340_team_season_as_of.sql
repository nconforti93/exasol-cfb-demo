-- Team-seasons as of a point (model/as-of). It does not read TEAM_SEASON: that view reads GAME through
-- TEAM_GAME, and a second read of GAME in one query lost the season filter (decision [33]; TEAM_SEASON
-- alone takes about 2 s for one season on AWS). Instead GAME is read once, through GAME_AS_OF:
-- - Games: every row of GAME_AS_OF from each named team's side (a two-row VALUES list, as in TEAM_GAME).
--   Records, points, the remaining and next games and Elo use the point's masked values. Conference and
--   classification from games count only games with FIXED participants, which are rows of GAME_AS_OF at
--   every point with their schedule facts, so they equal TEAM_SEASON's (same rules: the code on most of
--   them, a tie goes to the latest such game; records leave out nothing, conference records leave out
--   championship games).
-- - Memberships: TEAM_MEMBERSHIP (games-free) joined to AS_OF_POINT on the season.
-- Both are merged by a UNION ALL grouped by point and team, so a point filter reaches each branch; then
-- the classification precedence (CLASSIFICATION_TRANSITION, the FIXED games, the membership) and the tier
-- (named CONFERENCE_TIER row, else the era's default for a conference whose team-seasons with a
-- classification are mostly fbs) follow TEAM_SEASON. Kept: team-seasons with a membership or a FIXED game.
-- Ranks come from RANKING_AS_OF, SP+ from RATING_SP: the prior (season - 1) always, the season's own
-- rating when fetched before CUTOFF_AT, or at the last point (no cutoff) when its slice is final.
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.TEAM_SEASON_AS_OF (
  AS_OF_SEASON          COMMENT IS 'Season of the as-of point (AS_OF_POINT.AS_OF_SEASON); the team-season''s season',
  AS_OF_WEEK_SEQ        COMMENT IS 'Week sequence of the point: 0 preseason, regular week n = n, postseason week n = 100 + n',
  TEAM_ID               COMMENT IS 'CFBD team id; with the point it identifies the row (engine TEAM rows)',
  SCHOOL                COMMENT IS 'CFBD school name: the team-list name, else the name on the team''s latest game with FIXED participants, else the membership''s',
  CONFERENCE_NAME       COMMENT IS 'Conference in the season (CFBD name; engine input conf): the membership''s, else the one recorded on most of the team''s games with FIXED participants; NULL without either. Equal to TEAM_SEASON.CONFERENCE_NAME',
  CONFERENCE_DIVISION   COMMENT IS 'Division within the conference, from the membership; NULL without one',
  CONFERENCE_TIER       COMMENT IS 'Tier of the conference in the season (engine input conf_tier): AQ or NON_AQ (1998-2013), P5 or G5 (2014-2023), P4 or G6 (2024 on), INDEPENDENT for FBS Independents; NULL before 1998 and for lower divisions. Equal to TEAM_SEASON.CONFERENCE_TIER',
  IS_POWER_CONFERENCE   COMMENT IS 'TRUE for the tiers AQ, P5 and P4',
  CLASSIFICATION        COMMENT IS 'Classification in the season: fbs, fcs, ii or iii (CLASSIFICATION_TRANSITION, then the FIXED games, then the membership); the engine''s teams are the fbs rows. Equal to TEAM_SEASON.CLASSIFICATION',
  GAMES_PLAYED          COMMENT IS 'Games PLAYED at the point with a result (WINS + LOSSES + TIES)',
  WINS                  COMMENT IS 'Games won up to the point, every opponent and the postseason included (engine input wins)',
  LOSSES                COMMENT IS 'Games lost up to the point (engine input losses)',
  TIES                  COMMENT IS 'Games tied up to the point (possible at the FBS level before 1996)',
  CONF_WINS             COMMENT IS 'Conference games won up to the point (IS_CONFERENCE_GAME), without conference championship games (engine input conf_wins)',
  CONF_LOSSES           COMMENT IS 'Conference games lost up to the point, without conference championship games (engine input conf_losses)',
  CONF_TIES             COMMENT IS 'Conference games tied up to the point, without conference championship games',
  POINTS_FOR            COMMENT IS 'Points scored in the games PLAYED up to the point',
  POINTS_AGAINST        COMMENT IS 'Points allowed in the games PLAYED up to the point',
  CONF_POINT_DIFF       COMMENT IS 'Points for minus against in the conference games that CONF_WINS and CONF_LOSSES count (engine input conf_pd)',
  GAMES_REMAINING       COMMENT IS 'Games of the team that are NEXT or REMAINING at the point, counted over the rows of GAME_AS_OF only: a game whose participants are not known yet is not counted',
  NEXT_GAME_ID          COMMENT IS 'CFBD game id of the team''s first NEXT or REMAINING game by kickoff; NULL when none is left',
  ELO_AS_OF             COMMENT IS 'Team Elo at the point (integer): the postgame Elo of its last PLAYED game that has one, else the pregame Elo of its first game of the season (which depends only on earlier seasons) when visible at the point; NULL otherwise',
  ELO_SOURCE            COMMENT IS 'Where ELO_AS_OF comes from: POSTGAME, PRESEASON or NONE',
  AP_RANK               COMMENT IS 'Rank in the latest AP poll visible at the point (RANKING_AS_OF); NULL when unranked',
  COACHES_RANK          COMMENT IS 'Rank in the latest Coaches poll visible at the point; NULL when unranked',
  CFP_RANK              COMMENT IS 'Rank in the latest committee ranking visible at the point; NULL when unranked or before the first one',
  SP_PRIOR_RATING       COMMENT IS 'SP+ rating of the previous season as last fetched, final or not (RATING_SP of AS_OF_SEASON - 1), DECIMAL(6,2): the prior of the score model; it is in the past at every point',
  SP_CURRENT_RATING     COMMENT IS 'SP+ rating of the season, DECIMAL(6,2), only when visible at the point: fetched before CUTOFF_AT, or at the last point of the season when its slice is final; NULL otherwise',
  SP_CURRENT_FETCHED_AT COMMENT IS 'When SP_CURRENT_RATING was fetched (UTC); NULL when it is not visible'
) AS
WITH sides AS (
  SELECT g."AS_OF_SEASON", g."AS_OF_WEEK_SEQ", g."CUTOFF_AT", g."GAME_ID", g."START_AT",
         g."GAME_STATUS_AS_OF" AS "STATUS",
         CASE WHEN g."PARTICIPANTS_RULE" = 'FIXED' THEN 1 ELSE 0 END AS "IS_FIXED",
         CASE WHEN g."IS_CONFERENCE_GAME" AND NOT g."IS_CONF_CHAMPIONSHIP" THEN 1 ELSE 0 END
           AS "IN_CONFERENCE_RECORD",
         CASE WHEN x."SIDE" = 1 THEN g."HOME_TEAM_ID" ELSE g."AWAY_TEAM_ID" END AS "TEAM_ID",
         CASE WHEN x."SIDE" = 1 THEN g."HOME_TEAM" ELSE g."AWAY_TEAM" END AS "TEAM_NAME",
         CASE WHEN x."SIDE" = 1 THEN g."HOME_CONFERENCE" ELSE g."AWAY_CONFERENCE" END
           AS "TEAM_CONFERENCE",
         CASE WHEN x."SIDE" = 1 THEN g."HOME_CLASSIFICATION" ELSE g."AWAY_CLASSIFICATION" END
           AS "TEAM_CLASSIFICATION",
         CASE WHEN x."SIDE" = 1 THEN g."HOME_POINTS" ELSE g."AWAY_POINTS" END AS "POINTS_FOR",
         CASE WHEN x."SIDE" = 1 THEN g."AWAY_POINTS" ELSE g."HOME_POINTS" END AS "POINTS_AGAINST",
         CASE WHEN x."SIDE" = 1 THEN g."HOME_PREGAME_ELO" ELSE g."AWAY_PREGAME_ELO" END
           AS "PREGAME_ELO",
         CASE WHEN x."SIDE" = 1 THEN g."HOME_POSTGAME_ELO" ELSE g."AWAY_POSTGAME_ELO" END
           AS "POSTGAME_ELO"
    FROM {MODEL_SCHEMA}."GAME_AS_OF" g
   CROSS JOIN (VALUES (1), (2)) AS x("SIDE")
   WHERE (x."SIDE" = 1 AND g."HOME_TEAM_ID" IS NOT NULL)
      OR (x."SIDE" = 2 AND g."AWAY_TEAM_ID" IS NOT NULL)
),
results AS (
  SELECT s.*,
         CASE WHEN s."STATUS" = 'PLAYED' AND s."POINTS_FOR" IS NOT NULL
               AND s."POINTS_AGAINST" IS NOT NULL
              THEN CASE WHEN s."POINTS_FOR" > s."POINTS_AGAINST" THEN 'W'
                        WHEN s."POINTS_FOR" < s."POINTS_AGAINST" THEN 'L'
                        ELSE 'T' END END AS "GAME_RESULT",
         SUM(CASE WHEN s."IS_FIXED" = 1 AND s."TEAM_CONFERENCE" IS NOT NULL THEN 1 ELSE 0 END)
           OVER (PARTITION BY s."AS_OF_SEASON", s."AS_OF_WEEK_SEQ", s."TEAM_ID", s."TEAM_CONFERENCE")
           AS "CONFERENCE_VOTES",
         SUM(CASE WHEN s."IS_FIXED" = 1 AND s."TEAM_CLASSIFICATION" IS NOT NULL THEN 1 ELSE 0 END)
           OVER (PARTITION BY s."AS_OF_SEASON", s."AS_OF_WEEK_SEQ", s."TEAM_ID",
                              s."TEAM_CLASSIFICATION") AS "CLASSIFICATION_VOTES"
    FROM sides s
),
picks AS (
  SELECT r.*,
         ROW_NUMBER() OVER (
           PARTITION BY r."AS_OF_SEASON", r."AS_OF_WEEK_SEQ", r."TEAM_ID"
           ORDER BY CASE WHEN r."IS_FIXED" = 1 AND r."TEAM_CONFERENCE" IS NOT NULL THEN 0 ELSE 1 END,
                    r."CONFERENCE_VOTES" DESC, r."START_AT" DESC NULLS LAST, r."GAME_ID" DESC)
           AS "CONFERENCE_NO",
         ROW_NUMBER() OVER (
           PARTITION BY r."AS_OF_SEASON", r."AS_OF_WEEK_SEQ", r."TEAM_ID"
           ORDER BY CASE WHEN r."IS_FIXED" = 1 AND r."TEAM_CLASSIFICATION" IS NOT NULL THEN 0 ELSE 1 END,
                    r."CLASSIFICATION_VOTES" DESC, r."START_AT" DESC NULLS LAST, r."GAME_ID" DESC)
           AS "CLASSIFICATION_NO",
         ROW_NUMBER() OVER (
           PARTITION BY r."AS_OF_SEASON", r."AS_OF_WEEK_SEQ", r."TEAM_ID"
           ORDER BY r."IS_FIXED" DESC, r."START_AT" DESC NULLS LAST, r."GAME_ID" DESC)
           AS "LATEST_FIXED_NO",
         ROW_NUMBER() OVER (
           PARTITION BY r."AS_OF_SEASON", r."AS_OF_WEEK_SEQ", r."TEAM_ID"
           ORDER BY CASE WHEN r."STATUS" = 'PLAYED' AND r."POSTGAME_ELO" IS NOT NULL THEN 0 ELSE 1 END,
                    r."START_AT" DESC NULLS LAST, r."GAME_ID" DESC) AS "LAST_ELO_NO",
         ROW_NUMBER() OVER (
           PARTITION BY r."AS_OF_SEASON", r."AS_OF_WEEK_SEQ", r."TEAM_ID"
           ORDER BY r."START_AT" NULLS FIRST, r."GAME_ID") AS "FIRST_GAME_NO",
         ROW_NUMBER() OVER (
           PARTITION BY r."AS_OF_SEASON", r."AS_OF_WEEK_SEQ", r."TEAM_ID"
           ORDER BY CASE WHEN r."STATUS" IN ('NEXT', 'REMAINING') THEN 0 ELSE 1 END,
                    r."START_AT" NULLS LAST, r."GAME_ID") AS "NEXT_NO"
    FROM results r
),
game_points AS (
  SELECT p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", p."TEAM_ID",
         MAX(p."CUTOFF_AT") AS "CUTOFF_AT",
         SUM(p."IS_FIXED") AS "FIXED_GAMES",
         MAX(CASE WHEN p."LATEST_FIXED_NO" = 1 AND p."IS_FIXED" = 1 THEN p."TEAM_NAME" END)
           AS "GAME_NAME",
         MAX(CASE WHEN p."CONFERENCE_NO" = 1 AND p."IS_FIXED" = 1 THEN p."TEAM_CONFERENCE" END)
           AS "GAME_CONFERENCE",
         MAX(CASE WHEN p."CLASSIFICATION_NO" = 1 AND p."IS_FIXED" = 1
                  THEN p."TEAM_CLASSIFICATION" END) AS "GAME_CLASSIFICATION",
         SUM(CASE WHEN p."GAME_RESULT" IS NOT NULL THEN 1 ELSE 0 END) AS "GAMES_PLAYED",
         SUM(CASE WHEN p."GAME_RESULT" = 'W' THEN 1 ELSE 0 END) AS "WINS",
         SUM(CASE WHEN p."GAME_RESULT" = 'L' THEN 1 ELSE 0 END) AS "LOSSES",
         SUM(CASE WHEN p."GAME_RESULT" = 'T' THEN 1 ELSE 0 END) AS "TIES",
         SUM(CASE WHEN p."GAME_RESULT" = 'W' AND p."IN_CONFERENCE_RECORD" = 1 THEN 1 ELSE 0 END)
           AS "CONF_WINS",
         SUM(CASE WHEN p."GAME_RESULT" = 'L' AND p."IN_CONFERENCE_RECORD" = 1 THEN 1 ELSE 0 END)
           AS "CONF_LOSSES",
         SUM(CASE WHEN p."GAME_RESULT" = 'T' AND p."IN_CONFERENCE_RECORD" = 1 THEN 1 ELSE 0 END)
           AS "CONF_TIES",
         SUM(CASE WHEN p."GAME_RESULT" IS NOT NULL THEN p."POINTS_FOR" ELSE 0 END) AS "POINTS_FOR",
         SUM(CASE WHEN p."GAME_RESULT" IS NOT NULL THEN p."POINTS_AGAINST" ELSE 0 END)
           AS "POINTS_AGAINST",
         SUM(CASE WHEN p."GAME_RESULT" IS NOT NULL AND p."IN_CONFERENCE_RECORD" = 1
                  THEN p."POINTS_FOR" - p."POINTS_AGAINST" ELSE 0 END) AS "CONF_POINT_DIFF",
         SUM(CASE WHEN p."STATUS" IN ('NEXT', 'REMAINING') THEN 1 ELSE 0 END) AS "GAMES_REMAINING",
         MAX(CASE WHEN p."NEXT_NO" = 1 AND p."STATUS" IN ('NEXT', 'REMAINING') THEN p."GAME_ID" END)
           AS "NEXT_GAME_ID",
         MAX(CASE WHEN p."LAST_ELO_NO" = 1 AND p."STATUS" = 'PLAYED' THEN p."POSTGAME_ELO" END)
           AS "LAST_POSTGAME_ELO",
         MAX(CASE WHEN p."FIRST_GAME_NO" = 1 THEN p."PREGAME_ELO" END) AS "FIRST_PREGAME_ELO"
    FROM picks p
   GROUP BY p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", p."TEAM_ID"
),
facts AS (
  SELECT q."AS_OF_SEASON", q."AS_OF_WEEK_SEQ", m."TEAM_ID", q."CUTOFF_AT",
         1 AS "IS_MEMBER", m."SCHOOL" AS "MEMBER_NAME", m."CONFERENCE_NAME" AS "MEMBER_CONFERENCE",
         m."CONFERENCE_DIVISION" AS "MEMBER_DIVISION", m."CLASSIFICATION" AS "MEMBER_CLASSIFICATION",
         NULL AS "FIXED_GAMES", NULL AS "GAME_NAME", NULL AS "GAME_CONFERENCE",
         NULL AS "GAME_CLASSIFICATION", NULL AS "GAMES_PLAYED", NULL AS "WINS", NULL AS "LOSSES",
         NULL AS "TIES", NULL AS "CONF_WINS", NULL AS "CONF_LOSSES", NULL AS "CONF_TIES",
         NULL AS "POINTS_FOR", NULL AS "POINTS_AGAINST", NULL AS "CONF_POINT_DIFF",
         NULL AS "GAMES_REMAINING", NULL AS "NEXT_GAME_ID", NULL AS "LAST_POSTGAME_ELO",
         NULL AS "FIRST_PREGAME_ELO"
    FROM {MODEL_SCHEMA}."AS_OF_POINT" q
    JOIN {MODEL_SCHEMA}."TEAM_MEMBERSHIP" m
      ON m."SEASON" = q."AS_OF_SEASON"
  UNION ALL
  SELECT g."AS_OF_SEASON", g."AS_OF_WEEK_SEQ", g."TEAM_ID", g."CUTOFF_AT",
         0, NULL, NULL, NULL, NULL,
         g."FIXED_GAMES", g."GAME_NAME", g."GAME_CONFERENCE", g."GAME_CLASSIFICATION",
         g."GAMES_PLAYED", g."WINS", g."LOSSES", g."TIES", g."CONF_WINS", g."CONF_LOSSES",
         g."CONF_TIES", g."POINTS_FOR", g."POINTS_AGAINST", g."CONF_POINT_DIFF",
         g."GAMES_REMAINING", g."NEXT_GAME_ID", g."LAST_POSTGAME_ELO", g."FIRST_PREGAME_ELO"
    FROM game_points g
),
merged AS (
  SELECT x."AS_OF_SEASON", x."AS_OF_WEEK_SEQ", x."TEAM_ID",
         MAX(x."CUTOFF_AT") AS "CUTOFF_AT",
         MAX(x."IS_MEMBER") AS "IS_MEMBER", MAX(x."MEMBER_NAME") AS "MEMBER_NAME",
         MAX(x."MEMBER_CONFERENCE") AS "MEMBER_CONFERENCE",
         MAX(x."MEMBER_DIVISION") AS "MEMBER_DIVISION",
         MAX(x."MEMBER_CLASSIFICATION") AS "MEMBER_CLASSIFICATION",
         MAX(x."FIXED_GAMES") AS "FIXED_GAMES", MAX(x."GAME_NAME") AS "GAME_NAME",
         MAX(x."GAME_CONFERENCE") AS "GAME_CONFERENCE",
         MAX(x."GAME_CLASSIFICATION") AS "GAME_CLASSIFICATION",
         MAX(x."GAMES_PLAYED") AS "GAMES_PLAYED", MAX(x."WINS") AS "WINS",
         MAX(x."LOSSES") AS "LOSSES", MAX(x."TIES") AS "TIES", MAX(x."CONF_WINS") AS "CONF_WINS",
         MAX(x."CONF_LOSSES") AS "CONF_LOSSES", MAX(x."CONF_TIES") AS "CONF_TIES",
         MAX(x."POINTS_FOR") AS "POINTS_FOR", MAX(x."POINTS_AGAINST") AS "POINTS_AGAINST",
         MAX(x."CONF_POINT_DIFF") AS "CONF_POINT_DIFF",
         MAX(x."GAMES_REMAINING") AS "GAMES_REMAINING", MAX(x."NEXT_GAME_ID") AS "NEXT_GAME_ID",
         MAX(x."LAST_POSTGAME_ELO") AS "LAST_POSTGAME_ELO",
         MAX(x."FIRST_PREGAME_ELO") AS "FIRST_PREGAME_ELO"
    FROM facts x
   GROUP BY x."AS_OF_SEASON", x."AS_OF_WEEK_SEQ", x."TEAM_ID"
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
   WHERE m."IS_MEMBER" = 1 OR m."FIXED_GAMES" > 0
),
classified AS (
  SELECT n.*,
         CASE WHEN tr."CLASSIFICATION" IS NOT NULL THEN tr."CLASSIFICATION"
              WHEN n."GAME_CLASSIFICATION" IS NOT NULL THEN n."GAME_CLASSIFICATION"
              WHEN n."IS_MEMBER" = 1 THEN n."MEMBER_CLASSIFICATION" END AS "CLASSIFICATION"
    FROM named n
    LEFT JOIN {MODEL_SCHEMA}."CLASSIFICATION_TRANSITION" tr
      ON tr."TEAM_NAME" = n."SCHOOL"
     AND n."AS_OF_SEASON" BETWEEN tr."FIRST_SEASON" AND tr."LAST_SEASON"
),
conference_shares AS (
  SELECT c.*,
         SUM(CASE WHEN c."CLASSIFICATION" = 'fbs' THEN 1 ELSE 0 END)
           OVER (PARTITION BY c."AS_OF_SEASON", c."AS_OF_WEEK_SEQ", c."CONFERENCE_NAME")
           AS "FBS_MEMBERS",
         COUNT(c."CLASSIFICATION")
           OVER (PARTITION BY c."AS_OF_SEASON", c."AS_OF_WEEK_SEQ", c."CONFERENCE_NAME")
           AS "CLASSIFIED_MEMBERS"
    FROM classified c
),
ranks AS (
  SELECT k."AS_OF_SEASON", k."AS_OF_WEEK_SEQ", k."TEAM_ID",
         MIN(CASE WHEN k."POLL_CODE" = 'AP' THEN k."POLL_RANK" END) AS "AP_RANK",
         MIN(CASE WHEN k."POLL_CODE" = 'COACHES' THEN k."POLL_RANK" END) AS "COACHES_RANK",
         MIN(CASE WHEN k."POLL_CODE" = 'CFP' THEN k."POLL_RANK" END) AS "CFP_RANK"
    FROM {MODEL_SCHEMA}."RANKING_AS_OF" k
   WHERE k."TEAM_ID" IS NOT NULL AND k."POLL_CODE" IN ('AP', 'COACHES', 'CFP')
   GROUP BY k."AS_OF_SEASON", k."AS_OF_WEEK_SEQ", k."TEAM_ID"
),
ratings AS (
  SELECT r."SEASON", r."TEAM_ID", r."SP_RATING", r."IS_FINAL", r."FETCHED_AT"
    FROM {MODEL_SCHEMA}."RATING_SP" r
   WHERE r."TEAM_ID" IS NOT NULL
)
SELECT s."AS_OF_SEASON",
       s."AS_OF_WEEK_SEQ",
       s."TEAM_ID",
       s."SCHOOL",
       s."CONFERENCE_NAME",
       CASE WHEN s."IS_MEMBER" = 1 THEN s."MEMBER_DIVISION" END,
       COALESCE(tn."TIER", td."TIER"),
       COALESCE(tn."IS_POWER_CONFERENCE", td."IS_POWER_CONFERENCE", FALSE),
       s."CLASSIFICATION",
       CAST(COALESCE(s."GAMES_PLAYED", 0) AS DECIMAL(4,0)),
       CAST(COALESCE(s."WINS", 0) AS DECIMAL(4,0)),
       CAST(COALESCE(s."LOSSES", 0) AS DECIMAL(4,0)),
       CAST(COALESCE(s."TIES", 0) AS DECIMAL(4,0)),
       CAST(COALESCE(s."CONF_WINS", 0) AS DECIMAL(4,0)),
       CAST(COALESCE(s."CONF_LOSSES", 0) AS DECIMAL(4,0)),
       CAST(COALESCE(s."CONF_TIES", 0) AS DECIMAL(4,0)),
       CAST(COALESCE(s."POINTS_FOR", 0) AS DECIMAL(9,0)),
       CAST(COALESCE(s."POINTS_AGAINST", 0) AS DECIMAL(9,0)),
       CAST(COALESCE(s."CONF_POINT_DIFF", 0) AS DECIMAL(9,0)),
       CAST(COALESCE(s."GAMES_REMAINING", 0) AS DECIMAL(4,0)),
       s."NEXT_GAME_ID",
       CAST(COALESCE(s."LAST_POSTGAME_ELO", s."FIRST_PREGAME_ELO") AS DECIMAL(9,0)),
       CASE WHEN s."LAST_POSTGAME_ELO" IS NOT NULL THEN 'POSTGAME'
            WHEN s."FIRST_PREGAME_ELO" IS NOT NULL THEN 'PRESEASON'
            ELSE 'NONE' END,
       CAST(rk."AP_RANK" AS DECIMAL(4,0)),
       CAST(rk."COACHES_RANK" AS DECIMAL(4,0)),
       CAST(rk."CFP_RANK" AS DECIMAL(4,0)),
       pr."SP_RATING",
       CASE WHEN cu."FETCHED_AT" < s."CUTOFF_AT" OR (s."CUTOFF_AT" IS NULL AND cu."IS_FINAL")
            THEN cu."SP_RATING" END,
       CASE WHEN cu."FETCHED_AT" < s."CUTOFF_AT" OR (s."CUTOFF_AT" IS NULL AND cu."IS_FINAL")
            THEN cu."FETCHED_AT" END
  FROM conference_shares s
  LEFT JOIN {MODEL_SCHEMA}."CONFERENCE_TIER" tn
    ON tn."CONFERENCE_NAME" = s."CONFERENCE_NAME"
   AND s."AS_OF_SEASON" BETWEEN tn."FIRST_SEASON" AND COALESCE(tn."LAST_SEASON", s."AS_OF_SEASON")
  LEFT JOIN {MODEL_SCHEMA}."CONFERENCE_TIER" td
    ON td."CONFERENCE_NAME" IS NULL
   AND s."CONFERENCE_NAME" IS NOT NULL
   AND 2 * s."FBS_MEMBERS" > s."CLASSIFIED_MEMBERS"
   AND s."AS_OF_SEASON" BETWEEN td."FIRST_SEASON" AND COALESCE(td."LAST_SEASON", s."AS_OF_SEASON")
  LEFT JOIN ranks rk
    ON rk."AS_OF_SEASON" = s."AS_OF_SEASON"
   AND rk."AS_OF_WEEK_SEQ" = s."AS_OF_WEEK_SEQ"
   AND rk."TEAM_ID" = s."TEAM_ID"
  LEFT JOIN ratings pr
    ON pr."TEAM_ID" = s."TEAM_ID"
   AND pr."SEASON" = s."AS_OF_SEASON" - 1
  LEFT JOIN ratings cu
    ON cu."TEAM_ID" = s."TEAM_ID"
   AND cu."SEASON" = s."AS_OF_SEASON"
COMMENT IS 'One row per as-of point and team-season of the point''s season with a membership or a game with FIXED participants. Conference, division, tier and classification are the season''s (they are fixed before it starts, and equal TEAM_SEASON''s); records, conference records without championship games, points, CONF_POINT_DIFF, the remaining and next games and ELO_AS_OF count only what GAME_AS_OF shows at the point; AP, Coaches and committee ranks are the latest visible (RANKING_AS_OF); SP_PRIOR_RATING is the previous season''s SP+, SP_CURRENT_RATING the season''s own only when visible. The engine''s team inputs (team, conf, conf_tier, wins, losses, conf_wins, conf_losses, conf_pd, the SP+ prior and Elo for the rating) come from here, fixed-scale. Earlier seasons are fully known: use TEAM_SEASON.';
