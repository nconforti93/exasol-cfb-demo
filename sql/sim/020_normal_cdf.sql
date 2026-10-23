-- Standard normal CDF (sim-engine/score-model). Exasol has no built-in one. Abramowitz and Stegun 26.2.17,
-- absolute error below 7.5e-8; the win probability of a game margin and the engine's host checks use it.
CREATE OR REPLACE FUNCTION {SIM_SCHEMA}.NORMAL_CDF (X IN DOUBLE)
RETURN DOUBLE
IS
  K DOUBLE;
  TAIL DOUBLE;
BEGIN
  K := 1 / (1 + 0.2316419 * ABS(X));
  TAIL := EXP(-X * X / 2) / SQRT(2 * 3.14159265358979323846)
        * (K * (0.319381530 + K * (-0.356563782 + K * (1.781477937 + K * (-1.821255978 + K * 1.330274429)))));
  IF X >= 0 THEN
    RETURN 1 - TAIL;
  END IF;
  RETURN TAIL;
END NORMAL_CDF;
/
COMMENT ON FUNCTION {SIM_SCHEMA}.NORMAL_CDF IS 'Standard normal cumulative distribution function of X (Abramowitz and Stegun 26.2.17, absolute error below 7.5e-8). NULL when X is NULL. The score model uses it for the win probability of a game margin that the engine rounds to whole points.';
