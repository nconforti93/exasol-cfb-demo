"""The simulation side of the integration harness: what a reset keeps, and what is deployed.

- `SIM_REFERENCE_TABLES` are seeded by the deploy (`SIM_PARAMS`, `SEASON_RULES`); a reset never
  truncates them, only the data tables of the run.
- `deploy_digest` tells whether the deployed simulation files changed (`sql/sim/`,
  `sql/sim/data/`, the engine and the `SIMULATE` Lua, one hash).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

from engine import bundle
from loader import bundle as lua_bundle
from vs.host.namespace import Namespace

SIM_REFERENCE_TABLES = ("SIM_PARAMS", "SEASON_RULES")


def deploy_digest(
    directory: Path, data_directory: Path, engine_directory: Path, lua_directory: Path
) -> str:
    """A hash of the `*.sql` names and bytes of both directories, of the engine body and of the
    `SIMULATE` Lua sources (`engine/lua/*.lua` and the loader's `loadlib.lua`)."""
    digest = hashlib.sha256()
    for folder, pattern in (
        (directory, "*.sql"),
        (data_directory, "*.sql"),
        (lua_directory, "*.lua"),
    ):
        for path in sorted(folder.glob(pattern)):
            digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    digest.update((lua_bundle.LUA_DIR / lua_bundle.LIBRARY_FILE).read_bytes())
    digest.update(bundle.engine_version(bundle.script_body(engine_directory)).encode("ascii"))
    return digest.hexdigest()


def drop_reference_tables(
    tables: Iterable[tuple[str, str]], namespace: Namespace
) -> list[tuple[str, str]]:
    """`(schema, table)` rows without the reference tables of the simulation schema."""
    kept = {(namespace.sim_schema, name) for name in SIM_REFERENCE_TABLES}
    return [row for row in tables if row not in kept]
