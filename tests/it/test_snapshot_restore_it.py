"""Scenarios of `loader/snapshot-restore` (task 5.2) on Exasol Personal local, `TEST_` namespace.

The data comes from a replay-mode `BACKFILL` of the `snapshot` plan variant (TEAMS, GAMES 2017-2020
with the gap 2018, RATINGS_SP 2019-2020, the clock at 2020-10-20), so `CFB_CORE`, `LOAD_LOG` and
`FETCH_LOG` hold what a real run writes. Snapshots go to the private `snapshot_dir` of the test,
never to `~/.cfb-demo`. Zero CFBD calls: the mock and the replay store are the only sources.

A restored DOUBLE may differ from its source by its 15-digit text form (the spec's exception); the
synthetic data has such values already (`556.5500000000001` comes back as `556.55`), so the
comparisons use `assert_restored`, exact for every other type.

The host sessions: `snapshot()` and `restore()` run in the owner's connection unless a test passes
another one. A test that needs a second locking session uses `harness.owner_session()`, because the
own-session rule lets a session's next locking script abandon its own row.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyexasol
import pytest

from loader import snapshot as snap
from loader.runlock import RunLockRefused
from loader.snapshot import Manifest, SnapshotError, SnapshotRefused
from tests.it.backfill_helpers import (
    GAMES_PATH,
    WAIT_SECONDS,
    games_key,
    ratings_key,
    replay,
    running_batch,
    total,
    wait_for,
)
from tests.it.harness import Harness, Mock
from tests.synthetic import fake
from vs import bundle as vs_bundle
from vs.cfbd_vs.catalog import Catalog

pytestmark = pytest.mark.it

PLAN_VARIANT = "snapshot"
PLAN_CONFIG = {"now_override": "2020-10-20"}
LATER = "2021-08-02"
NAME = "week7"
SOURCE = {"target": "local", "host": "127.0.0.1", "namespace": "TEST_"}
STORED_FILES = {
    "CFB_CORE.GAMES.csv.gz",
    "CFB_CORE.RATINGS_SP.csv.gz",
    "CFB_CORE.TEAMS.csv.gz",
    "CFB_META.LOAD_LOG.csv.gz",
    "CFB_META.FETCH_LOG.csv.gz",
}
META_SCOPE = ("LOAD_LOG", "FETCH_LOG")
LATENCY_MS = 10_000
OTHER_CATALOG = "0" * 64
DOUBLE_DRIFT = 4e-12
State = dict[str, list[tuple[Any, ...]]]
AWKWARD_TEXT = '  a,"b";\tc\r\nd \U0001f3c8 é  '


def populate(harness: Harness) -> list[dict[str, Any]]:
    """Fetch every planned slice in replay mode with BACKFILL; returns its result table."""
    replay(harness, [str(row["SLICE_KEY"]) for row in harness.backfill_queue()])
    rows = harness.backfill()
    assert total(rows)["RUN_STATUS"] == "DONE"
    return rows


def snapshot(
    harness: Harness,
    root: Path,
    name: str = NAME,
    *,
    connection: Any = None,
    log: Callable[[str], None] | None = None,
) -> Manifest:
    return snap.take_snapshot(
        connection or harness.owner,
        harness.namespace,
        root,
        name,
        source=SOURCE,
        log=log or (lambda _: None),
    )


def restore(
    harness: Harness,
    root: Path,
    name: str = NAME,
    *,
    force: bool = False,
    connection: Any = None,
    log: Callable[[str], None] | None = None,
) -> snap.RestoreReport:
    return snap.restore_snapshot(
        connection or harness.owner,
        harness.namespace,
        root,
        name,
        force=force,
        log=log or (lambda _: None),
    )


def scope_state(harness: Harness) -> State:
    """Every non-empty scope table (`CFB_CORE`, `LOAD_LOG`, `FETCH_LOG`) as its sorted rows."""
    tables = harness.query(
        "SELECT TABLE_SCHEMA, TABLE_NAME FROM EXA_ALL_TABLES "
        "WHERE (TABLE_SCHEMA = {core} OR (TABLE_SCHEMA = {meta} AND TABLE_NAME IN "
        "('LOAD_LOG', 'FETCH_LOG'))) AND TABLE_ROW_COUNT > 0",
        {"core": harness.core, "meta": harness.meta},
    )
    state: State = {}
    for schema, table in tables:
        rows = harness.query(f'SELECT * FROM "{schema}"."{table}"')
        if rows:
            state[f"{schema}.{table}"] = sorted(rows, key=_row_order)
    return state


def _row_order(row: tuple[Any, ...]) -> tuple[str, str]:
    """Rows sort by their values without DOUBLEs first, so a DOUBLE's text drift keeps the order."""
    return repr(tuple(value for value in row if not isinstance(value, float))), repr(row)


