"""The performance smoke without a database: probe list, timing loop, verdicts, report."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from model import perf
from vs.host.namespace import Namespace

NAMESPACE = Namespace("PERF_")


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class TimedConnection:
    """Answers every query with `rows` and advances a fake clock by the cost of the query."""

    def __init__(self, clock: FakeClock, costs: dict[str, float] | None = None) -> None:
        self.clock = clock
        self.costs = costs or {}
        self.statements: list[str] = []

    def execute(self, query: str) -> _Rows:
        self.statements.append(query)
        for marker, cost in self.costs.items():
            if marker in query:
                self.clock.now += cost
        return _Rows([(1,), (2,)])


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def probes() -> list[perf.Probe]:
    return perf.probes(NAMESPACE, season=2019, week_seq=9)


def test_every_probe_has_a_target_a_basis_and_names_its_view() -> None:
    found = probes()
    assert len({probe.name for probe in found}) == len(found)
    for probe in found:
        assert probe.target_seconds > 0
        assert probe.basis in (perf.PLAN, perf.DERIVED)
        assert f'"PERF_CFB_MODEL"."{probe.view}"' in probe.sql


def test_the_plan_probes_are_the_ones_the_requirements_name() -> None:
    by_name = {probe.name: probe for probe in probes()}
    full_game = by_name["GAME grouped by season"]
    assert full_game.basis == perf.PLAN and full_game.target_seconds == 2.0
    assert full_game.sql == (
        'SELECT "SEASON", COUNT(*) FROM "PERF_CFB_MODEL"."GAME" GROUP BY "SEASON"'
    )
    point = by_name["as-of prototype, one point"]
    assert point.basis == perf.PLAN and point.target_seconds == 1.0


def test_the_task_views_are_covered_per_season_and_in_full() -> None:
    names = {probe.name for probe in probes()}
    assert {
        "GAME one season",
        "TEAM_GAME one season",
        "CONFERENCE_CHAMPIONSHIP_GAME in full",
        "SEASON_WEEK in full",
        "TEAM in full",
        "TEAM_GAME grouped by season",
    } <= names


def test_season_and_week_reach_the_per_season_and_prototype_queries() -> None:
    by_name = {probe.name: probe for probe in perf.probes(NAMESPACE, season=1999, week_seq=4)}
    assert '"SEASON" = 1999' in by_name["GAME one season"].sql
    prototype = by_name["as-of prototype, one point"].sql
    assert ') WHERE "AS_OF_SEASON" = 1999 AND "AS_OF_WEEK_SEQ" = 4' in prototype
    every_point = by_name["as-of prototype, every point of one season"].sql
    assert every_point.endswith(') WHERE "AS_OF_SEASON" = 1999')
    assert "PARTICIPANTS_RULE" in prototype


def test_the_prototype_filters_participants_like_the_as_of_view() -> None:
    sql = {probe.name: probe for probe in probes()}["as-of prototype, one point"].sql
    for rule in ("FIXED", "CONF_CHAMPIONSHIP", "SELECTION", "PREVIOUS_ROUND"):
        assert f"'{rule}'" in sql


def test_timing_discards_warm_ups_and_reports_median_and_worst() -> None:
    clock = FakeClock()
    durations: Iterator[float] = iter([9, 9, 9, 0.5, 0.1, 0.9, 0.3, 0.2])

    class Stepped(TimedConnection):
        def execute(self, query: str) -> _Rows:
            if not query.startswith("SELECT"):
                return super().execute(query)
            clock.now += next(durations)
            return super().execute(query)

    connection = Stepped(clock)
    probe = probes()[0]
    timing = perf.time_probe(connection, probe, warmups=3, runs=5, clock=clock)
    assert timing.median == pytest.approx(0.3)
    assert timing.worst == pytest.approx(0.9)
    assert timing.rows == 2
    selects = [s for s in connection.statements if s.startswith("SELECT")]
    assert len(selects) == 8


def test_the_query_cache_is_switched_off_before_any_probe() -> None:
    clock = FakeClock()
    connection = TimedConnection(clock)
    perf.run_probes(connection, probes()[:1], warmups=1, runs=1, clock=clock)
    assert connection.statements[0] == "ALTER SESSION SET QUERY_CACHE = 'OFF'"


@pytest.mark.parametrize(
    ("median", "verdict"),
    [(0.4, perf.PASS), (1.0, perf.PASS), (1.5, perf.OVER), (2.0, perf.OVER), (2.1, perf.OVER_2X)],
)
def test_verdict_compares_the_median_with_the_target(median: float, verdict: str) -> None:
    probe = perf.Probe("x", "GAME", "SELECT 1", 1.0, perf.PLAN)
    assert perf.verdict(perf.Timing(probe, median, median, 1)) == verdict


def test_report_lists_every_probe_and_counts_the_misses() -> None:
    fast = perf.Probe("fast", "GAME", "SELECT 1", 1.0, perf.PLAN)
    slow = perf.Probe("slow", "TEAM", "SELECT 1", 1.0, perf.DERIVED)
    timings = [perf.Timing(fast, 0.2, 0.3, 10), perf.Timing(slow, 3.5, 4.0, 5)]
    lines = perf.report(timings)
    text = "\n".join(lines)
    assert "fast" in text and "slow" in text
    assert "PASS" in text and perf.OVER_2X in text
    assert lines[-1] == "2 probes: 1 pass, 0 over target, 1 over 2x target"
    assert perf.misses(timings) == [timings[1]]
