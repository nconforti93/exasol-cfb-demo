"""`EXPLAIN VIRTUAL` results as manifests, and a manifest's slices as the HTTP requests they become
(pure). A query over serving views alone has no pushdown and reads "no virtual table: 0 calls"."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from vs.host import fetchlog
from vs.host.fetchlog import FetchLogError, Manifest

API_BASE_URL = "https://api.collegefootballdata.com"
NO_VIRTUAL_TABLE_TEXT = "no virtual table: 0 calls"
MESSAGE_LIMIT = 300

_SESSION_SUFFIX = re.compile(r"\s*\(Session:\s*\d+\)\s*$")


class ExplainError(ValueError):
    """The `EXPLAIN VIRTUAL` result is not what the virtual schema promises."""


@dataclass(frozen=True)
class Explained:
    """The pushdowns of one query, in the order the database returned them."""

    manifests: tuple[Manifest, ...]

    @property
    def no_virtual_table(self) -> bool:
        return not self.manifests


def parse_explain(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> Explained:
    """0..n manifests from the columns and rows of `EXPLAIN VIRTUAL <query>`."""
    if not rows:
        return Explained(())
    names = list(columns)
    if fetchlog.PUSHDOWN_SQL_COLUMN not in names:
        raise ExplainError(f"EXPLAIN VIRTUAL returned no {fetchlog.PUSHDOWN_SQL_COLUMN} column")
    position = names.index(fetchlog.PUSHDOWN_SQL_COLUMN)
    manifests = []
    for row in rows:
        try:
            manifests.append(fetchlog.parse_manifest(str(row[position])))
        except FetchLogError as error:
            raise ExplainError(f"a pushdown carries no usable manifest: {error}") from None
    return Explained(tuple(manifests))


def request_line(slice_key: str) -> str:
    """The HTTP request a slice key stands for. The key and the authorization header are never
    part of it."""
    path = slice_key if slice_key.startswith("/") else f"/{slice_key}"
    return f"GET {API_BASE_URL}{path}"


def describe_manifest(manifest: Manifest) -> dict[str, Any]:
    """A manifest for the API: its fields plus one request line per slice."""
    return {
        "table": manifest.table,
        "fetch_mode": manifest.fetch_mode,
        "read_through": manifest.read_through,
        "budget": manifest.budget,
        "planned_requests": manifest.planned_requests,
        "slice_keys": list(manifest.slice_keys),
        "requests": [request_line(key) for key in manifest.slice_keys],
        "sources": list(manifest.sources),
    }


def refusal_message(error: object) -> str:
    """The first meaningful line of a database error, without the session suffix."""
    lines = [line.strip() for line in str(error).splitlines()]
    lines = [line for line in lines if line not in ("", "(", ")")]
    text = _SESSION_SUFFIX.sub("", lines[0]) if lines else "the statement was refused"
    return text[:MESSAGE_LIMIT]