def assert_restored(after: State, before: State) -> int:
    """Same tables, rows and values, except that a DOUBLE may come back as its 15-digit text form
    (the spec's exception: relative change at most 4e-12). Returns how many DOUBLEs moved."""
    assert after.keys() == before.keys()
    moved = 0
    for key in before:
        assert len(after[key]) == len(before[key]), key
        for old, new in zip(before[key], after[key], strict=True):
            for position, (was, now) in enumerate(zip(old, new, strict=True)):
                if isinstance(was, float) and isinstance(now, float):
                    assert math.isclose(was, now, rel_tol=DOUBLE_DRIFT), (key, position, was, now)
                    moved += was != now
                else:
                    assert was == now, (key, position, was, now)
    return moved


def run_rows(harness: Harness, kind: str) -> list[dict[str, Any]]:
    return [run for run in harness.runs() if run["RUN_KIND"] == kind]


def landing_exists(harness: Harness) -> bool:
    rows = harness.query(
        "SELECT COUNT(*) FROM EXA_ALL_SCHEMAS WHERE SCHEMA_NAME = {s}",
        {"s": harness.namespace.restore_schema},
    )
    return int(rows[0][0]) > 0


def rewrite_manifest(directory: Path, change: Callable[[dict[str, Any]], None]) -> None:
    path = directory / snap.MANIFEST_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def table_entry(data: dict[str, Any], schema: str, table: str) -> dict[str, Any]:
    return next(t for t in data["tables"] if (t["schema"], t["table"]) == (schema, table))


def fetch_newer_data(harness: Harness) -> None:
    """Move the clock past the 2021 rollover: 2020 becomes final and is fetched again with changed
    upstream data, and the 2021 slices are new."""
    harness.write_recording(games_key(2020), fake.changed_games_body(2020))
    harness.write_recordings([games_key(2021), ratings_key(2021), ratings_key(2020)])
    harness.set_now(LATER)
    rows = harness.backfill()
    assert total(rows)["RUN_STATUS"] == "DONE"


def deployed_catalog_hash() -> str:
    text = vs_bundle.DEFAULT_CATALOG_PATH.read_text(encoding="utf-8")
    return Catalog.from_json(text).catalog_hash


