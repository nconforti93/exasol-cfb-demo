"""Bundler: `vs/cfbd_vs` plus the catalog become the two script bodies of the deployment.

The adapter script and the fetch UDF are each one Python source text that carries the modules
they need, as strings, and the compact catalog JSON. A `sys.modules` shim registers the package
`vs.cfbd_vs` and each module under its absolute name, in dependency order, so the runtime
package's own `from vs.cfbd_vs.x import y` lines resolve unchanged. Nothing is ever registered
under a bare name: `vs/cfbd_vs/http.py` must not shadow the standard library's `http`.

Output is deterministic: no timestamps, a fixed module order and `repr` literals. The script
text of Exasol is capped at 2,000,000 characters (probe 1.4n), so building a script of
`SIZE_LIMIT` characters or more fails with an error that names the size.

- Adapter: `adapter_call` is `vs.cfbd_vs.adapter.adapter_call`; `CATALOG_JSON` is set first.
- Fetch UDF: `run(ctx)` calls `fetch.run(ctx, exa)`, because `exa` is a global of the script.

`scripts/dns.py` embeds `resolver.py` on its own and is not part of this bundle.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

from vs.cfbd_vs.catalog import Catalog, CatalogError

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "vs" / "cfbd_vs"
DEFAULT_CATALOG_PATH = ROOT / "vs" / "catalog" / "catalog.json"
DEFAULT_OUT_DIR = ROOT / "build" / "sql"

PACKAGE = "vs.cfbd_vs"
ADAPTER_MODULE = f"{PACKAGE}.adapter"
FETCH_MODULE = f"{PACKAGE}.fetch"
ADAPTER_FILE = "cfbd_adapter.sql"
FETCH_FILE = "cfbd_fetch.sql"

SCRIPT_LIMIT = 2_000_000
SIZE_LIMIT = 1_990_000
SCRIPT_TERMINATOR = "\n/"
HEADER_END = " AS\n"

ADAPTER_SCRIPT_NAME = "CFBD_ADAPTER"
FETCH_SCRIPT_NAME = "CFBD_FETCH"
FETCH_PARAMETERS = (
    "TABLE_NAME VARCHAR(128), SLICE_KEY VARCHAR(2000), CATALOG_HASH VARCHAR(64), "
    "FETCH_MODE VARCHAR(16), CONNECTION_NAME VARCHAR(128), REPLAY_PATH VARCHAR(1024), "
    "DOH_URL VARCHAR(1024), MAX_PARALLEL_FETCHES DECIMAL(2,0)"
)

_SHIM = """\
import sys
import types

_PACKAGE = "vs.cfbd_vs"
for _name in ("vs", _PACKAGE):
    _package = types.ModuleType(_name)
    _package.__path__ = []
    sys.modules[_name] = _package
sys.modules["vs"].cfbd_vs = sys.modules[_PACKAGE]

_MODULES = (
{modules}
)
for _name, _source in _MODULES:
    _module = types.ModuleType(_name)
    _module.__file__ = "<bundle " + _name + ">"
    sys.modules[_name] = _module
    setattr(sys.modules[_PACKAGE], _name.rpartition(".")[2], _module)
    exec(compile(_source, _module.__file__, "exec"), _module.__dict__)
"""

_ADAPTER_ENTRY = """\

from vs.cfbd_vs import adapter

adapter.CATALOG_JSON = {catalog}
adapter_call = adapter.adapter_call
"""

_FETCH_ENTRY = """\

from vs.cfbd_vs import fetch

fetch.CATALOG_JSON = {catalog}


def run(ctx):
    fetch.run(ctx, exa)
"""


class BundleError(RuntimeError):
    """The bundle cannot be built, or a script would not fit Exasol's script text limit."""


@dataclass(frozen=True)
class Bundle:
    """The two script bodies, without the `CREATE SCRIPT` header and the closing `/`."""

    adapter_body: str
    fetch_body: str


@dataclass(frozen=True)
class BundleSizes:
    """Character counts of the two complete script statements as written to disk."""

    adapter_chars: int
    fetch_chars: int


def module_order(entry: str) -> list[str]:
    """The modules `entry` needs, dependencies first, in a deterministic order."""
    ordered: list[str] = []
    _visit(entry, ordered, [])
    return ordered


def build(catalog_text: str) -> Bundle:
    """Both script bodies for one catalog text, embedded in its compact form."""
    compact = _compact_catalog(catalog_text)
    return Bundle(
        adapter_body=_body(ADAPTER_MODULE, _ADAPTER_ENTRY, compact),
        fetch_body=_body(FETCH_MODULE, _FETCH_ENTRY, compact),
    )


def adapter_header(infra_schema: str) -> str:
    return (
        f"CREATE OR REPLACE PYTHON3 ADAPTER SCRIPT {infra_schema}.{ADAPTER_SCRIPT_NAME}{HEADER_END}"
    )


