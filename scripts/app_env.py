"""`make app-env`: create or complete `~/.cfb-demo/app.env`, the app's secrets file.

`init` writes `CFB_APP_PASSWORD` and `CFB_CONSOLE_PASSWORD` as random values (mode 0600, directory
0700) and a commented placeholder for `ANTHROPIC_API_KEY`. Keys already in the file are never
changed. It creates no database user (that is `make app-user`) and prints key names and the path,
never a value. The path is `CFB_APP_ENV` or `~/.cfb-demo/app.env`, refused inside the repository or
under OneDrive.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from vs.host import appenv, target

GENERATED_KEYS = ("CFB_APP_PASSWORD", "CFB_CONSOLE_PASSWORD")
PLACEHOLDER_KEY = "ANTHROPIC_API_KEY"
PASSWORD_BYTES = 24
HEADER = (
    "# App secrets, outside the repository and outside OneDrive. Mode 0600. Never share.\n"
    "# Lookup order: the process environment, then this file.\n"
)
PLACEHOLDER = f"# {PLACEHOLDER_KEY}=   (Phase 7 chat; set it here when you have one)\n"


def initialize(path: Path, repo_root: Path = appenv.ROOT) -> tuple[list[str], list[str]]:
    """Add the missing generated keys; returns (created keys, kept keys). No value is returned."""
    appenv.check_location(path, repo_root)
    existing = _read_existing(path)
    created = [key for key in GENERATED_KEYS if key not in existing]
    kept = [key for key in GENERATED_KEYS if key in existing]
    text = _new_text(path, created)
    if text:
        _append(path, text)
    return created, kept


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.app_env", description=__doc__)
    parser.add_argument("command", choices=("init",))
    parser.parse_args(argv)
    path = appenv.secrets_path(os.environ if environ is None else environ)
    try:
        created, kept = initialize(path)
    except appenv.SecretsFileError as error:
        print(f"app-env: {error}", file=sys.stderr)
        return 1
    print(f"secrets file: {path} (mode {appenv.SECRETS_MODE:04o})")
    print(f"created: {', '.join(created) or 'nothing'}")
    print(f"kept (unchanged): {', '.join(kept) or 'nothing'}")
    print(f"next: set {PLACEHOLDER_KEY} there if you need it; `make app-user` creates the users.")
    return 0


def _read_existing(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    appenv.check_mode(path)
    return target.parse_dotenv(path)


def _new_text(path: Path, created: list[str]) -> str:
    lines = [] if path.exists() else [HEADER]
    lines += [f"{key}={secrets.token_urlsafe(PASSWORD_BYTES)}\n" for key in created]
    if not _mentions(path, PLACEHOLDER_KEY):
        lines.append(PLACEHOLDER)
    return "".join(lines)


def _mentions(path: Path, key: str) -> bool:
    return path.exists() and key in path.read_text(encoding="utf-8")


def _append(path: Path, text: str) -> None:
    path.parent.mkdir(mode=appenv.SECRETS_DIRECTORY_MODE, parents=True, exist_ok=True)
    if not path.exists():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, appenv.SECRETS_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        return
    previous = path.read_text(encoding="utf-8")
    separator = "" if previous.endswith("\n") or not previous else "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(separator + text)


if __name__ == "__main__":
    raise SystemExit(main())
