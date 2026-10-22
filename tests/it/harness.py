"""The integration harness: the `TEST_` namespace on Exasol Personal local, behind one object.

`build_harness()` (used by the session fixture in `conftest.py`) does, in order:

1. Refuses to start when a test virtual schema reads the real connection (`guards`).
2. Drops anything a crashed earlier session left (idempotent): the namespace, the test users and
   every connection named after the mock one (`owned_connections`).
3. Starts the mock CFBD sidecar and creates the replay directory in BucketFS.
4. As `sys`: creates the connection `CFBD_API_MOCK` (address of the mock, a generated marker key
   as its password) and the users `CFB_TEST_OWNER` and `CFB_TEST_READER` with random passwords
   that only live in this process.
5. As the owner: deploys the namespace (`vs.host.deploy`), grants the script-bound connection
   access, deploys again (the fetch UDF is unchanged, so the grant stays, and `API_FEATURES` is
   synced from the mock's `/info`), creates the `WAIT_FOR_FILES` helper UDF.
6. Lets the reader select from the virtual schema.

`Harness.close()` drops everything again, whether or not tests failed, and verifies that no test
object is left. Nothing reads `.env` (except the DSN) or writes a secret to disk; the target
(`TARGET=local|aws`, see `vs/host/target.py`) supplies the sys password from the Personal secrets
file at run time. An error that would show a generated password is re-raised without it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, NoReturn

import pyexasol

from scripts import grants
from tests.it import guards, support
from tests.it.support import HarnessError
from tests.mock_cfbd import sidecar
from tests.synthetic import fake
from vs.host import deploy, fetchlog, redact, target
from vs.host.namespace import MOCK_CONNECTION, Namespace

PREFIX = "TEST_"
READER_USER = "CFB_TEST_READER"
HARNESS_SCHEMA = "TEST_CFB_HARNESS"
PROVIDER = "tests.synthetic.fake:response_for"
LOADED_BY = "IT_HARNESS"
WAIT_TIMEOUT_SECONDS = 30
PUSHDOWN_SQL_COLUMN = "PUSHDOWN_SQL"
STAGING_PROPERTIES = {"FETCH_MODE": "replay", "READ_THROUGH": "FALSE"}

OWNER = "owner"
READER = "reader"
SYS = "sys"

_IDENTIFIER = re.compile(r"[A-Z][A-Z0-9_]*")
_VS_PROPERTIES = (
    "SELECT SCHEMA_NAME, PROPERTY_NAME, PROPERTY_VALUE FROM EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES"
)
_VS_NAMES = "SELECT SCHEMA_NAME FROM EXA_ALL_VIRTUAL_SCHEMAS"
_CONNECTION_NAMES = "SELECT CONNECTION_NAME FROM EXA_DBA_CONNECTIONS"


def virtual_schema_property_rows(connection: Any) -> list[tuple[str, str, str]]:
    return [tuple(row) for row in connection.execute(_VS_PROPERTIES).fetchall()]


def virtual_schema_names(connection: Any) -> list[str]:
    return [str(row[0]) for row in connection.execute(_VS_NAMES).fetchall()]


def owned_connections(names: Iterable[str]) -> list[str]:
    """The connections the harness may drop: the mock and any name built on it.

    A name that merely starts with the mock connection's can never be the real connection.
    """
    return sorted(name for name in names if name.startswith(MOCK_CONNECTION))


def assert_no_real_connection(connection: Any, prefix: str = PREFIX) -> None:
    """Raise `GuardError` when a test virtual schema reads the real connection.

    A schema counts even without a single property row: it then falls back to the real default.
    """
    flagged = guards.schemas_on_real_connection(
        virtual_schema_property_rows(connection), prefix, virtual_schema_names(connection)
    )
    if flagged:
        raise guards.GuardError(
            f"refusing to run: virtual schema(s) {', '.join(flagged)} read the real CFBD "
            "connection; integration tests make zero CFBD calls. Fix or drop them first."
        )


def drop_namespace(connection: Any, namespace: Namespace) -> list[str]:
    """Drop every object of the test namespace as `sys`; returns the statements that failed."""
    statements = [
        f"DROP VIRTUAL SCHEMA IF EXISTS {namespace.vs_name} CASCADE",
        *(
            f"DROP SCHEMA IF EXISTS {schema} CASCADE"
            for schema in (
                namespace.infra_schema,
                namespace.meta_schema,
                namespace.core_schema,
                HARNESS_SCHEMA,
            )
        ),
        f"DROP CONNECTION IF EXISTS {MOCK_CONNECTION}",
        f"DROP USER IF EXISTS {READER_USER} CASCADE",
        f"DROP USER IF EXISTS {namespace.owner_user} CASCADE",
    ]
    failures = []
    for name in _stale_connections(connection):
        if _IDENTIFIER.fullmatch(name):
            statements.append(f"DROP CONNECTION IF EXISTS {name}")
        else:
            failures.append(f"refusing to drop connection with an unexpected name: {name!r}")
    for statement in statements:
        try:
            connection.execute(statement)
        except pyexasol.ExaError as error:
            failures.append(f"{statement}: {_first_line(error)}")
    return failures


def leftovers(connection: Any, namespace: Namespace) -> list[str]:
    """Names of test objects that still exist."""
    schemas = [
        namespace.vs_name,
        namespace.infra_schema,
        namespace.meta_schema,
        namespace.core_schema,
        HARNESS_SCHEMA,
    ]
    found: list[str] = []
    listing = ", ".join(f"'{name}'" for name in schemas)
    found += [
        f"schema {row[0]}"
        for row in connection.execute(
            f"SELECT SCHEMA_NAME FROM EXA_ALL_SCHEMAS WHERE SCHEMA_NAME IN ({listing})"
        ).fetchall()
    ]
    found += [
        f"virtual schema {row[0]}"
        for row in connection.execute(
            f"SELECT SCHEMA_NAME FROM EXA_ALL_VIRTUAL_SCHEMAS WHERE SCHEMA_NAME IN ({listing})"
        ).fetchall()
    ]
    found += [
        f"user {row[0]}"
        for row in connection.execute(
            "SELECT USER_NAME FROM EXA_DBA_USERS "
            f"WHERE USER_NAME IN ('{namespace.owner_user}', '{READER_USER}')"
        ).fetchall()
    ]
    found += [f"connection {name}" for name in _stale_connections(connection)]
    return found


def _stale_connections(connection: Any) -> list[str]:
    return owned_connections(
        str(row[0]) for row in connection.execute(_CONNECTION_NAMES).fetchall()
    )


class Mock:
    """The mock sidecar as the tests see it: the `MockHandle` API, plus bookkeeping.

    Every admin call is a `podman exec` (about 1.5 to 2 s on this host), so the harness only
    resets the mock where it matters, see `Harness.arm_mock`. `changed` is set by the calls that
    leave state behind (scripted rules, key echo, DoH answers).
    """

    def __init__(self, handle: sidecar.MockHandle) -> None:
        self._handle = handle
        self.changed = False

    @property
    def address(self) -> str:
        return self._handle.address

    @property
    def doh_url(self) -> str:
        return self._handle.doh_url

    def hits(self, path: str | None = None, query: Mapping[str, str] | None = None) -> int:
        return self._handle.hits(path, query)

    def log(self) -> list[dict[str, Any]]:
        return self._handle.log()

    def doh_log(self) -> list[dict[str, Any]]:
        return self._handle.doh_log()

    def script(self, path: str, **behaviour: Any) -> int:
        """Script a behaviour for matching requests; see `MockHandle.script`."""
        self.changed = True
        return self._handle.script(path, **behaviour)

    def set_echo_key(self, enabled: bool) -> None:
        self.changed = True
        self._handle.set_echo_key(enabled)

    def set_doh_answers(self, answers: Mapping[str, list[str]]) -> None:
        self.changed = True
        self._handle.set_doh_answers(answers)

    def reset(self) -> None:
        """Clear the logs, the scripted rules and key-echo mode."""
        self._handle.reset()
        self.changed = False

    def stop(self) -> None:
        self._handle.stop()


class Harness:
    """What the integration tests use. Obtain it from the `harness` fixture.

    Connections: `owner` and `reader` are pyexasol connections of the test users, `sys` is the DBA
    (use it only for privilege experiments). `query`, `explain`, `set_property`, ... use the owner
    unless told otherwise. All names come from `namespace`.
    """

    def __init__(
        self,
        *,
        namespace: Namespace,
        deployment: target.Target,
        sys_connection: Any,
        owner: Any,
        mock: Mock,
        marker_key: str,
        replay_area: support.ReplayArea,
        owner_password: str,
        reader_password: str,
    ) -> None:
        self.namespace = namespace
        self.sys = sys_connection
        self.owner = owner
        self.mock = mock
        self._mock_armed = False
        self.marker_key = marker_key
        self.replay_area = replay_area
        self.deploy_report: deploy.DeployReport | None = None
        self.baseline_properties: dict[str, str] = {}
        self._target = deployment
        self._owner_password = owner_password
        self._reader_password = reader_password
        self._reader: Any = None

    @property
    def vs(self) -> str:
        return self.namespace.vs_name

    @property
    def core(self) -> str:
        return self.namespace.core_schema

    @property
    def meta(self) -> str:
        return self.namespace.meta_schema

    @property
    def infra(self) -> str:
        return self.namespace.infra_schema

    @property
    def reader(self) -> Any:
        """A connection of `CFB_TEST_READER`: only `SELECT` on the virtual schema."""
        if self._reader is None:
            self._reader = self._target.connect(READER_USER, self._reader_password)
        return self._reader

    def connection_of(self, user: str) -> Any:
        return {OWNER: self.owner, READER: self.reader, SYS: self.sys}[user]

    def query(
        self, sql: str, params: Mapping[str, Any] | None = None, *, user: str = OWNER
    ) -> list[tuple[Any, ...]]:
        """All rows of a statement (pyexasol `{name!q}` style parameters)."""
        statement = self.connection_of(user).execute(sql, dict(params) if params else None)
        return [tuple(row) for row in statement.fetchall()] if statement.columns() else []

    def query_dicts(
        self, sql: str, params: Mapping[str, Any] | None = None, *, user: str = OWNER
    ) -> list[dict[str, Any]]:
        """Rows keyed by column name."""
        statement = self.connection_of(user).execute(sql, dict(params) if params else None)
        names = list(statement.columns())
        return [dict(zip(names, row, strict=True)) for row in statement.fetchall()]

    def pushdown_sql(self, query: str, *, user: str = OWNER) -> str:
        """The pushdown SQL of `EXPLAIN VIRTUAL <query>`; makes no API call.

        Read by column name: `EXPLAIN VIRTUAL` returns `PUSHDOWN_ID, PUSHDOWN_SQL, PUSHDOWN_JSON,
        PUSHDOWN_INVOLVED_TABLES`, so the SQL is not the last column. Exactly one pushdown is
        expected (one virtual table per query).
        """
        statement = self.connection_of(user).execute(f"EXPLAIN VIRTUAL {query}")
        position = list(statement.columns()).index(PUSHDOWN_SQL_COLUMN)
        rows = statement.fetchall()
        if len(rows) != 1:
            raise HarnessError(f"expected exactly one pushdown in EXPLAIN VIRTUAL, got {len(rows)}")
        return str(rows[0][position])

    def explain(self, query: str, *, user: str = OWNER) -> fetchlog.Manifest:
        """The `/*CFBD {...}*/` manifest of a query's pushdown SQL."""
        return fetchlog.parse_manifest(self.pushdown_sql(query, user=user))

    def arm_mock(self) -> None:
        """Reset the mock so a test starts from zero hits and no rules (the `mock` fixture)."""
        self.mock.reset()
        self._mock_armed = True

    def disarm_mock(self) -> None:
        """Clear scripted rules and key echo again if the test set any."""
        self._mock_armed = False
        if self.mock.changed:
            self.mock.reset()

    def mock_hits(self, path: str | None = None, query: Mapping[str, str] | None = None) -> int:
        """API requests the mock saw since the test began (`path` may end in `*`).

        Needs the `mock` fixture, which starts the count at zero.
        """
        if not self._mock_armed:
            raise HarnessError("request the `mock` fixture in the test before counting mock hits")
        return self.mock.hits(path, query)

    def properties(self) -> dict[str, str]:
        """The current properties of the test virtual schema."""
        rows = self.query(
            "SELECT PROPERTY_NAME, PROPERTY_VALUE FROM EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES "
            "WHERE SCHEMA_NAME = {vs}",
            {"vs": self.vs},
        )
        return {str(name): str(value) for name, value in rows}

    def set_property(self, name: str, value: str) -> None:
        """`ALTER VIRTUAL SCHEMA ... SET name = value`; the test cleanup restores the baseline."""
        guards.check_property_change(name, value)
        self._set_properties({name: value})

    def use_replay(self) -> None:
        """Switch to `FETCH_MODE = replay`; `REPLAY_PATH` already is the test replay path."""
        self.set_property("FETCH_MODE", "replay")

    def reset_properties(self) -> list[str]:
        """Restore the properties the deployment left; returns the names that were changed."""
        return self._restore(self.baseline_properties)

    def write_recording(
        self,
        slice_key: str,
        body: Any = None,
        *,
        status: int = support.SYNTHETIC_STATUS,
        wait: bool = True,
    ) -> None:
        """Record a response for a slice; `body` defaults to the synthetic response."""
        recording = (
            support.synthetic_recording(slice_key)
            if body is None
            else support.recording_for(slice_key, body, status=status)
        )
        self.replay_area.write(recording)
        if wait:
            self.wait_for_files([self.replay_area.udf_file(slice_key)])

    def write_recordings(self, slice_keys: Iterable[str], *, wait: bool = True) -> None:
        """Record the synthetic response of every slice key, then wait until a UDF can read them."""
        keys = list(slice_keys)
        for key in keys:
            self.write_recording(key, wait=False)
        if wait:
            self.wait_for_files([self.replay_area.udf_file(key) for key in keys])

    def wait_for_files(self, udf_paths: Sequence[str], *, present: bool = True) -> float:
        """Poll inside a UDF until every path is visible (or, `present=False`, all are gone)."""
        if not udf_paths:
            return 0.0
        rows = self.query(
            f"SELECT {HARNESS_SCHEMA}.{support.WAIT_SCRIPT_NAME}"
            "({paths}, {present!r}, {wait!d})",
            {
                "paths": json.dumps(list(udf_paths)),
                "present": _sql_boolean(present),
                "wait": WAIT_TIMEOUT_SECONDS,
            },
        )
        return float(rows[0][0])

    def register_slice(
        self, table: str, slice_key: str, rows: int = 0, *, final: bool = True
    ) -> None:
        """Insert a `LOAD_LOG` row; the slice then counts as stored (when final)."""
        self.query(
            f"INSERT INTO {self.meta}.LOAD_LOG VALUES "
            "({table}, {slice}, {final!r}, {rows!d}, CURRENT_TIMESTAMP, {by})",
            {
                "table": table,
                "slice": slice_key,
                "final": _sql_boolean(final),
                "rows": rows,
                "by": LOADED_BY,
            },
        )

    def stage_slices(self, table: str, where: str | None = None) -> list[str]:
        """Store the slices a filter plans in `CFB_CORE.<table>` and register them in `LOAD_LOG`.

        `where` is SQL over the table's `P_` columns (default: the minimal rule-satisfying filter,
        `fake.minimal_filter`). The rows come from the virtual schema itself, in replay mode over
        synthetic recordings and without read-through, so the mock sees no request and the slice
        is identical to what a live fetch would have stored. Returns the planned slice keys,
        including slices whose response has no rows. The test cleanup truncates the tables.
        """
        _identifier(table)
        condition = where or support.where_clause(table, fake.minimal_filter(table))
        query = f"SELECT * FROM {self.vs}.{table} WHERE {condition}"
        with self._temporary_properties(STAGING_PROPERTIES):
            manifest = self.explain(query)
            self.write_recordings(manifest.slice_keys)
            self.query(f"INSERT INTO {self.core}.{table} {query}")
        counts = dict(
            self.query(
                f"SELECT REQ_SLICE, COUNT(*) FROM {self.core}.{table} GROUP BY REQ_SLICE",
            )
        )
        for slice_key in manifest.slice_keys:
            self.register_slice(table, slice_key, int(counts.get(slice_key, 0)))
        return list(manifest.slice_keys)

    def reset_state(self) -> list[str]:
        """Leave the database as the deployment left it; returns problems instead of raising.

        Restores properties, truncates every non-empty `CFB_CORE`/`CFB_META` table and deletes the
        recordings (waiting until a UDF no longer sees them). The mock is the `mock` fixture's.
        """
        problems: list[str] = []
        steps = (
            ("properties", self.reset_properties),
            ("tables", self._truncate_dirty_tables),
            ("recordings", self._clear_recordings),
        )
        for label, step in steps:
            try:
                step()
            except Exception as error:
                problems.append(f"reset of {label} failed: {type(error).__name__}: {error}")
        return problems

    def close(self) -> list[str]:
        """Drop the whole namespace, stop the mock, remove the replay directory, close sessions.

        Returns what could not be cleaned up; an empty list means the database is as it was.
        """
        problems: list[str] = []
        for connection in (self._reader, self.owner):
            _close(connection)
        self._reader = None
        problems += drop_namespace(self.sys, self.namespace)
        problems += [f"left behind: {name}" for name in leftovers(self.sys, self.namespace)]
        for label, step in (
            ("mock", self.mock.stop),
            ("replay directory", self.replay_area.remove),
        ):
            try:
                step()
            except Exception as error:
                problems.append(f"{label} cleanup failed: {error}")
        _close(self.sys)
        return problems

    def _clear_recordings(self) -> None:
        removed = self.replay_area.clear()
        if removed:
            self.wait_for_files(removed, present=False)

    def _truncate_dirty_tables(self) -> None:
        rows = self.query(
            "SELECT TABLE_SCHEMA, TABLE_NAME FROM EXA_ALL_TABLES "
            "WHERE TABLE_SCHEMA IN ({core}, {meta}) AND TABLE_ROW_COUNT > 0",
            {"core": self.core, "meta": self.meta},
        )
        for schema, name in rows:
            self.query(f"TRUNCATE TABLE {_identifier(schema)}.{_identifier(name)}")

    @contextmanager
    def _temporary_properties(self, changes: Mapping[str, str]) -> Any:
        before = self.properties()
        self._set_properties(changes)
        try:
            yield
        finally:
            self._restore(before)

    def _set_properties(self, changes: Mapping[str, str]) -> None:
        assignments = " ".join(f"{_identifier(name)} = {{{name.lower()}}}" for name in changes)
        self.query(
            f"ALTER VIRTUAL SCHEMA {self.vs} SET {assignments}",
            {name.lower(): value for name, value in changes.items()},
        )

    def _restore(self, target: Mapping[str, str]) -> list[str]:
        current = self.properties()
        changed = {name: value for name, value in target.items() if current.get(name) != value}
        removed = {name: "" for name in current if name not in target}
        if changed or removed:
            self._set_properties({**changed, **removed})
        return sorted({*changed, *removed})