def test_snapshot_private_self_describing(harness: Harness, snapshot_dir: Path) -> None:
    """Scenario: A snapshot writes a private, self-describing copy."""
    populate(harness)
    manifest = snapshot(harness, snapshot_dir)
    directory = snapshot_dir / NAME

    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    files = {path.name for path in directory.iterdir()}
    assert files == STORED_FILES | {snap.MANIFEST_FILE}
    for path in directory.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name

    core_tables = {
        str(row[0])
        for row in harness.query(
            "SELECT TABLE_NAME FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = {s}", {"s": harness.core}
        )
    }
    listed = {(t.schema, t.table) for t in manifest.tables}
    assert listed == {("CFB_CORE", name) for name in core_tables} | {
        ("CFB_META", name) for name in META_SCOPE
    }
    assert Manifest.from_json((directory / snap.MANIFEST_FILE).read_text("utf-8")) == manifest
    assert manifest.catalog_hash == deployed_catalog_hash()
    plan_hash = harness.query(f"SELECT MAX(PLAN_HASH) FROM {harness.meta}.LOAD_PLAN")[0][0]
    assert manifest.plan_hash == plan_hash
    assert manifest.format == "csv-gzip"
    for table in manifest.tables:
        schema = harness.core if table.schema == "CFB_CORE" else harness.meta
        count = harness.query(f'SELECT COUNT(*) FROM "{schema}"."{table.table}"')[0][0]
        assert table.rows == count == table.fingerprint["rows"], table.label
        columns = harness.query(
            "SELECT COLUMN_NAME FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA = {s} AND "
            "COLUMN_TABLE = {t} ORDER BY COLUMN_ORDINAL_POSITION",
            {"s": schema, "t": table.table},
        )
        assert [c.name for c in table.columns] == [str(row[0]) for row in columns]
        assert set(table.fingerprint) == {"rows", *(c.name for c in table.columns)}
        if table.file is not None:
            content = (directory / table.file).read_bytes()
            assert hashlib.sha256(content).hexdigest() == table.sha256
            assert (
                gzip.decompress(content)
                .decode("utf-8")
                .startswith(snap.csv_header(table.columns) + "\n")
            )
    gap = harness.query(
        f"SELECT ROWS_LOADED, LOADED_BY FROM {harness.meta}.LOAD_LOG WHERE SLICE_KEY = {{k}}",
        {"k": games_key(2018)},
    )
    assert gap == [(0, "LOAD_PLAN_GAP")]

    texts = [(directory / snap.MANIFEST_FILE).read_text("utf-8")] + [
        gzip.decompress((directory / name).read_bytes()).decode("utf-8") for name in STORED_FILES
    ]
    for text in texts:
        assert harness.marker_key not in text
        assert "IDENTIFIED BY" not in text
    assert set(manifest.source) == {"target", "host", "namespace"}
    assert [(run["RUN_STATUS"], run["RUN_ARGS"]) for run in run_rows(harness, "SNAPSHOT")] == [
        ("DONE", NAME)
    ]


def test_rebuilt_deployment_restored_exactly(
    harness: Harness, snapshot_dir: Path, mock: Mock
) -> None:
    """Scenario: A rebuilt deployment is restored exactly, with zero calls."""
    populate(harness)
    before = scope_state(harness)
    assert {"LOAD_LOG", "FETCH_LOG", "GAMES"} <= {key.split(".")[1] for key in before}
    snapshot(harness, snapshot_dir)

    harness.rebuild()
    assert scope_state(harness) == {}
    assert harness.runs() == []
    removed = harness.replay_area.clear()
    harness.wait_for_files(removed, present=False)
    mock.reset()

    lines: list[str] = []
    report = restore(harness, snapshot_dir, log=lines.append)

    assert_restored(scope_state(harness), before)
    assert report.orphans == {}
    assert any(line.startswith("catalog ok") for line in lines)
    assert [(run["RUN_KIND"], run["RUN_STATUS"]) for run in harness.runs()] == [("RESTORE", "DONE")]
    assert not landing_exists(harness)
    assert mock.hits() == 0
    stored = harness.query(
        f"SELECT REQ_SOURCE, COUNT(*) FROM {harness.vs}.GAMES WHERE P_YEAR = 2019 "
        "GROUP BY REQ_SOURCE"
    )
    assert [source for source, _ in stored] == ["STORED"]
    assert mock.hits() == 0


def test_restore_resets_newer_data(harness: Harness, snapshot_dir: Path) -> None:
    """Scenario: A restore resets newer data to the snapshot."""
    populate(harness)
    before = scope_state(harness)
    snapshot(harness, snapshot_dir)
    fetch_newer_data(harness)
    newer = scope_state(harness)
    assert newer != before
    loaded = {
        str(row[0]) for row in harness.query(f"SELECT SLICE_KEY FROM {harness.meta}.LOAD_LOG")
    }
    assert games_key(2021) in loaded
    runs_before = harness.runs()
    steps_before = harness.steps()

    report = restore(harness, snapshot_dir)

    assert_restored(scope_state(harness), before)
    loaded = {
        str(row[0]) for row in harness.query(f"SELECT SLICE_KEY FROM {harness.meta}.LOAD_LOG")
    }
    assert games_key(2021) not in loaded and ratings_key(2021) not in loaded
    runs = harness.runs()
    assert runs[: len(runs_before)] == runs_before
    assert [
        (run["RUN_KIND"], run["RUN_STATUS"], run["RUN_ID"]) for run in runs[len(runs_before) :]
    ] == [("RESTORE", "DONE", report.run_id)]
    assert harness.steps() == steps_before


