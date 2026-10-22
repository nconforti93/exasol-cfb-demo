"""Scenarios of `cfbd-vs/properties`: defaults, validation and live effect of the schema properties.

The test virtual schema keeps its `INFRA_SCHEMA`, `META_SCHEMA`, `CORE_SCHEMA`, `CONNECTION_NAME`
and `REPLAY_PATH` properties throughout (they point it at the `TEST_` namespace and the mock).
`test_defaults_apply` therefore sets the other properties to non-default values and unsets them
again, instead of creating a second virtual schema that would default to the real schemas.
"""

from __future__ import annotations

import re

import pytest

from tests.it.harness import Harness, Mock
from tests.it.plan_helpers import error_of, explain_error_of, keys_of, table_query
from tests.it.support import where_clause
from tests.synthetic import fake

pytestmark = pytest.mark.it

CALENDAR = "CALENDAR"
REAL_META_SCHEMA = "CFB_META"
LOAD_LOG = "LOAD_LOG"
NON_DEFAULTS = {
    "FETCH_MODE": "replay",
    "READ_THROUGH": "FALSE",
    "MAX_CALLS_PER_QUERY": "3",
    "API_FEATURES": "weather",
}
SCHEMA_REFERENCE = re.compile(r'"([A-Z0-9_]*CFB_(?:INFRA|META|CORE))"')
INVALID_VALUES = [
    ("MAX_CALLS_PER_QUERY", "0", "an integer from 1 to 1000"),
    ("MAX_CALLS_PER_QUERY", "ten", "an integer from 1 to 1000"),
    ("FETCH_MODE", "record", "one of live, replay"),
    ("READ_THROUGH", "maybe", "one of TRUE, FALSE"),
    ("API_FEATURES", "weather,teleport", "a comma-separated subset of"),
    ("DOH_URL", "http://1.1.1.1/dns-query", "IP literal"),
    ("DOH_URL", "https://cloudflare-dns.com/dns-query", "IP literal"),
]


def calendar_query(harness: Harness) -> str:
    where = where_clause(CALENDAR, fake.minimal_filter(CALENDAR))
    return table_query(harness, CALENDAR, where)


def test_defaults_apply(harness: Harness) -> None:
    """properties: Defaults apply when properties are unset."""
    for name, value in NON_DEFAULTS.items():
        harness.set_property(name, value)
    configured = harness.explain(calendar_query(harness))
    assert (configured.fetch_mode, configured.read_through, configured.budget) == (
        "replay",
        False,
        3,
    )
    weather = table_query(harness, "GAMES_WEATHER", "P_YEAR = 2024")
    assert keys_of(harness, weather) == ("/games/weather?seasonType=both&year=2024",)
    for name in NON_DEFAULTS:
        harness.set_property(name, "")
    assert not set(NON_DEFAULTS) & set(harness.properties())
    defaults = harness.explain(calendar_query(harness))
    assert (defaults.fetch_mode, defaults.read_through, defaults.budget) == ("live", True, 10)
    assert "API_FEATURES" in explain_error_of(harness, weather)


@pytest.mark.parametrize(("name", "value", "allowed"), INVALID_VALUES)
def test_invalid_property_values_rejected(
    harness: Harness, name: str, value: str, allowed: str
) -> None:
    """properties: Invalid property values are rejected."""
    before = harness.properties()
    message = error_of(harness, f"ALTER VIRTUAL SCHEMA {harness.vs} SET {name} = '{value}'")
    assert f"property {name} must be" in message
    assert allowed in message
    assert harness.properties() == before


def test_unknown_property_rejected(harness: Harness) -> None:
    """properties: Unknown properties are rejected."""
    before = harness.properties()
    message = error_of(harness, f"ALTER VIRTUAL SCHEMA {harness.vs} SET MAX_CALL = '5'")
    assert "unknown property MAX_CALL" in message
    assert "known properties:" in message
    assert "MAX_CALLS_PER_QUERY" in message
    assert harness.properties() == before


def test_property_change_applies_next_query(harness: Harness) -> None:
    """properties: Property changes apply to the next query without a refresh."""
    harness.set_property("MAX_CALLS_PER_QUERY", "10")
    sql = table_query(harness, "GAMES", "P_YEAR BETWEEN 2013 AND 2024")
    assert "12 requests" in explain_error_of(harness, sql)
    harness.set_property("MAX_CALLS_PER_QUERY", "12")
    manifest = harness.explain(sql)
    assert manifest.planned_requests == 12
    assert manifest.budget == 12


def test_schema_properties_redirect(harness: Harness, mock: Mock) -> None:
    """properties: Schema properties redirect storage and fetch objects."""
    pushdown = harness.pushdown_sql(calendar_query(harness))
    assert set(SCHEMA_REFERENCE.findall(pushdown)) == {harness.infra, harness.meta, harness.core}
    assert all(name.startswith("TEST_") for name in SCHEMA_REFERENCE.findall(pushdown))
    real_log = f"{REAL_META_SCHEMA}.{LOAD_LOG}"
    present = harness.query(
        "SELECT COUNT(*) FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = {schema} AND TABLE_NAME = {name}",
        {"schema": REAL_META_SCHEMA, "name": LOAD_LOG},
        user="sys",
    )[0][0]
    before = harness.query(f"SELECT COUNT(*) FROM {real_log}", user="sys") if present else None
    harness.stage_slices(CALENDAR)
    assert harness.query(f"SELECT COUNT(*) FROM {harness.meta}.{LOAD_LOG}") == [(1,)]
    if before is not None:
        assert harness.query(f"SELECT COUNT(*) FROM {real_log}", user="sys") == before
    assert harness.mock_hits() == 0
