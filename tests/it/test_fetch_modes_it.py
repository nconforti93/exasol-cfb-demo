"""Scenarios of `cfbd-vs/fetch-modes` against the `TEST_` namespace and the mock (zero CFBD calls).

Live goes through the mock sidecar, replay through recordings in BucketFS (zero mock hits). One
test, `test_doh_fallback_resolves_and_verifies_tls`, carries the marker `network`: it needs HTTPS
egress to the real DoH endpoint and a TLS handshake with `api.collegefootballdata.com` (no HTTP
request to it, so no CFBD call). `make test-it` excludes it; `make test-network` runs only it.

Also covered here, beyond the scenario list: 401/403 as access errors, `@RAW` being owner-only and
a catalog-hash mismatch sending nothing.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest

from scripts import dns
from tests.it import doh_probe
from tests.it.fetch_helpers import (
    BEARER_PREFIX,
    CLOSED_DOH_URL,
    HostnameConnection,
    as_datetime,
    comparable,
    failure_text,
    fetch_raw,
    games_query,
    games_slice,
    hostname_connection,
    raw_fetch_sql,
    record_slice,
    recording_at,
    request_host,
    script_access_revoked,
    script_grant_present,
    target_of,
)
from tests.it.harness import HARNESS_SCHEMA, READER, Harness, Mock
from tests.synthetic import fake
from vs.cfbd_vs import replay, resolver

pytestmark = pytest.mark.it

TWO_YEARS = "P_YEAR IN (2019, 2020)"
ONE_YEAR = "P_YEAR = 2024"
MISSING_YEAR = "P_YEAR = 2023"
NO_ACCESS = "no access"
PRIVILEGE_ERROR = "insufficient privileges"
DOH_PROBE_SCRIPT = "DOH_PROBE"
ATTEMPT_COUNT = re.compile(r"after \d+ attempt")
RECORDED_MS = 321
RECORDED_AT = datetime(2024, 9, 1, 12, 34, 56, tzinfo=UTC)
CERTIFICATE_FAILURES = ("CERTIFICATE_VERIFY_FAILED", "SSLCertVerificationError", "verify failed")
UNREACHABLE_FAILURES = ("ConnectionRefusedError", "Connection refused")


def doh_entries(mock: Mock, kind: str) -> list[dict[str, Any]]:
    return [entry for entry in mock.doh_log() if entry["kind"] == kind]


def years(rows: list[dict[str, Any]]) -> dict[int, set[int]]:
    """`REQ_ATTEMPTS` of the rows, by `P_YEAR`."""
    found: dict[int, set[int]] = {}
    for row in rows:
        found.setdefault(int(row["P_YEAR"]), set()).add(int(row["REQ_ATTEMPTS"]))
    return found


def fail_on_unresolvable_host(harness: Harness, connection: HostnameConnection) -> str:
    text = failure_text(harness, games_query(harness, ONE_YEAR))
    assert connection.host in text
    assert harness.marker_key not in text
    assert harness.mock_hits("/games") == 0
    return text


def test_live_one_authenticated_get_per_request(harness: Harness, mock: Mock) -> None:
    """Scenario: Live mode sends one authenticated GET per planned request."""
    query = games_query(harness, TWO_YEARS)
    planned = harness.explain(query).slice_keys
    rows = harness.query_dicts(query)
    entries = mock.log()
    assert len(entries) == 2
    assert sorted(target_of(entry) for entry in entries) == sorted(planned)
    assert {entry["method"] for entry in entries} == {"GET"}
    assert {entry["headers"]["authorization"] for entry in entries} == {
        BEARER_PREFIX + harness.marker_key
    }
    assert {entry["headers"]["host"] for entry in entries} == {request_host(mock.address)}
    assert rows
    assert {row["REQ_SOURCE"] for row in rows} == {"LIVE"}


def test_429_and_5xx_retried(harness: Harness, mock: Mock) -> None:
    """Scenario: Rate limits and server errors are retried."""
    mock.script("/games", query={"year": "2019"}, times=1, status=429, retry_after=0)
    mock.script("/games", query={"year": "2020"}, times=2, status=503)
    rows = harness.query_dicts(games_query(harness, TWO_YEARS))
    assert years(rows) == {2019: {2}, 2020: {3}}
    assert harness.mock_hits("/games", {"year": "2019"}) == 2
    assert harness.mock_hits("/games", {"year": "2020"}) == 3


def test_persistent_failure_four_attempts(harness: Harness, mock: Mock) -> None:
    """Scenario: Persistent failures fail the query after four attempts."""
    mock.script("/games", query={"year": "2019"}, status=500)
    text = failure_text(harness, games_query(harness, "P_YEAR = 2019"))
    assert harness.mock_hits("/games") == 4
    assert "GAMES" in text
    assert games_slice(2019) in text
    assert "500" in text
    assert harness.marker_key not in text


@pytest.mark.parametrize(
    ("status", "message"),
    [(400, "year is not acceptable"), (404, "no such season"), (422, "cannot process year")],
)
def test_client_errors_not_retried(harness: Harness, mock: Mock, status: int, message: str) -> None:
    """Scenario: Client errors are not retried."""
    mock.script("/games", query={"year": "2019"}, status=status, body={"message": message})
    text = failure_text(harness, games_query(harness, "P_YEAR = 2019"))
    assert harness.mock_hits("/games") == 1
    assert games_slice(2019) in text
    assert str(status) in text
    assert message in text


@pytest.mark.parametrize("status", [401, 403])
def test_access_refusals_are_not_retried_and_say_so(
    harness: Harness, mock: Mock, status: int
) -> None:
    """Scenario: Client errors are not retried (401 and 403 are access errors)."""
    mock.script("/games", query={"year": "2019"}, status=status, body={"message": "tier"})
    text = failure_text(harness, games_query(harness, "P_YEAR = 2019"))
    assert harness.mock_hits("/games") == 1
    assert NO_ACCESS in text.lower()
    assert "/games" in text
    assert harness.marker_key not in text


def test_system_resolver_used_no_doh_call(harness: Harness, mock: Mock) -> None:
    """Scenario: The system resolver is used when it succeeds."""
    harness.set_property("DOH_URL", mock.doh_url)
    rows = harness.query_dicts(games_query(harness, ONE_YEAR))
    assert rows
    assert {row["REQ_SOURCE"] for row in rows} == {"LIVE"}
    assert harness.mock_hits("/games") == 1
    assert mock.doh_log() == []


@pytest.mark.network
def test_doh_fallback_resolves_and_verifies_tls(harness: Harness) -> None:
    """Scenario: DNS over HTTPS supplies the address when the system resolver fails.

    Needs HTTPS egress to the default DoH endpoint and a TLS handshake with
    `api.collegefootballdata.com` on 443. No HTTP request goes to that host (no CFBD call).
    The probe replaces the system resolver by one that fails for every host name
    (`tests/it/doh_probe.py`), so the DoH path runs whether or not the sandbox resolves names
    itself; the test never skips. Run it with `make test-network`.
    """
    harness.query(doh_probe.probe_ddl(HARNESS_SCHEMA, DOH_PROBE_SCRIPT))
    try:
        rows = harness.query(
            f"SELECT {HARNESS_SCHEMA}.{DOH_PROBE_SCRIPT}({{host}}, {{port}}, {{doh}})",
            {"host": dns.DEFAULT_HOST, "port": dns.DEFAULT_PORT, "doh": resolver.DEFAULT_DOH_URL},
        )
    finally:
        harness.query(f"DROP SCRIPT IF EXISTS {HARNESS_SCHEMA}.{DOH_PROBE_SCRIPT}")
    report = json.loads(rows[0][0])
    assert report["ok"] is True, f"DoH probe failed at {report.get('stage')}: {report.get('error')}"
    assert report["system_ok"] is False
    assert report["path"] == "doh"
    assert report["addresses"]
    assert report["tls"]["verified"] is True
    assert report["doh_requests"] == 1
    assert report["doh_urls"][0].startswith(resolver.DEFAULT_DOH_URL)
    assert f"name={dns.DEFAULT_HOST}" in report["doh_urls"][0]
    assert report["first"] == report["second"] == report["third"] == report["addresses"]


@pytest.mark.parametrize(
    ("label", "reasons"),
    [
        pytest.param("unreachable", UNREACHABLE_FAILURES, id="unreachable"),
        pytest.param("unverifiable certificate", CERTIFICATE_FAILURES, id="certificate"),
    ],
)
def test_doh_failure_reported(
    harness: Harness, mock: Mock, label: str, reasons: tuple[str, ...]
) -> None:
    """Scenario: DoH failures are reported clearly (unreachable endpoint, unverifiable certificate).

    The third branch of the scenario, a verified endpoint returning no A record, cannot be reached
    against the self-signed mock; `tests/unit/test_resolver.py` covers it.
    """
    with hostname_connection(harness, mock) as connection:
        url = CLOSED_DOH_URL if label == "unreachable" else mock.doh_url
        harness.set_property("DOH_URL", url)
        mock.set_doh_answers({connection.host: ["127.0.0.1"]})
        text = fail_on_unresolvable_host(harness, connection)
    assert url in text
    assert "make dns-check" in text
    assert "DoH" in text
    assert any(reason in text for reason in reasons), text
    assert ATTEMPT_COUNT.search(text) is None, text
    if label == "unreachable":
        assert mock.doh_log() == []
    else:
        assert len(doh_entries(mock, "connect")) == 1
        assert doh_entries(mock, "query") == []


def test_doh_off_raises_original_error(harness: Harness, mock: Mock) -> None:
    """Scenario: The DoH fallback can be switched off."""
    with hostname_connection(harness, mock) as connection:
        harness.set_property("DOH_URL", "off")
        mock.set_doh_answers({connection.host: ["127.0.0.1"]})
        text = fail_on_unresolvable_host(harness, connection)
    assert "DoH fallback is off" in text
    assert "DoH lookup via" not in text


def test_replay_without_network_or_key(harness: Harness, mock: Mock) -> None:
    """Scenario: Replay mode serves recordings without network or key."""
    slice_key = games_slice(2024)
    recording = recording_at(slice_key, ms=RECORDED_MS, requested_at=RECORDED_AT)
    harness.replay_area.write(recording)
    harness.wait_for_files([harness.replay_area.udf_file(slice_key)])
    query = games_query(harness, ONE_YEAR)
    with script_access_revoked(harness):
        assert not script_grant_present(harness)
        assert NO_ACCESS in failure_text(harness, query)
        harness.use_replay()
        rows = harness.query_dicts(query)
    assert rows
    assert {row["REQ_SOURCE"] for row in rows} == {"REPLAY"}
    assert {row["REQ_STATUS"] for row in rows} == {recording.status}
    assert {row["REQ_MS"] for row in rows} == {RECORDED_MS}
    assert {as_datetime(row["REQ_AT"]) for row in rows} == {RECORDED_AT.replace(tzinfo=None)}
    assert harness.mock_hits() == 0
    assert script_grant_present(harness)


def test_missing_recording_fails_clearly(harness: Harness, mock: Mock) -> None:
    """Scenario: A missing recording fails clearly."""
    harness.use_replay()
    text = failure_text(harness, games_query(harness, MISSING_YEAR))
    assert games_slice(2023) in text
    assert harness.replay_area.udf_path in text
    assert harness.mock_hits() == 0
    assert mock.doh_log() == []


def test_recording_replays_identically(harness: Harness, mock: Mock) -> None:
    """Scenario: A recorded response replays identically."""
    slice_key = games_slice(2024)
    query = games_query(harness, ONE_YEAR)
    record_slice(harness, slice_key)
    live = harness.query_dicts(query)
    harness.use_replay()
    replayed = harness.query_dicts(query)
    assert live
    assert {row["REQ_SOURCE"] for row in live} == {"LIVE"}
    assert {row["REQ_SOURCE"] for row in replayed} == {"REPLAY"}
    assert comparable(live) == comparable(replayed)
    assert {row["ROW_NO"] for row in live} == {row["ROW_NO"] for row in replayed}


def test_large_responses_recorded_in_parts(harness: Harness, mock: Mock) -> None:
    """Scenario: Large responses are recorded in parts."""
    slice_key = games_slice(2024)
    query = games_query(harness, ONE_YEAR)
    body = fake.large_response_for("/games", {"year": "2024"}).decode("utf-8")
    assert len(body) > replay.MAX_PART_CHARS
    mock.script("/games", query={"year": "2024"}, body=body)
    live = harness.query_dicts(query)
    parts = sorted(fetch_raw(harness, slice_key), key=lambda part: part["PART_NO"])
    assert len(parts) >= 2
    assert [part["PART_NO"] for part in parts] == list(range(1, len(parts) + 1))
    assert {part["PART_COUNT"] for part in parts} == {len(parts)}
    assert all(len(part["BODY_PART"]) <= replay.MAX_PART_CHARS for part in parts)
    assert "".join(part["BODY_PART"] for part in parts) == body
    harness.replay_area.write(replay.assemble_parts(parts))
    harness.wait_for_files([harness.replay_area.udf_file(slice_key)])
    harness.use_replay()
    replayed = harness.query_dicts(query)
    assert live
    assert comparable(live) == comparable(replayed)


def test_mode_switch_next_query(harness: Harness, mock: Mock) -> None:
    """Scenario: Switching the mode takes effect on the next query."""
    slice_key = games_slice(2024)
    query = games_query(harness, ONE_YEAR)
    harness.write_recordings([slice_key])
    first = harness.query_dicts(query)
    assert {row["REQ_SOURCE"] for row in first} == {"LIVE"}
    assert harness.mock_hits("/games") == 1
    harness.set_property("FETCH_MODE", "replay")
    second = harness.query_dicts(query)
    assert second
    assert {row["REQ_SOURCE"] for row in second} == {"REPLAY"}
    assert harness.mock_hits("/games") == 1


def test_raw_output_is_owner_only(harness: Harness, mock: Mock) -> None:
    """The owner records through the raw output; the reader's direct call is refused."""
    slice_key = games_slice(2024)
    parts = fetch_raw(harness, slice_key)
    assert parts
    assert {part["SLICE_KEY"] for part in parts} == {slice_key}
    assert harness.mock_hits("/games") == 1
    text = failure_text(harness, raw_fetch_sql(harness, slice_key), user=READER)
    assert PRIVILEGE_ERROR in text.lower()
    assert harness.marker_key not in text
    assert harness.mock_hits("/games") == 1


def test_catalog_hash_mismatch_sends_nothing(harness: Harness, mock: Mock) -> None:
    """A fetch UDF and an adapter from different catalogs refuse to fetch."""
    text = failure_text(harness, raw_fetch_sql(harness, games_slice(2024), catalog_hash="0" * 64))
    assert "different catalog versions" in text
    assert harness.mock_hits() == 0
