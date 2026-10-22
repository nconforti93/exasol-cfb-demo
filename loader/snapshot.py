"""Snapshots of the stored data and restores from them (`make snapshot`, `restore`, `snapshots`).

Layout: `~/.cfb-demo/snapshots/<name>/`, or `$CFB_SNAPSHOT_DIR/<name>/`. Directories are 0700 and
files 0600. A location inside the repository or a OneDrive folder is refused, and an existing
snapshot is never overwritten.

    CFB_CORE.<TABLE>.csv.gz      one per non-empty scope table: gzip CSV with a header line
    CFB_META.LOAD_LOG.csv.gz
    CFB_META.FETCH_LOG.csv.gz
    manifest.json                written last; a directory without it is an incomplete snapshot

Scope: every table of the plan's snapshot schemas (`CFB_CORE`) plus its meta tables (`LOAD_LOG`,
`FETCH_LOG`); `loader.plan.SnapshotScope`. Never `LOAD_RUN`, `REFRESH_LOG` or `LOAD_FAILURES`:
run history stays with each deployment (review M5). Schema names in the manifest and the file
names are logical (without the namespace prefix), so a snapshot of one namespace restores into
another.

Manifest (`manifest_version` 1): `name`, `created_at` (UTC), `source` (`target`, `host`,
`namespace`; never a credential), `db_version`, `catalog_hash` (of the deployed adapter script),
`plan_hash` (`LOAD_PLAN.PLAN_HASH`, the plan file's hash, decision [40]), `format` (`csv-gzip`,
decision [31]) and per table `schema`, `table`, `rows`, `columns` (`[[name, type], ...]` in
column order), `file` and `sha256` (null for an empty table) and `fingerprint`.

Fingerprint: `{"rows": n, "<COLUMN>": [non-NULL count, aggregate text], ...}`, computed by the
same generated SQL on both sides (`fingerprint_sql`) and compared as numbers:

    DECIMAL    SUM(x)
    DOUBLE     SUM of the value's text form (Exasol's 15 significant digits, the form EXPORT writes)
               read back as DOUBLE, rounded to 8 decimals and cast to DECIMAL(36,8); |x| >= 1E27
               only counts. A DOUBLE with more digits comes back rounded to its text form
               (relative change <= 4e-12), and the fingerprint describes that restored value
    BOOLEAN    count of TRUE
    DATE       SUM(DAYS_BETWEEN(x, DATE '1970-01-01'))
    TIMESTAMP  SUM(SECONDS_BETWEEN(x, TIMESTAMP '1970-01-01 00:00:00')); never POSIX_TIME, which
               depends on the session time zone
    VARCHAR    total length

`take_snapshot`: under the run lock (kind `SNAPSHOT`, so no loader run writes meanwhile) it
fingerprints every scope table, creates the directory, exports each non-empty table with pyexasol
`export_to_file` into a file it creates 0600, fingerprints every table again, and refuses to keep
a snapshot whose data changed in between. Then it writes the manifest. Any failure removes the
directory it created.

`restore_snapshot` makes no CFBD call (it never reads the virtual schema). In order:

1. Before the lock and before any change: the manifest is valid; every scope table exists with
   the manifest's columns in the manifest's order (the import is positional), and the set of
   `CFB_CORE` tables matches; a different catalog hash is refused unless `force` (slice keys may
   differ, so restored slices may become orphans); every file has its checksum and header line.
2. The run lock (kind `RESTORE`), taken before any transaction: acquiring commits.
3. The landing schema (`Namespace.restore_schema`, dropped first if a crashed restore left it;
   `CREATE SCHEMA` opens it, so the session's own schema is reopened): one table per file, `LIKE`
   its target, filled with `import_from_file`; every fingerprint must match the manifest.
4. One transaction replaces every scope table: `DELETE`, then `INSERT ... SELECT` from the landing
   table (tables without a file stay empty). A failure rolls it back, so the target is unchanged.
5. The landing schema is dropped, also after a failure. The lock row ends as the `RESTORE` run
   (`DONE`, or `FAILED` with the error). Orphan slices are reported afterwards.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from loader.plan import SNAPSHOT_META_TABLES, SnapshotScope
from loader.runlock import run_lock
from vs import bundle as vs_bundle
from vs.cfbd_vs.sqlrender import qualified_name, quote_identifier, string_literal
from vs.host.deploy import columns_by_table
from vs.host.namespace import Namespace

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR_VARIABLE = "CFB_SNAPSHOT_DIR"
DEFAULT_ROOT_PARTS = (".cfb-demo", "snapshots")
MANIFEST_FILE = "manifest.json"
MANIFEST_VERSION = 1
FORMAT = "csv-gzip"
FILE_SUFFIX = ".csv.gz"
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
SYNCED_FOLDER = "onedrive"
CORE = "CFB_CORE"
META = "CFB_META"
WHOLE_SCHEMAS = (CORE,)
SNAPSHOT_KIND = "SNAPSHOT"
RESTORE_KIND = "RESTORE"
DOUBLE_LIMIT = "1E27"
HEADER_LIMIT = 1 << 20
CHUNK = 1 << 20
LISTED_PROBLEMS = 20

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_IDENTIFIER = re.compile(r"[A-Z][A-Z0-9_]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
CATALOG_ANCHOR = '{"catalog_hash":'
_CATALOG_HASH = re.compile(r'\{"catalog_hash": ?"([0-9a-f]{64})"')
_TIMESTAMP = re.compile(r"TIMESTAMP(?:\(\d\))?")


class SnapshotError(RuntimeError):
    """A snapshot or restore failed; the message names what and where."""


class SnapshotRefused(SnapshotError):
    """A rule stopped the command before it changed anything; the message names the rule."""


class Connection(Protocol):
    """The part of a pyexasol connection used here."""

    def execute(self, query: str) -> Any: ...

    def export_to_file(
        self, dst: Any, query_or_table: Any, query_params: Any = None, export_params: Any = None
    ) -> Any: ...

    def import_from_file(self, src: Any, table: Any, import_params: Any = None) -> Any: ...


Log = Callable[[str], None]


def _silent(_: str) -> None:
    return None


def utc_now() -> datetime:
    return datetime.now(UTC)


# --- location ---------------------------------------------------------------------------------


def snapshot_root(
    environ: Mapping[str, str] | None = None, *, home: Path | None = None, repo: Path = ROOT
) -> Path:
    """`$CFB_SNAPSHOT_DIR`, else `~/.cfb-demo/snapshots`; refused inside the repo or OneDrive."""
    environ = os.environ if environ is None else environ
    configured = environ.get(SNAPSHOT_DIR_VARIABLE)
    if configured:
        root = Path(configured).expanduser()
    else:
        root = (home or Path.home()).joinpath(*DEFAULT_ROOT_PARTS)
    check_location(root, repo)
    return root


def check_location(path: Path, repo: Path = ROOT) -> None:
    """Refuse a snapshot location inside the repository or inside a OneDrive folder."""
    resolved = path.resolve()
    repository = repo.resolve()
    if resolved == repository or repository in resolved.parents:
        raise SnapshotRefused(
            f"snapshot directory {path} is inside the repository {repository}: snapshots hold "
            f"CFBD data, which never goes into git. Set {SNAPSHOT_DIR_VARIABLE} to a private "
            "local directory, for example ~/.cfb-demo/snapshots"
        )
    for candidate in (path.absolute(), resolved):
        if any(SYNCED_FOLDER in part.lower() for part in candidate.parts):
            raise SnapshotRefused(
                f"snapshot directory {path} is in a OneDrive folder ({candidate}): CFBD data "
                f"stays private and out of synced folders. Set {SNAPSHOT_DIR_VARIABLE} to a "
                "private local directory, for example ~/.cfb-demo/snapshots"
            )


def check_name(name: str) -> str:
    """A snapshot name: letters, digits, `.`, `_` and `-`, starting with a letter or digit."""
    if not _NAME.fullmatch(name):
        raise SnapshotRefused(
            f"invalid snapshot name {name!r}: use 1 to 64 letters, digits, '.', '_' or '-', "
            "starting with a letter or digit"
        )
    return name


def create_snapshot_directory(root: Path, name: str) -> Path:
    """Create `<root>/<name>` (0700, and 0700 for every parent it creates); never reuse one."""
    directory = root / check_name(name)
    if directory.exists() or directory.is_symlink():
        raise _exists(name, directory)
    missing: list[Path] = []
    current = root
    while not current.exists():
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        path.mkdir(mode=DIRECTORY_MODE, exist_ok=True)
        path.chmod(DIRECTORY_MODE)
    try:
        directory.mkdir(mode=DIRECTORY_MODE)
    except FileExistsError:
        raise _exists(name, directory) from None
    directory.chmod(DIRECTORY_MODE)
    return directory


def _exists(name: str, directory: Path) -> SnapshotRefused:
    return SnapshotRefused(
        f"snapshot {name} already exists at {directory}; a snapshot is never overwritten, "
        "choose another NAME"
    )


def open_private_file(path: Path) -> BinaryIO:
    """A new file opened for binary writing with mode 0600; an existing file is never replaced."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, FILE_MODE)
    os.fchmod(descriptor, FILE_MODE)
    return os.fdopen(descriptor, "wb")