def build_harness(*, deployment: target.Target | None = None) -> Harness:
    """Create the whole test namespace and return the `Harness`; undoes itself on failure.

    `deployment` defaults to `target.resolve()` (`TARGET`, `EXASOL_DSN`). An error that would show
    a generated password is re-raised as a `HarnessError` without it.
    """
    namespace = Namespace(PREFIX)
    deployment = deployment or target.resolve()
    require_local(deployment)
    sys_connection = deployment.connect()
    try:
        assert_no_real_connection(sys_connection)
    except BaseException:
        _close(sys_connection)
        raise
    try:
        clear_previous_run(sys_connection, namespace)
    except BaseException:
        _close(sys_connection)
        raise
    owner_password, reader_password = support.new_secret(), support.new_secret()
    secrets = [owner_password, reader_password]
    marker_key = support.new_marker_key()
    area = support.ReplayArea()
    mock: Mock | None = None
    harness: Harness | None = None
    try:
        area.create()
        mock = Mock(sidecar.start(PROVIDER))
        _create_connection(sys_connection, mock.address, marker_key)
        _create_users(sys_connection, namespace, owner_password, reader_password)
        owner = deployment.connect(namespace.owner_user, owner_password)
        harness = Harness(
            namespace=namespace,
            deployment=deployment,
            sys_connection=sys_connection,
            owner=owner,
            mock=mock,
            marker_key=marker_key,
            replay_area=area,
            owner_password=owner_password,
            reader_password=reader_password,
        )
        _deploy(harness, owner_password)
        return harness
    except BaseException as error:
        if harness is not None:
            harness.close()
        else:
            drop_namespace(sys_connection, namespace)
            if mock is not None:
                mock.stop()
            area.remove()
            _close(sys_connection)
        if isinstance(error, Exception):
            reraise_without_secrets(error, secrets)
        raise


