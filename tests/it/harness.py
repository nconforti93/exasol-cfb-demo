"""The integration harness: the `TEST_` namespace on Exasol Personal local, behind one object.

`build_harness()` (used by the session fixture in `conftest.py`) does, in order:

1. Refuses to start when a test virtual schema reads the real connection (`guards`).
2. Drops anything a crashed earlier session left (idempotent): the namespace, the test users and
   every connection named after the mock one (`owned_connections`).
3. Starts the mock CFBD sidecar and creates the replay directory in BucketFS.
4. As `sys`: creates the connection `CFBD_API_MOCK` (address of the mock, a generated marker key
   as its password) and the users `CFB_TEST_OWNER` and `CFB_TEST_READER` with random passwords
   that only live in this process. The owner also gets `IMPORT` and `EXPORT` (snapshot files).
5. As the owner: deploys the namespace (`vs.host.deploy`), grants the script-bound connection
   access, deploys again (the fetch UDF is unchanged, so the grant stays, and `API_FEATURES` is
   synced from the mock's `/info`), creates the `WAIT_FOR_FILES` helper UDF.
6. Deploys the loader (`loader.deploy`) with the base plan variant, and creates the registered-step
   test scripts (`step_scripts`) in the infra schema.
7. Lets the reader select from the virtual schema.

The loader plan: `deploy_plan(variant)` installs a variant of `tests/it/fixtures` (a module sets
`PLAN_VARIANT` and optionally `PLAN_CONFIG` for the `plan_variant` fixture, which deploys it once
per module), `set_now` moves the test clock, and the reset puts the module's plan back.

The model: `ensure_model()` (once per session and per change of the files) or `deploy_model()`
installs `TEST_CFB_MODEL` under the loader's run lock (`model/deploy.py`).
`seed_league(variant)` stores the synthetic league (`tests/synthetic/league.py`) in `TEST_CFB_CORE`
and `LOAD_LOG` by direct inserts and sets the league's clock. `write_league_recordings()` puts the
league's 2026 weekly slices into the replay store. `hold_run_lock()` holds the run lock in a second
session. `seed_scaled_league()` builds the `PERF_` namespace of the performance smoke
(`tests/it/model_support.py`). Model test modules use the plan variant `model`
(`PLAN_VARIANT = "model"`).

`Harness.close()` drops everything again, whether or not tests failed, and verifies that no test
object is left. Nothing reads `.env` (except the DSN) or writes a secret to disk; the target
(`TARGET=local|aws`, see `vs/host/target.py`) supplies the sys password from the Personal secrets
file at run time. An error that would show a generated password is re-raised without it.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, NoReturn

import pyexasol

from engine import deploy as sim_deploy
from loader import deploy as loader_deploy
from loader.runlock import HeldLock, run_lock
from model import deploy as model_deploy
from model import step as model_step
from scripts import grants
from tests.it import guards, loader_support, model_support, sim_support, step_scripts, support
from tests.it.loader_support import BASE_VARIANT, PlanState
from tests.it.support import HarnessError
from tests.mock_cfbd import sidecar
from tests.synthetic import fake
from tests.synthetic.league import League, base_league
from vs.host import deploy, fetchlog, redact, target
from vs.host.namespace import MOCK_CONNECTION, Namespace

PREFIX = "TEST_"
READER_USER = "CFB_TEST_READER"
APP_USER = "CFB_TEST_APP_WEB"
MCP_USER = "CFB_TEST_MCP_WEB"
HARNESS_SCHEMA = "TEST_CFB_HARNESS"
PROVIDER = "tests.synthetic.fake:response_for"
LOADED_BY = "IT_HARNESS"
WAIT_TIMEOUT_SECONDS = 30
PUSHDOWN_SQL_COLUMN = "PUSHDOWN_SQL"
LOADED_BY_FIELD = "loaded_by"
AD_HOC_VARIANT = "text-"
INFO_STATIC = "static"
INFO_COUNTING = "counting"
NOW_SETTING = "NOW_OVERRIDE"
STAGING_PROPERTIES = {"FETCH_MODE": "replay", "READ_THROUGH": "FALSE"}
OWNER_TRANSFER_PRIVILEGES = "IMPORT, EXPORT"
"""The test owner's snapshot and restore run as it, and pyexasol's file transfer is `EXPORT`
and `IMPORT` (the real deployment runs them as `sys`). Neither reads a connection: the mock key
stays bound to the fetch script."""

OWNER = "owner"
READER = "reader"
SYS = "sys"

_IDENTIFIER = re.compile(r"[A-Z][A-Z0-9_]*")
_VS_PROPERTIES = (
    "SELECT SCHEMA_NAME, PROPERTY_NAME, PROPERTY_VALUE FROM EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES"
)
_VS_NAMES = "SELECT SCHEMA_NAME FROM EXA_ALL_VIRTUAL_SCHEMAS"
_CONNECTION_NAMES = "SELECT CONNECTION_NAME FROM EXA_DBA_CONNECTIONS"
_NOW = re.compile(r"\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?")


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
        *(f"DROP SCHEMA IF EXISTS {schema} CASCADE" for schema in _all_schemas(namespace)),
        f"DROP CONNECTION IF EXISTS {MOCK_CONNECTION}",
        f"DROP USER IF EXISTS {READER_USER} CASCADE",
        f"DROP USER IF EXISTS {APP_USER} CASCADE",
        f"DROP USER IF EXISTS {MCP_USER} CASCADE",
        f"DROP USER IF EXISTS {namespace.owner_user} CASCADE",
        f"DROP ROLE IF EXISTS {namespace.app_role} CASCADE",
        f"DROP ROLE IF EXISTS {namespace.mcp_role} CASCADE",
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
    schemas = [namespace.vs_name, *_all_schemas(namespace)]
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
            f"WHERE USER_NAME IN ('{namespace.owner_user}', '{READER_USER}', "
            f"'{APP_USER}', '{MCP_USER}')"
        ).fetchall()
    ]
    found += [
        f"role {row[0]}"
        for row in connection.execute(
            "SELECT ROLE_NAME FROM EXA_DBA_ROLES "
            f"WHERE ROLE_NAME IN ('{namespace.app_role}', '{namespace.mcp_role}')"
        ).fetchall()
    ]
    found += [f"connection {name}" for name in _stale_connections(connection)]
    return found


def _namespace_schemas(namespace: Namespace) -> tuple[str, ...]:
    return (
        namespace.infra_schema,
        namespace.meta_schema,
        namespace.core_schema,
        namespace.stage_schema,
        namespace.restore_schema,
        namespace.model_schema,
        namespace.api_schema,
        namespace.api_live_schema,
        namespace.sim_schema,
        HARNESS_SCHEMA,
    )


def _perf_schemas() -> tuple[str, ...]:
    """The schemas of the performance namespace (`PERF_CFB_*`); the harness owns them too."""
    return tuple(
        schema
        for schema in _namespace_schemas(model_support.perf_namespace())
        if schema != HARNESS_SCHEMA
    )


def _all_schemas(namespace: Namespace) -> tuple[str, ...]:
    """Every schema the harness may drop: the test namespace's and the performance namespace's."""
    return (*_namespace_schemas(namespace), *_perf_schemas())


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

    def set_info(self, mode: str, **fields: Any) -> dict[str, Any]:
        """Set how `/info` answers; see `MockHandle.set_info`. Cleared by `reset`."""
        self.changed = True
        return self._handle.set_info(mode, **fields)

    def info_state(self) -> dict[str, Any]:
        """`{mode, base_used, fields, data_requests}` of the mock's `/info`."""
        return self._handle.info_state()

    def set_doh_answers(self, answers: Mapping[str, list[str]]) -> None:
        self.changed = True
        self._handle.set_doh_answers(answers)

    def reset(self) -> None:
        """Clear the logs, the scripted rules, key-echo mode and the `/info` mode."""
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
        self._plan: PlanState | None = None
        self._baseline: PlanState | None = None
        self._clock_dirty = False
        self._model_digest: str | None = None
        self._sim_digest: str | None = None
        self.loader_report: loader_deploy.LoaderReport | None = None
        self.model_report: model_deploy.ModelReport | None = None
        self.sim_report: sim_deploy.SimReport | None = None

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
    def stage(self) -> str:
        return self.namespace.stage_schema

    @property
    def model(self) -> str:
        return self.namespace.model_schema

    @property
    def sim(self) -> str:
        return self.namespace.sim_schema

    @property
    def reader(self) -> Any:
        """A connection of `CFB_TEST_READER`: only `SELECT` on the virtual schema."""
        if self._reader is None:
            self._reader = self._target.connect(READER_USER, self._reader_password)
        return self._reader

    @contextmanager
    def role_session(self, role: str) -> Iterator[Any]:
        """Connect as a disposable app or chat test user with only its serving role."""
        users = {self.namespace.app_role: APP_USER, self.namespace.mcp_role: MCP_USER}
        if role not in users:
            raise HarnessError(f"role {role!r} is not a test serving role")
        user = users[role]
        password = support.new_secret()
        connection = None
        try:
            self.sys.execute(
                f"CREATE USER {user} IDENTIFIED BY {{password!q}}", {"password": password}
            )
            self.sys.execute(f"GRANT CREATE SESSION TO {user}")
            self.sys.execute(f"GRANT {role} TO {user}")
            connection = self._target.connect(user, password)
            yield connection
        except Exception as error:
            reraise_without_secrets(error, [password])
        finally:
            if connection is not None:
                connection.close()
            self.sys.execute(f"DROP USER IF EXISTS {user} CASCADE")

    def connection_of(self, user: str) -> Any:
        if user == OWNER:
            return self.owner
        if user == SYS:
            return self.sys
        if user == READER:
            return self.reader
        raise HarnessError(f"unknown user {user!r}; use OWNER, READER or SYS")

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
        self,
        table: str,
        slice_key: str,
        rows: int = 0,
        *,
        final: bool = True,
        loaded_by: str = LOADED_BY,
        loaded_at: str | None = None,
    ) -> None:
        """Insert a `LOAD_LOG` row; the slice then counts as stored (when final).

        `LOADED_AT` is UTC like the loader's own rows (now, or `loaded_at` as `YYYY-MM-DD
        HH:MM:SS`). A `loaded_by` that starts with `BACKFILL#` or `REFRESH_WEEK#` makes the slice
        count as written by a loader run, so it can become an orphan.
        """
        moment = loader_support.UTC_NOW if loaded_at is None else "CAST({at} AS TIMESTAMP)"
        params: dict[str, Any] = {
            "table": table,
            "slice": slice_key,
            "final": _sql_boolean(final),
            "rows": rows,
            "by": loaded_by,
        }
        if loaded_at is not None:
            params["at"] = loaded_at
        self.query(
            f"INSERT INTO {self.meta}.LOAD_LOG VALUES "
            "({table}, {slice}, {final!r}, {rows!d}, " + moment + ", {by})",
            params,
        )

    def register_failure(
        self,
        table: str,
        slice_key: str,
        failures: int = 1,
        error: str = "forced failure",
        *,
        run_id: int | None = None,
    ) -> None:
        """Insert a `LOAD_FAILURES` row (UTC `LAST_FAILED_AT`); `failures` at the quarantine
        threshold quarantines the slice."""
        self.query(
            f"INSERT INTO {self.meta}.LOAD_FAILURES VALUES "
            "({table}, {slice}, {failures!d}, {error}, {run}, " + loader_support.UTC_NOW + ")",
            {
                "table": table,
                "slice": slice_key,
                "failures": failures,
                "error": error,
                "run": run_id,
            },
        )

    def stage_slices(
        self, table: str, where: str | None = None, *, loaded_by: str = LOADED_BY
    ) -> list[str]:
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
            self.register_slice(
                table, slice_key, int(counts.get(slice_key, 0)), loaded_by=loaded_by
            )
        return list(manifest.slice_keys)

    # --- the model: deploy, leagues, recordings, run lock --------------------------------------

    def deploy_model(self, directory: Path | None = None) -> model_deploy.ModelReport:
        """Lint and deploy `sql/model/` (or a fixture directory) into `TEST_CFB_MODEL` as the owner,
        under the loader's `DEPLOY` run lock. Raises what `model.deploy.deploy_model` raises
        (`ModelLintError` before any statement, `RunLockRefused`, `DeployError`); a failed deploy
        changes nothing, so the digest of what is deployed stays.
        """
        folder = directory or model_deploy.MODEL_DIR
        report = model_deploy.deploy_model(
            self.owner, self.namespace, model_deploy.ModelOptions(directory=folder)
        )
        self._model_digest = model_support.directory_digest(folder)
        self.model_report = report
        return report

    def ensure_model(self) -> None:
        """Deploy `sql/model/` unless exactly its current files are deployed (once per session).

        `rebuild` forgets the deployment; a test that deployed a fixture directory leaves a
        different digest, so the next `ensure_model` installs the real model again (the fixture's
        objects are dropped as orphans).
        """
        if self._model_digest != model_support.directory_digest(model_deploy.MODEL_DIR):
            self.deploy_model()

    def deploy_sim(self, options: sim_deploy.SimOptions | None = None) -> sim_deploy.SimReport:
        """Lint and deploy `sql/sim/` (or another directory) and the engine script into
        `TEST_CFB_SIM` as the owner, under the loader's `DEPLOY` run lock. Raises what
        `engine.deploy.deploy_sim` raises (`SimLintError` before any statement, `RunLockRefused`,
        `DeployError`); a failed deploy changes nothing, so the digest of what is deployed stays.
        """
        chosen = options or sim_deploy.SimOptions()
        report = sim_deploy.deploy_sim(self.owner, self.namespace, chosen)
        self._sim_digest = sim_support.deploy_digest(
            chosen.directory, chosen.data_directory, chosen.engine_directory, chosen.lua_directory
        )
        self.sim_report = report
        return report

    def ensure_sim(self) -> None:
        """Deploy `sql/sim/` unless exactly its current files and engine are deployed (once per
        session). A test that deployed other files leaves a different digest, so the next call
        installs the real files again; the data tables and their rows stay.
        """
        real = sim_deploy.SimOptions()
        wanted = sim_support.deploy_digest(
            real.directory, real.data_directory, real.engine_directory, real.lua_directory
        )
        if self._sim_digest != wanted:
            self.deploy_sim()

    @contextmanager
    def set_sim_params(self, **values: int | float | str | Decimal) -> Iterator[None]:
        """Overwrite constants of `SIM_PARAMS` for the block (keys as in the seed, numbers as
        text or numbers), then put the deployed values back. The reference table is not part of
        the per-test reset, so a test changes it only through this.
        """
        table = f"{self.sim}.SIM_PARAMS"
        keys = list(values)
        if not all(re.fullmatch(r"[A-Z0-9_]+", key) for key in keys):
            raise HarnessError(f"SIM_PARAMS keys are upper case words, got {keys}")
        listed = ", ".join(f"'{key}'" for key in keys)
        before = dict(
            self.query(f"SELECT PARAM_KEY, PARAM_VALUE FROM {table} WHERE PARAM_KEY IN ({listed})")
        )
        unknown = sorted(set(keys) - set(before))
        if unknown:
            raise HarnessError(f"unknown SIM_PARAMS keys {unknown}")

        def write(new_values: Mapping[str, Any]) -> None:
            for key, value in new_values.items():
                self.query(
                    f"UPDATE {table} SET PARAM_VALUE = {{value}} WHERE PARAM_KEY = {{key}}",
                    {"value": Decimal(str(value)), "key": key},
                )

        write(values)
        try:
            yield
        finally:
            write(before)

    def seed_league(self, variant: Callable[[League], League] | None = None) -> League:
        """Store the synthetic league in `TEST_CFB_CORE` and `LOAD_LOG`, and set the test clock.

        `variant` is applied to the base league (`tests.synthetic.league.with_*`, `mutate_after`
        with its arguments bound); the result is returned so tests can look up game ids. The data
        tables are emptied first, so calling it again re-seeds cleanly. The rows are what the
        foundation's flattener makes of API-shaped bodies, inserted directly (no virtual schema, no
        request). The clock is the league's `NOW_OVERRIDE`, 2026-10-28 12:00 UTC; the reset puts the
        module's clock back.
        """
        league = _league(variant)
        self._truncate_dirty_tables()
        model_support.seed_league_data(self.owner, self.namespace, league)
        self.set_now(model_support.SEED_CLOCK)
        self.rebuild_model()
        return league

    def rebuild_model(self) -> None:
        """Rebuild the model's derived tables from the stored rows (decision [40]), as the refresh
        step does: `REFRESH_MODEL(NULL, NULL, NULL, NULL)` as the owner. A test that changes stored
        rows (or a seed table a derived table reads) calls it before it reads the model; seeding
        and `mirror_edit` call it themselves. Nothing happens while no model is deployed.
        """
        if self._model_digest is None:
            return
        self.owner.execute(model_step.step_call(self.namespace))

    def write_league_recordings(
        self, league: League | None = None, *, wait: bool = True
    ) -> list[str]:
        """Put the replay recordings of the league's 2026 weekly slices (games, lines, SP+ and
        rankings) into the replay store, as a `REFRESH_WEEK` in replay mode would read them; returns
        the slice keys. `league` defaults to the base league.
        """
        recordings = (league or _league(None)).replay_recordings()
        for recording in recordings:
            self.replay_area.write(recording)
        keys = [recording.slice_key for recording in recordings]
        if wait:
            self.wait_for_files([self.replay_area.udf_file(key) for key in keys])
        return keys

    @contextmanager
    def hold_run_lock(
        self, kind: str = "RESTORE", args: str = "held by a test"
    ) -> Iterator[HeldLock]:
        """Hold the loader's run lock in a second owner session (another live session), so a
        `DEPLOY`, `BACKFILL` or `REFRESH_WEEK` of the harness's owner session is refused naming the
        run, until the block ends (the row ends `DONE`).

        `kind` is a host kind (`DEPLOY`, `RESTORE`, `SNAPSHOT`): a script kind's row is stale as
        soon as its session is idle, and this session is idle between statements.
        """
        with (
            self.owner_session() as session,
            run_lock(session, self.namespace, kind, args) as lock,
        ):
            yield lock

    def seed_scaled_league(
        self, *, force: bool = False, log: Callable[[str], None] | None = None
    ) -> dict[str, int]:
        """Build the `PERF_` namespace (`PERF_CFB_*`) and load the scaled league (about 250,000
        games) into it as the owner; returns rows per table, empty when it was already stored.

        Dropped with the rest of the harness's schemas at the start and the end of a session.
        """
        return model_support.seed_scaled_namespace(
            self.owner, model_support.perf_namespace(), force=force, log=log or _no_log
        )

    # --- the loader: plan variants, clock, scripts, logs ---------------------------------------

    @property
    def plan(self) -> PlanState | None:
        """The loader deployment the database holds now (None after a failed deploy)."""
        return self._plan

    def deploy_plan(
        self, variant: str = BASE_VARIANT, loader_config: Mapping[str, str] | None = None
    ) -> loader_deploy.LoaderReport:
        """Deploy the loader with a plan variant of `tests/it/fixtures` and `LOADER_CONFIG`
        overrides (`{"monthly_call_budget": "400", "now_override": "2020-10-20"}`).

        The plan tables are replaced wholesale, so switching variants is a redeploy. The reset
        after the test restores the module's plan (`use_plan`).
        """
        state = PlanState(variant, loader_support.freeze(loader_config))
        return self._deploy_loader(loader_support.variant_text(variant), state)

    def deploy_plan_text(
        self, plan_text: str, loader_config: Mapping[str, str] | None = None
    ) -> loader_deploy.LoaderReport:
        """Like `deploy_plan` for a plan written in the test (an invalid plan raises `DeployError`
        before any statement, so the deployed plan stays)."""
        digest = hashlib.sha256(plan_text.encode("utf-8")).hexdigest()[:12]
        state = PlanState(AD_HOC_VARIANT + digest, loader_support.freeze(loader_config))
        return self._deploy_loader(plan_text, state)

    def use_plan(
        self, variant: str = BASE_VARIANT, loader_config: Mapping[str, str] | None = None
    ) -> None:
        """Make a variant the plan every reset returns to; deploys it unless it is in place.

        The `plan_variant` fixture calls this once per test module.
        """
        wanted = PlanState(variant, loader_support.freeze(loader_config))
        self._baseline = wanted
        if self._plan != wanted:
            self.deploy_plan(variant, loader_config)

    def set_now(self, moment: str | None) -> None:
        """Set the loader's test clock (`NOW_OVERRIDE`, `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`,
        UTC); `None` returns to the real clock. Only the `TEST_` namespace keeps it. The reset
        puts the module's clock back."""
        if moment is not None and not _NOW.fullmatch(moment):
            raise HarnessError(
                f"the test clock is YYYY-MM-DD or YYYY-MM-DD HH:MM:SS, got {moment!r}"
            )
        self._write_clock(moment)
        self._clock_dirty = True

    def script(self, name: str, *arguments: object, user: str = OWNER) -> list[dict[str, Any]]:
        """`EXECUTE SCRIPT <infra>.<name>(arguments)`: the rows of its result table, by column.

        Never `WITH OUTPUT`. A script that raises raises `pyexasol.ExaError` here.
        """
        sql = loader_support.script_call_sql(f"{self.infra}.{name}", *arguments)
        return self.query_dicts(sql, user=user)

    def backfill(
        self,
        profile: str = "demo",
        from_season: int | None = None,
        max_calls: int | None = None,
        *,
        user: str = OWNER,
    ) -> list[dict[str, Any]]:
        """`BACKFILL(profile, from_season, max_calls)`: one row per table, then the total row
        (`TABLE_NAME = '*'`)."""
        return self.script("BACKFILL", profile, from_season, max_calls, user=user)

    def refresh(
        self,
        season: int | None,
        week: int | None,
        season_type: str | None = "regular",
        *,
        user: str = OWNER,
    ) -> list[dict[str, Any]]:
        """`REFRESH_WEEK(season, week, season_type)`: the step timeline."""
        return self.script("REFRESH_WEEK", season, week, season_type, user=user)

    def quota(self) -> dict[str, Any]:
        """The one row of `CFB_META.QUOTA` (reads `/info` through the virtual schema)."""
        rows = self._meta_rows("QUOTA")
        if len(rows) != 1:
            raise HarnessError(f"QUOTA must have exactly one row, got {len(rows)}")
        return rows[0]

    def load_status(self) -> list[dict[str, Any]]:
        """`CFB_META.LOAD_STATUS`, one row per plan entry, by entry."""
        return self._meta_rows("LOAD_STATUS", "ENTRY_ID")

    def load_slices(self, table: str | None = None) -> list[dict[str, Any]]:
        """`CFB_META.LOAD_SLICES` (every planned slice), by entry and slice key."""
        where = None if table is None else f"TABLE_NAME = '{_identifier(table)}'"
        return self._meta_rows("LOAD_SLICES", "ENTRY_ID, SLICE_KEY", where)

    def backfill_queue(self) -> list[dict[str, Any]]:
        """`CFB_META.BACKFILL_QUEUE` in loading order."""
        return self._meta_rows("BACKFILL_QUEUE", "QUEUE_ORDER")

    def runs(self) -> list[dict[str, Any]]:
        """`CFB_META.LOAD_RUN`, oldest first."""
        return self._meta_rows("LOAD_RUN", "RUN_ID")

    def steps(self, run_id: int | None = None) -> list[dict[str, Any]]:
        """`CFB_META.REFRESH_LOG` rows (of one run, or all), in run and step order."""
        where = None if run_id is None else f"RUN_ID = {int(run_id)}"
        return self._meta_rows("REFRESH_LOG", "RUN_ID, STEP_NO, STARTED_AT", where)

    def failures(self) -> list[dict[str, Any]]:
        """`CFB_META.LOAD_FAILURES`, by table and slice key."""
        return self._meta_rows("LOAD_FAILURES", "TABLE_NAME, SLICE_KEY")

    def script_info(
        self,
        *,
        used: int | None = None,
        limit: int | None = None,
        remaining: int | None = None,
        tier: str | None = None,
        reset_at: str | None = None,
        counting: bool = False,
    ) -> dict[str, Any]:
        """Script the mock's `/info` (cleared with the mock at the end of the test).

        Static (default): the given fields are served as they are; `remaining` defaults to
        `limit - used`. Counting: `used` is the starting point and every data request the mock
        receives adds one (`/info` itself is free), so `quota()["USED_CALLS"]` follows the
        loader's own requests. Returns the mock's `info_state()`.
        """
        fields: dict[str, Any] = {
            "limit": limit,
            "remaining": remaining,
            "tier": tier,
            "reset_at": reset_at,
        }
        if counting:
            return self.mock.set_info(INFO_COUNTING, base_used=used or 0, **fields)
        return self.mock.set_info(INFO_STATIC, used=used, **fields)

    def info_state(self) -> dict[str, Any]:
        """How the mock answers `/info`, and the data requests it counted."""
        return self.mock.info_state()

    def session_id(self, user: str = OWNER) -> int:
        """The session id of one of the harness connections."""
        return int(self.query("SELECT CURRENT_SESSION", user=user)[0][0])

    def kill_statement(self, session: int) -> None:
        """`KILL STATEMENT IN SESSION <id>` as `sys`: aborts what the session runs (it raises
        `ExaQueryAbortError` there) and keeps the session and its committed rows.

        A running UDF is not interrupted: the abort lands when the current UDF call returns, so a
        `TEST_SLEEP(n)` step ends after its `n` seconds.
        """
        self.query(loader_support.kill_statement_sql(session), user=SYS)

    @contextmanager
    def owner_session(self) -> Iterator[Any]:
        """A second pyexasol connection of the owner (another session; closed afterwards)."""
        connection = self._target.connect(self.namespace.owner_user, self._owner_password)
        try:
            yield connection
        finally:
            _close(connection)

    def rebuild(self) -> None:
        """Drop the namespace's deployment and deploy it again, as on a new machine.

        Drops the virtual schema and the infra, meta, core, stage and restore schemas (data, logs,
        plan, scripts), then deploys again: the fetch UDF is created anew, so the script-bound
        connection grant is applied again (foundation probe (o)), followed by the module's loader
        plan, the step scripts and the reader's grant. The users, the mock connection and the
        harness schema stay. The second deploy reads `/info` from the mock (free).
        """
        namespace = self.namespace
        statements = [
            f"DROP VIRTUAL SCHEMA IF EXISTS {namespace.vs_name} CASCADE",
            *(
                f"DROP SCHEMA IF EXISTS {schema} CASCADE"
                for schema in _namespace_schemas(namespace)
                if schema != HARNESS_SCHEMA
            ),
        ]
        for statement in statements:
            self.query(statement)
        self._plan = None
        self._model_digest = None
        self._sim_digest = None
        _deploy_namespace(self, self._owner_password, self._baseline or PlanState(BASE_VARIANT, ()))

    def reset_state(self, *, keep_data: bool = False) -> list[str]:
        """Leave the database as the deployment left it; returns problems instead of raising.

        Restores properties, truncates every non-empty data table (`CFB_CORE`, `CFB_META`,
        `CFB_STAGE` and the infra schema's step-call table) except the plan tables, deletes the
        recordings (waiting until a UDF no longer sees them), puts the module's loader plan and
        clock back, and resets the mock if the test changed it without the `mock` fixture.
        `TEST_CFB_MODEL` is never touched: its reference tables are seeded by the deploy.

        With `keep_data` (read-only model modules, marker `model_readonly`) the tables, the loader
        plan and the clock stay, so the league seeded once for the module survives every test.
        """
        problems: list[str] = []
        steps: list[tuple[str, Callable[[], object]]] = [("properties", self.reset_properties)]
        if not keep_data:
            steps.append(("tables", self._truncate_dirty_tables))
        steps.append(("recordings", self._clear_recordings))
        if not keep_data:
            steps.append(("loader plan", self._restore_plan))
        steps.append(("mock", self._reset_changed_mock))
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
        schemas = ", ".join(
            f"'{schema}'" for schema in [*loader_support.cleanup_schemas(self.namespace), self.sim]
        )
        rows = self.query(
            "SELECT TABLE_SCHEMA, TABLE_NAME FROM EXA_ALL_TABLES "
            f"WHERE TABLE_SCHEMA IN ({schemas}) AND TABLE_ROW_COUNT > 0"
        )
        dirty = sim_support.drop_reference_tables(rows, self.namespace)
        for schema, name in loader_support.tables_to_truncate(dirty, self.namespace):
            self.query(f"TRUNCATE TABLE {_identifier(schema)}.{_identifier(name)}")

    def _restore_plan(self) -> None:
        baseline = self._baseline
        if baseline is None:
            return
        if self._plan != baseline:
            self.deploy_plan(baseline.variant, baseline.config_overrides())
        elif self._clock_dirty:
            self._write_clock(baseline.now)
        self._clock_dirty = False

    def _reset_changed_mock(self) -> None:
        if not self._mock_armed and self.mock.changed:
            self.mock.reset()

    def _deploy_loader(self, plan_text: str, state: PlanState) -> loader_deploy.LoaderReport:
        options = loader_deploy.LoaderOptions(
            plan_text=plan_text, config_overrides=state.config_overrides()
        )
        loader_deploy.validate(self.namespace, options)
        try:
            report = loader_deploy.deploy(self.owner, self.namespace, options)
        except BaseException:
            self._plan = None
            raise
        self._plan = state
        self._clock_dirty = False
        self.loader_report = report
        return report

    def _write_clock(self, moment: str | None) -> None:
        table = f"{self.meta}.LOADER_CONFIG"
        self.query(f"DELETE FROM {table} WHERE SETTING_NAME = '{NOW_SETTING}'")
        if moment is not None:
            self.query(
                f"INSERT INTO {table} (SETTING_NAME, SETTING_VALUE) "
                f"VALUES ('{NOW_SETTING}', {{now}})",
                {"now": moment},
            )

    def _meta_rows(
        self, relation: str, order: str | None = None, where: str | None = None
    ) -> list[dict[str, Any]]:
        sql = f"SELECT * FROM {self.meta}.{_identifier(relation)}"
        if where:
            sql += f" WHERE {where}"
        if order:
            sql += f" ORDER BY {order}"
        return self.query_dicts(sql)

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
        grants.Step(
            f"let {namespace.owner_user} write and read snapshot files (pyexasol EXPORT/IMPORT)",
            f"GRANT {OWNER_TRANSFER_PRIVILEGES} TO {namespace.owner_user}",
        ),
    ):
        database.execute(step.sql, step.params)


def _deploy(harness: Harness, owner_password: str) -> None:
    harness.query(f"CREATE SCHEMA {HARNESS_SCHEMA}")
    harness.query(support.wait_script_sql(HARNESS_SCHEMA))
    _deploy_namespace(harness, owner_password, PlanState(BASE_VARIANT, ()))


def _deploy_namespace(harness: Harness, owner_password: str, plan: PlanState) -> None:
    namespace = harness.namespace
    properties = {"REPLAY_PATH": harness.replay_area.udf_path}
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
    harness.use_plan(plan.variant, plan.config_overrides())
    for statement in step_scripts.step_scripts_sql(namespace.infra_schema):
        harness.query(statement)
    step = grants.select_grant_step(namespace.vs_name, READER_USER)
    harness.query(step.sql, user=SYS)
    assert_no_real_connection(harness.sys)
    harness.baseline_properties = harness.properties()


def _league(variant: Callable[[League], League] | None) -> League:
    league = base_league()
    return variant(league) if variant is not None else league


def _no_log(_: str) -> None:
    return None


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
