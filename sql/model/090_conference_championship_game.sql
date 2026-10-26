-- Conference championship games (model/games). CFBD does not mark them, so they are detected inside
-- the seasons in which the conference held one (CONFERENCE_CHAMPIONSHIP): the conference's only
-- flagged conference game in its last regular week with such a game. Only weeks up to the season's
-- power-conference championship week count (cross-plan X1): the stored 2026 schedule flags Army-Navy
-- as an American Athletic conference game alone in the week after the championship games.
-- Fallback (decision [36]): CFBD does not flag the 2025 title games as conference games. When the
-- flagged rule finds no game for a conference and season, every game between two of its teams
-- counts, flagged or not, up to the power week computed the same way. The fallback never replaces
-- a game the flagged rule found, and a season without power-conference games (before 1998) has
-- none. Each rule's windows sit in their own step, partitioned by season first. Games are
-- deduplicated by game id, newest fetch first, so a game stored twice does not count twice in its
-- week. The partition starts with SEASON (a game id belongs to one season) so that a season filter
-- reaches the scan. GAME joins this view by GAME_ID; it reads CFB_CORE.GAMES, not GAME.
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.CONFERENCE_CHAMPIONSHIP_GAME (
  GAME_ID                 COMMENT IS 'CFBD game id (GAME.GAME_ID)',
  SEASON                  COMMENT IS 'Season of the game',
  CONFERENCE_NAME         COMMENT IS 'CFBD name of the conference whose championship game it is; both teams are recorded in it on the game',
  WEEK                    COMMENT IS 'Regular-season week of the game as CFBD labels it',
  WEEK_SEQ                COMMENT IS 'Week sequence of the game (SEASON_WEEK.WEEK_SEQ); equal to WEEK, because championship games are regular-season games in CFBD',
  START_AT                COMMENT IS 'Kickoff in UTC',
  HOME_TEAM_ID            COMMENT IS 'CFBD team id of the home (first-listed) team',
  HOME_TEAM               COMMENT IS 'CFBD school name of the home team',
  AWAY_TEAM_ID            COMMENT IS 'CFBD team id of the away team',
  AWAY_TEAM               COMMENT IS 'CFBD school name of the away team',
  IS_COMPLETED            COMMENT IS 'TRUE once CFBD reports the game as completed',
  WINNER_TEAM_ID          COMMENT IS 'Team id of the conference champion: the winner of the completed game; NULL while it is not played or after a tie',
  NOTES                   COMMENT IS 'CFBD notes of the game as stored, for example SEC Championship; a cross-check only, MODEL_CHECKS warns when notes and detection disagree',
  POWER_CHAMPIONSHIP_WEEK COMMENT IS 'The season''s power-conference championship week: the last regular-season week in which a conference that CONFERENCE_TIER marks as power for the season plays a flagged conference game between two of its teams. For a game found by the fallback (CFBD does not flag the 2025 title games), it counts that conference''s games flagged or not. Power conferences play no regular conference game after their title games, so once those are stored it is their week, and before that it is their last scheduled week. The rule looks only at weeks up to it; NULL in a season without such a game (before 1998), which has no limit'
) AS
WITH deduplicated AS (
  SELECT "SEASON", "ID", "SEASON_TYPE", "WEEK", "START_DATE", "CONFERENCE_GAME", "COMPLETED",
         "HOME_ID", "HOME_TEAM", "HOME_CONFERENCE", "HOME_POINTS",
         "AWAY_ID", "AWAY_TEAM", "AWAY_CONFERENCE", "AWAY_POINTS", "NOTES"
    FROM {CORE_SCHEMA}."GAMES"
 QUALIFY ROW_NUMBER() OVER (
           PARTITION BY "SEASON", "ID"
           ORDER BY "REQ_AT" DESC, "REQ_SLICE" DESC, "ROW_NO" DESC) = 1
),
conference_games AS (
  SELECT d.*
    FROM deduplicated d
   WHERE d."SEASON_TYPE" = 'regular'
     AND d."HOME_CONFERENCE" = d."AWAY_CONFERENCE"
),
power_weeks AS (
  SELECT c."SEASON",
         MAX(CASE WHEN c."CONFERENCE_GAME" THEN c."WEEK" END) AS "FLAGGED_WEEK",
         MAX(c."WEEK") AS "ANY_WEEK"
    FROM conference_games c
    JOIN {MODEL_SCHEMA}."CONFERENCE_TIER" t
      ON t."CONFERENCE_NAME" = c."HOME_CONFERENCE"
     AND c."SEASON" BETWEEN t."FIRST_SEASON" AND COALESCE(t."LAST_SEASON", c."SEASON")
   WHERE t."IS_POWER_CONFERENCE"
   GROUP BY c."SEASON"
),
candidates AS (
  SELECT c.*, p."FLAGGED_WEEK", p."ANY_WEEK",
         COALESCE(c."CONFERENCE_GAME", FALSE)
           AND (p."FLAGGED_WEEK" IS NULL OR c."WEEK" <= p."FLAGGED_WEEK") AS "IN_FLAGGED",
         COALESCE(c."WEEK" <= p."ANY_WEEK", FALSE) AS "IN_ANY"
    FROM conference_games c
    JOIN {MODEL_SCHEMA}."CONFERENCE_CHAMPIONSHIP" s
      ON s."CONFERENCE_NAME" = c."HOME_CONFERENCE"
     AND c."SEASON" BETWEEN s."FIRST_SEASON" AND COALESCE(s."LAST_SEASON", c."SEASON")
    LEFT JOIN power_weeks p
      ON p."SEASON" = c."SEASON"
),
last_weeks AS (
  SELECT k.*,
         MAX(CASE WHEN k."IN_FLAGGED" THEN k."WEEK" END)
           OVER (PARTITION BY k."SEASON", k."HOME_CONFERENCE") AS "FLAGGED_LAST",
         MAX(CASE WHEN k."IN_ANY" THEN k."WEEK" END)
           OVER (PARTITION BY k."SEASON", k."HOME_CONFERENCE") AS "ANY_LAST"
    FROM candidates k
),
counted AS (
  SELECT w.*,
         SUM(CASE WHEN w."IN_FLAGGED" AND w."WEEK" = w."FLAGGED_LAST" THEN 1 ELSE 0 END)
           OVER (PARTITION BY w."SEASON", w."HOME_CONFERENCE") AS "FLAGGED_COUNT",
         SUM(CASE WHEN w."IN_ANY" AND w."WEEK" = w."ANY_LAST" THEN 1 ELSE 0 END)
           OVER (PARTITION BY w."SEASON", w."HOME_CONFERENCE") AS "ANY_COUNT"
    FROM last_weeks w
)
SELECT k."ID",
       k."SEASON",
       k."HOME_CONFERENCE",
       k."WEEK",
       CAST(k."WEEK" AS DECIMAL(3,0)),
       k."START_DATE",
       k."HOME_ID",
       k."HOME_TEAM",
       k."AWAY_ID",
       k."AWAY_TEAM",
       COALESCE(k."COMPLETED", FALSE),
       CASE WHEN k."COMPLETED" AND k."HOME_POINTS" > k."AWAY_POINTS" THEN k."HOME_ID"
            WHEN k."COMPLETED" AND k."AWAY_POINTS" > k."HOME_POINTS" THEN k."AWAY_ID" END,
       k."NOTES",
       CASE WHEN k."FLAGGED_COUNT" = 1 THEN k."FLAGGED_WEEK" ELSE k."ANY_WEEK" END
  FROM counted k
 WHERE (k."FLAGGED_COUNT" = 1 AND k."IN_FLAGGED" AND k."WEEK" = k."FLAGGED_LAST")
    OR (k."FLAGGED_COUNT" <> 1 AND k."ANY_COUNT" = 1 AND k."IN_ANY" AND k."WEEK" = k."ANY_LAST")
COMMENT IS 'One row per detected conference championship game. CFBD does not mark these games: inside a season range of conference C in CONFERENCE_CHAMPIONSHIP, a game is detected when it is a regular-season game between two teams recorded in C on the game, flagged as a conference game, played no later than the season''s POWER_CHAMPIONSHIP_WEEK, C''s only such game in its week, and that week is C''s last week with such a game up to that limit. So a conference without a championship game in the season, a game not flagged as a conference game after a found title game (Army-Navy in 2024) and a flagged game after the power conferences'' championship week (Army-Navy in 2026) are never detected. Fallback: when this finds no game for C in the season, the same rule runs over every game between two teams of C, flagged as a conference game or not, with the power week counted the same way; CFBD does not flag the 2025 title games. A season without power-conference games (before 1998) has no fallback. The NOTES text is a cross-check only (MODEL_CHECKS: CONF_CHAMPIONSHIP_NOTES_MISMATCH, CONF_CHAMPIONSHIP_MISSING). Only FBS conferences are seeded; champions of conferences without a championship game come from the standings (Phase 4).';
