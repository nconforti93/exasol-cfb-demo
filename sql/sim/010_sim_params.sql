-- Reference seed: the constants of the score model, the resume ranking and the odds views
-- (sim-engine/score-model). Replaced on every deploy; change it in a pull request, never in the
-- database. FITTED rows are written by `make sim-fit` (engine/fit.py) from the completed
-- regular-season games of 2014-2025; 8,188 FBS games have a prior SP+ and an Elo.
-- ASSUMED rows are stated choices (published rules, S3 prototype values, or a fit that fell back).
CREATE OR REPLACE TABLE {SIM_SCHEMA}.SIM_PARAMS (
  PARAM_KEY    VARCHAR(40) ASCII NOT NULL COMMENT IS 'Name of the constant, upper case, unique; SCORE_PARAMS turns each key into a column',
  PARAM_VALUE  DECIMAL(12,4) NOT NULL COMMENT IS 'Value of the constant, four decimals',
  PARAM_ORIGIN VARCHAR(10) ASCII NOT NULL COMMENT IS 'Where the value comes from: FITTED (written by make sim-fit), ASSUMED (a stated choice, published or inherited from the S3 prototype) or UNFITTED (a placeholder until the fit has run)',
  PARAM_NOTE   VARCHAR(300) UTF8 NOT NULL COMMENT IS 'One sentence on what the constant means and its unit'
) COMMENT IS 'One row per constant of the score model, the resume ranking and the odds movement views. Replaced from git on every deploy; the fit and the owner change it through a reviewed seed file.';

INSERT INTO {SIM_SCHEMA}."SIM_PARAMS" VALUES
  ('HFA', 3.1367, 'FITTED', 'Home-field advantage in points, applied off neutral sites'),
  ('B_SP', 0.0164, 'FITTED', 'Weight of the previous season SP+ rating in a team rating, points per SP+ point'),
  ('B_ELO', 0.0414, 'FITTED', 'Weight of the current Elo above ELO_CENTER in a team rating, points per Elo point'),
  ('ELO_CENTER', 1510, 'FITTED', 'Mean Elo of the FBS teams in the fit, a whole number'),
  ('B_ELO_ONLY', 0.0422, 'FITTED', 'Points per Elo point when a team has an Elo but no previous SP+'),
  ('B_SP_ONLY', 0.6783, 'FITTED', 'Points per SP+ point when a team has a previous SP+ but no Elo'),
  ('NON_FBS_RATING', -28.4057, 'FITTED', 'Rating in points of a non-FBS opponent without an Elo'),
  ('SIGMA_1', 16.602, 'FITTED', 'Standard deviation of a game margin one week ahead, in points'),
  ('TAU', 2.8269, 'FITTED', 'Growth of the margin standard deviation per week ahead, in points; it sets the shared team-strength offsets of the engine'),
  ('TOTAL_INTERCEPT', 23.0703, 'FITTED', 'Intercept of the predicted game total in points'),
  ('TOTAL_SLOPE', 0.2891, 'FITTED', 'Points of predicted game total per point of the four prior offense and defense ratings'),
  ('TOTAL_DEFAULT', 55.4156, 'FITTED', 'Predicted game total in points when a team has no previous SP+'),
  ('ELO_PROB_SCALE', 511, 'FITTED', 'Elo points that separate a 10:1 win-odds ratio in the pregame probability from Elo'),
  ('ELO_PROB_HFA', 78, 'FITTED', 'Elo points of home-field advantage in the pregame probability from Elo'),
  ('FBS_DEFAULT_RATING', -10, 'ASSUMED', 'Rating in points of an FBS team with neither a previous SP+ nor an Elo'),
  ('OT_MARGIN', 3, 'ASSUMED', 'Margin in points of a game that went to overtime, a whole number'),
  ('MARGIN_CAP', 24, 'ASSUMED', 'Cap in points on one game margin in the resume point differential, a whole number'),
  ('W_WIN_PCT', 100, 'ASSUMED', 'Resume weight of the win percentage'),
  ('W_SOS', 2.0, 'ASSUMED', 'Resume weight of the strength of schedule, in points of mean opponent rating'),
  ('W_PD', 0.5, 'ASSUMED', 'Resume weight of the capped point differential per game'),
  ('W_RATING', 0, 'ASSUMED', 'Resume weight of the team rating'),
  ('MOVE_THRESHOLD', 0.0070, 'ASSUMED', 'Change in the probability of making the playoff between two points that counts as a move: three standard errors of a difference of two 100,000-season runs'),
  ('MIN_BRANCH_SAMPLE', 100, 'ASSUMED', 'Fewest simulated seasons in a what-if branch for the rooting guide to trust it');
