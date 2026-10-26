"""`scripts/snapshot.py` (`make snapshot`, `make restore`, `make snapshots`) without a database.

The library calls that need Exasol are replaced by recorders; the location rules, the name rules,
the listing and the exit codes run for real against private temporary directories.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from loader import snapshot as snap
from loader.runlock import RunLockRefused
from loader.snapshot import (
    Column,
    Manifest,
    RestoreReport,
    SnapshotError,
    SnapshotRefused,
    TableSnapshot,
)
from scripts import snapshot as cli
from vs.host import target
from vs.host.namespace import Namespace

ROOT = Path(__file__).resolve().parents[2]
CATALOG = "c" * 64


def manifest(name: str = "week7") -> Manifest:
    games = TableSnapshot(
        "CFB_CORE",
        "GAMES",
        2,
        (Column("ID", "DECIMAL(18,0)"),),
        "CFB_CORE.GAMES.csv.gz",
        "a" * 64,
        {"rows": 2, "ID": [2, "3"]},
    )
    load_log = TableSnapshot(
        "CFB_META",
        "LOAD_LOG",
        1,
        (Column("SLICE_KEY", "VARCHAR(2000) UTF8"),),
        "CFB_META.LOAD_LOG.csv.gz",
        "b" * 64,
        {"rows": 1, "SLICE_KEY": [1, "16"]},
    )
    return Manifest(
        name=name,
        created_at="2026-10-22T20:15:03Z",
        source={"target": "aws", "host": "node1", "namespace": ""},
        db_version="2026.2.0",
        catalog_hash=CATALOG,
        plan_hash=None,
        tables=(games, load_log),
    )


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def execute(self, query: str) -> Any:
        raise AssertionError(f"unexpected statement: {query}")

    def close(self) -> None:
        self.closed = True


@dataclass
class Calls:
    connections: list[FakeConnection] = field(default_factory=list)
    targets: list[str | None] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    restores: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)


@pytest.fixture
def snapshot_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "private" / "snapshots"
    monkeypatch.setenv(snap.SNAPSHOT_DIR_VARIABLE, str(root))
    return root


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[Calls]:
    recorded = Calls()

    def take(connection: Any, namespace: Namespace, root: Path, name: str, **kw: Any) -> Manifest:
        recorded.snapshots.append({"root": root, "name": name, "namespace": namespace, **kw})
        kw["log"]("exported CFB_CORE.GAMES: 2 rows")
        return manifest(name)

    def restore(
        connection: Any, namespace: Namespace, root: Path, name: str, **kw: Any
    ) -> RestoreReport:
        recorded.restores.append({"root": root, "name": name, "namespace": namespace, **kw})
        kw["log"]("catalog ok")
        return RestoreReport(manifest(name), 12, {"GAMES": 3})

    def check(connection: Any, namespace: Namespace, value: Manifest, **kw: Any) -> list[str]:
        recorded.checks.append({"name": value.name, **kw})
        return ["columns ok: every scope table matches the manifest", "catalog ok (cccccccccccc)"]

    monkeypatch.setattr(snap, "take_snapshot", take)
    monkeypatch.setattr(snap, "restore_snapshot", restore)
    monkeypatch.setattr(snap, "check_restore", check)
    monkeypatch.setattr(snap, "verify_files", lambda directory, value: None)
    yield recorded


def run(calls: Calls, *argv: str) -> int:
    def resolve(name: str | None = None, *, dsn: str | None = None) -> target.Target:
        calls.targets.append(name)
        return target.Target(
            name or "local", "127.0.0.1", 8563, "sys", Path("/x"), target.TLS_UNVERIFIED_LOCAL
        )

    def connector(_: target.Target) -> FakeConnection:
        connection = FakeConnection()
        calls.connections.append(connection)
        return connection

    return cli.main(list(argv), connector=connector, resolve=resolve)


def write_snapshot(root: Path, name: str = "week7") -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / snap.MANIFEST_FILE).write_text(manifest(name).to_json(), encoding="utf-8")
    return directory


# --- snapshot -------------------------------------------------------------------------------


def test_snapshot_refuses_repo_and_onedrive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: Calls,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Scenario: Snapshots never land in the repository or a synced folder."""
    inside = ROOT / "cache" / "snapshots-test"
    synced = tmp_path / "OneDrive - Example" / "snapshots"
    for directory, rule in ((inside, "inside the repository"), (synced, "OneDrive folder")):
        monkeypatch.setenv(snap.SNAPSHOT_DIR_VARIABLE, str(directory))
        assert run(calls, "snapshot", "--name", "week7") == cli.EXIT_REFUSED
        error = capsys.readouterr().err
        assert str(directory) in error and rule in error
        assert not directory.exists()
    assert calls.connections == [] and calls.snapshots == []


