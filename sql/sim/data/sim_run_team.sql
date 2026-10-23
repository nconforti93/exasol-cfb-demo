-- Data table: the input snapshot of a run, teams (sim-engine/sim-runs). It doubles as the roster of the what-if.
CREATE TABLE IF NOT EXISTS {SIM_SCHEMA}.SIM_RUN_TEAM (
  RUN_ID        DECIMAL(18,0) NOT NULL COMMENT IS 'The run this snapshot row belongs to',
  TEAM_IDX      DECIMAL(5,0) NOT NULL COMMENT IS 'Position of the team in the engine input, 0 to the number of teams minus 1, by team id',
  TEAM_ID       DECIMAL(18,0) NOT NULL COMMENT IS 'CFBD team id',
  SCHOOL        VARCHAR(200) UTF8 COMMENT IS 'School name, as in CFB_MODEL',
  CONFERENCE    VARCHAR(100) UTF8 COMMENT IS 'Conference name; NULL for an independent or a non-FBS team',
  TIER          VARCHAR(8) UTF8 NOT NULL COMMENT IS 'P4, G6, IND (FBS independent) or NON_FBS',
  HAS_CCG       BOOLEAN NOT NULL COMMENT IS 'TRUE when the conference plays a championship game in the season',
  IS_ELIGIBLE   BOOLEAN NOT NULL COMMENT IS 'TRUE for an FBS team with at least one game in the season; only these can be selected',
  RATING        DECIMAL(6,2) NOT NULL COMMENT IS 'Team rating in points against an average FBS team',
  RATING_SOURCE VARCHAR(8) UTF8 NOT NULL COMMENT IS 'Which inputs the rating came from: SP_ELO, ELO, SP or DEFAULT',
  SOS           DECIMAL(6,2) NOT NULL COMMENT IS 'Strength of schedule: mean rating of the opponents of the regular-season games',
  WINS          DECIMAL(2,0) NOT NULL COMMENT IS 'Wins so far at the as-of point',
  LOSSES        DECIMAL(2,0) NOT NULL COMMENT IS 'Losses so far at the as-of point',
  CONF_WINS     DECIMAL(2,0) NOT NULL COMMENT IS 'Conference wins so far',
  CONF_LOSSES   DECIMAL(2,0) NOT NULL COMMENT IS 'Conference losses so far',
  CONF_PD       DECIMAL(5,0) NOT NULL COMMENT IS 'Conference point differential so far',
  CAPPED_PD     DECIMAL(5,0) NOT NULL COMMENT IS 'Point differential so far with each game margin capped at the margin cap'
) COMMENT IS 'One row per run and team of the input snapshot: the standings, rating and schedule strength the engine started from.';
