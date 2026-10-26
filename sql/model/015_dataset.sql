-- Reference seed: the CFBD datasets that matter to the model, transcribed from
-- docs/cfbd-data-availability.md (read 2026-10-21). Endpoints and virtual-schema tables come from
-- vs/catalog/catalog.json, access classes from decision D15. This row is the whole coverage
-- statement of an ON_DEMAND or UNAVAILABLE dataset. COMPLETE_SHARE is 0.95 until open question Q13
-- calibrates it. FIRST_SEASON NULL means the dataset has no season (teams, venues) or is current-only.
CREATE OR REPLACE TABLE {MODEL_SCHEMA}.DATASET (
  DATASET_CODE        VARCHAR(32) UTF8 NOT NULL COMMENT IS 'Code of the dataset, for example GAMES, ELO or PLAYS',
  LABEL_TEXT          VARCHAR(100) UTF8 NOT NULL COMMENT IS 'Name of the dataset as docs/cfbd-data-availability.md calls it',
  ACCESS_CLASS        VARCHAR(16) UTF8 NOT NULL COMMENT IS 'How to get the data: STORED (in the demo load profile, read it from the model), ON_DEMAND (federated, query CFBD_VS selectively) or UNAVAILABLE (tier-gated on the project key)',
  CFBD_ENDPOINT       VARCHAR(100) UTF8 NOT NULL COMMENT IS 'CFBD API endpoint that serves the dataset',
  CORE_TABLE          VARCHAR(64) UTF8 COMMENT IS 'CFB_CORE mirror that stores the dataset; NULL unless ACCESS_CLASS is STORED',
  VS_TABLE            VARCHAR(64) UTF8 NOT NULL COMMENT IS 'CFBD_VS virtual table for the dataset',
  MODEL_OBJECT        VARCHAR(64) UTF8 COMMENT IS 'CFB_MODEL view that exposes a STORED dataset; NULL otherwise',
  FIRST_SEASON        DECIMAL(4,0) COMMENT IS 'First season CFBD declares data for; NULL when the dataset has no seasons or CFBD declares none',
  LAST_SEASON         DECIMAL(4,0) COMMENT IS 'Last season CFBD declares data for; NULL while the dataset is open',
  GAP_SEASONS         VARCHAR(100) UTF8 COMMENT IS 'Comma-separated seasons without data inside the declared range, for example 1871; NULL when there are none',
  COVERAGE_POPULATION VARCHAR(40) UTF8 COMMENT IS 'Population that the measured coverage share is computed over: COMPLETED_FBS_GAMES, COMPLETED_FBS_VS_FBS_GAMES or FBS_TEAM_SEASONS; NULL when coverage is not measured',
  COMPLETE_SHARE      DECIMAL(3,2) NOT NULL COMMENT IS 'Measured share (0 to 1) from which a stored, final season counts as complete, DECIMAL(3,2); 0.95 unless calibrated',
  NOTE                VARCHAR(300) UTF8 COMMENT IS 'Caveats from the CFBD data availability page'
) COMMENT IS 'One row per CFBD dataset: how it is stored or fetched, its declared seasons and gaps, and how its coverage is measured.';

