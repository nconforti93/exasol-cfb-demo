"""The loader's Python sources never issue `ALTER VIRTUAL SCHEMA` ("Loader leaves the VS alone").

Virtual schema properties are global, so a loader run, a snapshot or a restore must never change
them. `loader.bundle.check_source` guards the Lua; this module guards the Python of the loader
(`loader/*.py`) and its command-line wrappers (`scripts/loader_cli.py`, `scripts/snapshot.py`).
It scans every string constant, also the literal parts of f-strings, and skips docstrings. The
bundler's own error message names the statement and is the one allowed occurrence.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

from loader import bundle

ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    *sorted((ROOT / "loader").glob("*.py")),
    ROOT / "scripts" / "loader_cli.py",
    ROOT / "scripts" / "snapshot.py",
)
STATEMENT = re.compile(r"ALTER\s+VIRTUAL\s+SCHEMA", re.IGNORECASE)
_DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _docstrings(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, _DOCUMENTED) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def string_constants(source: str) -> Iterator[str]:
    """Every string constant of `source` except docstrings, f-string parts included."""
    tree = ast.parse(source)
    skipped = _docstrings(tree)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skipped
        ):
            yield node.value


def alter_statements(source: str) -> list[str]:
    return [
        text
        for text in string_constants(source)
        if STATEMENT.search(text) and text != bundle.ALTER_REJECTED
    ]


def test_the_scan_finds_an_alter_statement_also_in_an_f_string() -> None:
    source = (
        '"""Module doc: ALTER VIRTUAL SCHEMA is mentioned here only."""\n'
        "def f(vs):\n"
        '    """ALTER VIRTUAL SCHEMA in a docstring is fine."""\n'
        "    # ALTER VIRTUAL SCHEMA in a comment is fine\n"
        "    return f\"alter  virtual schema {vs} SET FETCH_MODE = 'live'\"\n"
    )
    assert alter_statements(source) == ["alter  virtual schema "]


def test_the_bundler_message_is_the_only_allowed_occurrence() -> None:
    assert alter_statements(f"MESSAGE = {bundle.ALTER_REJECTED!r}\n") == []
    assert alter_statements("SQL = 'ALTER VIRTUAL SCHEMA X REFRESH'\n") == [
        "ALTER VIRTUAL SCHEMA X REFRESH"
    ]


def test_no_python_loader_source_alters_the_virtual_schema() -> None:
    assert len(SOURCES) > 5
    found = {
        str(path.relative_to(ROOT)): statements
        for path in SOURCES
        if (statements := alter_statements(path.read_text(encoding="utf-8")))
    }
    assert found == {}
