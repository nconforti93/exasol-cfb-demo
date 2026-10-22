"""`make snapshot`, `make restore`, `make snapshots`: private copies of the stored data.

    python -m scripts.snapshot [--target local|aws] [--dsn HOST:PORT] [--prefix PREFIX_]
                               snapshot --name NAME
    python -m scripts.snapshot [...] restore --name NAME [--confirm yes] [--force yes]
    python -m scripts.snapshot list

    make snapshot NAME=n [TARGET=local|aws]
    make restore NAME=n CONFIRM=yes [FORCE=yes] [TARGET=local|aws]
    make snapshots

Snapshots live in `~/.cfb-demo/snapshots/<name>/` or `$CFB_SNAPSHOT_DIR/<name>/`, never inside the
repository or a OneDrive folder (`loader/snapshot.py` has the layout, the manifest and the checks).
Both commands connect as `sys` to the target (`vs/host/target.py`), take the loader's run lock,
and make zero CFBD calls: they never read the virtual schema.

- `snapshot` copies every `CFB_CORE` table plus `CFB_META.LOAD_LOG` and `FETCH_LOG` (the plan's
  `snapshot` section). An existing name is refused before anything is written.
- `restore` without `CONFIRM=yes` checks the snapshot against the target (columns, catalog,
  files), prints what it would replace and refuses. With `CONFIRM=yes` it replaces those tables
  in one transaction. A snapshot of another catalog needs `FORCE=yes`.
- `list` (`make snapshots`) prints every snapshot with its time, source, rows and size.

Exit codes: 0 ok, 1 refused (nothing was changed), 2 error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from loader import snapshot as snap
from loader.plan import PlanError, SnapshotScope, load_plan
from loader.runlock import RunLockError, RunLockRefused
from vs.host import target
from vs.host.namespace import Namespace, NamespaceError

CONFIRM_WORD = "yes"
SOCKET_TIMEOUT_SECONDS = 900
BYTES_PER_MEGABYTE = 1024 * 1024

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_ERROR = 2


class Refused(Exception):
    """A rule stopped the command before it changed anything."""


Connector = Callable[[target.Target], Any]


def connect_deployment(deployment: target.Target) -> Any:
    """A sys connection that waits `SOCKET_TIMEOUT_SECONDS` for each statement (long exports)."""
    return deployment.connect(socket_timeout=SOCKET_TIMEOUT_SECONDS)


def plan_scope() -> SnapshotScope:
    """The `snapshot` section of `loader/load-plan.yaml`."""
    return load_plan().snapshot


def _print(line: str) -> None:
    print(line, flush=True)


def _list(root: Path) -> int:
    listings = snap.list_snapshots(root)
    if not listings:
        _print(f"no snapshots in {root}")
        return EXIT_OK
    _print(f"snapshots in {root}:")
    width = max(len(item.name) for item in listings)
    for item in listings:
        size = f"{item.size_bytes / BYTES_PER_MEGABYTE:.1f} MB"
        if item.problem is not None:
            _print(f"  {item.name.ljust(width)}  incomplete or unreadable ({item.problem}), {size}")
            continue
        _print(
            f"  {item.name.ljust(width)}  {item.created_at}  from {item.source}  "
            f"{item.tables} tables, {item.rows} rows  {size}  catalog {item.catalog_hash[:12]}"
        )
    return EXIT_OK


def _summary(manifest: snap.Manifest) -> str:
    source = " ".join(
        manifest.source[key] for key in ("target", "host") if manifest.source.get(key)
    )
    tables = sum(1 for table in manifest.tables if table.rows > 0)
    rows = sum(table.rows for table in manifest.tables)
    return (
        f"snapshot {manifest.name}: created {manifest.created_at} from {source}, "
        f"{tables} tables with rows, {rows} rows, catalog {manifest.catalog_hash[:12]}"
    )


class _Command:
    def __init__(
        self,
        args: argparse.Namespace,
        root: Path,
        connector: Connector,
        resolve: Callable[..., target.Target],
        scope: Callable[[], SnapshotScope],
    ) -> None:
        self._args = args
        self._root = root
        self._connector = connector
        self._resolve = resolve
        self._scope = scope
        self._namespace = Namespace(args.prefix)

    def snapshot(self) -> int:
        name = snap.check_name(self._args.name)
        if (self._root / name).exists():
            raise snap.SnapshotRefused(
                f"snapshot {name} already exists at {self._root / name}; a snapshot is never "
                "overwritten, choose another NAME"
            )
        scope = self._scope()
        deployment = self._resolve(self._args.target, dsn=self._args.dsn)
        source = {
            "target": deployment.name,
            "host": deployment.host,
            "namespace": self._namespace.prefix,
        }
        connection = self._connector(deployment)
        try:
            manifest = snap.take_snapshot(
                connection,
                self._namespace,
                self._root,
                name,
                source=source,
                scope=scope,
                log=_print,
            )
        finally:
            connection.close()
        files = sum(1 for table in manifest.tables if table.file is not None)
        rows = sum(table.rows for table in manifest.tables)
        _print(
            f"snapshot {name}: {files} files, {rows} rows in {self._root / name}; "
            f"catalog {manifest.catalog_hash[:12]}; 0 CFBD calls"
        )
        return EXIT_OK

    def restore(self) -> int:
        name = snap.check_name(self._args.name)
        directory = self._root / name
        manifest = snap.read_manifest(directory)
        _print(_summary(manifest))
        force = self._args.force == CONFIRM_WORD
        deployment = self._resolve(self._args.target, dsn=self._args.dsn)
        connection = self._connector(deployment)
        try:
            if self._args.confirm != CONFIRM_WORD:
                return self._preview(connection, manifest, directory, deployment, force)
            report = snap.restore_snapshot(
                connection, self._namespace, self._root, name, force=force, log=_print
            )
        finally:
            connection.close()
        _print(f"restore {name}: done as RESTORE run {report.run_id}; 0 CFBD calls")
        for table, count in report.orphans.items():
            _print(f"warning: orphan slices of {table}: {count} (planned keys no longer match)")
        if report.orphans:
            _print(
                "warning: orphan slices block fetching their table; see "
                f"{self._namespace.meta_schema}.LOAD_ORPHANS (migrate or delete them)"
            )
        return EXIT_OK

    def _preview(
        self,
        connection: Any,
        manifest: snap.Manifest,
        directory: Path,
        deployment: target.Target,
        force: bool,
    ) -> int:
        for line in snap.check_restore(connection, self._namespace, manifest, force=force):
            _print(line)
        snap.verify_files(directory, manifest)
        _print("files ok")
        raise Refused(
            f"nothing was changed; rerun with CONFIRM={CONFIRM_WORD} to replace every CFB_CORE "
            f"table, LOAD_LOG and FETCH_LOG of {deployment.describe()} with snapshot "
            f"{manifest.name}"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.snapshot",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--target", default=None, help="local or aws (default: $TARGET, else local)"
    )
    parser.add_argument("--dsn", default=None, help="overrides host and port of the target")
    parser.add_argument("--prefix", default="", help="namespace prefix, for example PROBE_")
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot", help="write a new snapshot")
    snapshot.add_argument("--name", required=True)
    restore = commands.add_parser("restore", help="replace the stored data with a snapshot")
    restore.add_argument("--name", required=True)
    restore.add_argument("--confirm", default="", help=f"must be '{CONFIRM_WORD}' to restore")
    restore.add_argument(
        "--force", default="", help=f"'{CONFIRM_WORD}' restores a snapshot of another catalog"
    )
    commands.add_parser("list", help="list the snapshots")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    connector: Connector = connect_deployment,
    resolve: Callable[..., target.Target] = target.resolve,
    scope: Callable[[], SnapshotScope] = plan_scope,
) -> int:
    args = _parser().parse_args(argv)
    try:
        root = snap.snapshot_root()
        if args.command == "list":
            return _list(root)
        command = _Command(args, root, connector, resolve, scope)
        return command.snapshot() if args.command == "snapshot" else command.restore()
    except (Refused, snap.SnapshotRefused, RunLockRefused) as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except (
        snap.SnapshotError,
        RunLockError,
        target.TargetError,
        NamespaceError,
        PlanError,
        OSError,
    ) as error:
        print(f"{args.command} failed: {error}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as error:
        message = str(getattr(error, "message", error)).strip().splitlines()
        print(
            f"{args.command} failed: {message[0] if message else type(error).__name__}",
            file=sys.stderr,
        )
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