# --- columns and fingerprints -----------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    """A column as the manifest records it: its name and its normalized type."""

    name: str
    type: str


Fingerprint = dict[str, Any]


def aggregate_sql(column: Column) -> str:
    """The fingerprint aggregate of one column (module documentation)."""
    name = quote_identifier(column.name)
    kind = column.type.upper()
    if kind.startswith("DECIMAL"):
        return f"SUM({name})"
    if kind.startswith("DOUBLE"):
        text = f"CAST(CAST({name} AS VARCHAR(100)) AS DOUBLE)"
        return (
            f"SUM(CASE WHEN ABS({text}) < {DOUBLE_LIMIT} "
            f"THEN CAST(ROUND({text}, 8) AS DECIMAL(36,8)) END)"
        )
    if kind == "BOOLEAN":
        return f"COUNT(CASE WHEN {name} = TRUE THEN 1 END)"
    if kind == "DATE":
        return f"SUM(DAYS_BETWEEN({name}, DATE '1970-01-01'))"
    if _TIMESTAMP.fullmatch(kind):
        return f"SUM(SECONDS_BETWEEN({name}, TIMESTAMP '1970-01-01 00:00:00'))"
    if kind.startswith(("VARCHAR", "CHAR")):
        return f"SUM(LENGTH({name}))"
    raise SnapshotError(
        f"column {column.name}: no fingerprint for type {column.type}; snapshots support DECIMAL, "
        "DOUBLE, BOOLEAN, DATE, TIMESTAMP and VARCHAR"
    )


