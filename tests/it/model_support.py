"""The model side of the integration harness: seeding the synthetic leagues, the `PERF_` namespace.

Nothing here needs the harness object; every function takes a pyexasol connection (the harness
passes the test owner's) and a `Namespace`.

- `insert_rows` loads rows into a table with pyexasol's CSV `IMPORT` (`import_from_iterable`),
  the same path the snapshot restore uses; the owner holds the `IMPORT` privilege.
- `seed_league_data` stores a `League` (`tests/synthetic/league.py`): the mirror rows of every
  stored table in `CFB_CORE` and the `LOAD_LOG` registrations, exactly what a loader would have
  stored. The caller empties the tables first.
- `seed_scaled_namespace` builds the `PERF_` namespace for the performance smoke: schemas, meta
  tables, mirrors and the loader (plan variant `model`, without a virtual schema, which a
  performance run does not need and a host command cannot create), then loads the scaled league in
  batches. It is idempotent: a namespace that already holds the whole scaled league is left alone.
- `directory_digest` tells whether the deployed model files changed.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from loader import deploy as loader_deploy
from loader.plan import default_catalog
from tests.it import loader_support
from tests.synthetic import league as league_module
from tests.synthetic import scaled_league
from tests.synthetic.league import League
from tests.synthetic.slices import Row, Slice, load_log_row, slice_rows
from vs.host import metaddl
from vs.host.deploy import MIRRORS_PATH, ensure_tables, mirror_tables
from vs.host.deploy import meta_tables as foundation_meta_tables
from vs.host.namespace import Namespace

MODEL_VARIANT = "model"
PERF_PREFIX = "PERF_"
CLOCK_FORMAT = "%Y-%m-%d %H:%M:%S"
SEED_CLOCK = league_module.NOW_OVERRIDE.strftime(CLOCK_FORMAT)
IMPORT_BATCH_ROWS = 50_000
LOAD_LOG_TABLE = "LOAD_LOG"
GAMES_TABLE = "GAMES"
CLOCK_SETTING = "NOW_OVERRIDE"

Progress = Callable[[str], None]

_REFRESH_STEPS = re.compile(r"^refresh_steps:\n(?:  - .*\n)+", re.MULTILINE)


def _silent(_: str) -> None:
    return None


def perf_namespace() -> Namespace:
    """The namespace of the performance smoke: `PERF_CFB_*`."""
    return Namespace(PERF_PREFIX)


def perf_plan_text() -> str:
    """The `model` plan variant without registered steps: `PERF_` has no step scripts."""
    text = loader_support.variant_text(MODEL_VARIANT)
    if _REFRESH_STEPS.search(text) is None:
        raise ValueError("the model plan variant has no refresh_steps block to empty")
    return _REFRESH_STEPS.sub("refresh_steps: []\n", text, count=1)


def directory_digest(directory: Path) -> str:
    """A hash of the names and bytes of the `*.sql` files of a directory (not subdirectories)."""
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.sql")):
        digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def insert_rows(connection: Any, schema: str, table: str, rows: Iterable[Row]) -> int:
    """Load `rows` (tuples in the table's column order) with a CSV import; returns the count."""
    batch = list(rows)
    if batch:
        connection.import_from_iterable(batch, (schema, table))
    return len(batch)


def seed_league_data(connection: Any, namespace: Namespace, league: League) -> dict[str, int]:
    """Store the league's mirror rows and `LOAD_LOG` registrations; returns rows per table.

    The tables must be empty (the harness truncates them first), or natural keys are stored twice.
    """
    counts = {
        table: insert_rows(connection, namespace.core_schema, table, league.rows(table))
        for table in league.tables()
    }
    counts[LOAD_LOG_TABLE] = insert_rows(
        connection, namespace.meta_schema, LOAD_LOG_TABLE, league.load_log_rows()
    )
    return counts


def write_clock(connection: Any, namespace: Namespace, moment: str | None) -> None:
    """Set (or, with `None`, remove) `NOW_OVERRIDE` in the namespace's `LOADER_CONFIG`."""
    table = f"{namespace.meta_schema}.LOADER_CONFIG"
    connection.execute(f"DELETE FROM {table} WHERE SETTING_NAME = '{CLOCK_SETTING}'")
    if moment is not None:
        connection.execute(
            f"INSERT INTO {table} (SETTING_NAME, SETTING_VALUE) VALUES ('{CLOCK_SETTING}', "
            f"'{moment}')"
        )


VIEWS_NEEDING_THE_VIRTUAL_SCHEMA = ("quota",)


class _PerfDeployment(loader_deploy._Deployment):
    """The loader deployment for a namespace without a virtual schema (`PERF_`).

    It skips the virtual schema check and the views that read `<vs>.INFO` (`QUOTA`); the model
    reads none of them.
    """

    def _require_foundation(self) -> None:
        return None

    def _create_views(self) -> None:
        names = [
            name
            for name in metaddl.loader_view_names()
            if name not in VIEWS_NEEDING_THE_VIRTUAL_SCHEMA
        ]
        for name in names:
            self._execute(metaddl.render_loader_view(name, self._namespace).strip())
        self._lines.append(
            f"views in {self._namespace.meta_schema}: "
            f"{', '.join(name.upper() for name in names)} replaced"
        )


def deploy_perf_foundation(connection: Any, namespace: Namespace) -> list[str]:
    """Schemas, meta tables, mirrors and the loader (plan variant `model`) of `namespace`.

    The foundation's own deploy (`vs/host/deploy.py`) also creates the adapter, the fetch UDF and a
    virtual schema over a connection; none of them is needed to query the model, and a host command
    cannot count on the mock connection. Returns the report lines.
    """
    lines = []
    for schema in (namespace.infra_schema, namespace.meta_schema, namespace.core_schema):
        connection.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    lines.append(ensure_tables(connection, foundation_meta_tables(namespace), "meta tables"))
    mirrors = mirror_tables(MIRRORS_PATH.read_text(encoding="utf-8"), default_catalog(), namespace)
    lines.append(ensure_tables(connection, mirrors, "mirror tables"))
    options = loader_deploy.LoaderOptions(plan_text=perf_plan_text())
    prepared = loader_deploy._prepare(namespace, options)
    lines.extend(_PerfDeployment(connection, namespace, options, prepared).run().lines)
    write_clock(connection, namespace, SEED_CLOCK)
    return lines


def scaled_league_is_stored(connection: Any, namespace: Namespace) -> bool:
    """Whether `CFB_CORE.GAMES` of the namespace holds exactly the scaled league's games."""
    found = connection.execute(
        "SELECT COUNT(*) FROM EXA_ALL_TABLES "
        f"WHERE TABLE_SCHEMA = '{namespace.core_schema}' AND TABLE_NAME = '{GAMES_TABLE}'"
    ).fetchall()
    if int(found[0][0]) == 0:
        return False
    games = connection.execute(
        f"SELECT COUNT(*) FROM {namespace.core_schema}.{GAMES_TABLE}"
    ).fetchall()
    log = connection.execute(f"SELECT COUNT(*) FROM {namespace.meta_schema}.LOAD_LOG").fetchall()
    return int(games[0][0]) == scaled_league.total_games() and int(log[0][0]) > 0


def seed_scaled_namespace(
    connection: Any,
    namespace: Namespace | None = None,
    *,
    force: bool = False,
    log: Progress = _silent,
) -> dict[str, int]:
    """Deploy the performance namespace and load the scaled league; returns rows per table.

    A namespace that already holds the whole league is not touched (`force` reloads it).
    """
    namespace = namespace or perf_namespace()
    for line in deploy_perf_foundation(connection, namespace):
        log(line)
    if not force and scaled_league_is_stored(connection, namespace):
        log(f"scaled league already stored in {namespace.core_schema}")
        return {}
    for table in _tables_to_empty(connection, namespace):
        connection.execute(f"TRUNCATE TABLE {table}")
    writer = _BatchWriter(connection, namespace, log)
    for item in scaled_league.ScaledLeague().iter_slices():
        writer.add(item)
    return writer.finish()


def _tables_to_empty(connection: Any, namespace: Namespace) -> list[str]:
    rows = connection.execute(
        "SELECT TABLE_SCHEMA, TABLE_NAME FROM EXA_ALL_TABLES "
        f"WHERE TABLE_SCHEMA IN ('{namespace.core_schema}', '{namespace.meta_schema}') "
        "AND TABLE_ROW_COUNT > 0"
    ).fetchall()
    kept = {(namespace.meta_schema, name) for name in loader_support.PLAN_TABLES}
    return [f"{schema}.{name}" for schema, name in map(tuple, rows) if (schema, name) not in kept]


class _BatchWriter:
    """Collects rows per table and imports a table when its batch is full."""

    def __init__(self, connection: Any, namespace: Namespace, log: Progress) -> None:
        self._connection = connection
        self._namespace = namespace
        self._log = log
        self._pending: dict[tuple[str, str], list[Row]] = {}
        self._totals: dict[str, int] = {}

    def add(self, item: Slice) -> None:
        rows = slice_rows(item)
        self._queue(self._namespace.core_schema, item.table, rows)
        self._queue(self._namespace.meta_schema, LOAD_LOG_TABLE, [load_log_row(item, len(rows))])

    def finish(self) -> dict[str, int]:
        for key in list(self._pending):
            self._flush(key)
        return dict(self._totals)

    def _queue(self, schema: str, table: str, rows: Sequence[Row]) -> None:
        key = (schema, table)
        self._pending.setdefault(key, []).extend(rows)
        if len(self._pending[key]) >= IMPORT_BATCH_ROWS:
            self._flush(key)

    def _flush(self, key: tuple[str, str]) -> None:
        rows = self._pending.pop(key, [])
        if not rows:
            return
        schema, table = key
        count = insert_rows(self._connection, schema, table, rows)
        self._totals[table] = self._totals.get(table, 0) + count
        self._log(f"{schema}.{table}: {self._totals[table]:,} rows")
