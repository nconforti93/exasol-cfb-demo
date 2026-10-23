-- Rankings as of a point (model/as-of): AS_OF_POINT joined to RANKING on the season. A ranking is visible
-- at the point by the label rule (RANKING.VISIBLE_AT_WEEK_SEQ at most the point's sequence) or when it was
-- fetched before CUTOFF_AT (decision [8]: a slice fetched before the cutoff holds only polls released
-- before it). Per point and poll name, the latest visible label (WEEK_SEQ) is shown; the window that finds
-- it partitions by the point, in its own step.
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.RANKING_AS_OF (
  AS_OF_SEASON      COMMENT IS 'Season of the as-of point (AS_OF_POINT.AS_OF_SEASON); filter or join on it with AS_OF_WEEK_SEQ',
  AS_OF_WEEK_SEQ    COMMENT IS 'Week sequence of the point: 0 preseason, regular week n = n, postseason week n = 100 + n',
  POLL_NAME         COMMENT IS 'CFBD poll name (RANKING.POLL_NAME)',
  POLL_CODE         COMMENT IS 'Code from the POLL seed: AP, COACHES, CFP, FCS_COACHES, D2_COACHES or D3_COACHES; OTHER for a poll that is not in the seed',
  SEASON_TYPE       COMMENT IS 'Season type of the poll''s label: regular or postseason',
  WEEK              COMMENT IS 'Week of the poll''s label as CFBD gives it. The label can run ahead of the games it reflects: the AP poll labeled week 10 follows the games of week 9',
  WEEK_SEQ          COMMENT IS 'Week sequence of the label (RANKING.WEEK_SEQ): the latest label of the poll that is visible at the point',
  POLL_RANK         COMMENT IS 'Position in the poll (1 = best)',
  TEAM_ID           COMMENT IS 'CFBD team id of the ranked team',
  SCHOOL            COMMENT IS 'School name as the poll reports it',
  CONFERENCE        COMMENT IS 'Conference of the team as the poll reports it (CFBD name)',
  FIRST_PLACE_VOTES COMMENT IS 'First-place votes as CFBD reports them; NULL for polls without votes, such as the committee',
  POINTS            COMMENT IS 'Poll points as CFBD reports them'
) AS
WITH visible AS (
  SELECT p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", r."POLL_NAME", r."POLL_CODE", r."SEASON_TYPE",
         r."WEEK", r."WEEK_SEQ", r."POLL_RANK", r."TEAM_ID", r."SCHOOL", r."CONFERENCE",
         r."FIRST_PLACE_VOTES", r."POINTS"
    FROM {MODEL_SCHEMA}."AS_OF_POINT" p
    JOIN {MODEL_SCHEMA}."RANKING" r
      ON r."SEASON" = p."AS_OF_SEASON"
   WHERE r."VISIBLE_AT_WEEK_SEQ" <= p."AS_OF_WEEK_SEQ"
      OR r."FETCHED_AT" < p."CUTOFF_AT"
),
latest AS (
  SELECT v.*,
         MAX(v."WEEK_SEQ") OVER (PARTITION BY v."AS_OF_SEASON", v."AS_OF_WEEK_SEQ", v."POLL_NAME")
           AS "LATEST_SEQ"
    FROM visible v
)
SELECT l."AS_OF_SEASON",
       l."AS_OF_WEEK_SEQ",
       l."POLL_NAME",
       l."POLL_CODE",
       l."SEASON_TYPE",
       l."WEEK",
       l."WEEK_SEQ",
       l."POLL_RANK",
       l."TEAM_ID",
       l."SCHOOL",
       l."CONFERENCE",
       l."FIRST_PLACE_VOTES",
       l."POINTS"
  FROM latest l
 WHERE l."WEEK_SEQ" = l."LATEST_SEQ"
COMMENT IS 'One row per as-of point, poll and ranked team, from the latest week of each poll that is visible at the point: by the label rule (RANKING.VISIBLE_AT_WEEK_SEQ, so the AP poll labeled week 10 is visible after week 9, and the committee''s selection ranking at the last regular week), or because it was fetched before the point''s CUTOFF_AT (the live demo shows a Tuesday committee ranking at once). Historic seasons, fetched later, use the label rule alone. Earlier seasons are fully known: use RANKING. Under the label rule, a ranking that CFBD labels after the point it follows becomes visible one week late: the 2024 committee selection ranking only after the point that follows the selection (cross-plan X2).';
