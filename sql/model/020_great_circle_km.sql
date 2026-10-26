-- Great-circle distance in kilometres (model/venues). Exasol's ST_DISTANCE on SRID 4326 is planar, in degrees
-- (probe 1.1c), so this is the one kilometre distance of the model. No model view uses it; analyses and the
-- chat call it for ad-hoc questions. The LEAST(1, ...) keeps rounding near antipodal points inside ASIN's domain.
CREATE OR REPLACE FUNCTION {MODEL_SCHEMA}.GREAT_CIRCLE_KM (LAT1 IN DOUBLE, LON1 IN DOUBLE, LAT2 IN DOUBLE, LON2 IN DOUBLE)
RETURN DOUBLE
IS
  H DOUBLE;
BEGIN
  H := POWER(SIN(RADIANS(LAT2 - LAT1) / 2), 2)
     + COS(RADIANS(LAT1)) * COS(RADIANS(LAT2)) * POWER(SIN(RADIANS(LON2 - LON1) / 2), 2);
  RETURN 2 * 6371.0088 * ASIN(LEAST(1, SQRT(H)));
END GREAT_CIRCLE_KM;
/
COMMENT ON FUNCTION {MODEL_SCHEMA}.GREAT_CIRCLE_KM IS 'Great-circle (haversine) distance in kilometres between two WGS 84 points given as latitude and longitude in degrees, on a sphere with the mean Earth radius of 6,371.0088 km. Arguments are LAT1, LON1, LAT2, LON2. NULL when any argument is NULL. Use it instead of ST_DISTANCE, which on SRID 4326 returns planar degrees.';
