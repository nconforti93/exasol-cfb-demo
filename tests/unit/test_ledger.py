import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vs.host import ledger
from vs.host.ledger import Entry, Ledger, LedgerError

PLAN = "add-cfbd-vs-foundation"


def entry(**overrides: object) -> Entry:
    values: dict[str, object] = {
        "ts": "2026-10-22T15:00:00.000Z",
        "plan": PLAN,
        "target": "aws",
        "run": "20261022T150000Z",
        "slice_key": "/games?seasonType=both&year=2024",
        "cost": 1,
        "status": 200,
        "attempts": 1,
        "ms": 812,
        "bytes": 12345,
        "remaining_before": 3000,
        "remaining_after": 2999,
        "error": None,
    }
    values.update(overrides)
    return Entry(**values)  # type: ignore[arg-type]


def test_append_writes_one_json_line_with_exactly_the_documented_fields(tmp_path: Path) -> None:
    path = tmp_path / "cache" / "live-calls.jsonl"
    Ledger(path).append(entry())
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert tuple(json.loads(lines[0])) == ledger.FIELDS
    assert ledger.FIELDS == (
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


def test_entries_round_trip_in_append_order(tmp_path: Path) -> None:
    book = Ledger(tmp_path / "live-calls.jsonl")
    first = entry()
    second = entry(slice_key="/info", cost=0, status=200, bytes=None, error=None)
    book.append(first)
    book.append(second)
    assert book.entries() == [first, second]


def test_a_missing_ledger_has_no_entries(tmp_path: Path) -> None:
    assert Ledger(tmp_path / "absent.jsonl").entries() == []


def test_blank_lines_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "live-calls.jsonl"
    path.write_text(entry().to_line() + "\n\n", encoding="utf-8")
    assert Ledger(path).entries() == [entry()]


@pytest.mark.parametrize(
    "line",
    [
        "{not json",
        "[1, 2]",
        json.dumps({"ts": "2026-10-22T15:00:00.000Z"}),
        entry().to_line().replace('"cost": 1', '"cost": -1'),
        entry().to_line().replace('"cost": 1', '"cost": "1"'),
        entry().to_line().replace('"cost": 1', '"cost": true'),
        entry().to_line().replace('"attempts": 1', '"attempts": 1.5'),
        entry().to_line().replace('"plan": "add-cfbd-vs-foundation"', '"plan": ""'),
        entry().to_line().replace('"ts": "2026-10-22T15:00:00.000Z"', '"ts": "yesterday"'),
    ],
)
def test_an_unreadable_line_fails_closed_and_names_the_line(tmp_path: Path, line: str) -> None:
    path = tmp_path / "live-calls.jsonl"
    path.write_text(entry().to_line() + "\n" + line + "\n", encoding="utf-8")
    with pytest.raises(LedgerError, match=r"live-calls\.jsonl:2"):
        Ledger(path).entries()


def test_plan_total_sums_the_cost_of_that_plan_only() -> None:
    entries = [
        entry(cost=1),
        entry(cost=4),
        entry(slice_key="/info", cost=0),
        entry(plan="add-cfb-persistence-refresh", cost=7),
    ]
    assert ledger.plan_total(entries, PLAN) == 5
    assert ledger.plan_total(entries, "add-cfb-persistence-refresh") == 7
    assert ledger.plan_total(entries, "unknown") == 0


def test_month_total_sums_every_plan_within_the_utc_month_only() -> None:
    entries = [
        entry(cost=3),
        entry(plan="other", cost=10),
        entry(ts="2026-09-30T23:59:59.999Z", cost=100),
        entry(ts="2026-11-01T00:00:00.000Z", cost=1000),
    ]
    assert ledger.month_total(entries, "2026-10") == 13


def test_month_of_and_timestamp_use_utc() -> None:
    moment = datetime(2026, 10, 31, 23, 30, 0, 123456, tzinfo=UTC)
    assert ledger.timestamp(moment) == "2026-10-31T23:30:00.123Z"
    assert ledger.month_of(moment) == "2026-10"


def test_the_exclusive_lock_refuses_a_second_holder(tmp_path: Path) -> None:
    book = Ledger(tmp_path / "live-calls.jsonl")
    second = Ledger(tmp_path / "live-calls.jsonl")
    with book.exclusive(), pytest.raises(LedgerError, match="another"), second.exclusive():
        pass
    with book.exclusive():
        pass


def test_the_default_path_is_the_shared_cache_ledger() -> None:
    assert ledger.LEDGER_PATH.parts[-2:] == ("cache", "live-calls.jsonl")
    assert ledger.MONTH_CAP == 400