def fetch_header(infra_schema: str, *, replace: bool = False) -> str:
    create = "CREATE OR REPLACE" if replace else "CREATE"
    return (
        f"{create} PYTHON3 SET SCRIPT {infra_schema}.{FETCH_SCRIPT_NAME}"
        f"({FETCH_PARAMETERS}) EMITS (...){HEADER_END}"
    )


def adapter_script(body: str, infra_schema: str) -> str:
    """The complete `CREATE OR REPLACE ... ADAPTER SCRIPT` statement."""
    return _statement(adapter_header(infra_schema), body, ADAPTER_SCRIPT_NAME)


def fetch_script(body: str, infra_schema: str, *, replace: bool = False) -> str:
    """The complete fetch UDF statement; `replace` adds `OR REPLACE`."""
    return _statement(fetch_header(infra_schema, replace=replace), body, FETCH_SCRIPT_NAME)


def stored_body(body: str) -> str:
    """The text Exasol stores for a script body: the sent body plus one trailing newline."""
    return body + "\n"


def script_body(text: str) -> str:
    """Everything after the first ` AS\\n` of a script text, as sent or as stored."""
    _, found, body = text.partition(HEADER_END)
    if not found:
        raise BundleError("script text has no ' AS\\n' header terminator")
    return body


def write_bundle(
    catalog_text: str | None = None,
    out_dir: Path = DEFAULT_OUT_DIR,
    infra_schema: str = "CFB_INFRA",
) -> BundleSizes:
    """Write `cfbd_adapter.sql` and `cfbd_fetch.sql` for the real namespace to `out_dir`."""
    text = (
        DEFAULT_CATALOG_PATH.read_text(encoding="utf-8") if catalog_text is None else catalog_text
    )
    built = build(text)
    adapter = adapter_script(built.adapter_body, infra_schema)
    fetch = fetch_script(built.fetch_body, infra_schema)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ADAPTER_FILE).write_bytes(adapter.encode("utf-8"))
    (out_dir / FETCH_FILE).write_bytes(fetch.encode("utf-8"))
    return BundleSizes(len(adapter), len(fetch))


def main() -> int:
    try:
        sizes = write_bundle()
    except (BundleError, CatalogError, OSError) as error:
        print(f"bundle failed: {error}", file=sys.stderr)
        return 2
    print(f"wrote {DEFAULT_OUT_DIR / ADAPTER_FILE} ({sizes.adapter_chars:,} characters)")
    print(f"wrote {DEFAULT_OUT_DIR / FETCH_FILE} ({sizes.fetch_chars:,} characters)")
    return 0


def _compact_catalog(catalog_text: str) -> str:
    return Catalog.from_json(catalog_text).to_json(indent=None)


def _body(entry_module: str, entry_template: str, compact_catalog: str) -> str:
    modules = ",\n".join(
        f'    ("{name}", {_source(name)!r})' for name in module_order(entry_module)
    )
    shim = _SHIM.replace("{modules}", modules)
    return shim + entry_template.replace("{catalog}", repr(compact_catalog))


def _statement(header: str, body: str, name: str) -> str:
    text = header + body + SCRIPT_TERMINATOR
    if len(text) >= SIZE_LIMIT:
        raise BundleError(
            f"script {name} is {len(text):,} characters long; Exasol caps script text at "
            f"{SCRIPT_LIMIT:,}, so the bundle fails from {SIZE_LIMIT:,} characters "
            "(probe 1.4n). Shrink the catalog or switch to the gzip+base64 fallback"
        )
    return text


def _source(module: str) -> str:
    path = _path(module)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise BundleError(f"cannot read module {module}: {error}") from None


def _path(module: str) -> Path:
    return PACKAGE_DIR / f"{module.removeprefix(PACKAGE + '.')}.py"


def _visit(module: str, ordered: list[str], stack: list[str]) -> None:
    if module in ordered:
        return
    if module in stack:
        raise BundleError(f"circular import: {' -> '.join([*stack, module])}")
    for dependency in sorted(_imports(module)):
        _visit(dependency, ordered, [*stack, module])
    ordered.append(module)


def _imports(module: str) -> set[str]:
    tree = ast.parse(_source(module), filename=str(_path(module)))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.update(_package_modules(node.module, [alias.name for alias in node.names]))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.update(_package_modules(alias.name, []))
        elif isinstance(node, ast.ImportFrom) and node.level > 0:
            raise BundleError(f"{module}: relative imports are not supported by the shim")
    return found


def _package_modules(imported: str, names: list[str]) -> set[str]:
    if imported == PACKAGE:
        return {f"{PACKAGE}.{name}" for name in names if _path(f"{PACKAGE}.{name}").exists()}
    if imported.startswith(PACKAGE + "."):
        return {imported}
    if imported == "vs" or imported.startswith("vs."):
        raise BundleError(f"runtime module imports {imported}, which is outside {PACKAGE}")
    return set()


if __name__ == "__main__":
    sys.exit(main())
