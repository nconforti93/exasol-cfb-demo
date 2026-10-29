"""Release check, fast part: the demo freeze (`make lint`, `make demo-freeze`).

While `demo/freeze.yaml` is in force, the stored-data definitions that the rehearsal kits' snapshots
depend on must not change. Each frozen path is pinned by a content digest of the working tree: a
SHA-256 over the sorted (path, git blob id) pairs of the files git would track there, so an
uncommitted change counts too (decision [44]). The check reads only the repository: no home file,
no deployment file, no history and no database.

    python -m scripts.release_check --fast [--now YYYY-MM-DD]
    python -m scripts.release_check freeze --from YYYY-MM-DD --until YYYY-MM-DD [--force]

Exit codes: 0 pass (warnings allowed), 2 a FAIL, 1 a usage or setup error.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

FROZEN_PATHS = (
    "vs/catalog",
    "sql/core",
    "sql/meta/load_log.sql",
    "sql/meta/fetch_log.sql",
    "loader/load-plan.yaml",
)
FREEZE_FILE = "demo/freeze.yaml"
SHORT = 12
WAY_OUT = (
    "revert the change, or have the owner approve a migration that re-fingerprints the "
    "existing snapshots and then re-run `make demo-freeze`"
)


class CheckError(RuntimeError):
    """The check cannot run (not a git repository, an unreadable freeze file)."""


@dataclass(frozen=True)
class Freeze:
    start: date
    until: date
    pins: dict[str, str]


@dataclass(frozen=True)
class Finding:
    level: str  # FAIL, WARN or OK
    rule: str
    path: str
    message: str

    def line(self) -> str:
        return f"{self.level} {self.rule} {self.path}: {self.message}"


def _git(root: Path, *args: str, stdin: str | None = None) -> str:
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=root,
            input=stdin,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise CheckError(f"git {args[0]} failed in {root}: {error}") from None
    return done.stdout


def digest(root: Path, path: str) -> str:
    """The content digest of `path` in the working tree; tracked and untracked, not ignored."""
    listed = _git(root, "ls-files", "-co", "--exclude-standard", "-z", "--", path)
    files = sorted({name for name in listed.split("\0") if name and (root / name).is_file()})
    blobs = _git(root, "hash-object", "--stdin-paths", stdin="\n".join(files) + "\n").split()
    if files and len(blobs) != len(files):
        raise CheckError(f"git hash-object returned {len(blobs)} ids for {len(files)} files")
    hasher = hashlib.sha256()
    for name, blob in zip(files, blobs if files else [], strict=True):
        hasher.update(f"{name}\0{blob}\n".encode())
    return hasher.hexdigest()


def write_freeze(root: Path, start: date, until: date, *, force: bool = False) -> Path:
    """Pin every frozen path's current digest in `demo/freeze.yaml`."""
    if until < start:
        raise CheckError("--until must not be before --from")
    target = root / FREEZE_FILE
    if target.exists() and not force:
        raise CheckError(f"{FREEZE_FILE} exists; pass --force (FORCE=yes) to re-pin it")
    body = {
        "from": start.isoformat(),
        "until": until.isoformat(),
        "paths": {path: digest(root, path) for path in FROZEN_PATHS},
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Demo freeze: written by `make demo-freeze`; checked by `make lint`.\n"
        "# Content digests of the working tree (scripts/release_check.py). Do not edit by hand.\n"
    )
    target.write_text(header + yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return target


def read_freeze(root: Path) -> Freeze | None:
    target = root / FREEZE_FILE
    if not target.exists():
        return None
    try:
        body = yaml.safe_load(target.read_text(encoding="utf-8"))
        pins = body["paths"]
        if not isinstance(pins, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in pins.items()
        ):
            raise TypeError("paths must map strings to digests")
        return Freeze(_date(body["from"]), _date(body["until"]), dict(pins))
    except (yaml.YAMLError, KeyError, TypeError, ValueError) as error:
        raise CheckError(f"{FREEZE_FILE} is unreadable: {error}") from None


def _date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def check_freeze(root: Path, today: date) -> list[Finding]:
    freeze = read_freeze(root)
    if freeze is None:
        return [Finding("OK", "freeze", FREEZE_FILE, "no freeze file")]
    if today < freeze.start:
        return [Finding("OK", "freeze", FREEZE_FILE, f"not in force until {freeze.start}")]
    if today > freeze.until:
        return [
            Finding("WARN", "freeze", FREEZE_FILE, f"ended {freeze.until}; remove {FREEZE_FILE}")
        ]
    findings = []
    for path in FROZEN_PATHS:
        pinned = freeze.pins.get(path)
        if pinned is None:
            findings.append(Finding("FAIL", "freeze", path, "not pinned in the freeze file"))
            continue
        current = digest(root, path)
        if current != pinned:
            findings.append(
                Finding(
                    "FAIL",
                    "freeze",
                    path,
                    f"changed during the freeze {freeze.start}..{freeze.until} "
                    f"(pinned {pinned[:SHORT]}, now {current[:SHORT]}); kits whose snapshots "
                    f"were taken since {freeze.start} are at risk; {WAY_OUT}",
                )
            )
    if not findings:
        findings.append(
            Finding("OK", "freeze", FREEZE_FILE, f"{len(FROZEN_PATHS)} paths unchanged")
        )
    return findings


def _root() -> Path:
    return Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").strip())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release_check", description=__doc__.splitlines()[0])
    parser.add_argument("--fast", action="store_true", help="the lint subset (the freeze rule)")
    parser.add_argument("--now", type=date.fromisoformat, help="today's date for the check")
    parser.add_argument("--root", type=Path, help="repository root (default: git top level)")
    sub = parser.add_subparsers(dest="command")
    freeze = sub.add_parser("freeze", help="write demo/freeze.yaml")
    freeze.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    freeze.add_argument("--until", type=date.fromisoformat, required=True)
    freeze.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = args.root or _root()
        if args.command == "freeze":
            written = write_freeze(root, args.start, args.until, force=args.force)
            print(f"wrote {written.relative_to(root)}: {len(FROZEN_PATHS)} paths pinned")
            return 0
        if not args.fast:
            parser.error("only --fast is implemented; the full check is deferred (decision [43])")
        findings = check_freeze(root, args.now or datetime.now(UTC).date())
    except CheckError as error:
        print(f"release_check: {error}", file=sys.stderr)
        return 1
    for finding in findings:
        print(finding.line())
    return 2 if any(finding.level == "FAIL" for finding in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
