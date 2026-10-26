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

Phase 2 (`add-cfb-persistence-refresh`) additions, on the same file and format:

- The default path is the **main** working tree's `cache/live-calls.jsonl`, also from a git
  worktree (`shared_root`), so there is one ledger. `LEDGER_PATH` in the environment overrides it.
- `vs/live/ledger.yaml` holds the caps (`load_config`): `plans` (calls per plan, all months) and
  `months` (calls per UTC month, all plans; the owner's authorization). A month without an entry
  refuses live calls. `Ledger.check(plan, month, estimate, used_calls)` raises `LedgerRefused` when
  the plan total plus the estimate would exceed the plan cap, or when the larger of the key's
  `/info` `usedCalls` and the file's month total plus the estimate would exceed the month cap.
  `Ledger.room` is the calls that still fit both caps.
- `Ledger.record_run` appends one entry for a whole loader run (`make backfill`, `make refresh`):
  `slice_key` is `<KIND>#<run id>`, `cost` the measured calls (the `/info` delta).
- `LEDGER=off` (`is_off`, `Ledger.from_environment`) turns the ledger off for people who run their
  own key: nothing is read, checked or written.

`python -m vs.host.ledger --summary` prints the totals against the caps.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "vs" / "live" / "ledger.yaml"
LEDGER_FILE = Path("cache") / "live-calls.jsonl"
OFF_VARIABLE = "LEDGER"
PATH_VARIABLE = "LEDGER_PATH"
OFF_VALUE = "off"
CONFIG_HINT = "vs/live/ledger.yaml"
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

_MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
_GITDIR = "gitdir:"
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")
_REQUIRED_TEXT = ("ts", "plan", "target", "run", "slice_key")
_OPTIONAL_COUNTS = ("status", "attempts", "ms", "bytes", "remaining_before", "remaining_after")


class LedgerError(RuntimeError):
    """The ledger cannot be read, written or locked."""


class LedgerRefused(LedgerError):
    """A live call would break a cap, or no cap says it is allowed."""


@dataclass(frozen=True)
class LedgerConfig:
    """The caps: calls per plan (all months) and calls per `YYYY-MM` month (all plans)."""

    plans: Mapping[str, int]
    months: Mapping[str, int]


def shared_root(root: Path = ROOT) -> Path:
    """The main working tree of the repository `root` belongs to.

    `root` itself in the main tree, or when it has no usable git metadata. In a linked worktree
    `.git` is a file that points into `<main>/.git/worktrees/<name>`.
    """
    marker = root / ".git"
    if not marker.is_file():
        return root
    first = marker.read_text(encoding="utf-8").strip().splitlines()[:1]
    if not first or not first[0].startswith(_GITDIR):
        return root
    gitdir = (root / first[0].removeprefix(_GITDIR).strip()).resolve()
    commondir = gitdir / "commondir"
    if commondir.is_file():
        common = (gitdir / commondir.read_text(encoding="utf-8").strip()).resolve()
    else:
        common = gitdir.parent.parent
    return common.parent if common.name == ".git" else root


def default_path(root: Path = ROOT) -> Path:
    """`cache/live-calls.jsonl` of the main working tree."""
    return shared_root(root) / LEDGER_FILE


LEDGER_PATH = default_path()


def is_off(environ: Mapping[str, str] | None = None) -> bool:
    """True when `LEDGER=off` (any case) is set."""
    environ = os.environ if environ is None else environ
    return environ.get(OFF_VARIABLE, "").strip().lower() == OFF_VALUE


def load_config(path: Path = CONFIG_PATH) -> LedgerConfig:
    """Read and validate the caps file."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise LedgerError(f"ledger config not found: {path}") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        raise LedgerError(f"ledger config {path} is not valid YAML") from None
    if not isinstance(data, dict):
        raise LedgerError(f"ledger config {path} must be a mapping with `plans` and `months`")
    extra = sorted(set(data) - {"plans", "months"}, key=str)
    if extra:
        raise LedgerError(f"ledger config {path}: unknown key {extra[0]!r}")
    plans = _caps(data, "plans", path)
    months = _caps(data, "months", path)
    for month in months:
        if not _MONTH.fullmatch(month):
            raise LedgerError(f"ledger config {path}: month {month!r} is not YYYY-MM")
    return LedgerConfig(plans, months)


def _caps(data: Mapping[str, Any], key: str, path: Path) -> dict[str, int]:
    section = data.get(key)
    if not isinstance(section, dict):
        raise LedgerError(f"ledger config {path} needs a mapping `{key}`")
    caps: dict[str, int] = {}
    for name, cap in section.items():
        if not _is_count(cap):
            raise LedgerError(
                f"ledger config {path}: {key}.{name} must be a non-negative integer, got {cap!r}"
            )
        caps[str(name)] = cap
    return caps


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
    """The ledger file plus a lock file beside it that serializes the runs that spend calls.

    A disabled ledger (`LEDGER=off`) reads, checks and writes nothing. The caps come from
    `config`, else from `config_path`, loaded on first use.
    """

    def __init__(
        self,
        path: Path = LEDGER_PATH,
        *,
        enabled: bool = True,
        config: LedgerConfig | None = None,
        config_path: Path = CONFIG_PATH,
    ) -> None:
        self.path = path
        self.enabled = enabled
        self._config = config
        self._config_path = config_path

    @classmethod
    def from_environment(
        cls,
        *,
        path: Path | None = None,
        config_path: Path = CONFIG_PATH,
        environ: Mapping[str, str] | None = None,
    ) -> Ledger:
        """The ledger of this process: `path`, else `$LEDGER_PATH`, else the main tree's file."""
        environ = os.environ if environ is None else environ
        given = environ.get(PATH_VARIABLE)
        chosen = path or (Path(given) if given else LEDGER_PATH)
        return cls(chosen, enabled=not is_off(environ), config_path=config_path)

    @property
    def config(self) -> LedgerConfig:
        """The caps; loaded (and validated) on first use."""
        if self._config is None:
            self._config = load_config(self._config_path)
        return self._config

    def entries(self) -> list[Entry]:
        """Every entry in file order; raises `LedgerError` on any line that does not parse."""
        if not self.enabled:
            return []
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
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(entry.to_line() + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Hold the ledger lock; raises `LedgerError` at once when another run holds it."""
        if not self.enabled:
            yield
            return
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

    def room(self, plan: str, month: str, used_calls: int | None) -> int | None:
        """Calls `plan` may still spend now; 0 without a cap; `None` when the ledger is off."""
        if not self.enabled:
            return None
        plan_cap = self.config.plans.get(plan)
        month_cap = self.config.months.get(month)
        if plan_cap is None or month_cap is None:
            return 0
        entries = self.entries()
        month_used = max(month_total(entries, month), used_calls or 0)
        return max(0, min(plan_cap - plan_total(entries, plan), month_cap - month_used))

    def check(
        self,
        plan: str,
        month: str,
        estimate: int,
        used_calls: int | None,
        *,
        headroom: int = 0,
    ) -> None:
        """Raise `LedgerRefused` unless `estimate` + `headroom` more calls fit both caps.

        `used_calls` is the key's `/info` `usedCalls`, the authority for the month; the file's
        month total counts too. `None` (`/info` unreadable) leaves the file's total alone.
        """
        if estimate < 0 or headroom < 0:
            raise LedgerError(
                f"estimate and headroom must not be negative, got {estimate} and {headroom}"
            )
        if not self.enabled:
            return
        plan_cap = self.config.plans.get(plan)
        if plan_cap is None:
            raise LedgerRefused(
                f"the ledger has no cap for plan {plan!r}; add one to {CONFIG_HINT}"
            )
        month_cap = self.config.months.get(month)
        if month_cap is None:
            raise LedgerRefused(
                f"the ledger has no entry for month {month}: live calls are refused until the "
                f"owner adds one to {CONFIG_HINT}"
            )
        entries = self.entries()
        wanted = estimate + headroom
        spent = plan_total(entries, plan)
        if spent + wanted > plan_cap:
            raise LedgerRefused(
                f"plan {plan}: cap {plan_cap}, already {spent}, estimate {estimate}"
                f"{f' + headroom {headroom}' if headroom else ''} would make {spent + wanted}"
            )
        in_month = month_total(entries, month)
        used, source = (in_month, "ledger file")
        if used_calls is not None:
            used, source = max(used_calls, in_month), "/info"
        if used + wanted > month_cap:
            raise LedgerRefused(
                f"month {month}: cap {month_cap}; {source} used {used} + estimate {estimate}"
                f"{f' + headroom {headroom}' if headroom else ''} would make {used + wanted}"
            )

    def record_run(
        self,
        plan: str,
        target: str,
        *,
        kind: str,
        run_id: int,
        calls: int,
        remaining_before: int | None = None,
        remaining_after: int | None = None,
        error: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Append the measured calls of one loader run as one entry (`cost` = `calls`)."""
        self.append(
            Entry(
                ts=timestamp(clock()),
                plan=plan,
                target=target,
                run=f"{kind.lower()}-{run_id}",
                slice_key=f"{kind}#{run_id}",
                cost=calls,
                remaining_before=remaining_before,
                remaining_after=remaining_after,
                error=error.splitlines()[0] if error else None,
            )
        )

    def summary_lines(self) -> list[str]:
        """Totals per plan and per month against their caps."""
        if not self.enabled:
            return [f"live-call ledger: off ({OFF_VARIABLE}=off); no checks, no records"]
        entries = self.entries()
        by_plan = Counter[str]()
        by_month = Counter[str]()
        for item in entries:
            by_plan[item.plan] += item.cost
            by_month[item.ts[:7]] += item.cost
        lines = [f"live-call ledger: {self.path}", "plans:"]
        for plan in sorted(set(self.config.plans) | set(by_plan)):
            lines.append(_total_line(plan, by_plan[plan], self.config.plans.get(plan), "no cap"))
        lines.append("months (all plans; /info used calls are the authority, see make quota):")
        for month in sorted(set(self.config.months) | set(by_month)):
            lines.append(
                _total_line(month, by_month[month], self.config.months.get(month), "no entry")
            )
        return lines


def _total_line(name: str, total: int, cap: int | None, missing: str) -> str:
    return f"  {name}: {total} of {cap}" if cap is not None else f"  {name}: {total} ({missing})"


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m vs.host.ledger --summary [--path FILE] [--config FILE]`."""
    parser = argparse.ArgumentParser(description="Live-call ledger of the CFBD key.")
    parser.add_argument("--summary", action="store_true", help="print the totals against the caps")
    parser.add_argument("--path", type=Path, help="ledger file (default: main tree cache)")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="caps file")
    arguments = parser.parse_args(argv)
    if not arguments.summary:
        parser.error("nothing to do; pass --summary")
    try:
        book = Ledger.from_environment(path=arguments.path, config_path=arguments.config)
        lines = book.summary_lines()
    except LedgerError as error:
        print(f"ledger: {error}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


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


if __name__ == "__main__":
    sys.exit(main())