def fingerprint_sql(schema: str, table: str, columns: Sequence[Column]) -> str:
    """One query: the row count, then per column its non-NULL count and its aggregate, as text."""
    parts = ["CAST(COUNT(*) AS VARCHAR(40))"]
    try:
        for column in columns:
            parts.append(f"CAST(COUNT({quote_identifier(column.name)}) AS VARCHAR(40))")
            parts.append(f"CAST({aggregate_sql(column)} AS VARCHAR(100))")
    except SnapshotError as error:
        raise SnapshotError(f"{schema}.{table}: {error}") from None
    return f"SELECT {', '.join(parts)} FROM {qualified_name(schema, table)}"


def read_fingerprint(columns: Sequence[Column], row: Sequence[Any]) -> Fingerprint:
    """The fingerprint of one result row of `fingerprint_sql`."""
    values = list(row)
    if len(values) != 1 + 2 * len(columns):
        raise SnapshotError(
            f"a fingerprint row has {len(values)} values for {len(columns)} columns"
        )
    fingerprint: Fingerprint = {"rows": int(values[0])}
    for index, column in enumerate(columns):
        count, aggregate = values[1 + 2 * index], values[2 + 2 * index]
        fingerprint[column.name] = [int(count), None if aggregate is None else str(aggregate)]
    return fingerprint


def fingerprint_differences(label: str, expected: Fingerprint, found: Fingerprint) -> list[str]:
    """Where two fingerprints differ, one line per difference; aggregates compare as numbers."""
    problems: list[str] = []
    if expected.get("rows") != found.get("rows"):
        problems.append(f"{label}: expected {expected.get('rows')} rows, found {found.get('rows')}")
    names = [name for name in expected if name != "rows"]
    names += [name for name in found if name != "rows" and name not in expected]
    for name in names:
        wanted, have = expected.get(name), found.get(name)
        if not _same_entry(wanted, have):
            problems.append(f"{label}.{name}: expected {wanted!r}, found {have!r}")
    return problems


def _same_entry(wanted: Any, have: Any) -> bool:
    if not isinstance(wanted, list) or not isinstance(have, list) or len(wanted) != len(have):
        return bool(wanted == have)
    if wanted[0] != have[0]:
        return False
    return _same_number(wanted[1], have[1])


