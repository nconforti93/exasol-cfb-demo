"""Helpers shared by the fetch-log, fetch-modes and key-protection integration tests.

Kept out of `harness.py` (owned by the harness task): statements that call the fetch UDF
directly, the second connection whose address is a host name, script-bound grant changes,
failure text, and row comparison. Nothing here names the real CFBD connection.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pyexasol

from scripts import grants
from tests.it.harness import HARNESS_SCHEMA, OWNER, SYS, Harness, Mock
from tests.it.support import split_slice_key
from tests.synthetic import fake
from vs.cfbd_vs import replay
from vs.host.namespace import MOCK_CONNECTION

HOSTNAME_CONNECTION = "CFBD_API_MOCK_HOSTNAME"
UNRESOLVABLE_HOST = "cfbd-mock.invalid"
CLOSED_DOH_URL = "https://127.0.0.1:1/dns-query"
BEARER_PREFIX = "Bearer "
REDACTION = "[REDACTED]"
RAW_TABLE = "@RAW"
RAW_COLUMN_TYPES = (
    ("SLICE_KEY", "VARCHAR(2000) UTF8"),
    ("PART_NO", "DECIMAL(9,0)"),
    ("PART_COUNT", "DECIMAL(9,0)"),
    ("BODY_PART", "VARCHAR(2000000) UTF8"),
    ("HTTP_STATUS", "DECIMAL(3,0)"),
    ("ATTEMPTS", "DECIMAL(3,0)"),
    ("MS", "DECIMAL(9,0)"),
    ("REQUESTED_AT", "TIMESTAMP"),
)
TIMING_COLUMNS = frozenset({"REQ_SOURCE", "REQ_MS", "REQ_AT"})
STAGED_TABLE = "FETCH_LOG_STAGED"
PEEK_SCRIPT = "PEEK_CONNECTION"
PEEK_DENIED = "DENIED"
PEEK_READ = "READ"
PEEK_SCRIPT_SQL = (
    f"CREATE OR REPLACE PYTHON3 SCALAR SCRIPT {HARNESS_SCHEMA}.{PEEK_SCRIPT}"
    "(CONN VARCHAR(128)) RETURNS VARCHAR(2000) UTF8 AS\n"
    "def run(ctx):\n"
    "    try:\n"
    "        connection = exa.get_connection(ctx.CONN)\n"
    "    except Exception as error:\n"
    f"        return '{PEEK_DENIED} ' + type(error).__name__ + ': ' + str(error)[:500]\n"
    f"    return '{PEEK_READ} ' + str(connection.address)\n"
)


def as_datetime(value: Any) -> datetime:
    """A TIMESTAMP value of a result row (pyexasol returns text without a mapper) as naive UTC."""
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def games_query(harness: Harness, where: str, columns: str = "*") -> str:
    """`SELECT <columns> FROM <vs>.GAMES WHERE <where>`."""
    return f"SELECT {columns} FROM {harness.vs}.GAMES WHERE {where}"


def failure_text(harness: Harness, sql: str, *, user: str = OWNER) -> str:
    """The error text of a statement that must fail; fails the test when it does not."""
    try:
        harness.query(sql, user=user)
    except pyexasol.ExaError as error:
        return str(error)
    raise AssertionError(f"the statement was expected to fail: {sql[:300]}")


def games_slice(year: int, week: int | None = None) -> str:
    """The slice key the planner builds for a `GAMES` filter on year (and week)."""
    week_part = "" if week is None else f"&week={week}"
    return f"/games?seasonType=both{week_part}&year={year}"


def target_of(entry: Mapping[str, Any]) -> str:
    """The request target (path and query string) as the mock received it."""
    return str(entry["target"])


def request_host(address: str) -> str:
    """`host:port` of a mock address, as the `Host` header of a request to it."""
    return address.removeprefix("http://").removeprefix("https://").rstrip("/")


def comparable(
    rows: Iterable[Mapping[str, Any]], ignore: Iterable[str] = TIMING_COLUMNS
) -> list[tuple[tuple[str, Any], ...]]:
    """Rows without the ignored columns, as sorted tuples, so two results compare as sets."""
    dropped = set(ignore)
    prepared = [
        tuple(sorted((name, value) for name, value in row.items() if name not in dropped))
        for row in rows
    ]
    return sorted(prepared, key=repr)


def raw_fetch_sql(
    harness: Harness,
    slice_key: str,
    *,
    catalog_hash: str | None = None,
    connection: str = MOCK_CONNECTION,
) -> str:
    """A direct call of the fetch UDF in raw mode: the owner's way to record a slice."""
    emits = ", ".join(f"{name} {sql_type}" for name, sql_type in RAW_COLUMN_TYPES)
    literal_hash = catalog_hash or fake.catalog().catalog_hash
    return (
        f"SELECT {harness.namespace.fetch_script}("
        f"'{RAW_TABLE}', '{slice_key}', '{literal_hash}', 'live', '{connection}', "
        f"'{harness.replay_area.udf_path}', '', NULL) EMITS ({emits}) FROM DUAL"
    )


