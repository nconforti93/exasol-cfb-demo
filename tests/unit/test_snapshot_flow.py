"""`take_snapshot` and `restore_snapshot` over a scripted in-memory database (no Exasol).

The fake answers the statements the two flows send: the system tables, the `RUN_LOCK` script, the
fingerprint queries (one deterministic aggregate per row count) and pyexasol's file transfer as
real gzip CSV. These tests pin the order of the flows: the lock comes before the first file or
change, a failure cleans up and ends the run `FAILED`, and every refusal happens before a change.
The engine behaviour is covered by `tests/it/test_snapshot_restore_it.py`.
"""

from __future__ import annotations

import csv
import gzip
import io
import re
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from loader import snapshot as snap
from loader.plan import SnapshotScope
from loader.runlock import RunLockRefused
from loader.snapshot import SnapshotError, SnapshotRefused
from vs.host.namespace import Namespace

TEST = Namespace("TEST_")
CATALOG = "c" * 64
PLAN = "p" * 64
GAMES = (("ID", "DECIMAL(18,0)"), ("NOTES", "VARCHAR(2000) UTF8"))
LOG = (("TABLE_NAME", "VARCHAR(128) UTF8"), ("SLICE_KEY", "VARCHAR(2000) UTF8"))
SOURCE = {"target": "local", "host": "127.0.0.1", "namespace": "TEST_"}
SCOPE = SnapshotScope(("CFB_CORE",), ("LOAD_LOG",))


class Result:
    def __init__(self, rows: Sequence[tuple[Any, ...]]) -> None:
        self._rows = list(rows)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class FakeDatabase:
    """Tables as `(schema, table) -> (columns, rows)`; records every statement."""

    def __init__(self) -> None:
        self.tables: dict[tuple[str, str], tuple[tuple[tuple[str, str], ...], list[list[Any]]]] = {
            ("TEST_CFB_CORE", "GAMES"): (GAMES, [["1", "a,b"], ["2", 'say "hi"']]),
            ("TEST_CFB_CORE", "TEAMS"): ((("ID", "DECIMAL(18,0)"),), []),
            ("TEST_CFB_META", "LOAD_LOG"): (LOG, [["GAMES", "/games?year=2019"]]),
        }
        self.statements: list[str] = []
        self.attr = {"autocommit": True}
        self.catalog = CATALOG
        self.held = False
        self.on_acquire: Any = None
        self.on_fingerprint: Any = None
        self.fail_on: str | None = None
        self.refuse = False
        self.fail_export_of: tuple[str, str] | None = None
        self.lose_imported_row = False

    def set_autocommit(self, value: bool) -> None:
        self.statements.append(f"autocommit {value}")
        self.attr["autocommit"] = value

    def execute(self, query: str) -> Result:
        self.statements.append(query)
        if self.fail_on and query.startswith(self.fail_on):
            raise RuntimeError(f"failed: {self.fail_on}")
        if query.startswith("SELECT COUNT(*) FROM EXA_ALL_SCRIPTS"):
            return Result([(1,)])
        if "RUN_LOCK\"('acquire'" in query:
            if self.refuse:
                kind = query.split("'acquire', '", 1)[1].split("'", 1)[0]
                raise RuntimeError(f"{kind} refused: BACKFILL run 3 is running (session 9)")
            if self.on_acquire:
                self.on_acquire()
            self.held = True
            return Result([(7,)])
        if "RUN_LOCK\"('release'" in query:
            self.held = False
            return Result([(1,)])
        if "FROM EXA_ALL_COLUMNS" in query:
            schema = re.search(r"COLUMN_SCHEMA = '([A-Z_]+)'", query)
            assert schema is not None
            return Result(
                [
                    (table, name, type_text, True)
                    for (owner, table), (columns, _) in sorted(self.tables.items())
                    if owner == schema.group(1)
                    for name, type_text in columns
                ]
            )
        if query.startswith("SELECT SUBSTR(SCRIPT_TEXT"):
            return Result([(f'{{"catalog_hash":"{self.catalog}","spec_version":"x"}}',)])
        if 'MAX("PLAN_HASH")' in query:
            return Result([(PLAN,)])
        if "EXA_METADATA" in query:
            return Result([("2026.2.0",)])
        if query.startswith("SELECT CURRENT_SCHEMA"):
            return Result([(None,)])
        if query.startswith("SELECT CAST(COUNT(*)"):
            return Result([self._fingerprint(query)])
        if "LOAD_ORPHANS" in query:
            return Result([("GAMES", 2)])
        like = re.match(
            r'CREATE TABLE "([A-Z_]+)"\."([A-Z_]+)" \(LIKE "([A-Z_]+)"\."([A-Z_]+)"\)', query
        )
        if like:
            columns = self.tables[(like.group(3), like.group(4))][0]
            self.tables[(like.group(1), like.group(2))] = (columns, [])
        return Result([])

    def _fingerprint(self, query: str) -> tuple[Any, ...]:
        found = re.search(r'FROM "([A-Z_]+)"\."([A-Z_]+)"$', query)
        assert found is not None
        columns, rows = self.tables[(found.group(1), found.group(2))]
        if self.on_fingerprint:
            self.on_fingerprint(found.group(2))
        count = str(len(rows))
        values: list[Any] = [count]
        for _ in columns:
            values += [count, str(len(rows) * 10) if rows else None]
        return tuple(values)

    def export_to_file(
        self, dst: Any, query_or_table: Any, query_params: Any = None, export_params: Any = None
    ) -> None:
        self.statements.append(f"EXPORT {query_or_table}")
        if query_or_table == self.fail_export_of:
            raise RuntimeError("export failed")
        columns, rows = self.tables[query_or_table]
        assert export_params["columns"] == [name for name, _ in columns]
        text = io.StringIO()
        writer = csv.writer(text, lineterminator="\n")
        writer.writerow(export_params["columns"])
        writer.writerows(rows)
        dst.write(gzip.compress(text.getvalue().encode("utf-8")))

    def import_from_file(self, src: Any, table: Any, import_params: Any = None) -> None:
        self.statements.append(f"IMPORT {table}")
        lines = gzip.decompress(src.read()).decode("utf-8")
        rows = [list(row) for row in csv.reader(io.StringIO(lines))][import_params["skip"] :]
        self.tables[table][1].extend(rows[:-1] if self.lose_imported_row else rows)


