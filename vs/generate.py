"""Catalog generator CLI: pinned spec + reviewed overrides + reserved keywords → catalog + mirrors.

`python -m vs.generate` (or `make generate`) reads the three inputs, runs the prose lint and the
table derivation, and writes `vs/catalog/catalog.json` and `sql/core/cfb_core_mirrors.sql`. Every
error of the reachable stages is listed on stderr and the exit status is 1; nothing is written
unless the whole run succeeded, and the two outputs are replaced together (temp files first, then
rename, with a rollback when the second rename fails).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vs.cfbd_vs.catalog import Catalog
from vs.gen.mirrors import MirrorError, render_mirrors
from vs.gen.overrides import Overrides, OverridesError, SpecIndex, parse_overrides
from vs.gen.prose_lint import find_uncovered
from vs.gen.schema_walk import SchemaWalkError, read_operations
from vs.gen.tables import TablesError, derive_tables

GENERATOR_VERSION = "1"

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = REPO_ROOT / "vs" / "openapi" / "cfbd-openapi-5.33.0.json"
DEFAULT_OVERRIDES = REPO_ROOT / "vs" / "catalog-overrides.yaml"
DEFAULT_KEYWORDS = REPO_ROOT / "vs" / "gen" / "exasol_reserved_keywords.txt"
DEFAULT_CATALOG_OUT = REPO_ROOT / "vs" / "catalog" / "catalog.json"
DEFAULT_MIRRORS_OUT = REPO_ROOT / "sql" / "core" / "cfb_core_mirrors.sql"


class GenerateError(ValueError):
    """Generation failed; `errors` lists every problem found."""

    def __init__(self, errors: Sequence[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = tuple(errors)


@dataclass(frozen=True)
class Generated:
    """The rendered outputs of a successful run."""

    catalog: Catalog
    catalog_text: str
    mirrors_text: str


def generate(spec_path: Path, overrides_path: Path, keywords_path: Path) -> Generated:
    """Run every stage and return the outputs; raise `GenerateError` listing all errors."""
    spec_bytes = _read_bytes(spec_path, "spec")
    overrides_bytes = _read_bytes(overrides_path, "overrides file")
    reserved = _read_keywords(keywords_path)
    spec = _parse_spec(spec_bytes, spec_path)
    overrides = _load_overrides(overrides_bytes, spec)
    errors = _lint_prose(spec, overrides)
    operations = _walk(spec)
    tables = _derive(operations, overrides, reserved)
    problems = [*errors, *operations.errors, *tables.errors]
    if problems or operations.value is None or tables.value is None:
        raise GenerateError(problems)
    version = str(spec.get("info", {}).get("version", ""))
    catalog = Catalog(
        catalog_hash="",
        spec_version=version,
        generator_version=GENERATOR_VERSION,
        excluded=overrides.excluded,
        tables=tables.value,
    ).with_content_hash()
    try:
        mirrors_text = render_mirrors(catalog.tables, catalog.catalog_hash)
    except MirrorError as error:
        raise GenerateError([str(error)]) from None
    return Generated(catalog, catalog.to_json(), mirrors_text)


def write_outputs(files: Sequence[tuple[Path, str]]) -> None:
    """Replace all files together: stage temp files, rename them, roll back on failure."""
    staged: list[tuple[Path, Path]] = []
    try:
        for target, text in files:
            target.parent.mkdir(parents=True, exist_ok=True)
            staged.append((target, _stage(target, text)))
        _replace_all(staged)
    finally:
        for _, temp in staged:
            temp.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns the process exit status."""
    args = _parser().parse_args(argv)
    try:
        result = generate(args.spec, args.overrides, args.keywords)
    except GenerateError as error:
        print(f"generation failed with {len(error.errors)} error(s):", file=sys.stderr)
        for line in error.errors:
            print(f"  - {line}", file=sys.stderr)
        return 1
    try:
        write_outputs(
            [(args.catalog_out, result.catalog_text), (args.mirrors_out, result.mirrors_text)]
        )
    except OSError as error:
        print(f"could not write the outputs, nothing was changed: {error}", file=sys.stderr)
        return 1
    _report(result.catalog, args)
    return 0


