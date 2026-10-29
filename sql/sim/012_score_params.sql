-- The constants of SIM_PARAMS as one row with one column per key (sim-engine/score-model), so the score
-- model's formulas read SCORE_PARAMS.HFA instead of a lookup. A key missing from SIM_PARAMS gives NULL.
CREATE OR REPLACE FORCE VIEW {SIM_SCHEMA}.SCORE_PARAMS (
  HFA                COMMENT IS 'Home-field advantage in points, applied off neutral sites',
  B_SP               COMMENT IS 'Weight of the previous season SP+ rating in a team rating',
  B_ELO              COMMENT IS 'Weight of the current Elo above ELO_CENTER in a team rating',
  ELO_CENTER         COMMENT IS 'Elo of an average FBS team in the fit',
  B_ELO_ONLY         COMMENT IS 'Points per Elo point when a team has an Elo but no previous SP+',
  B_SP_ONLY          COMMENT IS 'Points per SP+ point when a team has a previous SP+ but no Elo',
  NON_FBS_RATING     COMMENT IS 'Rating in points of a non-FBS opponent without an Elo',
  FBS_DEFAULT_RATING COMMENT IS 'Rating in points of an FBS team with neither a previous SP+ nor an Elo',
  SIGMA_1            COMMENT IS 'Standard deviation of a game margin one week ahead, in points',
  TAU                COMMENT IS 'Growth of the margin standard deviation per week ahead, in points',
  TOTAL_INTERCEPT    COMMENT IS 'Intercept of the predicted game total in points',
  TOTAL_SLOPE        COMMENT IS 'Points of predicted game total per point of the four prior offense and defense ratings',
  TOTAL_DEFAULT      COMMENT IS 'Predicted game total in points when a team has no previous SP+',
  ELO_PROB_SCALE     COMMENT IS 'Elo points that separate a 10:1 win-odds ratio in the pregame probability from Elo',
  ELO_PROB_HFA       COMMENT IS 'Elo points of home-field advantage in the pregame probability from Elo',
  OT_MARGIN          COMMENT IS 'Margin in points of a game that went to overtime',
  MARGIN_CAP         COMMENT IS 'Cap in points on one game margin in the resume point differential',
  W_WIN_PCT          COMMENT IS 'Resume weight of the win percentage',
  W_SOS              COMMENT IS 'Resume weight of the strength of schedule',
  W_PD               COMMENT IS 'Resume weight of the capped point differential per game',
  W_RATING           COMMENT IS 'Resume weight of the team rating'
) AS
SELECT
  MAX(CASE WHEN "PARAM_KEY" = 'HFA' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'B_SP' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'B_ELO' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'ELO_CENTER' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'B_ELO_ONLY' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'B_SP_ONLY' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'NON_FBS_RATING' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'FBS_DEFAULT_RATING' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'SIGMA_1' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'TAU' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'TOTAL_INTERCEPT' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'TOTAL_SLOPE' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'TOTAL_DEFAULT' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'ELO_PROB_SCALE' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'ELO_PROB_HFA' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'OT_MARGIN' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'MARGIN_CAP' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'W_WIN_PCT' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'W_SOS' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'W_PD' THEN "PARAM_VALUE" END),
  MAX(CASE WHEN "PARAM_KEY" = 'W_RATING' THEN "PARAM_VALUE" END)
FROM {SIM_SCHEMA}.SIM_PARAMS
COMMENT IS 'One row per parameter set: the constants of SIM_PARAMS as columns, so the score model and the engine inputs read them without a lookup.';