def test_snapshot_never_overwritten(
    snapshot_root: Path, calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scenario: A snapshot is never overwritten."""
    existing = write_snapshot(snapshot_root)
    before = (existing / snap.MANIFEST_FILE).read_bytes()
    assert run(calls, "snapshot", "--name", "week7") == cli.EXIT_REFUSED
    error = capsys.readouterr().err
    assert "week7 already exists" in error and str(existing) in error
    assert (existing / snap.MANIFEST_FILE).read_bytes() == before
    assert sorted(path.name for path in existing.iterdir()) == [snap.MANIFEST_FILE]
    assert calls.connections == []


def test_an_invalid_name_is_refused_before_connecting(
    snapshot_root: Path, calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(calls, "snapshot", "--name", "../week7") == cli.EXIT_REFUSED
    assert "invalid snapshot name" in capsys.readouterr().err
    assert calls.connections == []


def test_a_snapshot_runs_on_the_target_and_reports_zero_calls(
    snapshot_root: Path, calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(calls, "--target", "aws", "snapshot", "--name", "week8") == cli.EXIT_OK
    (call,) = calls.snapshots
    assert (call["root"], call["name"], call["namespace"]) == (snapshot_root, "week8", Namespace())
    assert call["source"] == {"target": "aws", "host": "127.0.0.1", "namespace": ""}
    assert call["scope"].schemas == ("CFB_CORE",)
    assert calls.targets == ["aws"]
    out = capsys.readouterr().out
    assert "exported CFB_CORE.GAMES: 2 rows" in out
    assert "snapshot week8: 2 files, 3 rows" in out and "0 CFBD calls" in out
    assert calls.connections[0].closed


def test_a_refused_lock_exits_1(
    snapshot_root: Path,
    calls: Calls,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refused(*_: Any, **__: Any) -> Manifest:
        raise RunLockRefused("SNAPSHOT refused: BACKFILL run 7 is running (session 1)")

    monkeypatch.setattr(snap, "take_snapshot", refused)
    assert run(calls, "snapshot", "--name", "week8") == cli.EXIT_REFUSED
    assert "refused: SNAPSHOT refused: BACKFILL run 7 is running" in capsys.readouterr().err
    assert calls.connections[0].closed


def test_a_failed_snapshot_exits_2(
    snapshot_root: Path,
    calls: Calls,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def failed(*_: Any, **__: Any) -> Manifest:
        raise SnapshotError("the data changed while the snapshot was written")

    monkeypatch.setattr(snap, "take_snapshot", failed)
    assert run(calls, "snapshot", "--name", "week8") == cli.EXIT_ERROR
    assert "snapshot failed: the data changed" in capsys.readouterr().err


def test_an_unreachable_target_exits_2(
    snapshot_root: Path, calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    def resolve(name: str | None = None, *, dsn: str | None = None) -> target.Target:
        raise target.TargetError("deployment file not found: /x/deployment.json")

    code = cli.main(["snapshot", "--name", "week8"], resolve=resolve)
    assert code == cli.EXIT_ERROR
    assert "deployment file not found" in capsys.readouterr().err


# --- restore --------------------------------------------------------------------------------


def test_a_restore_without_confirm_previews_and_changes_nothing(
    snapshot_root: Path, calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    write_snapshot(snapshot_root)
    assert run(calls, "restore", "--name", "week7") == cli.EXIT_REFUSED
    captured = capsys.readouterr()
    assert "snapshot week7: created 2026-10-22T20:15:03Z from aws node1" in captured.out
    assert "catalog ok" in captured.out
    assert "CONFIRM=yes" in captured.err
    assert calls.checks == [{"name": "week7", "force": False}]
    assert calls.restores == []


def test_a_confirmed_restore_runs_and_reports_orphans(
    snapshot_root: Path, calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    write_snapshot(snapshot_root)
    assert run(calls, "restore", "--name", "week7", "--confirm", "yes", "--force", "yes") == 0
    (call,) = calls.restores
    assert (call["name"], call["force"], call["root"]) == ("week7", True, snapshot_root)
    out = capsys.readouterr().out
    assert "restore week7: done as RESTORE run 12" in out and "0 CFBD calls" in out
    assert "warning: orphan slices of GAMES: 3" in out


def test_a_refused_restore_exits_1_and_a_failed_one_exits_2(
    snapshot_root: Path,
    calls: Calls,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_snapshot(snapshot_root)
    for error, code, text in (
        (SnapshotRefused("the snapshot's catalog differs"), cli.EXIT_REFUSED, "refused: "),
        (SnapshotError("GAMES.csv.gz: the checksum differs"), cli.EXIT_ERROR, "restore failed: "),
    ):

        def raising(*_: Any, error: Exception = error, **__: Any) -> RestoreReport:
            raise error

        monkeypatch.setattr(snap, "restore_snapshot", raising)
        assert run(calls, "restore", "--name", "week7", "--confirm", "yes") == code
        assert text + str(error) in capsys.readouterr().err


def test_a_missing_snapshot_exits_2(
    snapshot_root: Path, calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(calls, "restore", "--name", "nothing", "--confirm", "yes") == cli.EXIT_ERROR
    assert "no snapshot at" in capsys.readouterr().err
    assert calls.connections == []


# --- list -----------------------------------------------------------------------------------


def test_the_listing_shows_every_snapshot(
    snapshot_root: Path, calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    write_snapshot(snapshot_root, "week7")
    (snapshot_root / "half").mkdir()
    assert run(calls, "list") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "week7" in out and "2026-10-22T20:15:03Z" in out and "aws node1" in out
    assert "2 tables, 3 rows" in out and CATALOG[:12] in out
    assert "half" in out and "incomplete" in out
    assert calls.connections == []


def test_an_empty_listing_names_the_directory(
    snapshot_root: Path, calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(calls, "list") == cli.EXIT_OK
    assert f"no snapshots in {snapshot_root}" in capsys.readouterr().out


def test_the_module_runs_as_a_script() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.snapshot", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "make restore" in result.stdout
