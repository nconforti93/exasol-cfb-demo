"""Pure helpers of the integration harness: secrets, WHERE clauses, recordings, the replay area.

Nothing here needs a database. `tests/it/harness.py` builds the database-facing `Harness` on top.
"""

from __future__ import annotations

import secrets
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from tests.synthetic import fake
from vs.cfbd_vs import replay
from vs.cfbd_vs.rules import Value

MARKER_PREFIX = "cfb-test-marker-"
SECRET_BYTES = 24
MARKER_BYTES = 16
SYNTHETIC_STATUS = 200
SYNTHETIC_MS = 1
WAIT_SCRIPT_NAME = "WAIT_FOR_FILES"
BUCKETFS_ROOT = (
    Path.home()
    / ".exasol"
    / "personal"
    / "deployments"
    / "local"
    / "local"
    / "runtime"
    / "exa"
    / "bucketfs"
)
UDF_BUCKETS = "/buckets"


class HarnessError(RuntimeError):
    """The harness was asked for something it cannot do, or the environment is not as expected."""


def new_secret() -> str:
    """A random password, generated in memory and never written anywhere."""
    return secrets.token_urlsafe(SECRET_BYTES)


def new_marker_key() -> str:
    """A random fake API key; tests search for it to prove it never leaks."""
    return MARKER_PREFIX + secrets.token_hex(MARKER_BYTES)


def where_clause(table: str, filters: Mapping[str, Value]) -> str:
    """`P_<COLUMN> = <literal> AND ...` for API parameter names, in the table's parameter order."""
    params = {param.name: param for param in fake.catalog().table(table).params}
    unknown = [name for name in filters if name not in params]
    if unknown:
        raise HarnessError(f"{table} has no parameter {', '.join(unknown)}")
    return " AND ".join(
        f"{param.column} = {_literal(filters[name])}"
        for name, param in params.items()
        if name in filters
    )


def split_slice_key(slice_key: str) -> tuple[str, dict[str, str]]:
    """A slice key as the request `(path, decoded query)` the mock and the provider answer."""
    path, _, query = slice_key.partition("?")
    return path, dict(parse_qsl(query, keep_blank_values=True))


def recording_for(
    slice_key: str, body: Any, *, status: int = SYNTHETIC_STATUS, ms: int = SYNTHETIC_MS
) -> replay.Recording:
    """A recording of `slice_key` whose body is `body` (text, bytes or a JSON value)."""
    if isinstance(body, bytes):
        text = body.decode("utf-8")
    elif isinstance(body, str):
        text = body
    else:
        text = fake.to_bytes(body).decode("utf-8")
    return replay.Recording(slice_key, status, ms, datetime.now(UTC), text)


def synthetic_recording(slice_key: str) -> replay.Recording:
    """The recording of `slice_key` as the synthetic provider (and so the mock) would answer."""
    path, query = split_slice_key(slice_key)
    return recording_for(slice_key, fake.response_for(path, query))


def wait_script_sql(schema: str) -> str:
    """`WAIT_FOR_FILES(paths_json, want_present, timeout_s)`: poll inside the UDF sandbox.

    Returns the seconds waited once every path exists (or, with `want_present` false, none does);
    raises with the first paths still in the wrong state after the timeout. BucketFS files show
    up in a UDF after about 0.1 to 1 s, so the poll runs where the UDF reads them.
    """
    return (
        f"CREATE OR REPLACE PYTHON3 SCALAR SCRIPT {schema}.{WAIT_SCRIPT_NAME}"
        "(PATHS_JSON VARCHAR(2000000), WANT_PRESENT BOOLEAN, TIMEOUT_S DECIMAL(5,1)) "
        "RETURNS DOUBLE AS\n"
        "import json\n"
        "import os\n"
        "import time\n"
        "\n"
        "\n"
        "def run(ctx):\n"
        "    paths = json.loads(ctx.PATHS_JSON)\n"
        "    wanted = bool(ctx.WANT_PRESENT)\n"
        "    started = time.monotonic()\n"
        "    deadline = started + float(ctx.TIMEOUT_S)\n"
        "    while True:\n"
        "        wrong = [p for p in paths if os.path.exists(p) != wanted]\n"
        "        if not wrong:\n"
        "            return round(time.monotonic() - started, 3)\n"
        "        if time.monotonic() > deadline:\n"
        "            state = 'visible' if wanted else 'gone'\n"
        "            message = f'{len(wrong)} of {len(paths)} files not {state}: {wrong[:3]}'\n"
        "            raise RuntimeError(message)\n"
        "        time.sleep(0.05)\n"
    )


@dataclass
class ReplayArea:
    """A BucketFS directory for test recordings: its host path and the path a UDF sees.

    `create` remembers which directories it had to make, so `remove` deletes exactly those (when
    empty) and leaves what was there before, including another plan's data.
    """

    service: str = "bfsdefault"
    bucket: str = "default"
    directory: str = "cfbd/test-replay"
    bucketfs_root: Path = BUCKETFS_ROOT
    _created: list[Path] = field(default_factory=list, init=False, repr=False)

    @property
    def udf_path(self) -> str:
        """The value of the `REPLAY_PATH` property."""
        return f"{UDF_BUCKETS}/{self.service}/{self.bucket}/{self.directory}"

    @property
    def host_dir(self) -> Path:
        return self.bucketfs_root / self.service / self.bucket / self.directory

    def udf_file(self, slice_key: str) -> str:
        return replay.recording_path(self.udf_path, slice_key)

    def host_file(self, slice_key: str) -> Path:
        return self.host_dir / replay.relative_path(slice_key)

    def create(self) -> None:
        """Make the directory, remembering every level that did not exist."""
        current = self.bucketfs_root
        for segment in (self.service, self.bucket, *self.directory.split("/")):
            current = current / segment
            if not current.exists():
                current.mkdir()
                self._created.append(current)

    def write(self, recording: replay.Recording) -> Path:
        """Write a recording under the host directory and return its path."""
        return Path(replay.ReplayStore(str(self.host_dir)).save(recording))

    def clear(self) -> list[str]:
        """Delete every recording; return the UDF paths of the files that were deleted."""
        if not self.host_dir.exists():
            return []
        removed = [
            f"{self.udf_path}/{path.relative_to(self.host_dir).as_posix()}"
            for path in sorted(self.host_dir.rglob("*"))
            if path.is_file()
        ]
        for child in self.host_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        return removed

    def remove(self) -> None:
        """Delete the recordings and the directories `create` made, deepest first."""
        self.clear()
        for path in reversed(self._created):
            if path == self.host_dir:
                shutil.rmtree(path, ignore_errors=True)
            else:
                _rmdir_if_empty(path)
        self._created.clear()


def _rmdir_if_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        return


def _literal(value: Value) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return repr(value)
    return "'" + value.replace("'", "''") + "'"