def require_local(deployment: target.Target) -> None:
    """The mock sidecar is a container next to the local Exasol, so only `local` can run it."""
    if deployment.name != "local":
        raise HarnessError(
            "the integration tests need the local deployment (TARGET=local), "
            f"not {deployment.name}: the mock CFBD sidecar only runs next to the local Exasol "
            "container"
        )


def clear_previous_run(connection: Any, namespace: Namespace) -> None:
    """Drop what a crashed session left (stale test connections included) and verify it is gone."""
    problems = drop_namespace(connection, namespace)
    problems += [f"left behind: {name}" for name in leftovers(connection, namespace)]
    if problems:
        raise HarnessError(
            "cannot clear the leftovers of an earlier test run:\n  " + "\n  ".join(problems)
        )


def reraise_without_secrets(error: Exception, secrets: Iterable[str]) -> NoReturn:
    """Re-raise `error`, or a `HarnessError` without the secrets when it carries any."""
    clean = redact.scrub(error, secrets)
    if clean is error:
        raise error
    raise HarnessError(str(clean)) from None


def _create_connection(connection: Any, address: str, marker_key: str) -> None:
    connection.execute(
        f"CREATE CONNECTION {MOCK_CONNECTION} TO {{address}} USER '' IDENTIFIED BY {{key}}",
        {"address": address, "key": marker_key},
    )