def test_restore_refuses_column_mismatch(harness: Harness, snapshot_dir: Path) -> None:
    """Scenario: A restore into a deployment with different columns is refused."""
    populate(harness)
    snapshot(harness, snapshot_dir)
    fetch_newer_data(harness)
    newer = scope_state(harness)

    def add_column(data: dict[str, Any]) -> None:
        games = table_entry(data, "CFB_CORE", "GAMES")
        games["columns"].append(["EXTRA_COLUMN", "DECIMAL(18,0)"])
        games["fingerprint"]["EXTRA_COLUMN"] = [0, None]

    rewrite_manifest(snapshot_dir / NAME, add_column)
    with pytest.raises(SnapshotRefused) as refused:
        restore(harness, snapshot_dir)
    message = str(refused.value)
    assert (
        "CFB_CORE.GAMES: column EXTRA_COLUMN is in the snapshot but not in the database" in message
    )
    assert scope_state(harness) == newer
    assert run_rows(harness, "RESTORE") == []
    assert not landing_exists(harness)


def test_restore_needs_force_for_other_catalog(harness: Harness, snapshot_dir: Path) -> None:
    """Scenario: A snapshot from another catalog needs FORCE."""
    populate(harness)
    before = scope_state(harness)
    snapshot(harness, snapshot_dir)
    fetch_newer_data(harness)
    newer = scope_state(harness)
    rewrite_manifest(snapshot_dir / NAME, lambda data: data.update(catalog_hash=OTHER_CATALOG))

    with pytest.raises(SnapshotRefused) as refused:
        restore(harness, snapshot_dir)
    message = str(refused.value)
    assert OTHER_CATALOG in message and deployed_catalog_hash() in message
    assert "orphans" in message
    assert scope_state(harness) == newer
    assert run_rows(harness, "RESTORE") == []

    lines: list[str] = []
    report = restore(harness, snapshot_dir, force=True, log=lines.append)
    assert_restored(scope_state(harness), before)
    assert any("FORCE=yes" in line for line in lines)
    assert [(run["RUN_STATUS"], run["RUN_ARGS"]) for run in run_rows(harness, "RESTORE")] == [
        ("DONE", f"{NAME} FORCE")
    ]
    assert report.orphans == {}


def test_failed_restore_changes_nothing(harness: Harness, snapshot_dir: Path) -> None:
    """Scenario: A failed restore changes nothing (a changed file; a file that passes its checksum
    but not its fingerprint)."""
    populate(harness)
    snapshot(harness, snapshot_dir)
    fetch_newer_data(harness)
    newer = scope_state(harness)
    directory = snapshot_dir / NAME
    games_file = directory / "CFB_CORE.GAMES.csv.gz"
    original = gzip.decompress(games_file.read_bytes()).decode("utf-8")
    header, first, rest = original.split("\n", 2)
    altered = "\n".join([header, re.sub(r"^\d+", "999", first), rest]).encode("utf-8")
    games_file.write_bytes(gzip.compress(altered))

    with pytest.raises(SnapshotError, match=r"CFB_CORE\.GAMES\.csv\.gz: the checksum differs"):
        restore(harness, snapshot_dir)
    assert scope_state(harness) == newer
    assert run_rows(harness, "RESTORE") == []

    digest = hashlib.sha256(games_file.read_bytes()).hexdigest()
    rewrite_manifest(
        directory, lambda data: table_entry(data, "CFB_CORE", "GAMES").update(sha256=digest)
    )
    with pytest.raises(SnapshotError) as failed:
        restore(harness, snapshot_dir)
    assert "fingerprints" in str(failed.value)
    assert "CFB_CORE.GAMES.csv.gz.ROW_NO" in str(failed.value)
    assert scope_state(harness) == newer
    assert not landing_exists(harness)
    restores = run_rows(harness, "RESTORE")
    assert [run["RUN_STATUS"] for run in restores] == ["FAILED"]
    assert "fingerprints" in restores[0]["DETAIL"]


