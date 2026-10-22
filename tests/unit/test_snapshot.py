"""`loader/snapshot.py` without a database: paths, the manifest, the fingerprint SQL, the file
checks and the order of the restore's swap transaction.

The round trips through Exasol are integration tests (`tests/it/test_snapshot_restore_it.py`).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import stat
from pathlib import Path
from typing import Any

import pytest

from loader import snapshot as snap
from loader.snapshot import Column, Manifest, SnapshotError, SnapshotRefused, TableSnapshot
from vs import bundle as vs_bundle
from vs.cfbd_vs.catalog import Catalog
from vs.host.namespace import Namespace

TEST = Namespace("TEST_")
SHA = "a" * 64
CATALOG = "c" * 64
GAMES_COLUMNS = (
    Column("ID", "DECIMAL(18,0)"),
    Column("HOME_POINTS", "DOUBLE"),
    Column("NEUTRAL_SITE", "BOOLEAN"),
    Column("START_DATE", "TIMESTAMP"),
    Column("NOTES", "VARCHAR(2000) UTF8"),
)
LOG_COLUMNS = (Column("TABLE_NAME", "VARCHAR(128) UTF8"), Column("LOADED_AT", "TIMESTAMP"))


def games(rows: int = 2, file: str | None = "CFB_CORE.GAMES.csv.gz") -> TableSnapshot:
    fingerprint: dict[str, Any] = {"rows": rows}
    for column in GAMES_COLUMNS:
        fingerprint[column.name] = [rows, str(rows * 10) if rows else None]
    return TableSnapshot(
        "CFB_CORE", "GAMES", rows, GAMES_COLUMNS, file, SHA if file else None, fingerprint
    )


def load_log(rows: int = 1) -> TableSnapshot:
    fingerprint: dict[str, Any] = {
        "rows": rows,
        "TABLE_NAME": [rows, "5"],
        "LOADED_AT": [rows, "9"],
    }
    return TableSnapshot(
        "CFB_META", "LOAD_LOG", rows, LOG_COLUMNS, "CFB_META.LOAD_LOG.csv.gz", SHA, fingerprint
    )


def empty_teams() -> TableSnapshot:
    columns = (Column("ID", "DECIMAL(18,0)"),)
    return TableSnapshot("CFB_CORE", "TEAMS", 0, columns, None, None, {"rows": 0, "ID": [0, None]})


def manifest(*tables: TableSnapshot) -> Manifest:
    return Manifest(
        name="week7",
        created_at="2026-10-22T20:15:03Z",
        source={"target": "aws", "host": "node1.example", "namespace": ""},
        db_version="2026.2.0",
        catalog_hash=CATALOG,
        plan_hash="p" * 64,
        tables=tables or (games(), load_log(), empty_teams()),
    )


# --- location rules -------------------------------------------------------------------------


def test_the_default_root_is_private_below_home(tmp_path: Path) -> None:
    root = snap.snapshot_root({}, home=tmp_path / "home", repo=tmp_path / "repo")
    assert root == tmp_path / "home" / ".cfb-demo" / "snapshots"


def test_the_environment_variable_overrides_the_root(tmp_path: Path) -> None:
    chosen = tmp_path / "elsewhere"
    environ = {snap.SNAPSHOT_DIR_VARIABLE: str(chosen)}
    assert snap.snapshot_root(environ, home=tmp_path, repo=tmp_path / "repo") == chosen


def test_a_root_inside_the_repository_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    inside = repo / "cache" / "snapshots"
    with pytest.raises(SnapshotRefused) as refused:
        snap.snapshot_root({snap.SNAPSHOT_DIR_VARIABLE: str(inside)}, home=tmp_path, repo=repo)
    assert str(inside) in str(refused.value)
    assert "inside the repository" in str(refused.value)


@pytest.mark.parametrize(
    "path",
    [
        "/mnt/c/Users/nc/OneDrive - EXASOL AG/snapshots",
        "/home/nc/onedrive/snapshots",
        "/data/OneDrive/x/y",
    ],
)
def test_a_root_in_a_onedrive_folder_is_refused(tmp_path: Path, path: str) -> None:
    with pytest.raises(SnapshotRefused, match="OneDrive") as refused:
        snap.check_location(Path(path), repo=tmp_path / "repo")
    assert path in str(refused.value)


def test_a_symlink_into_the_repository_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(repo / "data")
    with pytest.raises(SnapshotRefused, match="inside the repository"):
        snap.check_location(link / "snapshots", repo=repo)


def test_a_root_outside_both_is_accepted(tmp_path: Path) -> None:
    snap.check_location(tmp_path / "private" / "snapshots", repo=tmp_path / "repo")


@pytest.mark.parametrize("name", ["week7", "aws-demo-2026-10-24", "a.b_c", "W" * 64])
def test_valid_snapshot_names(name: str) -> None:
    assert snap.check_name(name) == name


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "../x", ".hidden", "week 7", "W" * 65])
def test_invalid_snapshot_names_are_refused(name: str) -> None:
    with pytest.raises(SnapshotRefused, match="snapshot name"):
        snap.check_name(name)


def test_the_snapshot_directory_is_created_private_with_private_parents(tmp_path: Path) -> None:
    root = tmp_path / "home" / ".cfb-demo" / "snapshots"
    directory = snap.create_snapshot_directory(root, "week7")
    assert directory == root / "week7"
    for path in (directory, root, root.parent):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_an_existing_snapshot_is_never_overwritten(tmp_path: Path) -> None:
    existing = tmp_path / "week7"
    existing.mkdir()
    (existing / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SnapshotRefused, match="already exists") as refused:
        snap.create_snapshot_directory(tmp_path, "week7")
    assert str(existing) in str(refused.value)
    assert (existing / "manifest.json").read_text(encoding="utf-8") == "{}"


def test_private_files_are_0600_and_never_replace_a_file(tmp_path: Path) -> None:
    path = tmp_path / "x.csv.gz"
    with snap.open_private_file(path) as handle:
        handle.write(b"data")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        snap.open_private_file(path)
    assert path.read_bytes() == b"data"


# --- fingerprints ---------------------------------------------------------------------------


def test_each_type_has_its_aggregate() -> None:
    aggregates = {c.name: snap.aggregate_sql(c) for c in GAMES_COLUMNS}
    assert aggregates["ID"] == 'SUM("ID")'
    assert aggregates["NEUTRAL_SITE"] == 'COUNT(CASE WHEN "NEUTRAL_SITE" = TRUE THEN 1 END)'
    assert aggregates["START_DATE"] == (
        "SUM(SECONDS_BETWEEN(\"START_DATE\", TIMESTAMP '1970-01-01 00:00:00'))"
    )
    assert aggregates["NOTES"] == 'SUM(LENGTH("NOTES"))'
    assert snap.aggregate_sql(Column("D", "DATE")) == (
        "SUM(DAYS_BETWEEN(\"D\", DATE '1970-01-01'))"
    )


def test_a_double_is_summed_as_its_exported_text_rounded_to_8_decimals() -> None:
    text = 'CAST(CAST("HOME_POINTS" AS VARCHAR(100)) AS DOUBLE)'
    assert snap.aggregate_sql(GAMES_COLUMNS[1]) == (
        f"SUM(CASE WHEN ABS({text}) < 1E27 THEN CAST(ROUND({text}, 8) AS DECIMAL(36,8)) END)"
    )


@pytest.mark.parametrize(
    "type_text", ["TIMESTAMP WITH LOCAL TIME ZONE", "GEOMETRY", "HASHTYPE(16 BYTE)", "INTERVAL"]
)
def test_a_type_without_an_aggregate_is_an_error_naming_it(type_text: str) -> None:
    with pytest.raises(SnapshotError, match=f"X.*{type_text[:8]}"):
        snap.aggregate_sql(Column("X", type_text))


def test_the_fingerprint_query_counts_rows_and_every_column() -> None:
    sql = snap.fingerprint_sql("TEST_CFB_CORE", "GAMES", GAMES_COLUMNS[:1] + GAMES_COLUMNS[4:])
    assert sql == (
        "SELECT CAST(COUNT(*) AS VARCHAR(40)), "
        'CAST(COUNT("ID") AS VARCHAR(40)), CAST(SUM("ID") AS VARCHAR(100)), '
        'CAST(COUNT("NOTES") AS VARCHAR(40)), CAST(SUM(LENGTH("NOTES")) AS VARCHAR(100)) '
        'FROM "TEST_CFB_CORE"."GAMES"'
    )


def test_a_fingerprint_row_becomes_counts_and_aggregate_texts() -> None:
    columns = GAMES_COLUMNS[:2]
    fingerprint = snap.read_fingerprint(columns, ("3", "3", "6", "0", None))
    assert fingerprint == {"rows": 3, "ID": [3, "6"], "HOME_POINTS": [0, None]}


def test_fingerprints_compare_their_numbers_not_their_text() -> None:
    expected = {"rows": 2, "A": [2, "1.50"], "B": [0, None]}
    assert snap.fingerprint_differences("CFB_CORE.T", expected, {**expected, "A": [2, "1.5"]}) == []


def test_fingerprint_differences_name_the_table_and_column() -> None:
    expected = {"rows": 2, "A": [2, "3"], "B": [1, "7"]}
    found = {"rows": 2, "A": [2, "4"], "B": [1, "7"]}
    assert snap.fingerprint_differences("CFB_CORE.T", expected, found) == [
        "CFB_CORE.T.A: expected [2, '3'], found [2, '4']"
    ]
    assert snap.fingerprint_differences("CFB_CORE.T", expected, {**expected, "rows": 3}) == [
        "CFB_CORE.T: expected 2 rows, found 3"
    ]


# --- catalog hash ---------------------------------------------------------------------------


def test_the_catalog_hash_is_read_from_the_bundled_adapter_script() -> None:
    catalog_text = vs_bundle.DEFAULT_CATALOG_PATH.read_text(encoding="utf-8")
    built = vs_bundle.build(catalog_text)
    script = vs_bundle.adapter_script(built.adapter_body, "TEST_CFB_INFRA")
    expected = Catalog.from_json(catalog_text).catalog_hash
    assert snap.catalog_hash_in(script) == expected
    fragment = script[script.index(snap.CATALOG_ANCHOR) :][:100]
    assert snap.catalog_hash_in(fragment) == expected


def test_a_text_without_an_embedded_catalog_has_no_hash() -> None:
    assert snap.catalog_hash_in('CATALOG_JSON = None  # "catalog_hash": "x"') is None


# --- columns --------------------------------------------------------------------------------


def test_identical_columns_have_no_difference() -> None:
    assert snap.column_differences("CFB_CORE.GAMES", GAMES_COLUMNS, GAMES_COLUMNS) == []


def test_column_differences_name_the_table_and_the_column() -> None:
    found = (
        GAMES_COLUMNS[0],
        Column("HOME_POINTS", "DECIMAL(18,0)"),
        *GAMES_COLUMNS[2:4],
        Column("EXTRA", "BOOLEAN"),
    )
    assert snap.column_differences("CFB_CORE.GAMES", GAMES_COLUMNS, found) == [
        "CFB_CORE.GAMES: column NOTES is in the snapshot but not in the database",
        "CFB_CORE.GAMES: column EXTRA is in the database but not in the snapshot",
        "CFB_CORE.GAMES.HOME_POINTS: DOUBLE in the snapshot, DECIMAL(18,0) in the database",
    ]


def test_a_different_column_order_is_a_difference() -> None:
    reordered = (GAMES_COLUMNS[1], GAMES_COLUMNS[0], *GAMES_COLUMNS[2:])
    assert snap.column_differences("CFB_CORE.GAMES", GAMES_COLUMNS, reordered) == [
        "CFB_CORE.GAMES: the columns are in a different order"
    ]


# --- manifest -------------------------------------------------------------------------------


def test_the_manifest_round_trips_through_json() -> None:
    original = manifest()
    text = original.to_json()
    assert Manifest.from_json(text) == original
    data = json.loads(text)
    assert data["format"] == "csv-gzip"
    assert data["tables"][0]["columns"][1] == ["HOME_POINTS", "DOUBLE"]
    assert data["tables"][0]["fingerprint"]["rows"] == 2


def test_files_are_named_after_the_logical_schema_and_table() -> None:
    assert snap.file_name("CFB_META", "LOAD_LOG") == "CFB_META.LOAD_LOG.csv.gz"


def mutated(change: dict[str, Any], table: int = 0) -> str:
    data = json.loads(manifest().to_json())
    data["tables"][table].update(change)
    return json.dumps(data)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "not JSON"),
        (json.dumps({"name": "x"}), "missing"),
        (json.dumps({**json.loads(manifest().to_json()), "format": "parquet-zstd"}), "format"),
        (json.dumps({**json.loads(manifest().to_json()), "manifest_version": 9}), "version"),
        (mutated({"file": "../../etc/passwd"}), "file"),
        (mutated({"file": "CFB_CORE.TEAMS.csv.gz"}), "file"),
        (mutated({"file": None, "sha256": None}), "rows but no file"),
        (mutated({"rows": 0}), "file but no rows"),
        (mutated({"sha256": "xyz"}), "sha256"),
        (mutated({"schema": "CFB_SIM"}), "schema"),
        (mutated({"table": "GAMES; DROP"}), "table"),
        (mutated({"columns": [["ID", "DECIMAL(18,0)"], ["ID", "DOUBLE"]]}), "twice"),
        (mutated({"fingerprint": {"rows": 2}}), "fingerprint"),
        (mutated({"table": "LOAD_LOG", "file": "CFB_CORE.LOAD_LOG.csv.gz"}), "twice"),
        (mutated({"table": "LOAD_RUN", "file": "CFB_META.LOAD_RUN.csv.gz"}, 1), "not a snapshot"),
        (json.dumps({**json.loads(manifest().to_json()), "catalog_hash": "short"}), "catalog"),
    ],
)
def test_an_invalid_manifest_is_rejected(text: str, message: str) -> None:
    with pytest.raises(SnapshotError, match=message):
        Manifest.from_json(text)


# --- files ----------------------------------------------------------------------------------


def write_snapshot_file(directory: Path, table: TableSnapshot, body: str) -> TableSnapshot:
    assert table.file is not None
    path = directory / table.file
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        handle.write(body)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return TableSnapshot(
        table.schema, table.table, table.rows, table.columns, table.file, digest, table.fingerprint
    )


def test_the_header_line_lists_the_columns() -> None:
    assert snap.csv_header(LOG_COLUMNS) == "TABLE_NAME,LOADED_AT"


def test_matching_files_pass_the_checks(tmp_path: Path) -> None:
    log = write_snapshot_file(tmp_path, load_log(), "TABLE_NAME,LOADED_AT\nGAMES,x\n")
    snap.verify_files(tmp_path, manifest(log, empty_teams()))


def test_a_changed_file_fails_naming_it(tmp_path: Path) -> None:
    log = write_snapshot_file(tmp_path, load_log(), "TABLE_NAME,LOADED_AT\nGAMES,x\n")
    assert log.file is not None
    (tmp_path / log.file).write_bytes(gzip.compress(b"TABLE_NAME,LOADED_AT\nGAMES,y\n"))
    with pytest.raises(SnapshotError, match=r"CFB_META\.LOAD_LOG\.csv\.gz.*checksum"):
        snap.verify_files(tmp_path, manifest(log))


def test_a_file_whose_header_differs_fails_naming_it(tmp_path: Path) -> None:
    log = write_snapshot_file(tmp_path, load_log(), "LOADED_AT,TABLE_NAME\nx,GAMES\n")
    with pytest.raises(SnapshotError, match=r"CFB_META\.LOAD_LOG\.csv\.gz.*header"):
        snap.verify_files(tmp_path, manifest(log))


def test_a_missing_file_fails_naming_it(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError, match=r"CFB_META\.LOAD_LOG\.csv\.gz.*missing"):
        snap.verify_files(tmp_path, manifest(load_log()))


# --- the swap -------------------------------------------------------------------------------


def test_the_swap_empties_every_table_and_refills_the_snapshot_rows() -> None:
    statements = snap.swap_statements(TEST, (games(), empty_teams(), load_log()))
    assert statements == [
        'DELETE FROM "TEST_CFB_CORE"."GAMES"',
        'INSERT INTO "TEST_CFB_CORE"."GAMES" ("ID", "HOME_POINTS", "NEUTRAL_SITE", "START_DATE", '
        '"NOTES") SELECT "ID", "HOME_POINTS", "NEUTRAL_SITE", "START_DATE", "NOTES" '
        'FROM "TEST_CFB_RESTORE"."GAMES"',
        'DELETE FROM "TEST_CFB_CORE"."TEAMS"',
        'DELETE FROM "TEST_CFB_META"."LOAD_LOG"',
        'INSERT INTO "TEST_CFB_META"."LOAD_LOG" ("TABLE_NAME", "LOADED_AT") '
        'SELECT "TABLE_NAME", "LOADED_AT" FROM "TEST_CFB_RESTORE"."LOAD_LOG"',
    ]


def test_logical_schemas_map_to_the_namespace() -> None:
    assert snap.namespace_schema(TEST, "CFB_CORE") == "TEST_CFB_CORE"
    assert snap.namespace_schema(TEST, "CFB_META") == "TEST_CFB_META"
    assert snap.namespace_schema(Namespace(), "CFB_CORE") == "CFB_CORE"
    with pytest.raises(SnapshotError, match="CFB_SIM"):
        snap.namespace_schema(TEST, "CFB_SIM")


class TransactionConnection:
    def __init__(self, fail_on: str | None = None) -> None:
        self.log: list[str] = []
        self.fail_on = fail_on

    def set_autocommit(self, value: bool) -> None:
        self.log.append(f"autocommit {value}")

    def execute(self, query: str) -> Any:
        self.log.append(query)
        if self.fail_on and query.startswith(self.fail_on):
            raise RuntimeError("insert failed")
        return None


def test_the_swap_is_one_transaction() -> None:
    connection = TransactionConnection()
    snap.run_transaction(connection, ["DELETE FROM A", "INSERT INTO A"])
    assert connection.log == [
        "autocommit False",
        "DELETE FROM A",
        "INSERT INTO A",
        "COMMIT",
        "autocommit True",
    ]


def test_a_failing_swap_is_rolled_back_and_never_committed() -> None:
    connection = TransactionConnection(fail_on="INSERT")
    with pytest.raises(RuntimeError, match="insert failed"):
        snap.run_transaction(connection, ["DELETE FROM A", "INSERT INTO A", "DELETE FROM B"])
    assert connection.log == [
        "autocommit False",
        "DELETE FROM A",
        "INSERT INTO A",
        "ROLLBACK",
        "autocommit True",
    ]


# --- listing --------------------------------------------------------------------------------


def write_manifest(directory: Path, value: Manifest) -> None:
    directory.mkdir(parents=True)
    (directory / snap.MANIFEST_FILE).write_text(value.to_json(), encoding="utf-8")


def test_the_listing_shows_every_snapshot_with_its_source_and_size(tmp_path: Path) -> None:
    write_manifest(tmp_path / "week7", manifest())
    (tmp_path / "week7" / "CFB_CORE.GAMES.csv.gz").write_bytes(b"x" * 1000)
    (tmp_path / "broken").mkdir()
    listings = snap.list_snapshots(tmp_path)
    assert [item.name for item in listings] == ["broken", "week7"]
    broken, week7 = listings
    assert broken.problem is not None and "manifest" in broken.problem
    assert week7.problem is None
    assert week7.created_at == "2026-10-22T20:15:03Z"
    assert week7.source == "aws node1.example"
    assert (week7.tables, week7.rows) == (2, 3)
    assert week7.size_bytes >= 1000


def test_an_empty_or_missing_root_lists_nothing(tmp_path: Path) -> None:
    assert snap.list_snapshots(tmp_path / "none") == []
