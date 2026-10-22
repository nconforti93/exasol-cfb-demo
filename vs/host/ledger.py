"""The live-call ledger `cache/live-calls.jsonl`, shared by every plan that spends CFBD calls.

One JSON object per line, UTF-8, appended and never rewritten. Every line has these fields, in
this order:

- `ts`: UTC time the entry was written, `YYYY-MM-DDTHH:MM:SS.mmmZ`.
- `plan`: the plan whose budget the entry counts against, e.g. `add-cfbd-vs-foundation`.
- `target`: the Exasol deployment that sent the request (`local` or `aws`).
- `run`: id of the run that wrote the entry; groups the entries of one recorder run.
- `slice_key`: the request path sent (`/games?seasonType=both&year=2024`); `/info` for quota
  reads.
- `status`: HTTP status of the final attempt; null when no response arrived or it is unknown.
- `attempts`: HTTP attempts made, retries included; null when unknown.
- `cost`: calls charged against the caps. 0 for `/info` (free). Otherwise the attempts (all four
  when unknown), or the drop of `remainingCalls` across the request when that is larger.
- `ms`: wall time of the request including retries; null when unknown.
- `bytes`: UTF-8 size of the response body; null when there is none.
- `remaining_before`, `remaining_after`: `/info` `remainingCalls` read right before and right
  after the request; null when unknown. An `/info` read has `cost` 0 and the value it returned
  in both fields.
- `error`: first line of the failure; null on success.

Caps: a plan's total is the sum of `cost` over its entries; the month total is the sum over the
entries of every plan whose `ts` falls in that UTC month. A line that does not parse makes every
read fail, so a budget that cannot be proven is never treated as available.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = ROOT / "cache" / "live-calls.jsonl"
MONTH_CAP = 400
FIELDS = (
    "ts",
    "plan",
    "target",
    "run",
    "slice_key",
    "status",
    "attempts",
    "cost",
    "ms",
    "bytes",
    "remaining_before",
    "remaining_after",
    "error",
)

_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")
_REQUIRED_TEXT = ("ts", "plan", "target", "run", "slice_key")
_OPTIONAL_COUNTS = ("status", "attempts", "ms", "bytes", "remaining_before", "remaining_after")


class LedgerError(RuntimeError):
    """The ledger cannot be read, written or locked."""


@dataclass(frozen=True)
class Entry:
    """One ledger line; see the module documentation for the fields."""

    ts: str
    plan: str
    target: str
    run: str
    slice_key: str
    cost: int
    status: int | None = None
    attempts: int | None = None
    ms: int | None = None
    bytes: int | None = None
    remaining_before: int | None = None
    remaining_after: int | None = None
    error: str | None = None

    def to_line(self) -> str:
        """The JSON line of this entry, fields in the documented order."""
        values = asdict(self)
        return json.dumps({name: values[name] for name in FIELDS}, ensure_ascii=False)


class Ledger:
    """The ledger file plus a lock file beside it that serializes the runs that spend calls."""

    def __init__(self, path: Path = LEDGER_PATH) -> None:
        self.path = path

    def entries(self) -> list[Entry]:
        """Every entry in file order; raises `LedgerError` on any line that does not parse."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as error:
            raise LedgerError(f"cannot read the ledger {self.path}: {error}") from None
        return [
            _parse(line, f"{self.path}:{number}")
            for number, line in enumerate(text.splitlines(), start=1)
            if line.strip()
        ]

    def append(self, entry: Entry) -> None:
        """Append one line and flush it to disk before returning."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(entry.to_line() + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Hold the ledger lock; raises `LedgerError` at once when another run holds it."""
        lock_path = self.path.with_name(self.path.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LedgerError(
                    f"another run holds the ledger lock {lock_path}; wait for it to finish"
                ) from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def plan_total(entries: Iterable[Entry], plan: str) -> int:
    """Calls charged to `plan`."""
    return sum(entry.cost for entry in entries if entry.plan == plan)


def month_total(entries: Iterable[Entry], month: str) -> int:
    """Calls charged to every plan in the UTC month `YYYY-MM`."""
    return sum(entry.cost for entry in entries if entry.ts.startswith(month + "-"))


def month_of(moment: datetime) -> str:
    """The UTC month `YYYY-MM` of a timezone-aware moment."""
    return moment.astimezone(UTC).strftime("%Y-%m")


def timestamp(moment: datetime) -> str:
    """A timezone-aware moment as a ledger `ts`."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(line: str, where: str) -> Entry:
    try:
        data = json.loads(line)
    except ValueError:
        raise LedgerError(f"{where}: not valid JSON") from None
    if not isinstance(data, dict):
        raise LedgerError(f"{where}: not a JSON object")
    missing = [name for name in FIELDS if name not in data]
    if missing:
        raise LedgerError(f"{where}: missing field(s) {', '.join(missing)}")
    for name in _REQUIRED_TEXT:
        if not isinstance(data[name], str) or not data[name]:
            raise LedgerError(f"{where}: field {name!r} must be a non-empty string")
    if not _TIMESTAMP.match(data["ts"]):
        raise LedgerError(f"{where}: field 'ts' is not a UTC timestamp")
    if not _is_count(data["cost"]):
        raise LedgerError(f"{where}: field 'cost' must be a non-negative integer")
    for name in _OPTIONAL_COUNTS:
        if data[name] is not None and not _is_count(data[name]):
            raise LedgerError(f"{where}: field {name!r} must be a non-negative integer or null")
    if data["error"] is not None and not isinstance(data["error"], str):
        raise LedgerError(f"{where}: field 'error' must be a string or null")
    return Entry(**{name: data[name] for name in FIELDS})


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
