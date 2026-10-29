"""What a live virtual-schema query cost: the pure helpers of `scripts/record.py` and the app.

A query's live calls are read from the manifest of its `EXPLAIN VIRTUAL` (what it may fetch) and
from the `REQ_*` columns of its result (what it did). Charges err on the high side: a request
whose attempts are unknown is charged `MAX_ATTEMPTS`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from vs.cfbd_vs.http import MAX_ATTEMPTS
from vs.host import fetchlog

SOURCE_LIVE = "LIVE"


@dataclass(frozen=True)
class SliceCall:
    """What a query did with one planned slice, from its `REQ_*` columns."""

    slice_key: str
    source: str
    status: int | None
    attempts: int | None
    ms: int | None
    rows: int | None
    requested_at: str | None


def charged(attempts: int | None, before: int | None, after: int | None) -> int:
    """Calls to charge: the attempts (all of them when unknown), or the observed drop if larger."""
    counted = MAX_ATTEMPTS if attempts is None else attempts
    if before is None or after is None:
        return counted
    return max(counted, before - after)


def query_costs(attempts: Sequence[int | None], before: int, after: int | None) -> list[int]:
    """Charges for a query's live slices: the attempts, 1 for an unknown slice (all four without
    an `/info` reading afterwards), and the rest of the observed drop on the last slice."""
    unknown = 1 if after is not None else MAX_ATTEMPTS
    costs = [unknown if count is None else count for count in attempts]
    if costs and after is not None:
        costs[-1] += max(0, before - after - sum(costs))
    return costs


def slice_calls(
    manifest: fetchlog.Manifest, names: Sequence[str], rows: Sequence[tuple[Any, ...]]
) -> list[SliceCall] | None:
    """Per planned slice what the query read, or None when the result has no `REQ_*` columns."""
    if not all(column in names for column in fetchlog.REQUEST_COLUMNS):
        return None
    positions = [list(names).index(column) for column in fetchlog.REQUEST_COLUMNS]
    distinct = {tuple(row[position] for position in positions) for row in rows}
    metadata = [dict(zip(fetchlog.REQUEST_COLUMNS, values, strict=True)) for values in distinct]
    logged = fetchlog.build_fetch_log_rows(manifest, metadata, logged_at=datetime.now(UTC))
    return [
        SliceCall(
            row.slice_key,
            row.source,
            _optional_int(row.http_status),
            _optional_int(row.attempts),
            _optional_int(row.ms),
            _optional_int(row.rows_returned),
            None if row.requested_at is None else str(row.requested_at),
        )
        for row in logged
    ]


def planned_live(manifest: fetchlog.Manifest) -> list[str]:
    """The slices a query may fetch live (the budget counts them all, [4])."""
    return list(manifest.slice_keys) if manifest.fetch_source == SOURCE_LIVE else []


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)