def take(db: FakeDatabase, root: Path, name: str = "week7") -> snap.Manifest:
    return snap.take_snapshot(db, TEST, root, name, source=SOURCE, scope=SCOPE)


def restore(db: FakeDatabase, root: Path, *, force: bool = False) -> snap.RestoreReport:
    return snap.restore_snapshot(db, TEST, root, "week7", force=force)


def releases(db: FakeDatabase) -> list[str]:
    return [s for s in db.statements if "'release'" in s]


# --- snapshot -------------------------------------------------------------------------------


def test_a_snapshot_takes_the_lock_before_it_writes_anything(tmp_path: Path) -> None:
    db = FakeDatabase()

    def nothing_written() -> None:
        assert list(tmp_path.iterdir()) == []

    db.on_acquire = nothing_written
    manifest = take(db, tmp_path)
    directory = tmp_path / "week7"
    assert sorted(p.name for p in directory.iterdir()) == [
        "CFB_CORE.GAMES.csv.gz",
        "CFB_META.LOAD_LOG.csv.gz",
        "manifest.json",
    ]
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in directory.iterdir())
    assert [(t.label, t.rows, t.file is not None) for t in manifest.tables] == [
        ("CFB_CORE.GAMES", 2, True),
        ("CFB_CORE.TEAMS", 0, False),
        ("CFB_META.LOAD_LOG", 1, True),
    ]
    assert (manifest.catalog_hash, manifest.plan_hash, manifest.db_version) == (
        CATALOG,
        PLAN,
        "2026.2.0",
    )
    assert snap.read_manifest(directory) == manifest
    snap.verify_files(directory, manifest)
    assert releases(db) == [
        "EXECUTE SCRIPT \"TEST_CFB_INFRA\".\"RUN_LOCK\"('release', 'DONE', 7, "
        "'snapshot week7: 2 files, 3 rows, catalog cccccccccccc')"
    ]