def _same_number(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except InvalidOperation:
        return bool(left == right)


def column_differences(
    label: str, expected: Sequence[Column], found: Sequence[Column]
) -> list[str]:
    """Differences between the manifest's columns of a table and the database's."""
    wanted = {column.name: column.type for column in expected}
    have = {column.name: column.type for column in found}
    problems = [
        f"{label}: column {name} is in the snapshot but not in the database"
        for name in wanted
        if name not in have
    ]
    problems += [
        f"{label}: column {name} is in the database but not in the snapshot"
        for name in have
        if name not in wanted
    ]
    problems += [
        f"{label}.{name}: {type_text} in the snapshot, {have[name]} in the database"
        for name, type_text in wanted.items()
        if name in have and have[name] != type_text
    ]
    if not problems and [c.name for c in expected] != [c.name for c in found]:
        problems.append(f"{label}: the columns are in a different order")
    return problems


# --- manifest ---------------------------------------------------------------------------------


def file_name(schema: str, table: str) -> str:
    """`<SCHEMA>.<TABLE>.csv.gz`, with the logical schema name."""
    return f"{schema}.{table}{FILE_SUFFIX}"


@dataclass(frozen=True)
class TableSnapshot:
    """One scope table in the manifest; `file` and `sha256` are None for an empty table."""

    schema: str
    table: str
    rows: int
    columns: tuple[Column, ...]
    file: str | None
    sha256: str | None
    fingerprint: Fingerprint

    @property
    def label(self) -> str:
        return f"{self.schema}.{self.table}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "table": self.table,
            "rows": self.rows,
            "columns": [[column.name, column.type] for column in self.columns],
            "file": self.file,
            "sha256": self.sha256,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class Manifest:
    """`manifest.json` of one snapshot (module documentation)."""

    name: str
    created_at: str
    source: Mapping[str, str]
    db_version: str
    catalog_hash: str
    plan_hash: str | None
    tables: tuple[TableSnapshot, ...]
    format: str = FORMAT

    def to_json(self) -> str:
        data = {
            "manifest_version": MANIFEST_VERSION,
            "name": self.name,
            "created_at": self.created_at,
            "source": dict(self.source),
            "db_version": self.db_version,
            "catalog_hash": self.catalog_hash,
            "plan_hash": self.plan_hash,
            "format": self.format,
            "tables": [table.to_dict() for table in self.tables],
        }
        return json.dumps(data, indent=2, ensure_ascii=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Manifest:
        """Parse and validate a manifest; every problem raises `SnapshotError`."""
        try:
            data = json.loads(text)
        except ValueError:
            raise SnapshotError("manifest.json is not JSON") from None
        if not isinstance(data, dict):
            raise SnapshotError("manifest.json is not a JSON object")
        keys = ("manifest_version", "name", "created_at", "source", "db_version", "catalog_hash")
        missing = [key for key in (*keys, "plan_hash", "format", "tables") if key not in data]
        if missing:
            raise SnapshotError(f"manifest.json is missing {', '.join(missing)}")
        if data["manifest_version"] != MANIFEST_VERSION:
            raise SnapshotError(
                f"manifest version {data['manifest_version']!r} is not supported "
                f"(expected {MANIFEST_VERSION})"
            )
        if data["format"] != FORMAT:
            raise SnapshotError(f"snapshot format {data['format']!r} is not {FORMAT!r}")
        if not isinstance(data["catalog_hash"], str) or not _SHA256.fullmatch(data["catalog_hash"]):
            raise SnapshotError("manifest catalog_hash is not a SHA-256 hex digest")
        source = data["source"]
        if not isinstance(source, dict) or not all(isinstance(v, str) for v in source.values()):
            raise SnapshotError("manifest source must map names to text")
        if not isinstance(data["tables"], list):
            raise SnapshotError("manifest tables must be a list")
        tables = tuple(_table_from_dict(item) for item in data["tables"])
        _require_unique([table.table for table in tables], "table")
        return cls(
            name=str(data["name"]),
            created_at=str(data["created_at"]),
            source={str(k): str(v) for k, v in source.items()},
            db_version=str(data["db_version"]),
            catalog_hash=data["catalog_hash"],
            plan_hash=None if data["plan_hash"] is None else str(data["plan_hash"]),
            tables=tables,
            format=data["format"],
        )


def _table_from_dict(item: Any) -> TableSnapshot:
    if not isinstance(item, dict):
        raise SnapshotError("a manifest table entry is not an object")
    schema, table = item.get("schema"), item.get("table")
    label = f"{schema}.{table}"
    if schema not in (CORE, META):
        raise SnapshotError(f"{label}: unknown schema {schema!r}; expected {CORE} or {META}")
    if not isinstance(table, str) or not _IDENTIFIER.fullmatch(table):
        raise SnapshotError(f"{label}: table name is not an UPPER_CASE identifier")
    if schema == META and table not in SNAPSHOT_META_TABLES:
        raise SnapshotError(
            f"{label}: not a snapshot table; of {META} only {', '.join(SNAPSHOT_META_TABLES)} are"
        )
    columns = _columns_from(item.get("columns"), label)
    rows = item.get("rows")
    if isinstance(rows, bool) or not isinstance(rows, int) or rows < 0:
        raise SnapshotError(f"{label}: rows must be a whole number")
    stored, digest = item.get("file"), item.get("sha256")
    if rows > 0 and stored is None:
        raise SnapshotError(f"{label}: {rows} rows but no file")
    if rows == 0 and stored is not None:
        raise SnapshotError(f"{label}: a file but no rows")
    if stored is not None and stored != file_name(schema, table):
        raise SnapshotError(f"{label}: file must be {file_name(schema, table)}, got {stored!r}")
    if (stored is None) != (digest is None) or (
        digest is not None and not (isinstance(digest, str) and _SHA256.fullmatch(digest))
    ):
        raise SnapshotError(f"{label}: sha256 must be the hex digest of its file")
    fingerprint = item.get("fingerprint")
    if not _valid_fingerprint(fingerprint, rows, columns):
        raise SnapshotError(f"{label}: the fingerprint does not cover its rows and columns")
    assert isinstance(fingerprint, dict)
    return TableSnapshot(schema, table, rows, columns, stored, digest, fingerprint)


def _columns_from(raw: Any, label: str) -> tuple[Column, ...]:
    if not isinstance(raw, list) or not raw:
        raise SnapshotError(f"{label}: columns must be a non-empty list")
    columns: list[Column] = []
    for pair in raw:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or not isinstance(pair[0], str)
            or not _IDENTIFIER.fullmatch(pair[0])
            or not isinstance(pair[1], str)
        ):
            raise SnapshotError(f"{label}: a column must be [UPPER_CASE name, type]")
        columns.append(Column(pair[0], pair[1]))
    _require_unique([column.name for column in columns], f"{label}: column")
    return tuple(columns)


def _valid_fingerprint(raw: Any, rows: int, columns: Sequence[Column]) -> bool:
    if not isinstance(raw, dict) or raw.get("rows") != rows:
        return False
    if set(raw) != {"rows", *(column.name for column in columns)}:
        return False
    for column in columns:
        entry = raw[column.name]
        if not (
            isinstance(entry, list)
            and len(entry) == 2
            and isinstance(entry[0], int)
            and (entry[1] is None or isinstance(entry[1], str))
        ):
            return False
    return True


def _require_unique(names: Sequence[str], what: str) -> None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise SnapshotError(f"{what} {name} listed twice")
        seen.add(name)


def read_manifest(directory: Path) -> Manifest:
    """The validated manifest of a snapshot directory."""
    path = directory / MANIFEST_FILE
    if not directory.is_dir():
        raise SnapshotError(f"no snapshot at {directory}")
    if not path.is_file():
        raise SnapshotError(f"{directory} has no {MANIFEST_FILE}: an incomplete snapshot")
    return Manifest.from_json(path.read_text(encoding="utf-8"))


# --- files ------------------------------------------------------------------------------------


def csv_header(columns: Sequence[Column]) -> str:
    """The header line of a snapshot file: the column names, comma-separated."""
    return ",".join(column.name for column in columns)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_header(path: Path) -> str:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        return handle.readline(HEADER_LIMIT).rstrip("\r\n")


def verify_files(directory: Path, manifest: Manifest) -> None:
    """Every file of the manifest exists, has its checksum and its header line."""
    for table in manifest.tables:
        if table.file is None:
            continue
        path = directory / table.file
        if not path.is_file():
            raise SnapshotError(f"{table.file} is missing from {directory}")
        digest = sha256_of(path)
        if digest != table.sha256:
            raise SnapshotError(
                f"{table.file}: the checksum differs from the manifest (file {digest}, manifest "
                f"{table.sha256}): the file changed after the snapshot"
            )
        try:
            header = read_header(path)
        except (OSError, EOFError, UnicodeDecodeError) as error:
            raise SnapshotError(f"{table.file}: cannot read its header line: {error}") from None
        if header != csv_header(table.columns):
            raise SnapshotError(
                f"{table.file}: the header line {header[:200]!r} differs from the manifest "
                f"columns {csv_header(table.columns)[:200]!r}"
            )


# --- the database side ------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeTable:
    """A table of the snapshot scope in one namespace."""

    logical_schema: str
    schema: str
    table: str
    columns: tuple[Column, ...]

    @property
    def label(self) -> str:
        return f"{self.logical_schema}.{self.table}"


def namespace_schema(namespace: Namespace, logical: str) -> str:
    """The namespace's schema for a logical scope schema (`CFB_CORE`, `CFB_META`)."""
    schemas = {CORE: namespace.core_schema, META: namespace.meta_schema}
    if logical not in schemas:
        raise SnapshotError(f"schema {logical} is not part of a snapshot ({CORE}, {META})")
    return schemas[logical]


def scope_tables(
    connection: Connection, namespace: Namespace, scope: SnapshotScope
) -> list[ScopeTable]:
    """Every table of the scope schemas, then the scope's meta tables, with their columns."""
    tables: list[ScopeTable] = []
    for logical in scope.schemas:
        schema = namespace_schema(namespace, logical)
        found = _columns(connection, schema)
        if not found:
            raise SnapshotError(f"schema {schema} has no tables: run `make deploy` first")
        tables += [ScopeTable(logical, schema, name, found[name]) for name in sorted(found)]
    meta = _columns(connection, namespace.meta_schema)
    for name in scope.meta_tables:
        if name not in meta:
            raise SnapshotError(f"{namespace.meta_schema}.{name} does not exist: run `make deploy`")
        tables.append(ScopeTable(META, namespace.meta_schema, name, meta[name]))
    _require_unique([table.table for table in tables], "table")
    return tables


def _columns(connection: Connection, schema: str) -> dict[str, tuple[Column, ...]]:
    return {
        table: tuple(Column(name, type_text) for name, type_text, _ in columns)
        for table, columns in columns_by_table(connection, schema).items()
    }


def table_fingerprint(
    connection: Connection, schema: str, table: str, columns: Sequence[Column]
) -> Fingerprint:
    rows = connection.execute(fingerprint_sql(schema, table, columns)).fetchall()
    return read_fingerprint(columns, rows[0])


def catalog_hash_in(script_text: str) -> str | None:
    """The catalog hash of the catalog JSON embedded in an adapter script text, if any."""
    match = _CATALOG_HASH.search(script_text)
    return None if match is None else match.group(1)


def deployed_catalog_hash(connection: Connection, namespace: Namespace) -> str:
    """The catalog hash embedded in the namespace's adapter script (`vs/bundle.py`)."""
    rows = connection.execute(
        f"SELECT SUBSTR(SCRIPT_TEXT, INSTR(SCRIPT_TEXT, {string_literal(CATALOG_ANCHOR)}), 100) "
        "FROM EXA_ALL_SCRIPTS "
        f"WHERE SCRIPT_SCHEMA = {string_literal(namespace.infra_schema)} "
        f"AND SCRIPT_NAME = {string_literal(vs_bundle.ADAPTER_SCRIPT_NAME)}"
    ).fetchall()
    found = catalog_hash_in(str(rows[0][0])) if rows and rows[0][0] else None
    if found is None:
        where = f"{namespace.infra_schema}.{vs_bundle.ADAPTER_SCRIPT_NAME}"
        raise SnapshotError(
            f"cannot read the deployed catalog hash from {where}: run `make deploy`"
        )
    return found


def deployed_plan_hash(connection: Connection, namespace: Namespace) -> str | None:
    rows = connection.execute(
        f'SELECT MAX("PLAN_HASH") FROM {qualified_name(namespace.meta_schema, "LOAD_PLAN")}'
    ).fetchall()
    return None if not rows or rows[0][0] is None else str(rows[0][0])


def database_version(connection: Connection) -> str:
    rows = connection.execute(
        "SELECT PARAM_VALUE FROM EXA_METADATA WHERE PARAM_NAME = 'databaseProductVersion'"
    ).fetchall()
    return str(rows[0][0]) if rows else "unknown"


def orphan_counts(connection: Connection, namespace: Namespace) -> dict[str, int]:
    """Orphan slices per table (`LOAD_ORPHANS`); reads no virtual table."""
    orphans = qualified_name(namespace.meta_schema, "LOAD_ORPHANS")
    rows = connection.execute(
        f'SELECT "TABLE_NAME", COUNT(*) FROM {orphans} GROUP BY "TABLE_NAME" ORDER BY "TABLE_NAME"'
    ).fetchall()
    return {str(table): int(count) for table, count in rows}


# --- snapshot ---------------------------------------------------------------------------------


def take_snapshot(
    connection: Connection,
    namespace: Namespace,
    root: Path,
    name: str,
    *,
    source: Mapping[str, str],
    scope: SnapshotScope | None = None,
    clock: Callable[[], datetime] = utc_now,
    log: Log = _silent,
) -> Manifest:
    """Write snapshot `name` below `root` (module documentation); returns its manifest."""
    check_location(root)
    directory = root / check_name(name)
    if directory.exists() or directory.is_symlink():
        raise _exists(name, directory)
    with run_lock(connection, namespace, SNAPSHOT_KIND, name) as lock:
        log(f"run lock: SNAPSHOT run {lock.run_id}")
        tables = scope_tables(connection, namespace, scope or SnapshotScope())
        catalog_hash = deployed_catalog_hash(connection, namespace)
        plan_hash = deployed_plan_hash(connection, namespace)
        db_version = database_version(connection)
        before = _fingerprints(connection, tables)
        directory = create_snapshot_directory(root, name)
        try:
            entries = tuple(
                _export(connection, directory, table, before[table.label], log) for table in tables
            )
            _require_unchanged(before, _fingerprints(connection, tables))
            manifest = Manifest(
                name=name,
                created_at=clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                source=dict(source),
                db_version=db_version,
                catalog_hash=catalog_hash,
                plan_hash=plan_hash,
                tables=entries,
            )
            with open_private_file(directory / MANIFEST_FILE) as handle:
                handle.write(manifest.to_json().encode("utf-8"))
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        files = [entry for entry in entries if entry.file is not None]
        lock.detail = (
            f"snapshot {name}: {len(files)} files, {sum(e.rows for e in entries)} rows, "
            f"catalog {catalog_hash[:12]}"
        )
    log(f"snapshot {name}: {len(files)} files and {MANIFEST_FILE} in {directory}")
    return manifest


def _fingerprints(connection: Connection, tables: Sequence[ScopeTable]) -> dict[str, Fingerprint]:
    return {
        table.label: table_fingerprint(connection, table.schema, table.table, table.columns)
        for table in tables
    }


def _export(
    connection: Connection,
    directory: Path,
    table: ScopeTable,
    fingerprint: Fingerprint,
    log: Log,
) -> TableSnapshot:
    rows = int(fingerprint["rows"])
    if rows == 0:
        return TableSnapshot(
            table.logical_schema, table.table, 0, table.columns, None, None, fingerprint
        )
    name = file_name(table.logical_schema, table.table)
    path = directory / name
    with open_private_file(path) as handle:
        connection.export_to_file(
            handle,
            (table.schema, table.table),
            export_params={
                "format": "gz",
                "with_column_names": True,
                "columns": [column.name for column in table.columns],
            },
        )
    log(f"exported {table.label}: {rows} rows, {path.stat().st_size} bytes")
    return TableSnapshot(
        table.logical_schema, table.table, rows, table.columns, name, sha256_of(path), fingerprint
    )


def _require_unchanged(before: Mapping[str, Fingerprint], after: Mapping[str, Fingerprint]) -> None:
    problems = [
        problem
        for label in before
        for problem in fingerprint_differences(label, before[label], after[label])
    ]
    if problems:
        raise SnapshotError(
            "the data changed while the snapshot was written, so it was not kept; run it again:\n  "
            + "\n  ".join(problems[:LISTED_PROBLEMS])
        )


# --- restore ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class RestoreReport:
    """What a restore did: the snapshot, the `RESTORE` run and the orphan slices afterwards."""

    manifest: Manifest
    run_id: int
    orphans: Mapping[str, int]


def check_restore(
    connection: Connection, namespace: Namespace, manifest: Manifest, *, force: bool
) -> list[str]:
    """Refuse a snapshot that does not fit the deployment; returns what the checks found.

    Columns and tables must match exactly (also with `force`); a different catalog hash is
    refused unless `force`. Reads only system tables and the plan table; changes nothing.
    """
    found = {
        logical: _columns(connection, namespace_schema(namespace, logical))
        for logical in sorted({table.schema for table in manifest.tables} | set(WHOLE_SCHEMAS))
    }
    problems: list[str] = []
    for table in manifest.tables:
        columns = found[table.schema].get(table.table)
        if columns is None:
            problems.append(f"{table.label}: the table is in the snapshot but not in the database")
        else:
            problems += column_differences(table.label, table.columns, columns)
    listed = {(table.schema, table.table) for table in manifest.tables}
    problems += [
        f"{logical}.{name}: the table is in the database but not in the snapshot"
        for logical in WHOLE_SCHEMAS
        for name in sorted(found[logical])
        if (logical, name) not in listed
    ]
    if problems:
        raise SnapshotRefused(
            "the snapshot does not fit this deployment, nothing was changed:\n  "
            + "\n  ".join(problems[:LISTED_PROBLEMS])
        )
    deployed = deployed_catalog_hash(connection, namespace)
    lines = ["columns ok: every scope table matches the manifest"]
    if deployed == manifest.catalog_hash:
        lines.append(f"catalog ok ({deployed[:12]})")
    else:
        risk = (
            f"the snapshot's catalog {manifest.catalog_hash} differs from the deployed catalog "
            f"{deployed}: slice keys may have changed, so restored slices may become orphans "
            "that block re-fetching their tables"
        )
        if not force:
            raise SnapshotRefused(f"{risk}. Nothing was changed; FORCE=yes restores it anyway")
        lines.append(f"catalog differs, restoring anyway (FORCE=yes): {risk}")
    plan = deployed_plan_hash(connection, namespace)
    if plan == manifest.plan_hash:
        lines.append("plan: the deployed load plan is the snapshot's")
    else:
        lines.append(
            f"plan: the deployed load plan {_short(plan)} differs from the snapshot's "
            f"{_short(manifest.plan_hash)}; orphan slices are reported after the restore"
        )
    return lines


def _short(value: str | None) -> str:
    return "none" if value is None else value[:12]


def restore_snapshot(
    connection: Connection,
    namespace: Namespace,
    root: Path,
    name: str,
    *,
    force: bool = False,
    log: Log = _silent,
) -> RestoreReport:
    """Restore snapshot `name` from `root` into `namespace` (module documentation)."""
    check_location(root)
    directory = root / check_name(name)
    manifest = read_manifest(directory)
    for line in check_restore(connection, namespace, manifest, force=force):
        log(line)
    verify_files(directory, manifest)
    files = [table for table in manifest.tables if table.file is not None]
    log(f"files ok: {len(files)} files match their checksums and header lines")
    arguments = f"{name} FORCE" if force else name
    with run_lock(connection, namespace, RESTORE_KIND, arguments) as lock:
        log(f"run lock: RESTORE run {lock.run_id}")
        try:
            _land(connection, namespace, directory, manifest, log)
            _check_landing(connection, namespace, manifest)
            log(f"fingerprints ok: {len(files)} landed tables match the manifest")
            run_transaction(connection, swap_statements(namespace, manifest.tables))
        except BaseException as error:
            _drop_landing_after(connection, namespace, error)
            raise
        _drop_landing(connection, namespace)
        rows = sum(table.rows for table in manifest.tables)
        log(f"restored {len(manifest.tables)} tables ({rows} rows) in one transaction")
        lock.detail = f"restored {name}: {len(manifest.tables)} tables, {rows} rows" + (
            ", FORCE" if force else ""
        )
    return RestoreReport(manifest, lock.run_id, orphan_counts(connection, namespace))


def _land(
    connection: Connection, namespace: Namespace, directory: Path, manifest: Manifest, log: Log
) -> None:
    landing = quote_identifier(namespace.restore_schema)
    connection.execute(f"DROP SCHEMA IF EXISTS {landing} CASCADE")
    current = connection.execute("SELECT CURRENT_SCHEMA FROM DUAL").fetchall()[0][0]
    connection.execute(f"CREATE SCHEMA {landing}")
    connection.execute(
        "CLOSE SCHEMA" if current is None else f"OPEN SCHEMA {quote_identifier(str(current))}"
    )
    for table in manifest.tables:
        if table.file is None:
            continue
        target = qualified_name(namespace_schema(namespace, table.schema), table.table)
        connection.execute(
            f"CREATE TABLE {qualified_name(namespace.restore_schema, table.table)} (LIKE {target})"
        )
        with (directory / table.file).open("rb") as handle:
            connection.import_from_file(
                handle,
                (namespace.restore_schema, table.table),
                import_params={
                    "format": "gz",
                    "skip": 1,
                    "columns": [column.name for column in table.columns],
                },
            )
        log(f"landed {table.label}: {table.rows} rows")


def _check_landing(connection: Connection, namespace: Namespace, manifest: Manifest) -> None:
    problems: list[str] = []
    for table in manifest.tables:
        if table.file is None:
            continue
        found = table_fingerprint(connection, namespace.restore_schema, table.table, table.columns)
        problems += fingerprint_differences(table.file, table.fingerprint, found)
    if problems:
        raise SnapshotError(
            "the restored data does not match the manifest's fingerprints, nothing was changed:"
            "\n  " + "\n  ".join(problems[:LISTED_PROBLEMS])
        )


def swap_statements(namespace: Namespace, tables: Sequence[TableSnapshot]) -> list[str]:
    """The statements of the swap transaction: empty every scope table, refill it from landing."""
    statements: list[str] = []
    for table in tables:
        target = qualified_name(namespace_schema(namespace, table.schema), table.table)
        statements.append(f"DELETE FROM {target}")
        if table.rows > 0:
            columns = ", ".join(quote_identifier(column.name) for column in table.columns)
            landing = qualified_name(namespace.restore_schema, table.table)
            statements.append(f"INSERT INTO {target} ({columns}) SELECT {columns} FROM {landing}")
    return statements


def run_transaction(connection: Any, statements: Sequence[str]) -> None:
    """Run `statements` in one transaction: COMMIT at the end, ROLLBACK on any failure."""
    switch = getattr(connection, "set_autocommit", None)
    if switch is not None:
        switch(False)
    try:
        for statement in statements:
            connection.execute(statement)
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    finally:
        if switch is not None:
            switch(True)


def _drop_landing(connection: Connection, namespace: Namespace) -> None:
    connection.execute(
        f"DROP SCHEMA IF EXISTS {quote_identifier(namespace.restore_schema)} CASCADE"
    )


def _drop_landing_after(connection: Connection, namespace: Namespace, error: BaseException) -> None:
    try:
        connection.execute("ROLLBACK")
        _drop_landing(connection, namespace)
    except Exception as problem:
        error.add_note(
            f"the landing schema {namespace.restore_schema} could not be dropped ({problem}); "
            "the next restore drops it"
        )


# --- listing ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotListing:
    """One snapshot directory as `make snapshots` shows it; `problem` is set when unreadable."""

    name: str
    size_bytes: int
    created_at: str = ""
    source: str = ""
    tables: int = 0
    rows: int = 0
    catalog_hash: str = ""
    problem: str | None = None


def list_snapshots(root: Path) -> list[SnapshotListing]:
    """Every directory below `root`, by name, read from its manifest."""
    if not root.is_dir():
        return []
    listings: list[SnapshotListing] = []
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        size = sum(path.stat().st_size for path in directory.iterdir() if path.is_file())
        try:
            manifest = read_manifest(directory)
        except SnapshotError as error:
            listings.append(SnapshotListing(directory.name, size, problem=str(error)))
            continue
        source = " ".join(
            manifest.source[key] for key in ("target", "host") if manifest.source.get(key)
        )
        listings.append(
            SnapshotListing(
                directory.name,
                size,
                manifest.created_at,
                source,
                sum(1 for table in manifest.tables if table.rows > 0),
                sum(table.rows for table in manifest.tables),
                manifest.catalog_hash,
            )
        )
    return listings
