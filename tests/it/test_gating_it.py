"""Scenarios of `cfbd-vs/gating`: gated tables stay visible but refuse queries without the feature.

After the deployment, `API_FEATURES` is `graphQl` (what the mock's `/info` reports), so every
gated table refuses queries until a test sets the feature it needs.
"""

from __future__ import annotations

import pytest

from tests.it.harness import Harness, Mock
from tests.it.plan_helpers import error_of, keys_of, table_query
from tests.it.support import where_clause
from tests.synthetic import fake

pytestmark = pytest.mark.it

WEATHER = "GAMES_WEATHER"
GATED_TABLES = {
    "GAMES_WEATHER": "weather",
    "SCOREBOARD": "scoreboard",
    "LIVE_PLAYS": "livePlayByPlay",
    "GAMES_PREVIEW_ADJUSTED": "adjustedMetrics",
    "WEPA_PLAYERS_KICKING": "adjustedMetrics",
    "WEPA_PLAYERS_PASSING": "adjustedMetrics",
    "WEPA_PLAYERS_RUSHING": "adjustedMetrics",
    "WEPA_TEAM_SEASON": "adjustedMetrics",
}
PARAMETER_COLUMNS = (
    "SELECT COLUMN_TABLE, COLUMN_NAME, COLUMN_COMMENT FROM EXA_ALL_COLUMNS "
    "WHERE COLUMN_SCHEMA = {vs} AND COLUMN_NAME LIKE 'P\\_%' ESCAPE '\\'"
)
ACCESS_DENIED_YEAR = "2024"
UNAUTHORIZED_YEAR = "2023"


def weather_query(harness: Harness, year: int = 2024) -> str:
    return table_query(harness, WEATHER, f"P_YEAR = {year}")


def minimal_query(harness: Harness, table: str) -> str:
    return table_query(harness, table, where_clause(table, fake.minimal_filter(table)) or None)


def test_gated_tables_exist_documented(harness: Harness) -> None:
    """gating: Gated endpoints exist as documented tables."""
    rows = harness.query(PARAMETER_COLUMNS, {"vs": harness.vs})
    comments: dict[str, list[str]] = {}
    for table, _, comment in rows:
        comments.setdefault(str(table), []).append(str(comment))
    for table, feature in GATED_TABLES.items():
        assert table in comments, f"{table} is missing from the virtual schema"
        assert comments[table], f"{table} has no parameter columns"
        for comment in comments[table]:
            assert feature in comment, f"a parameter comment of {table} lacks {feature}: {comment}"


def test_gated_refused_without_feature(harness: Harness, mock: Mock) -> None:
    """gating: A gated table refuses queries when the key lacks the feature."""
    assert "weather" not in harness.properties().get("API_FEATURES", "")
    message = error_of(harness, weather_query(harness))
    assert "weather" in message
    assert "API_FEATURES" in message
    assert "make deploy" in message
    assert harness.mock_hits() == 0


def test_gated_allowed_with_feature(harness: Harness) -> None:
    """gating: A gated table works when the key has the feature."""
    harness.set_property("API_FEATURES", "weather")
    assert keys_of(harness, weather_query(harness)) == ("/games/weather?seasonType=both&year=2024",)


def test_gating_fails_closed(harness: Harness) -> None:
    """gating: Gating fails closed while features are unknown."""
    harness.set_property("API_FEATURES", "")
    assert "API_FEATURES" not in harness.properties()
    for table, feature in GATED_TABLES.items():
        message = error_of(harness, f"EXPLAIN VIRTUAL {minimal_query(harness, table)}")
        assert feature in message, table
        assert "API_FEATURES" in message, table
    ungated = table_query(harness, "RATINGS_SP", "P_YEAR = 2024")
    assert keys_of(harness, ungated) == ("/ratings/sp?year=2024",)


def test_api_access_refusal_reported(harness: Harness, mock: Mock) -> None:
    """gating: An access refusal from the API is reported clearly."""
    harness.set_property("API_FEATURES", "weather")
    mock.script("/games/weather", query={"year": ACCESS_DENIED_YEAR}, status=403)
    mock.script("/games/weather", query={"year": UNAUTHORIZED_YEAR}, status=401)
    for year, status in ((ACCESS_DENIED_YEAR, 403), (UNAUTHORIZED_YEAR, 401)):
        message = error_of(harness, weather_query(harness, int(year)))
        assert "/games/weather" in message
        assert f"HTTP {status}" in message
        assert "the key has no access to this endpoint" in message
        assert harness.marker_key not in message
        assert harness.mock_hits("/games/weather", {"year": year}) == 1
    assert harness.mock_hits("/games/weather") == 2
