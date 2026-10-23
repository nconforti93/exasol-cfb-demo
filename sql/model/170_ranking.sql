-- Poll rankings (model/ratings-rankings): one row per poll, season, season type, week and ranked team of
-- CFB_CORE.RANKINGS_POLLS_RANKS (the rows carry their own SEASON, SEASON_TYPE, WEEK and POLL keys). The
-- newest fetch wins per key (REQ_AT, then the later slice key); rows of one fetch with the same key, such as
-- more than one committee snapshot of a week, take the later position of the poll in the response (POLLS_NO),
-- then the later row. A rank without a team id is keyed by its school name, so unmatched rows stay apart.
-- The poll code, the label lead and the final-before-postseason flag come from the POLL seed (an unknown poll
-- keeps its rows as OTHER with a lead of 0). The season's regular-season state comes from SEASON_WEEK.
-- Visible at (the label rule): regular labels minus the poll's lead; postseason labels at their own sequence,
-- except a poll whose final ranking comes before the postseason (the committee), visible at the last regular
-- week. IS_FINAL_POLL is derived (the stored rows do not carry CFBD's isFinal): postseason polls released
-- after the postseason, and the committee's last stored ranking of a season whose regular weeks are all done.
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.RANKING (
  SEASON               COMMENT IS 'Season of the poll',
  SEASON_TYPE          COMMENT IS 'Season type as CFBD labels the poll: regular or postseason',
  WEEK                 COMMENT IS 'Week as CFBD labels the poll within the season type. The label can run ahead of the games it reflects: an AP poll labeled week 10 is released after the games of week 9',
  WEEK_SEQ             COMMENT IS 'Week sequence of the label: regular week n = n, postseason week n = 100 + n, so regular week 1 and postseason week 1 stay separate; NULL for another season type',
  POLL_NAME            COMMENT IS 'CFBD poll name as it appears in the rankings data',
  POLL_CODE            COMMENT IS 'Code from the POLL seed: AP, COACHES, CFP, FCS_COACHES, D2_COACHES or D3_COACHES; OTHER for a poll name that is not in the seed (MODEL_CHECKS: POLL_UNKNOWN)',
  POLL_RANK            COMMENT IS 'Position in the poll (1 = best); tied teams share a position as CFBD reports it',
  TEAM_ID              COMMENT IS 'CFBD team id of the ranked team (TEAM.TEAM_ID)',
  SCHOOL               COMMENT IS 'School name as the poll reports it',
  CONFERENCE           COMMENT IS 'Conference of the team as the poll reports it (CFBD name)',
  FIRST_PLACE_VOTES    COMMENT IS 'First-place votes as CFBD reports them; NULL for polls without votes, such as the committee',
  POINTS               COMMENT IS 'Poll points as CFBD reports them',
  VISIBLE_AT_WEEK_SEQ  COMMENT IS 'Week sequence from which the ranking is known by the label rule: the label''s WEEK_SEQ minus the poll''s label lead for regular labels (0 = before the first game), the label''s own WEEK_SEQ for postseason labels, and the last regular week of the season for a poll whose final ranking comes before the postseason (the committee''s selection ranking). The as-of views also show a ranking fetched before the point''s cutoff; historic seasons use the label rule alone. NULL when WEEK_SEQ is NULL',
  IS_FINAL_POLL        COMMENT IS 'Derived, as the stored ranks do not carry CFBD''s isFinal: TRUE for postseason-labeled polls released after the postseason, and for the committee''s last stored ranking of a season whose regular-season weeks are all done (SEASON_WEEK.IS_DONE); FALSE for a season in progress',
  FETCHED_AT           COMMENT IS 'When the row shown was fetched from CFBD (REQ_AT, UTC); the as-of views compare it with the cutoff'
) AS
WITH ranks AS (
  SELECT r."SEASON", r."SEASON_TYPE", r."WEEK", r."POLL", r."RANK", r."TEAM_ID", r."SCHOOL",
         r."CONFERENCE", r."FIRST_PLACE_VOTES", r."POINTS", r."REQ_AT"
    FROM {CORE_SCHEMA}."RANKINGS_POLLS_RANKS" r
 QUALIFY ROW_NUMBER() OVER (
           PARTITION BY r."SEASON", r."SEASON_TYPE", r."WEEK", r."POLL", r."TEAM_ID",
                        CASE WHEN r."TEAM_ID" IS NULL THEN r."SCHOOL" END
           ORDER BY r."REQ_AT" DESC, r."REQ_SLICE" DESC, r."POLLS_NO" DESC, r."ROW_NO" DESC) = 1
),
seasons AS (
  SELECT w."SEASON",
         MAX(CASE WHEN w."SEASON_TYPE" = 'regular' THEN w."WEEK_SEQ" END) AS "LAST_REGULAR_SEQ",
         MIN(CASE WHEN w."SEASON_TYPE" <> 'regular' THEN NULL WHEN w."IS_DONE" THEN 1 ELSE 0 END)
           AS "REGULAR_DONE"
    FROM {MODEL_SCHEMA}."SEASON_WEEK" w
   GROUP BY w."SEASON"
),
coded AS (
  SELECT r.*,
         COALESCE(p."POLL_CODE", 'OTHER') AS "CODE",
         COALESCE(p."LABEL_LEAD_WEEKS", 0) AS "LEAD",
         COALESCE(p."FINAL_BEFORE_POSTSEASON", FALSE) AS "FINAL_BEFORE",
         CASE r."SEASON_TYPE" WHEN 'regular' THEN r."WEEK" WHEN 'postseason' THEN 100 + r."WEEK" END
           AS "SEQ"
    FROM ranks r
    LEFT JOIN {MODEL_SCHEMA}."POLL" p
      ON p."POLL_NAME" = r."POLL"
),
latest AS (
  SELECT c.*,
         MAX(c."SEQ") OVER (PARTITION BY c."SEASON", c."POLL") AS "LAST_SEQ"
    FROM coded c
)
SELECT l."SEASON",
       l."SEASON_TYPE",
       l."WEEK",
       CAST(l."SEQ" AS DECIMAL(3,0)),
       l."POLL",
       l."CODE",
       l."RANK",
       l."TEAM_ID",
       l."SCHOOL",
       l."CONFERENCE",
       l."FIRST_PLACE_VOTES",
       l."POINTS",
       CAST(CASE WHEN l."SEQ" IS NULL THEN NULL
                 WHEN l."SEASON_TYPE" = 'postseason'
                 THEN CASE WHEN l."FINAL_BEFORE" THEN COALESCE(s."LAST_REGULAR_SEQ", l."SEQ") ELSE l."SEQ" END
                 ELSE l."SEQ" - l."LEAD" END AS DECIMAL(3,0)),
       CASE WHEN l."FINAL_BEFORE"
            THEN CASE WHEN l."SEQ" = l."LAST_SEQ" AND s."REGULAR_DONE" = 1 THEN TRUE ELSE FALSE END
            ELSE CASE WHEN l."SEASON_TYPE" = 'postseason' THEN TRUE ELSE FALSE END END,
       l."REQ_AT"
  FROM latest l
  LEFT JOIN seasons s
    ON s."SEASON" = l."SEASON"
COMMENT IS 'One row per poll, season, season type, week and ranked team of CFB_CORE.RANKINGS_POLLS_RANKS (stored from 2014; the newest fetch wins, ties within a fetch by the poll''s position in the response). It codes the poll, places the label in the week order (regular week n = n, postseason week n = 100 + n), says from which week the ranking is known (VISIBLE_AT_WEEK_SEQ: the label runs ahead of the games, and the committee''s selection ranking is known at the last regular week) and derives IS_FINAL_POLL. For the state at a point use RANKING_AS_OF.';
