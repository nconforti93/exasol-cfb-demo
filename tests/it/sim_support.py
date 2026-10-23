"""The simulation side of the integration harness: what a reset keeps, and what is deployed.

- `SIM_REFERENCE_TABLES` are seeded by the deploy (`SIM_PARAMS`, `SEASON_RULES`); a reset never
  truncates them, only the data tables of the run.
- `deploy_digest` tells whether the deployed simulation files changed (`sql/sim/` and
  `sql/sim/data/`, one hash).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

from engine import bundle
from vs.host.namespace import Namespace

SIM_REFERENCE_TABLES = ("SIM_PARAMS", "SEASON_RULES")


def deploy_digest(directory: Path, data_directory: Path, engine_directory: Path) -> str:
    """A hash of the `*.sql` names and bytes of both directories and of the engine body."""
    digest = hashlib.sha256()
    for folder in (directory, data_directory):
        for path in sorted(folder.glob("*.sql")):
            digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    digest.update(bundle.engine_version(bundle.script_body(engine_directory)).encode("ascii"))
    return digest.hexdigest()


def drop_reference_tables(
    tables: Iterable[tuple[str, str]], namespace: Namespace
) -> list[tuple[str, str]]:
    """`(schema, table)` rows without the reference tables of the simulation schema."""
    kept = {(namespace.sim_schema, name) for name in SIM_REFERENCE_TABLES}
    return [row for row in tables if row not in kept]