def _create_users(
    connection: Any, namespace: Namespace, owner_password: str, reader_password: str
) -> None:
    database = grants.PyexasolDatabase(connection)
    report = grants.apply(database, namespace, MOCK_CONNECTION, owner_password)
    if report.exit_code != grants.EXIT_OK:
        raise HarnessError("grants failed:\n" + "\n".join(report.lines))
    for step in (
        grants.create_user_step(READER_USER, reader_password),
        grants.Step(f"let {READER_USER} log in", f"GRANT CREATE SESSION TO {READER_USER}"),
    ):
        database.execute(step.sql, step.params)


def _deploy(harness: Harness, owner_password: str) -> None:
    namespace = harness.namespace
    properties = {"REPLAY_PATH": harness.replay_area.udf_path}
    harness.query(f"CREATE SCHEMA {HARNESS_SCHEMA}")
    harness.query(support.wait_script_sql(HARNESS_SCHEMA))
    first = deploy.deploy(
        harness.owner,
        namespace,
        deploy.DeployOptions(
            connection_name=MOCK_CONNECTION, properties=properties, sync_api_features=False
        ),
    )
    report = grants.apply(
        grants.PyexasolDatabase(harness.sys), namespace, MOCK_CONNECTION, owner_password
    )
    if report.script_grant not in ("granted", "already granted"):
        raise HarnessError("script grant not in place:\n" + "\n".join(report.lines))
    second = deploy.deploy(
        harness.owner,
        namespace,
        deploy.DeployOptions(connection_name=MOCK_CONNECTION, properties=properties),
    )
    if second.fetch_udf != "unchanged" or second.api_features is None:
        raise HarnessError(
            "the second deploy did not keep the grant or sync API_FEATURES:\n"
            + "\n".join(first.lines + second.lines)
        )
    harness.deploy_report = second
    step = grants.select_grant_step(namespace.vs_name, READER_USER)
    harness.query(step.sql, user=SYS)
    assert_no_real_connection(harness.sys)
    harness.baseline_properties = harness.properties()


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise HarnessError(f"{value!r} is not an UPPER_CASE identifier")
    return value


def _sql_boolean(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def _close(connection: Any) -> None:
    if connection is None:
        return
    try:
        connection.close()
    except Exception:
        return


def _first_line(error: Exception) -> str:
    message = str(getattr(error, "message", error)).strip()
    return message.splitlines()[0] if message else type(error).__name__