@dataclass(frozen=True)
class _Stage:
    """A stage result: the value when it could run, and the errors it found."""

    value: Any
    errors: tuple[str, ...] = ()


def _read_bytes(path: Path, what: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise GenerateError([f"cannot read the {what} {path}: {error.strerror or error}"]) from None


def _read_keywords(path: Path) -> frozenset[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        reason = error.strerror or str(error)
        raise GenerateError(
            [f"cannot read the reserved keywords file {path}: {reason} (task 1.4p writes it)"]
        ) from None
    words = [line.strip() for line in text.splitlines() if line.strip()]
    lowercase = [w for w in words if w != w.upper()]
    if lowercase:
        raise GenerateError([f"{path}: keywords must be uppercase, found {lowercase[:5]}"])
    if not words:
        raise GenerateError([f"{path}: the reserved keywords file is empty"])
    return frozenset(words)


def _parse_spec(data: bytes, path: Path) -> Mapping[str, Any]:
    try:
        spec = json.loads(data)
    except json.JSONDecodeError as error:
        raise GenerateError([f"the spec {path} is not valid JSON: {error}"]) from None
    if not isinstance(spec, dict):
        raise GenerateError([f"the spec {path} must be a JSON object"])
    return spec


def _load_overrides(data: bytes, spec: Mapping[str, Any]) -> Overrides:
    try:
        return parse_overrides(data.decode("utf-8"), SpecIndex.from_openapi(spec))
    except OverridesError as error:
        raise GenerateError([f"overrides: {e}" for e in error.errors]) from None
    except (KeyError, TypeError, AttributeError) as error:
        raise GenerateError([f"the spec cannot be indexed for the overrides: {error!r}"]) from None


def _lint_prose(spec: Mapping[str, Any], overrides: Overrides) -> list[str]:
    uncovered = find_uncovered(spec, overrides)
    return [f"prose rule not covered: {f.describe()}" for f in uncovered]


def _walk(spec: Mapping[str, Any]) -> _Stage:
    try:
        return _Stage(read_operations(spec))
    except SchemaWalkError as error:
        return _Stage(None, (f"spec: {error}",))


def _derive(operations: _Stage, overrides: Overrides, reserved: frozenset[str]) -> _Stage:
    if operations.value is None:
        return _Stage(None)
    try:
        return _Stage(derive_tables(operations.value, overrides, reserved))
    except TablesError as error:
        return _Stage(None, tuple(f"tables: {e}" for e in error.errors))


def _stage(target: Path, text: str) -> Path:
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return Path(name)


def _replace_all(staged: Sequence[tuple[Path, Path]]) -> None:
    previous = [(target, target.read_bytes() if target.is_file() else None) for target, _ in staged]
    done: list[Path] = []
    try:
        for target, temp in staged:
            os.replace(temp, target)
            done.append(target)
    except OSError:
        for target, old in previous:
            if target in done:
                _restore(target, old)
        raise


def _restore(target: Path, old: bytes | None) -> None:
    if old is None:
        target.unlink(missing_ok=True)
    else:
        target.write_bytes(old)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m vs.generate", description=__doc__)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--keywords", type=Path, default=DEFAULT_KEYWORDS)
    parser.add_argument("--catalog-out", type=Path, default=DEFAULT_CATALOG_OUT)
    parser.add_argument("--mirrors-out", type=Path, default=DEFAULT_MIRRORS_OUT)
    return parser


def _report(catalog: Catalog, args: argparse.Namespace) -> None:
    endpoints = sum(1 for t in catalog.tables if t.kind == "endpoint")
    children = len(catalog.tables) - endpoints
    print(
        f"catalog {catalog.catalog_hash[:12]}: {endpoints} endpoint tables, {children} child "
        f"tables, {len(catalog.excluded)} excluded"
    )
    print(f"wrote {args.catalog_out}")
    print(f"wrote {args.mirrors_out}")


if __name__ == "__main__":
    sys.exit(main())
