-- Venue dimension (model/venues). One row per venue id; the newest fetch wins (REQ_AT, then the later slice
-- key, then the later row). GEO_POINT is NULL unless both coordinates exist: string concatenation turns a NULL
-- into '' and 'POINT ( 1)' would make the cast fail every query over this view (probe 1.1c).
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.VENUE (
  VENUE_ID           COMMENT IS 'CFBD venue id',
  VENUE_NAME         COMMENT IS 'Venue name as CFBD spells it; the name that games carry as their venue',
  CITY               COMMENT IS 'City of the venue as CFBD reports it',
  STATE_CODE         COMMENT IS 'State or province code as CFBD reports it (the mirror column STATE_)',
  ZIP                COMMENT IS 'Postal code as CFBD reports it',
  COUNTRY_CODE       COMMENT IS 'Country code as CFBD reports it',
  TIME_ZONE          COMMENT IS 'IANA time zone name of the venue as CFBD reports it',
  LATITUDE           COMMENT IS 'Latitude in degrees north (WGS 84) as CFBD reports it; NULL when unknown',
  LONGITUDE          COMMENT IS 'Longitude in degrees east (WGS 84, negative in the Americas) as CFBD reports it; NULL when unknown',
  ELEVATION          COMMENT IS 'Elevation exactly as CFBD reports it, as text and without a stated unit; NULL when unknown',
  CAPACITY           COMMENT IS 'Seating capacity as CFBD reports it',
  CONSTRUCTION_YEAR  COMMENT IS 'Year the venue was built, as CFBD reports it',
  IS_GRASS           COMMENT IS 'TRUE when the playing surface is grass; NULL when CFBD does not say',
  IS_DOME            COMMENT IS 'TRUE when the venue is a dome; NULL when CFBD does not say',
  HAS_LOCATION       COMMENT IS 'TRUE when both latitude and longitude are known, which is when GEO_POINT is not NULL',
  GEO_POINT          COMMENT IS 'Location as a GEOMETRY point with SRID 4326 (x = longitude, y = latitude, degrees); NULL when HAS_LOCATION is FALSE. ST_DISTANCE on it is planar, in degrees: use GREAT_CIRCLE_KM for kilometres, and compare with ST_WITHIN against a polygon cast to GEOMETRY(4326)'
) AS
SELECT
  "ID",
  "NAME",
  "CITY",
  "STATE_",
  "ZIP",
  "COUNTRY_CODE",
  "TIMEZONE",
  "LATITUDE",
  "LONGITUDE",
  "ELEVATION",
  "CAPACITY",
  "CONSTRUCTION_YEAR",
  "GRASS",
  "DOME",
  "LATITUDE" IS NOT NULL AND "LONGITUDE" IS NOT NULL,
  CASE
    WHEN "LONGITUDE" IS NULL OR "LATITUDE" IS NULL THEN NULL
    ELSE CAST('POINT (' || "LONGITUDE" || ' ' || "LATITUDE" || ')' AS GEOMETRY(4326))
  END
FROM {CORE_SCHEMA}."VENUES"
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY "ID"
  ORDER BY "REQ_AT" DESC, "REQ_SLICE" DESC, "ROW_NO" DESC
) = 1
COMMENT IS 'One row per venue, as CFBD stored it in CFB_CORE.VENUES (newest fetch wins). It carries the venue address, surface and capacity, and its location as a GEOMETRY(4326) point.';
