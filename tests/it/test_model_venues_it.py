"""Integration tests of model/venues: `VENUE`, its `GEO_POINT` and `GREAT_CIRCLE_KM`.

Read-only module over the synthetic league (marker `model_readonly`): the one test that adds a
stored row removes it again before it ends. The expectations are the spec's own numbers and an
independent haversine, not the SQL under test.
"""

from __future__ import annotations

import math
from collections.abc import Iterator

import pytest

from tests.it.harness import Harness
from tests.synthetic import league_teams as lt
from tests.synthetic.league import League

PLAN_VARIANT = "model"
pytestmark = [pytest.mark.it, pytest.mark.model_readonly]

EARTH_RADIUS_KM = 6371.0088
GEO_POINT_TYPE = "GEOMETRY(4326)"
WITHIN_ALABAMA_BOX = "POLYGON ((-88 32, -85 32, -85 35, -88 35, -88 32))"  # 32 to 35 N, 88 to 85 W
LATER_FETCH = "2030-01-01 00:00:00"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    h = (
        math.sin((phi2 - phi1) / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def venue_row(harness: Harness, name: str) -> dict[str, object]:
    rows = harness.query_dicts(
        f"SELECT * FROM {harness.model}.VENUE WHERE VENUE_NAME = {{name}}", {"name": name}
    )
    assert len(rows) == 1, name
    return rows[0]


def test_venue_point_srid_4326(harness: Harness, league: League) -> None:
    point = harness.query(
        f"SELECT ST_X(GEO_POINT), ST_Y(GEO_POINT) FROM {harness.model}.VENUE "
        "WHERE VENUE_NAME = 'Ashford Field'"
    )
    assert point == [(-87.0, 33.0)]
    column_type = harness.query(
        "SELECT COLUMN_TYPE FROM EXA_ALL_COLUMNS "
        f"WHERE COLUMN_SCHEMA = '{harness.model}' AND COLUMN_TABLE = 'VENUE' "
        "AND COLUMN_NAME = 'GEO_POINT'"
    )
    assert column_type == [(GEO_POINT_TYPE,)]
    assert venue_row(harness, "Ashford Field")["HAS_LOCATION"] is True


def test_venue_without_coordinates(harness: Harness, league: League) -> None:
    old_grounds = venue_row(harness, lt.OLD_GROUNDS)
    assert old_grounds["GEO_POINT"] is None
    assert old_grounds["HAS_LOCATION"] is False
    assert old_grounds["LATITUDE"] is None and old_grounds["LONGITUDE"] is None
    every_row = harness.query(f"SELECT GEO_POINT FROM {harness.model}.VENUE")
    assert len(every_row) == len(lt.venues())


def test_one_row_per_venue_with_every_column(harness: Harness, league: League) -> None:
    assert harness.query(
        f"SELECT COUNT(*), COUNT(DISTINCT VENUE_ID) FROM {harness.model}.VENUE"
    ) == [(len(lt.venues()),) * 2]
    row = venue_row(harness, "Neutral Dome")
    expected = lt.VENUE_BY_NAME["Neutral Dome"]
    assert row["VENUE_ID"] == expected.id
    assert (row["STATE_CODE"], row["COUNTRY_CODE"]) == ("AL", "US")
    assert (row["IS_DOME"], row["IS_GRASS"]) == (True, False)
    assert row["LATITUDE"] == expected.latitude and row["LONGITUDE"] == expected.longitude


def test_great_circle_km(harness: Harness, league: League) -> None:
    one_degree, zero, missing = harness.query(
        f"SELECT {harness.model}.GREAT_CIRCLE_KM(33.0, -87.0, 34.0, -87.0), "
        f"{harness.model}.GREAT_CIRCLE_KM(33.0, -87.0, 33.0, -87.0), "
        f"{harness.model}.GREAT_CIRCLE_KM(NULL, -87.0, 34.0, -87.0)"
    )[0]
    assert 111.1 < one_degree < 111.3
    assert zero == 0
    assert missing is None


def test_great_circle_km_matches_an_independent_haversine(harness: Harness, league: League) -> None:
    rows = harness.query(
        "SELECT a.LATITUDE AS LAT1, a.LONGITUDE AS LON1, b.LATITUDE AS LAT2, b.LONGITUDE AS LON2, "
        f"{harness.model}.GREAT_CIRCLE_KM(a.LATITUDE, a.LONGITUDE, b.LATITUDE, b.LONGITUDE) "
        f"FROM {harness.model}.VENUE a CROSS JOIN {harness.model}.VENUE b "
        "WHERE a.HAS_LOCATION AND b.HAS_LOCATION"
    )
    assert len(rows) > 100
    for lat1, lon1, lat2, lon2, km in rows:
        assert km == pytest.approx(haversine_km(lat1, lon1, lat2, lon2), abs=1e-6)


def test_great_circle_km_is_not_the_planar_distance(harness: Harness, league: League) -> None:
    km, degrees = harness.query(
        f"SELECT {harness.model}.GREAT_CIRCLE_KM("
        "a.LATITUDE, a.LONGITUDE, b.LATITUDE, b.LONGITUDE), "
        "ST_DISTANCE(a.GEO_POINT, b.GEO_POINT) "
        f"FROM {harness.model}.VENUE a CROSS JOIN {harness.model}.VENUE b "
        "WHERE a.VENUE_NAME = 'Ashford Field' AND b.VENUE_NAME = 'Harbor Park'"
    )[0]
    assert km == pytest.approx(haversine_km(33.0, -87.0, 40.0, -74.0), abs=1e-6)
    assert degrees == pytest.approx(math.hypot(13.0, 7.0), abs=1e-9)


def test_spatial_within(harness: Harness, league: League) -> None:
    inside = harness.query(
        f"SELECT VENUE_NAME FROM {harness.model}.VENUE "
        f"WHERE ST_WITHIN(GEO_POINT, CAST('{WITHIN_ALABAMA_BOX}' AS GEOMETRY(4326))) "
        "ORDER BY VENUE_NAME"
    )
    assert inside == [("Ashford Field",), ("Brookvale Bowl",)]


VENUE_COLUMNS = (
    "ROW_NO, ID, NAME, CITY, STATE_, ZIP, COUNTRY_CODE, TIMEZONE, LATITUDE, LONGITUDE, "
    "ELEVATION, CAPACITY, CONSTRUCTION_YEAR, GRASS, DOME, REQ_SLICE, REQ_SOURCE, REQ_STATUS, "
    "REQ_ATTEMPTS, REQ_MS, REQ_ROWS, REQ_AT"
)


def add_ashford_fetch(
    harness: Harness, *, row_offset: int, latitude: str, capacity: int, slice_key: str
) -> None:
    """Store another fetch of Ashford Field, newer than the league's, with changed values."""
    core = harness.core
    harness.owner.execute(
        f"INSERT INTO {core}.VENUES ({VENUE_COLUMNS}) SELECT ROW_NO + {row_offset}, ID, NAME, "
        f"CITY, STATE_, ZIP, COUNTRY_CODE, TIMEZONE, {latitude}, LONGITUDE, ELEVATION, {capacity}, "
        f"CONSTRUCTION_YEAR, GRASS, DOME, '{slice_key}', REQ_SOURCE, REQ_STATUS, REQ_ATTEMPTS, "
        f"REQ_MS, REQ_ROWS, TIMESTAMP '{LATER_FETCH}' FROM {core}.VENUES "
        "WHERE NAME = 'Ashford Field' AND CAPACITY >= 10"
    )
    harness.rebuild_model()


@pytest.fixture
def extra_fetches(harness: Harness, league: League) -> Iterator[None]:
    yield
    harness.owner.execute(f"DELETE FROM {harness.core}.VENUES WHERE CAPACITY < 10")
    harness.rebuild_model()


def test_the_newest_fetch_of_a_venue_wins(
    harness: Harness, league: League, extra_fetches: None
) -> None:
    add_ashford_fetch(harness, row_offset=1000, latitude="LATITUDE", capacity=2, slice_key="a")
    add_ashford_fetch(harness, row_offset=2000, latitude="35.5", capacity=3, slice_key="b")
    row = venue_row(harness, "Ashford Field")
    assert (row["CAPACITY"], row["LATITUDE"]) == (3, 35.5)
    assert harness.query(
        f"SELECT ST_Y(GEO_POINT) FROM {harness.model}.VENUE WHERE VENUE_NAME = 'Ashford Field'"
    ) == [(35.5,)]
    assert harness.query(f"SELECT COUNT(*) FROM {harness.model}.VENUE") == [(len(lt.venues()),)]


def test_one_missing_coordinate_gives_no_point_and_no_error(
    harness: Harness, league: League, extra_fetches: None
) -> None:
    add_ashford_fetch(harness, row_offset=1000, latitude="NULL", capacity=2, slice_key="a")
    row = venue_row(harness, "Ashford Field")
    assert row["LONGITUDE"] == -87.0
    assert (row["HAS_LOCATION"], row["GEO_POINT"]) == (False, None)
    assert len(harness.query(f"SELECT GEO_POINT FROM {harness.model}.VENUE")) == len(lt.venues())