def test_snapshot_and_restore_refused_during_run(
    harness: Harness, snapshot_dir: Path, mock: Mock
) -> None:
    """Scenario: Snapshot and restore are refused while a loader run is active."""
    populate(harness)
    snapshot(harness, snapshot_dir)
    harness.set_property("FETCH_MODE", "live")
    harness.set_now(LATER)
    mock.script(GAMES_PATH, delay_ms=LATENCY_MS)
    outcome: list[Any] = []

    with harness.owner_session() as session:

        def run_backfill() -> None:
            try:
                rows = session.execute(
                    f"EXECUTE SCRIPT {harness.infra}.BACKFILL('demo', NULL, NULL)"
                ).fetchall()
                outcome.append(rows)
            except BaseException as error:
                outcome.append(error)

        worker = threading.Thread(target=run_backfill)
        worker.start()
        try:
            running = wait_for(lambda: running_batch(harness), "the backfill's batch")
            during = scope_state(harness)
            with pytest.raises(RunLockRefused) as refused_snapshot:
                snapshot(harness, snapshot_dir, "week8")
            with pytest.raises(RunLockRefused) as refused_restore:
                restore(harness, snapshot_dir)
            after = scope_state(harness)
        finally:
            worker.join(WAIT_SECONDS)

    holder = f"BACKFILL run {running['RUN_ID']} is running"
    assert f"SNAPSHOT refused: {holder}" in str(refused_snapshot.value)
    assert f"RESTORE refused: {holder}" in str(refused_restore.value)
    assert not (snapshot_dir / "week8").exists()
    assert after == during
    assert not landing_exists(harness)
    assert run_rows(harness, "RESTORE") == []
    assert [run["RUN_ARGS"] for run in run_rows(harness, "SNAPSHOT")] == [NAME]
    assert not isinstance(outcome[0], BaseException), outcome[0]


def test_backfill_refused_while_snapshot_or_restore_holds_the_lock(
    harness: Harness, snapshot_dir: Path
) -> None:
    """The lock of a snapshot or a restore, a host session idle between its statements, refuses
    a concurrent backfill (hand-off H1b: host rows are stale only when their session is gone)."""
    populate(harness)
    refusals: list[str] = []

    def try_backfill(line: str) -> None:
        if line.startswith("run lock: "):
            with pytest.raises(pyexasol.ExaError) as refused:
                harness.backfill()
            refusals.append(str(refused.value))

    with harness.owner_session() as host:
        snapshot(harness, snapshot_dir, connection=host, log=try_backfill)
        report = restore(harness, snapshot_dir, connection=host, log=try_backfill)

    snapshot_run = run_rows(harness, "SNAPSHOT")[0]["RUN_ID"]
    assert len(refusals) == 2
    assert f"BACKFILL refused: SNAPSHOT run {snapshot_run} is running" in refusals[0]
    assert f"BACKFILL refused: RESTORE run {report.run_id} is running" in refusals[1]
    assert [run["RUN_STATUS"] for run in run_rows(harness, "RESTORE")] == ["DONE"]
    assert len(run_rows(harness, "BACKFILL")) == 1


def test_awkward_values_round_trip(harness: Harness, snapshot_dir: Path) -> None:
    """CSV specials, leading and trailing blanks and full-precision DOUBLEs survive the round trip;
    a DOUBLE with more than 15 significant digits comes back within 4e-12 and the fingerprint,
    which reads its 15-digit text form, still matches."""
    populate(harness)
    games = f"{harness.core}.GAMES"
    harness.query(
        f"UPDATE {games} SET NOTES = {{text}}, EXCITEMENT_INDEX = 0.1 + ID / 7.0E3, "
        "HOME_POSTGAME_WIN_PROBABILITY = 1 / 3, "
        "AWAY_POSTGAME_WIN_PROBABILITY = 123456789.123456789 "
        "WHERE P_YEAR = 2019",
        {"text": AWKWARD_TEXT},
    )
    harness.query(f"UPDATE {games} SET NOTES = '', HOME_LINE_SCORES = NULL WHERE P_YEAR = 2017")
    before = scope_state(harness)
    snapshot(harness, snapshot_dir)
    fetch_newer_data(harness)

    restore(harness, snapshot_dir)

    assert assert_restored(scope_state(harness), before) > 0
    notes = harness.query(f"SELECT DISTINCT NOTES FROM {games} WHERE P_YEAR = 2019")
    assert notes == [(AWKWARD_TEXT,)]
