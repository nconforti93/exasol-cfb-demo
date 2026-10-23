"""Bundler: `loader/lua/*.lua` become the Lua script statements of the loader deployment.

Each script is `CREATE OR REPLACE LUA SCRIPT <infra>.<NAME>(<parameters>) RETURNS TABLE AS`
followed by three parts, in this order: the namespace constants, `loader/lua/loadlib.lua` and the
entry file (`backfill.lua`, `refresh_week.lua`, or `run_lock.lua`, the run lock of host processes
that `loader/runlock.py` calls). The bundler holds no Lua logic.

Namespace constants (a deploy-time rendering, so one source serves the real and every prefixed
namespace). Both tables are keyed by `META_SCHEMA`, `CORE_SCHEMA`, `STAGE_SCHEMA`,
`RESTORE_SCHEMA`, `INFRA_SCHEMA` and `VS_NAME`:

- `QUOTED.<KEY>` is the double-quoted identifier, concatenated into statement text (no Lua
  source uses `::name`; every value is bound as `:name`).
- `PLAIN.<KEY>` is the bare name, for value binds such as `WHERE SCHEMA_NAME = :v`, and messages.

Source checks (`check_source`, applied to the loadlib and entry file of every script):

- no bare `query(`: every statement goes through `pquery`, so a failure can be rolled back and
  logged before it is re-raised;
- no `ALTER VIRTUAL SCHEMA`: virtual schema properties are global, so a loader never changes them;
- no `{UPPER_CASE}` placeholder: names come from `QUOTED` and `PLAIN`, never from templating.

Comments are ignored by all three checks; string contents are checked by the last two.

Output is deterministic: no timestamps, a fixed part order and LF line endings. The script text
of Exasol is capped at 2,000,000 characters, so a script of `SIZE_LIMIT` characters or more fails
with an error that names the size (same limit as `vs/bundle.py`).
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from vs.bundle import (
    HEADER_END,
    SCRIPT_LIMIT,
    SCRIPT_TERMINATOR,
    SIZE_LIMIT,
    BundleError,
    script_body,
)
from vs.host.namespace import Namespace

__all__ = [
    "ALTER_REJECTED",
    "SCRIPTS",
    "SIZE_LIMIT",
    "BundleError",
    "BundleResult",
    "LuaScript",
    "build_script",
    "check_source",
    "script_body",
    "write_bundle",
]

ROOT = Path(__file__).resolve().parents[1]
LUA_DIR = ROOT / "loader" / "lua"
DEFAULT_OUT_DIR = ROOT / "build" / "sql"
LIBRARY_FILE = "loadlib.lua"

_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_PLACEHOLDER = re.compile(r"\{[A-Z][A-Z0-9_]*\}")
_ALTER_VIRTUAL_SCHEMA = re.compile(r"ALTER\s+VIRTUAL\s+SCHEMA", re.IGNORECASE)
ALTER_REJECTED = "ALTER VIRTUAL SCHEMA is not allowed"
_BARE_QUERY = re.compile(r"(?<![\w.:])query\s*(?:[(\"'{]|\[=*\[)")
_LONG_BRACKET = re.compile(r"\[(=*)\[")


@dataclass(frozen=True)
class LuaScript:
    """One loader script: its SQL name, untyped parameter names, entry file and output file."""

    name: str
    parameters: tuple[str, ...]
    entry_file: str
    file_name: str

    def header(self, infra_schema: str) -> str:
        parameters = ", ".join(self.parameters)
        return (
            f"CREATE OR REPLACE LUA SCRIPT {infra_schema}.{self.name}({parameters}) "
            f"RETURNS TABLE{HEADER_END}"
        )


SCRIPTS = (
    LuaScript(
        "BACKFILL",
        ("run_profile", "from_season", "max_calls"),
        "backfill.lua",
        "loader_backfill.sql",
    ),
    LuaScript(
        "REFRESH_WEEK",
        ("season", "week", "season_type"),
        "refresh_week.lua",
        "loader_refresh_week.sql",
    ),
    LuaScript(
        "RUN_LOCK",
        ("lock_action", "lock_word", "lock_run", "lock_text"),
        "run_lock.lua",
        "loader_run_lock.sql",
    ),
)


@dataclass(frozen=True)
class BundleResult:
    """What `write_bundle` did: characters per written script, and scripts without sources yet."""

    sizes: Mapping[str, int] = field(default_factory=dict)
    skipped: tuple[str, ...] = ()


def build_script(script: LuaScript, namespace: Namespace, lua_dir: Path = LUA_DIR) -> str:
    """The complete `CREATE OR REPLACE LUA SCRIPT` statement of `script` for `namespace`."""
    library = _read(lua_dir, LIBRARY_FILE)
    entry = _read(lua_dir, script.entry_file)
    _require_clean(LIBRARY_FILE, library)
    _require_clean(script.entry_file, entry)
    body = "\n".join([render_constants(namespace), library, entry])
    text = script.header(namespace.infra_schema) + body + SCRIPT_TERMINATOR
    if len(text) >= SIZE_LIMIT:
        raise BundleError(
            f"script {script.name} is {len(text):,} characters long; Exasol caps script text at "
            f"{SCRIPT_LIMIT:,}, so the bundle fails from {SIZE_LIMIT:,} characters. "
            "Shrink the Lua sources"
        )
    return text


def render_constants(namespace: Namespace) -> str:
    """The Lua `PLAIN` and `QUOTED` tables of the namespace names."""
    names = _names(namespace)
    plain = "".join(f'  {key} = "{value}",\n' for key, value in names.items())
    quoted = "".join(f'  {key} = "\\"{value}\\"",\n' for key, value in names.items())
    return (
        "-- Generated by loader/bundle.py: the names of this deployment.\n"
        f"local PLAIN = {{\n{plain}}}\n"
        f"local QUOTED = {{\n{quoted}}}\n"
    )


def check_source(name: str, text: str) -> list[str]:
    """The problems of one Lua source, each as `<name>:<line>: <message>`; empty when clean."""
    without_comments, code = _masks(_normalize(text))
    problems: list[tuple[int, str]] = []
    for match in _BARE_QUERY.finditer(code):
        problems.append((_line(code, match.start()), "bare query(); use pquery()"))
    for match in _ALTER_VIRTUAL_SCHEMA.finditer(without_comments):
        problems.append((_line(without_comments, match.start()), ALTER_REJECTED))
    for match in _PLACEHOLDER.finditer(without_comments):
        problems.append(
            (
                _line(without_comments, match.start()),
                f"unresolved placeholder {match.group()}; use QUOTED or PLAIN",
            )
        )
    return [f"{name}:{line}: {message}" for line, message in sorted(problems)]


def available_scripts(lua_dir: Path = LUA_DIR) -> tuple[LuaScript, ...]:
    """The scripts whose entry file and library both exist (the others are written later)."""
    if not (lua_dir / LIBRARY_FILE).exists():
        return ()
    return tuple(s for s in SCRIPTS if (lua_dir / s.entry_file).exists())


def write_bundle(
    out_dir: Path = DEFAULT_OUT_DIR,
    namespace: Namespace | None = None,
    lua_dir: Path = LUA_DIR,
) -> BundleResult:
    """Write one `loader_<script>.sql` per script of `SCRIPTS` for `namespace` (default real).

    A script whose sources do not exist yet is skipped and named in the result.
    """
    chosen = namespace or Namespace()
    available = available_scripts(lua_dir)
    sizes: dict[str, int] = {}
    if available:
        out_dir.mkdir(parents=True, exist_ok=True)
    for script in available:
        text = build_script(script, chosen, lua_dir)
        (out_dir / script.file_name).write_bytes(text.encode("utf-8"))
        sizes[script.name] = len(text)
    skipped = tuple(s.name for s in SCRIPTS if s not in available)
    return BundleResult(sizes=sizes, skipped=skipped)


def main() -> int:
    try:
        result = write_bundle()
    except (BundleError, OSError) as error:
        print(f"loader bundle failed: {error}", file=sys.stderr)
        return 2
    for script in SCRIPTS:
        if script.name in result.sizes:
            print(
                f"wrote {DEFAULT_OUT_DIR / script.file_name} "
                f"({result.sizes[script.name]:,} characters)"
            )
        else:
            print(f"skipped {script.name}: {LUA_DIR / script.entry_file} does not exist yet")
    return 0


def _names(namespace: Namespace) -> dict[str, str]:
    names = {
        "META_SCHEMA": namespace.meta_schema,
        "CORE_SCHEMA": namespace.core_schema,
        "STAGE_SCHEMA": namespace.stage_schema,
        "RESTORE_SCHEMA": namespace.restore_schema,
        "INFRA_SCHEMA": namespace.infra_schema,
        "VS_NAME": namespace.vs_name,
    }
    for key, value in names.items():
        if not _NAME.fullmatch(value):
            raise BundleError(f"{key} {value!r} must be an UPPER_CASE identifier")
    return names


def _read(lua_dir: Path, file_name: str) -> str:
    try:
        return _normalize((lua_dir / file_name).read_text(encoding="utf-8"))
    except OSError as error:
        raise BundleError(f"cannot read {file_name}: {error}") from None


def _normalize(text: str) -> str:
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    return unified if unified.endswith("\n") else unified + "\n"


def _require_clean(name: str, text: str) -> None:
    problems = check_source(name, text)
    if problems:
        raise BundleError("Lua source check failed:\n  " + "\n  ".join(problems))


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _masks(source: str) -> tuple[str, str]:
    """Two same-length copies of `source`: comments blanked, and comments plus strings blanked.

    Newlines and string delimiters stay, so offsets and line numbers still match the source.
    """
    without_comments: list[str] = []
    code: list[str] = []
    position = 0
    length = len(source)

    def emit(text: str, *, comment: bool = False, string: bool = False) -> None:
        blank = "".join(ch if ch == "\n" else " " for ch in text)
        without_comments.append(blank if comment else text)
        code.append(blank if comment or string else text)

    while position < length:
        if source.startswith("--", position):
            end = _comment_end(source, position)
            emit(source[position:end], comment=True)
        elif source[position] in "\"'":
            end = _quoted_end(source, position)
            emit(source[position], string=False)
            emit(source[position + 1 : end - 1], string=True)
            emit(source[end - 1 : end], string=False)
        elif _LONG_BRACKET.match(source, position):
            end = _long_end(source, position)
            opener = _LONG_BRACKET.match(source, position)
            assert opener is not None
            width = len(opener.group())
            emit(source[position : position + width])
            emit(source[position + width : end - width], string=True)
            emit(source[end - width : end])
        else:
            emit(source[position])
            end = position + 1
        position = end
    return "".join(without_comments), "".join(code)


def _comment_end(source: str, start: int) -> int:
    opener = _LONG_BRACKET.match(source, start + 2)
    if opener is not None:
        return _long_end(source, start + 2)
    newline = source.find("\n", start)
    return len(source) if newline < 0 else newline


def _long_end(source: str, start: int) -> int:
    opener = _LONG_BRACKET.match(source, start)
    assert opener is not None
    closer = "]" + opener.group(1) + "]"
    found = source.find(closer, start + len(opener.group()))
    return len(source) if found < 0 else found + len(closer)


def _quoted_end(source: str, start: int) -> int:
    quote = source[start]
    position = start + 1
    while position < len(source):
        char = source[position]
        if char == "\\":
            position += 2
        elif char == quote:
            return position + 1
        elif char == "\n":
            return position
        else:
            position += 1
    return len(source)


if __name__ == "__main__":
    sys.exit(main())