def fetch_raw(
    harness: Harness, slice_key: str, *, user: str = OWNER, catalog_hash: str | None = None
) -> list[dict[str, Any]]:
    """The raw parts of one slice, as dicts keyed by the raw column names."""
    sql = raw_fetch_sql(harness, slice_key, catalog_hash=catalog_hash)
    return harness.query_dicts(sql, user=user)


def record_slice(harness: Harness, slice_key: str) -> replay.Recording:
    """Record a slice from the mock through the raw output and store the recording."""
    recording = replay.assemble_parts(fetch_raw(harness, slice_key))
    harness.replay_area.write(recording)
    harness.wait_for_files([harness.replay_area.udf_file(slice_key)])
    return recording


def recording_at(
    slice_key: str, *, ms: int, requested_at: datetime, status: int = 200
) -> replay.Recording:
    """The synthetic response of a slice as a recording with chosen time and latency."""
    path, query = split_slice_key(slice_key)
    body = fake.response_for(path, query).decode("utf-8")
    return replay.Recording(slice_key, status, ms, requested_at, body)


def script_grant_present(harness: Harness) -> bool:
    """Whether the owner holds the script-bound grant of the mock connection for the fetch UDF."""
    database = grants.PyexasolDatabase(harness.sys)
    return database.script_grant_exists(
        MOCK_CONNECTION, harness.namespace.fetch_script, harness.namespace.owner_user
    )


def grant_script_access(harness: Harness, connection: str = MOCK_CONNECTION) -> None:
    """The DBA step after a redeploy: bind the connection to the fetch UDF again."""
    step = grants.script_grant_step(
        connection, harness.namespace.fetch_script, harness.namespace.owner_user
    )
    harness.query(step.sql, user=SYS)


@contextmanager
def script_access_revoked(harness: Harness) -> Iterator[None]:
    """Revoke the fetch UDF's access to the mock connection; always grant it back."""
    namespace = harness.namespace
    harness.query(
        f"REVOKE ACCESS ON CONNECTION {MOCK_CONNECTION} FOR SCRIPT {namespace.fetch_script} "
        f"FROM {namespace.owner_user}",
        user=SYS,
    )
    try:
        yield
    finally:
        grant_script_access(harness)


@dataclass(frozen=True)
class HostnameConnection:
    """A connection whose address names a host the UDF sandbox cannot resolve."""

    name: str
    host: str
    address: str


@contextmanager
def hostname_connection(harness: Harness, mock: Mock) -> Iterator[HostnameConnection]:
    """Create `HOSTNAME_CONNECTION` (marker key, unresolvable host, the mock's port), bind it to
    the fetch UDF, point the virtual schema at it, and undo all of it afterwards."""
    port = request_host(mock.address).rpartition(":")[2]
    address = f"http://{UNRESOLVABLE_HOST}:{port}"
    harness.query(f"DROP CONNECTION IF EXISTS {HOSTNAME_CONNECTION}", user=SYS)
    harness.query(
        f"CREATE CONNECTION {HOSTNAME_CONNECTION} TO {{address}} USER '' IDENTIFIED BY {{key}}",
        {"address": address, "key": harness.marker_key},
        user=SYS,
    )
    try:
        grant_script_access(harness, HOSTNAME_CONNECTION)
        harness.set_property("CONNECTION_NAME", HOSTNAME_CONNECTION)
        yield HostnameConnection(HOSTNAME_CONNECTION, UNRESOLVABLE_HOST, address)
    finally:
        with suppress(pyexasol.ExaError):
            harness.query(f"DROP CONNECTION IF EXISTS {HOSTNAME_CONNECTION}", user=SYS)


@contextmanager
def peek_script(harness: Harness) -> Iterator[str]:
    """A second script of the owner that tries `exa.get_connection`; yields its qualified name."""
    harness.query(PEEK_SCRIPT_SQL)
    try:
        yield f"{HARNESS_SCHEMA}.{PEEK_SCRIPT}"
    finally:
        with suppress(pyexasol.ExaError):
            harness.query(f"DROP SCRIPT IF EXISTS {HARNESS_SCHEMA}.{PEEK_SCRIPT}")