def test_a_refused_lock_writes_nothing(tmp_path: Path) -> None:
    db = FakeDatabase()
    db.refuse = True
    with pytest.raises(RunLockRefused):
        take(db, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_failed_export_removes_the_directory_and_ends_the_run_failed(tmp_path: Path) -> None:
    db = FakeDatabase()
    db.fail_on = "SELECT PARAM_VALUE"
    with pytest.raises(RuntimeError):
        take(db, tmp_path)
    assert list(tmp_path.iterdir()) == []
    assert "'FAILED'" in releases(db)[0]

    db = FakeDatabase()
    db.fail_export_of = ("TEST_CFB_META", "LOAD_LOG")
    with pytest.raises(RuntimeError, match="export failed"):
        take(db, tmp_path)
    assert not (tmp_path / "week7").exists()
    assert "'FAILED', 7, 'export failed'" in releases(db)[0]


def test_data_that_changes_during_the_snapshot_is_not_kept(tmp_path: Path) -> None:
    db = FakeDatabase()
    seen: list[str] = []

    def grow(table: str) -> None:
        seen.append(table)
        if seen.count("LOAD_LOG") == 2:
            db.tables[("TEST_CFB_META", "LOAD_LOG")][1].append(["GAMES", "/games?year=2020"])

    db.on_fingerprint = grow
    with pytest.raises(SnapshotError, match=r"changed while the snapshot was written") as failed:
        take(db, tmp_path)
    assert "CFB_META.LOAD_LOG: expected 1 rows, found 2" in str(failed.value)
    assert not (tmp_path / "week7").exists()


def test_an_unreadable_catalog_hash_stops_the_snapshot(tmp_path: Path) -> None:
    db = FakeDatabase()
    db.catalog = "not-a-hash"
    with pytest.raises(SnapshotError, match="deployed catalog hash"):
        take(db, tmp_path)
    assert list(tmp_path.iterdir()) == []


# --- restore --------------------------------------------------------------------------------


def snapshotted(tmp_path: Path) -> FakeDatabase:
    take(FakeDatabase(), tmp_path)
    db = FakeDatabase()
    db.tables[("TEST_CFB_CORE", "GAMES")][1].append(["3", "newer"])
    return db


def test_a_restore_lands_checks_and_swaps_in_one_transaction(tmp_path: Path) -> None:
    db = snapshotted(tmp_path)
    report = restore(db, tmp_path)
    acquire = next(i for i, sql in enumerate(db.statements) if "'acquire'" in sql)
    checks = [i for i, sql in enumerate(db.statements) if "EXA_ALL_COLUMNS" in sql]
    assert checks and max(checks) < acquire
    landing = [sql for sql in db.statements[acquire + 1 :] if not sql.startswith("SELECT")]
    assert landing[:3] == [
        'DROP SCHEMA IF EXISTS "TEST_CFB_RESTORE" CASCADE',
        'CREATE SCHEMA "TEST_CFB_RESTORE"',
        "CLOSE SCHEMA",
    ]
    swap = db.statements[db.statements.index("autocommit False") :]
    assert swap[:6] == [
        "autocommit False",
        'DELETE FROM "TEST_CFB_CORE"."GAMES"',
        'INSERT INTO "TEST_CFB_CORE"."GAMES" ("ID", "NOTES") SELECT "ID", "NOTES" '
        'FROM "TEST_CFB_RESTORE"."GAMES"',
        'DELETE FROM "TEST_CFB_CORE"."TEAMS"',
        'DELETE FROM "TEST_CFB_META"."LOAD_LOG"',
        'INSERT INTO "TEST_CFB_META"."LOAD_LOG" ("TABLE_NAME", "SLICE_KEY") SELECT "TABLE_NAME", '
        '"SLICE_KEY" FROM "TEST_CFB_RESTORE"."LOAD_LOG"',
    ]
    assert swap[6:9] == [
        "COMMIT",
        "autocommit True",
        'DROP SCHEMA IF EXISTS "TEST_CFB_RESTORE" CASCADE',
    ]
    assert db.tables[("TEST_CFB_RESTORE", "GAMES")][1] == [["1", "a,b"], ["2", 'say "hi"']]
    assert "'DONE', 7, 'restored week7: 3 tables, 3 rows'" in releases(db)[0]
    assert (report.run_id, dict(report.orphans)) == (7, {"GAMES": 2})


def test_a_landing_fingerprint_mismatch_changes_nothing(tmp_path: Path) -> None:
    db = snapshotted(tmp_path)
    db.lose_imported_row = True
    with pytest.raises(SnapshotError, match=r"CFB_CORE\.GAMES\.csv\.gz: expected 2 rows, found 1"):
        restore(db, tmp_path)
    assert not [s for s in db.statements if s.startswith(("DELETE", "INSERT"))]
    assert db.statements[-4:-1] == [
        "ROLLBACK",
        'DROP SCHEMA IF EXISTS "TEST_CFB_RESTORE" CASCADE',
        "ROLLBACK",
    ]
    assert "'FAILED'" in db.statements[-1]


def test_a_failed_swap_is_rolled_back_and_the_landing_dropped(tmp_path: Path) -> None:
    db = snapshotted(tmp_path)
    db.fail_on = 'INSERT INTO "TEST_CFB_META"'
    with pytest.raises(RuntimeError):
        restore(db, tmp_path)
    after = db.statements[db.statements.index('DELETE FROM "TEST_CFB_META"."LOAD_LOG"') :]
    assert after[2:4] == ["ROLLBACK", "autocommit True"]
    assert "COMMIT" not in after
    assert 'DROP SCHEMA IF EXISTS "TEST_CFB_RESTORE" CASCADE' in after
    assert "'FAILED'" in releases(db)[0]


def test_another_catalog_is_refused_before_the_lock_unless_forced(tmp_path: Path) -> None:
    db = snapshotted(tmp_path)
    db.catalog = "d" * 64
    with pytest.raises(SnapshotRefused) as refused:
        restore(db, tmp_path)
    assert CATALOG in str(refused.value) and "d" * 64 in str(refused.value)
    assert not [s for s in db.statements if "RUN_LOCK" in s]
    restore(db, tmp_path, force=True)
    assert "'acquire', 'RESTORE', NULL, 'week7 FORCE'" in "".join(db.statements)


def test_a_column_or_table_difference_is_refused_also_with_force(tmp_path: Path) -> None:
    db = snapshotted(tmp_path)
    db.tables[("TEST_CFB_CORE", "GAMES")] = ((("ID", "DECIMAL(18,0)"),), [])
    db.tables[("TEST_CFB_CORE", "VENUES")] = ((("ID", "DECIMAL(18,0)"),), [])
    with pytest.raises(SnapshotRefused) as refused:
        restore(db, tmp_path, force=True)
    message = str(refused.value)
    assert "CFB_CORE.GAMES: column NOTES is in the snapshot but not in the database" in message
    assert "CFB_CORE.VENUES: the table is in the database but not in the snapshot" in message
    assert not [s for s in db.statements if "RUN_LOCK" in s]


def test_a_missing_table_is_refused(tmp_path: Path) -> None:
    db = snapshotted(tmp_path)
    del db.tables[("TEST_CFB_META", "LOAD_LOG")]
    with pytest.raises(SnapshotRefused, match=r"CFB_META\.LOAD_LOG: the table is in the snapshot"):
        restore(db, tmp_path)


def test_scope_tables_need_a_deployment(tmp_path: Path) -> None:
    db = FakeDatabase()
    db.tables = {}
    with pytest.raises(SnapshotError, match="TEST_CFB_CORE has no tables"):
        take(db, tmp_path)
    db.tables = {("TEST_CFB_CORE", "GAMES"): (GAMES, [])}
    with pytest.raises(SnapshotError, match=r"TEST_CFB_META\.LOAD_LOG does not exist"):
        take(db, tmp_path)
