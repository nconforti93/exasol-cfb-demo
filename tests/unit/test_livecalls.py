"""Unit tests of the cost helpers shared by the recorder and the app (`vs/host/livecalls.py`)."""

from __future__ import annotations

from typing import Any

from vs.cfbd_vs.http import MAX_ATTEMPTS
from vs.host import fetchlog, livecalls
from vs.host.livecalls import SliceCall

WEEK8 = "/games?seasonType=both&week=8&year=2019"
WEEK9 = "/games?seasonType=both&week=9&year=2019"
NAMES = ["ID", *fetchlog.REQUEST_COLUMNS]


def manifest(*slice_keys: str, sources: tuple[str, ...] = ("LIVE",)) -> fetchlog.Manifest:
    return fetchlog.Manifest(
        table="GAMES",
        fetch_mode="live",
        read_through=True,
        budget=10,
        planned_requests=len(slice_keys),
        slice_keys=slice_keys,
        sources=sources,
    )


def row(slice_key: str, source: str, status: int | None, attempts: int, ms: int | None) -> Any:
    return (1, slice_key, source, status, attempts, ms, 1, "2026-10-22 15:00:00.000000")


def test_the_charge_never_undercounts() -> None:
    assert livecalls.charged(1, 100, 99) == 1
    assert livecalls.charged(1, 100, 100) == 1
    assert livecalls.charged(1, 100, 97) == 3
    assert livecalls.charged(None, 100, 100) == MAX_ATTEMPTS
    assert livecalls.charged(None, None, None) == MAX_ATTEMPTS
    assert livecalls.charged(0, 100, 100) == 0
    assert livecalls.charged(2, None, 50) == 2
    assert livecalls.charged(1, 100, 3000) == 1


def test_query_costs_count_attempts_and_put_the_rest_of_the_drop_on_the_last_slice() -> None:
    assert livecalls.query_costs([1, 1], 100, 98) == [1, 1]
    assert livecalls.query_costs([1, 1], 100, 97) == [1, 2]
    assert livecalls.query_costs([None, 2], 100, 97) == [1, 2]
    assert livecalls.query_costs([None, 1], 100, None) == [MAX_ATTEMPTS, 1]
    assert livecalls.query_costs([], 100, 90) == []


def test_only_a_live_manifest_has_planned_live_slices() -> None:
    assert livecalls.planned_live(manifest(WEEK8, WEEK9)) == [WEEK8, WEEK9]
    assert livecalls.planned_live(manifest(WEEK8, sources=("REPLAY",))) == []
    assert livecalls.planned_live(manifest(WEEK8, sources=("STORED",))) == []
    assert livecalls.planned_live(manifest(sources=())) == []


def test_slice_calls_read_one_call_per_planned_slice_from_the_request_columns() -> None:
    rows = [row(WEEK8, "LIVE", 200, 1, 700), row(WEEK8, "LIVE", 200, 1, 700)]
    calls = livecalls.slice_calls(manifest(WEEK8, WEEK9), NAMES, rows)
    assert calls == [
        SliceCall(WEEK8, "LIVE", 200, 1, 700, 1, "2026-10-22 15:00:00.000000"),
        SliceCall(WEEK9, "LIVE", None, None, None, 0, None),
    ]


def test_slice_calls_are_unknown_without_request_columns() -> None:
    assert livecalls.slice_calls(manifest(WEEK8), ["COUNT(*)"], [(5,)]) is None


def test_the_live_source_name_matches_the_fetch_log_sources() -> None:
    assert livecalls.SOURCE_LIVE in fetchlog.FETCH_SOURCES
