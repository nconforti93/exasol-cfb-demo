"""Scenarios of `cfbd-vs/key-protection`, in the `TEST_` dedicated-owner model (decision [20]).

The real deployment is `sys`-owned; the scenarios scoped to `TEST_` in the spec (second script of
the owner, redeployed fetch UDF) are checked here with `CFB_TEST_OWNER`. The key is the marker key
of the mock connection `CFBD_API_MOCK`; the real connection is never used or named.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import pytest

from tests.it.fetch_helpers import (
    PEEK_DENIED,
    REDACTION,
    failure_text,
    games_query,
    games_slice,
    grant_script_access,
    peek_script,
    raw_fetch_sql,
    request_host,
    script_grant_present,
    target_of,
)
from tests.it.harness import OWNER, READER, Harness, Mock
from tests.synthetic import fake
from vs.host import deploy
from vs.host.namespace import MOCK_CONNECTION

pytestmark = pytest.mark.it

ONE_YEAR = "P_YEAR = 2024"
NO_ACCESS = "no access"
PRIVILEGE_ERROR = "insufficient privileges"
OTHER_HOSTS = ("http://other-host/", "../../other-path")


def live_rows(
    harness: Harness, where: str = ONE_YEAR, *, user: str = OWNER
) -> list[dict[str, Any]]:
    return harness.query_dicts(games_query(harness, where), user=user)


def test_only_fetch_udf_reads_key(harness: Harness, mock: Mock) -> None:
    """Scenario: Only the fetch UDF can read the key (`TEST_` namespace)."""
    assert {row["REQ_SOURCE"] for row in live_rows(harness)} == {"LIVE"}
    with peek_script(harness) as script:
        answer = harness.query(f"SELECT {script}('{MOCK_CONNECTION}')")[0][0]
    assert answer.startswith(PEEK_DENIED), answer
    assert harness.marker_key not in answer
    assert {row["REQ_SOURCE"] for row in live_rows(harness)} == {"LIVE"}


def test_reader_queries_without_connection_privilege(harness: Harness, mock: Mock) -> None:
    """Scenario: Readers query without any connection privilege."""
    rows = live_rows(harness, user=READER)
    assert rows
    assert {row["REQ_SOURCE"] for row in rows} == {"LIVE"}
    hits_after_query = harness.mock_hits()
    direct = failure_text(harness, raw_fetch_sql(harness, games_slice(2024)), user=READER)
    assert PRIVILEGE_ERROR in direct.lower()
    assert harness.marker_key not in direct
    assert harness.mock_hits() == hits_after_query
    listing = failure_text(harness, "SELECT * FROM EXA_DBA_CONNECTIONS", user=READER)
    assert harness.marker_key not in listing
    visible = harness.query("SELECT * FROM EXA_ALL_CONNECTIONS", user=READER)
    assert harness.marker_key not in repr(visible)


def test_key_never_in_sql_metadata_results(harness: Harness, mock: Mock) -> None:
    """Scenario: The key never appears in SQL, metadata or results."""
    query = games_query(harness, ONE_YEAR)
    texts: list[str] = []
    for user in (OWNER, READER):
        rows = harness.query_dicts(query, user=user)
        assert rows
        texts.append(repr(rows))
        texts.append(harness.pushdown_sql(query, user=user))
        texts.append(
            repr(
                harness.query(
                    "SELECT PROPERTY_NAME, PROPERTY_VALUE FROM EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES "
                    "WHERE SCHEMA_NAME = {vs}",
                    {"vs": harness.vs},
                    user=user,
                )
            )
        )
        texts.append(
            repr(
                harness.query(
                    "SELECT * FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA = {vs} "
                    "AND COLUMN_TABLE = 'GAMES'",
                    {"vs": harness.vs},
                    user=user,
                )
            )
        )
        texts.append(repr(harness.query("SELECT * FROM EXA_ALL_CONNECTIONS", user=user)))
    texts.append(
        repr(
            harness.query(
                "SELECT SCRIPT_NAME, SCRIPT_TEXT FROM EXA_ALL_SCRIPTS WHERE SCRIPT_SCHEMA = {s}",
                {"s": harness.infra},
            )
        )
    )
    assert len(texts) == 11
    for text in texts:
        assert harness.marker_key not in text


def test_key_redacted_from_errors_and_bodies(harness: Harness, mock: Mock) -> None:
    """Scenario: The key is redacted from errors and response bodies."""
    mock.set_echo_key(True)
    mock.script("/games", query={"year": "2019"}, status=500)
    body = fake.body_for("/games", {"year": "2020"})
    body[0]["notes"] = f"credential {harness.marker_key} in a field"
    mock.script("/games", query={"year": "2020"}, body=body)
    text = failure_text(harness, games_query(harness, "P_YEAR = 2019"))
    assert harness.marker_key not in text
    assert REDACTION in text
    rows = live_rows(harness, "P_YEAR = 2020")
    assert rows
    assert harness.marker_key not in repr(rows)
    assert any(REDACTION in str(row["NOTES"]) for row in rows)


@pytest.mark.parametrize("value", OTHER_HOSTS)
def test_key_only_sent_to_connection_address(harness: Harness, mock: Mock, value: str) -> None:
    """Scenario: The key is only sent to its own connection's address."""
    rows = live_rows(harness, f"{ONE_YEAR} AND P_TEAM = '{value}'")
    assert rows
    entries = mock.log()
    assert len(entries) == 1
    entry = entries[0]
    encoded = f"team={quote(value, safe='')}"
    assert entry["path"] == "/games"
    assert entry["headers"]["host"] == request_host(mock.address)
    assert encoded in target_of(entry)
    assert value not in target_of(entry)
    assert {row["REQ_SLICE"] for row in rows} == {target_of(entry)}
    assert harness.properties()["CONNECTION_NAME"] == MOCK_CONNECTION


def test_redeployed_fetch_udf_needs_grant(harness: Harness, mock: Mock) -> None:
    """Scenario: A redeployed fetch UDF needs the grant again (`TEST_` namespace)."""
    assert script_grant_present(harness)
    options = deploy.DeployOptions(
        connection_name=MOCK_CONNECTION,
        properties={"REPLAY_PATH": harness.replay_area.udf_path},
        sync_api_features=False,
        force_fetch_replace=True,
    )
    try:
        report = deploy.deploy(harness.owner, harness.namespace, options)
        assert report.fetch_udf == "replaced"
        assert not script_grant_present(harness)
        text = failure_text(harness, games_query(harness, ONE_YEAR))
        assert MOCK_CONNECTION in text
        assert NO_ACCESS in text
        assert harness.marker_key not in text
        grant_script_access(harness)
        assert script_grant_present(harness)
        assert {row["REQ_SOURCE"] for row in live_rows(harness)} == {"LIVE"}
    finally:
        if not script_grant_present(harness):
            grant_script_access(harness)
