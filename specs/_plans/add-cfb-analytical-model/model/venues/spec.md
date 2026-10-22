# Feature: Venue geography

Venues carry their location as a spatial point, so analyses and the chat can ask how far a team travels or which games were played in a region. Exasol has no `GEOGRAPHY` type: the point is a `GEOMETRY` with SRID 4326 (WGS 84, longitude and latitude in degrees). Distances in kilometres come from a great-circle function, because Exasol's spatial functions compute in the plane of the coordinates.

## Background

- `VENUE`: one row per venue in `CFB_CORE.VENUES`.
  - Columns: name, city, `STATE_CODE`, zip, country code, time zone, latitude, longitude, elevation as reported, capacity, construction year, `IS_GRASS`, `IS_DOME`.
  - `GEO_POINT` is a `GEOMETRY(4326)` point with x = longitude and y = latitude. It is NULL when either coordinate is missing; `HAS_LOCATION` says which.
- `GREAT_CIRCLE_KM(LAT1, LON1, LAT2, LON2)`: an SQL function that returns the great-circle (haversine) distance in kilometres on a sphere with the mean Earth radius of 6,371.0088 km. It returns NULL if any argument is NULL.
- `TEAM_GAME.TRAVEL_KM` is the great-circle distance from the team's current home venue (`TEAM.HOME_VENUE_ID`) to the game's venue.
  - It is 0 for a home game at the home venue.
  - It is NULL when either venue has no location.
  - Its comment says that historic stadium moves are not modeled: the current home venue stands in for every season.
- The examples run over the synthetic test league in the integration-test namespace:
  - Ashford Field: 33.0° N, 87.0° W;
  - Brookvale Bowl: 34.0° N, 87.0° W;
  - Neutral Dome: 29.95° N, 90.08° W;
  - Old Grounds: no coordinates.

## Scenarios

### Scenario: A venue location is a point with SRID 4326

* *GIVEN* Ashford Field at latitude 33.0 and longitude -87.0
* *WHEN* `SELECT ST_X(GEO_POINT), ST_Y(GEO_POINT) FROM CFB_MODEL.VENUE` is queried for Ashford Field
* *THEN* the result MUST be -87.0 and 33.0
* *AND* the column type of `GEO_POINT` in `EXA_ALL_COLUMNS` MUST be `GEOMETRY(4326)`

### Scenario: A venue without coordinates has no point

* *GIVEN* Old Grounds has no latitude and no longitude
* *WHEN* `CFB_MODEL.VENUE` is queried for Old Grounds
* *THEN* `GEO_POINT` MUST be NULL and `HAS_LOCATION` FALSE
* *AND* `CFB_MODEL.MODEL_CHECKS` MUST report `VENUE_WITHOUT_LOCATION` for 1885, the season of its game

### Scenario: Great-circle distances are in kilometres

* *GIVEN* a deployed model
* *WHEN* `GREAT_CIRCLE_KM(33.0, -87.0, 34.0, -87.0)`, `GREAT_CIRCLE_KM(33.0, -87.0, 33.0, -87.0)` and `GREAT_CIRCLE_KM(NULL, -87.0, 34.0, -87.0)` are evaluated
* *THEN* the first result MUST be between 111.1 and 111.3
* *AND* the second MUST be 0
* *AND* the third MUST be NULL

### Scenario: Travel distance per team and game

* *GIVEN* the 2024 week-14 game Ashford against Brookvale at Ashford Field, where Brookvale's home venue is Brookvale Bowl
* *AND* the 1885 game Ashford against Marston at Old Grounds
* *WHEN* `CFB_MODEL.TEAM_GAME` is queried for both games
* *THEN* Ashford's `TRAVEL_KM` MUST be 0 and Brookvale's between 111.1 and 111.3 in the 2024 game
* *AND* `TRAVEL_KM` MUST be NULL for both teams in the 1885 game

### Scenario: Venue points work with Exasol's spatial functions

* *GIVEN* the stored venues
* *WHEN* the venues whose `GEO_POINT` lies within the SRID 4326 polygon from 32° N to 35° N and from 88° W to 85° W are selected with `ST_WITHIN`
* *THEN* the result MUST be exactly Ashford Field and Brookvale Bowl