INSERT INTO {MODEL_SCHEMA}."DATASET" VALUES
  ('GAMES', 'Games, scores, records, matchups', 'STORED', '/games', 'GAMES', 'GAMES', 'GAME', 1869, NULL, '1871', NULL, 0.95, 'No games for 1871; schedules can precede results'),
  ('SCORES', 'Game scores', 'STORED', '/games', 'GAMES', 'GAMES', 'GAME', 1869, NULL, '1871', 'COMPLETED_FBS_GAMES', 0.95, 'Scores come with the games; no games for 1871'),
  ('ELO', 'Elo', 'STORED', '/games', 'GAMES', 'GAMES', 'GAME', 1890, NULL, NULL, 'COMPLETED_FBS_GAMES', 0.95, 'Pregame and postgame Elo per game'),
  ('PREGAME_WP', 'Pregame win probability', 'STORED', '/metrics/wp/pregame', 'METRICS_WP_PREGAME', 'METRICS_WP_PREGAME', 'GAME', 2013, NULL, NULL, 'COMPLETED_FBS_VS_FBS_GAMES', 0.95, 'Derived from spreads; games without a spread omitted'),
  ('SP_PLUS', 'SP+ (team and conference)', 'STORED', '/ratings/sp', 'RATINGS_SP', 'RATINGS_SP', 'RATING_SP', 1970, NULL, NULL, 'FBS_TEAM_SEASONS', 0.95, 'Current season changes as games are played'),
  ('POLLS', 'AP Top 25 and other polls', 'STORED', '/rankings', 'RANKINGS_POLLS_RANKS', 'RANKINGS_POLLS_RANKS', 'RANKING', 1936, NULL, NULL, NULL, 0.95, 'AP from 1936, Coaches from 2001, committee from 2014; other polls differ'),
  ('CALENDAR', 'Season calendar', 'STORED', '/calendar', 'CALENDAR', 'CALENDAR', 'SEASON_WEEK', 2002, NULL, NULL, NULL, 0.95, 'Weeks derived from games'),
  ('CONFERENCE_MEMBERSHIP', 'Team/conference affiliations', 'STORED', '/conferences/affiliations', 'CONFERENCES_AFFILIATIONS', 'CONFERENCES_AFFILIATIONS', 'TEAM_SEASON', 1869, NULL, NULL, 'FBS_TEAM_SEASONS', 0.95, 'Accepts a year or a range'),
  ('TEAMS', 'Team list', 'STORED', '/teams', 'TEAMS', 'TEAMS', 'TEAM', NULL, NULL, NULL, NULL, 0.95, 'Current team list; historic programs appear only in games'),
  ('VENUES', 'Venues', 'STORED', '/venues', 'VENUES', 'VENUES', 'VENUE', NULL, NULL, NULL, NULL, 0.95, 'Venue list with coordinates where CFBD has them'),
  ('PLAYS', 'Raw plays', 'ON_DEMAND', '/plays', NULL, 'PLAYS', NULL, 2001, NULL, NULL, NULL, 0.95, 'Coverage and fields vary by game and source'),
  ('DRIVES', 'Drives', 'ON_DEMAND', '/drives', NULL, 'DRIVES', NULL, 2001, NULL, NULL, NULL, 0.95, 'Coverage varies by game, especially early'),
  ('TEAM_BOX_SCORES', 'Team box-score stats', 'ON_DEMAND', '/games/teams', NULL, 'GAMES_TEAMS', NULL, 2004, NULL, NULL, NULL, 0.95, 'Team and player box-score stats from 2004'),
  ('PLAYER_BOX_SCORES', 'Player box-score stats', 'ON_DEMAND', '/games/players', NULL, 'GAMES_PLAYERS', NULL, 2004, NULL, NULL, NULL, 0.95, 'Team and player box-score stats from 2004'),
  ('BETTING_LINES', 'Betting lines, ATS', 'ON_DEMAND', '/lines', NULL, 'LINES', NULL, 2013, NULL, NULL, NULL, 0.95, 'Coverage varies; only the pregame spread is stored (PREGAME_WP)'),
  ('SRS', 'SRS', 'ON_DEMAND', '/ratings/srs', NULL, 'RATINGS_SRS', NULL, 1897, NULL, '1918', NULL, 0.95, 'No 1918'),
  ('FPI', 'FPI', 'ON_DEMAND', '/ratings/fpi', NULL, 'RATINGS_FPI', NULL, 2005, NULL, NULL, NULL, 0.95, 'Third-party schedule'),
  ('RECRUITING', 'HS recruits and team rankings', 'ON_DEMAND', '/recruiting/players', NULL, 'RECRUITING_PLAYERS', NULL, 2000, NULL, NULL, NULL, 0.95, 'Year is the recruiting class'),
  ('TRANSFER_PORTAL', 'Transfer portal', 'ON_DEMAND', '/player/portal', NULL, 'PLAYER_PORTAL', NULL, 2021, NULL, NULL, NULL, 0.95, NULL),
  ('DRAFT', 'NFL draft picks', 'ON_DEMAND', '/draft/picks', NULL, 'DRAFT_PICKS', NULL, 1967, NULL, '1988,1996', NULL, 0.95, 'No 1988, 1996'),
  ('COACHES', 'Coaches, seasons, tenures', 'ON_DEMAND', '/coaches', NULL, 'COACHES', NULL, 1886, NULL, '1887', NULL, 0.95, 'No coach-seasons for 1887'),
  ('ROSTERS', 'Rosters', 'ON_DEMAND', '/roster', NULL, 'ROSTER', NULL, 2004, NULL, NULL, NULL, 0.95, 'Field completeness varies'),
  ('MATCHUP', 'Head-to-head matchup (CFBD)', 'ON_DEMAND', '/teams/matchup', NULL, 'TEAMS_MATCHUP', NULL, 1869, NULL, '1871', NULL, 0.95, 'Slow (about 4 s); the model answers head-to-head from stored games'),
  ('GAME_PREVIEW', 'Game preview', 'ON_DEMAND', '/games/{gameId}/preview', NULL, 'GAMES_PREVIEW', NULL, NULL, NULL, NULL, NULL, 0.95, 'Available for upcoming and recent games only'),
  ('WEATHER', 'Weather', 'UNAVAILABLE', '/games/weather', NULL, 'GAMES_WEATHER', NULL, 2001, NULL, NULL, NULL, 0.95, 'Tier-gated on the project key; not every game');
