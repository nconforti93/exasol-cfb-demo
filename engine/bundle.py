"""The `SIM_ENGINE` script: `engine/core.py` and `engine/udf.py` as one `PYTHON3 SET` script.

The script body carries both modules as strings and a `sys.modules` shim registers them as
`engine.core` and `engine.udf` (the technique of `vs/bundle.py`), so `udf.py` runs unchanged on the
host and in the database: `from engine import core`. Nothing is registered under a bare name.

    CREATE OR REPLACE PYTHON3 SET SCRIPT <infra>.SIM_ENGINE(<MODEL_INPUT_COLUMNS>)
      EMITS (<STAGE_COLUMNS>) AS <body>
    /

- `STAGE_COLUMNS` (from `engine/core.py`, which builds exactly these columns) are the columns of
  `CFB_SIM.SIM_STAGE`, in order (a unit test compares them with `sql/sim/data/sim_stage.sql`), so
  `INSERT INTO SIM_STAGE SELECT SIM_ENGINE(...)` needs no column list.
- `MODEL_INPUT_COLUMNS` are the four run columns, then the model-row columns of
  `sim-engine/engine-contract`, both from `engine/core.py`, which parses them
  (`CFB_SIM.SIM_RUN_MODEL_ROWS` produces them): a `TEAM`, `GAME` or `PARAM` row fills the columns
  of its kind and leaves the others NULL.
- `engine_version(body)` is the first 16 hex digits of the SHA-256 of the body; the script's comment
  names it (`script_comment`) and every run records it.
- Output is deterministic: no timestamps, a fixed module order and `repr` literals. The script text
  of Exasol is capped at 2,000,000 characters, so a script of `SIZE_LIMIT` characters or more fails
  with an error that names the size.

`python -m engine.bundle` builds the script for the real namespace and prints its size and version
(the `make bundle` check of the plan).
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from engine.core import MODEL_COLUMNS, RUN_COLUMNS, STAGE_COLUMNS
from vs.bundle import HEADER_END, SCRIPT_LIMIT, SCRIPT_TERMINATOR, SIZE_LIMIT, BundleError
from vs.cfbd_vs.sqlrender import qualified_name, string_literal
from vs.host.namespace import Namespace

__all__ = [
    "ENGINE_DIR",
    "MODEL_COLUMNS",
    "MODEL_INPUT_COLUMNS",
    "RUN_COLUMNS",
    "SCRIPT_NAME",
    "SIZE_LIMIT",
    "STAGE_COLUMNS",
    "BundleError",
    "EngineScript",
    "build",
    "engine_version",
    "script_body",
    "script_comment",
    "script_header",
]

ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT / "engine"
PACKAGE = "engine"
MODULES = ("core", "udf")
SCRIPT_NAME = "SIM_ENGINE"
VERSION_LENGTH = 16

MODEL_INPUT_COLUMNS = RUN_COLUMNS + MODEL_COLUMNS

_SHIM = """\
import sys
import types

_PACKAGE = "engine"
_package = types.ModuleType(_PACKAGE)
_package.__path__ = []
sys.modules[_PACKAGE] = _package

_MODULES = (
{modules}
)
for _name, _source in _MODULES:
    _module = types.ModuleType(_name)
    _module.__file__ = "<bundle " + _name + ">"
    sys.modules[_name] = _module
    setattr(_package, _name.rpartition(".")[2], _module)
    exec(compile(_source, _module.__file__, "exec"), _module.__dict__)

from engine import udf


def run(ctx):
    udf.run(ctx, exa)
"""


@dataclass(frozen=True)
class EngineScript:
    """The script as built: the `CREATE` statement, its body, version and comment statement."""

    create: str
    body: str
    version: str
    comment: str

    @property
    def statements(self) -> tuple[str, str]:
        """`CREATE OR REPLACE ... SCRIPT` and `COMMENT ON SCRIPT`, in the order they run."""
        return self.create, self.comment


def engine_version(body: str) -> str:
    """The first 16 hex digits of the SHA-256 of the script body."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:VERSION_LENGTH]


def script_body(engine_dir: Path = ENGINE_DIR) -> str:
    """The body: the shim, both modules as `repr` literals, and the entry function `run`."""
    modules = ",\n".join(
        f'    ("{PACKAGE}.{name}", {_source(engine_dir, name)!r})' for name in MODULES
    )
    return _SHIM.replace("{modules}", modules)


def _source(engine_dir: Path, name: str) -> str:
    return (engine_dir / f"{name}.py").read_text(encoding="utf-8")


def script_header(infra_schema: str) -> str:
    """`CREATE OR REPLACE PYTHON3 SET SCRIPT ... EMITS (...) AS` up to and including the newline."""
    parameters = ", ".join(f"{name} {sql_type}" for name, sql_type in MODEL_INPUT_COLUMNS)
    emits = ", ".join(f"{name} {sql_type}" for name, sql_type in STAGE_COLUMNS)
    return (
        f"CREATE OR REPLACE PYTHON3 SET SCRIPT {qualified_name(infra_schema, SCRIPT_NAME)}"
        f"({parameters}) EMITS ({emits}){HEADER_END}"
    )


def script_comment(infra_schema: str, version: str) -> str:
    """`COMMENT ON SCRIPT` that names the engine version."""
    text = (
        f"The season simulation engine (ENGINE_VERSION {version}): one call simulates one batch "
        "of seasons and emits the tagged rows of CFB_SIM.SIM_STAGE. Python 3 SET script, "
        "bundled from engine/core.py and engine/udf.py."
    )
    return (
        f"COMMENT ON SCRIPT {qualified_name(infra_schema, SCRIPT_NAME)} IS {string_literal(text)}"
    )


def build(namespace: Namespace, engine_dir: Path = ENGINE_DIR) -> EngineScript:
    """The engine script of `namespace`; raises `BundleError` when it is too long for Exasol."""
    body = script_body(engine_dir)
    version = engine_version(body)
    create = script_header(namespace.infra_schema) + body + SCRIPT_TERMINATOR
    if len(create) >= SIZE_LIMIT:
        raise BundleError(
            f"script {SCRIPT_NAME} is {len(create):,} characters long; Exasol caps script text "
            f"at {SCRIPT_LIMIT:,}, so the bundle fails from {SIZE_LIMIT:,} characters. "
            "Shrink the engine sources"
        )
    return EngineScript(create, body, version, script_comment(namespace.infra_schema, version))


def main() -> int:
    try:
        script = build(Namespace(""))
    except (BundleError, OSError) as error:
        print(f"bundle failed: {error}", file=sys.stderr)
        return 2
    print(f"{SCRIPT_NAME}: {len(script.create):,} characters, ENGINE_VERSION {script.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
