"""The performance smoke of the model (`make model-perf`): probes, timing loop, verdicts, report.

The probes are queries over the model views of one namespace (the scaled league in `PERF_`). Each
carries a target in seconds: `PLAN` targets are the ones the plan's Requirements name, `DERIVED`
targets put the other probes in the nearest class of those (one season or one as-of point: 1 s;
a full scan of a narrow view: 2 s; a wide unfiltered report: 3 s).

`time_probe` runs warm-ups first and then the measured runs, with the query cache off (the caller
switches it off for the session through `run_probes`), and reports the median and the worst run.
A probe passes when its median is within the target, is over it up to twice the target, and
needs a restructured view beyond that (plan decision [2]).
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from vs.cfbd_vs.sqlrender import qualified_name
from vs.host.namespace import Namespace

PLAN = "plan"
DERIVED = "derived"
PASS = "PASS"
OVER = "OVER"
OVER_2X = "OVER 2X"

ONE_POINT_SECONDS = 1.0
FULL_SCAN_SECONDS = 2.0
FULL_REPORT_SECONDS = 3.0
OVER_FACTOR = 2.0

DEFAULT_SEASON = 2019
DEFAULT_WEEK_SEQ = 9
DEFAULT_WARMUPS = 3
DEFAULT_RUNS = 5
QUERY_CACHE_OFF = "ALTER SESSION SET QUERY_CACHE = 'OFF'"

Clock = Callable[[], float]


@dataclass(frozen=True)
class Probe:
    """One timed query: what it measures, the SQL, and the target it is judged against."""

    name: str
    view: str
    sql: str
    target_seconds: float
    basis: str


@dataclass(frozen=True)
class Timing:
    """The result of timing one probe."""

    probe: Probe
    median: float
    worst: float
    rows: int


def probes(
    namespace: Namespace, *, season: int = DEFAULT_SEASON, week_seq: int = DEFAULT_WEEK_SEQ
) -> list[Probe]:
    """The probes over `namespace.model_schema`: one season, one point and unfiltered shapes."""
    schema = namespace.model_schema

    def view(name: str) -> str:
        return qualified_name(schema, name)

    def per_season(name: str) -> Probe:
        return Probe(
            f"{name} one season",
            name,
            f'SELECT * FROM {view(name)} WHERE "SEASON" = {int(season)}',
            ONE_POINT_SECONDS,
            DERIVED,
        )

    def grouped(name: str, target: float, basis: str) -> Probe:
        return Probe(
            f"{name} grouped by season",
            name,
            f'SELECT "SEASON", COUNT(*) FROM {view(name)} GROUP BY "SEASON"',
            target,
            basis,
        )

    def full(name: str, target: float) -> Probe:
        return Probe(f"{name} in full", name, f"SELECT * FROM {view(name)}", target, DERIVED)

    return [
        grouped("GAME", FULL_SCAN_SECONDS, PLAN),
        per_season("GAME"),
        per_season("TEAM_GAME"),
        grouped("TEAM_GAME", FULL_REPORT_SECONDS, DERIVED),
        full("CONFERENCE_CHAMPIONSHIP_GAME", FULL_SCAN_SECONDS),
        per_season("CONFERENCE_CHAMPIONSHIP_GAME"),
        full("SEASON_WEEK", FULL_SCAN_SECONDS),
        per_season("SEASON_WEEK"),
        full("SEASON", FULL_SCAN_SECONDS),
        full("TEAM", FULL_REPORT_SECONDS),
        full("CONFERENCE", FULL_SCAN_SECONDS),
        full("VENUE", FULL_SCAN_SECONDS),
        per_season("RANKING"),
        full("RANKING", FULL_REPORT_SECONDS),
        Probe(
            "as-of prototype, one point",
            "SEASON_WEEK",
            _prototype(view("SEASON_WEEK"), view("GAME"), season, week_seq),
            ONE_POINT_SECONDS,
            PLAN,
        ),
        Probe(
            "as-of prototype, every point of one season",
            "SEASON_WEEK",
            _prototype(view("SEASON_WEEK"), view("GAME"), season, None),
            FULL_REPORT_SECONDS,
            DERIVED,
        ),
    ]


def _prototype(weeks: str, games: str, season: int, week_seq: int | None) -> str:
    """One as-of point (or every point of the season) of `SEASON_WEEK` joined to `GAME`.

    Written the way the as-of views will be queried: the whole point-by-game join is a derived
    table, and the filter on its `AS_OF_*` columns sits above the join, which is where Exasol can
    carry it to both sides of the join on the season. The participants filter and the status and
    masking columns follow the `GAME_AS_OF` sketch of the plan (Key Interfaces § SQL file
    conventions).
    """
    week_filter = "" if week_seq is None else f' AND "AS_OF_WEEK_SEQ" = {int(week_seq)}'
    return f"""
SELECT * FROM (
  WITH "POINTS" AS (
    SELECT w."SEASON" AS "AS_OF_SEASON",
           w."WEEK_SEQ" AS "AS_OF_WEEK_SEQ",
           LEAD(w."WEEK_SEQ") OVER (PARTITION BY w."SEASON" ORDER BY w."WEEK_SEQ")
             AS "NEXT_WEEK_SEQ",
           LEAD(w."FIRST_GAME_AT") OVER (PARTITION BY w."SEASON" ORDER BY w."WEEK_SEQ")
             AS "CUTOFF_AT",
           MAX(CASE WHEN w."SEASON_TYPE" = 'regular' THEN w."WEEK_SEQ" END)
             OVER (PARTITION BY w."SEASON") AS "LAST_REGULAR_WEEK_SEQ"
      FROM {weeks} w
  )
  SELECT p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", g."GAME_ID", g."PARTICIPANTS_RULE",
         CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" AND g."IS_COMPLETED" THEN 'PLAYED'
              WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" THEN 'NOT_PLAYED'
              WHEN g."WEEK_SEQ" = p."NEXT_WEEK_SEQ" THEN 'NEXT' ELSE 'REMAINING' END AS "STATUS",
         CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ" THEN g."MARGIN" END AS "MARGIN",
         CASE WHEN g."WEEK_SEQ" <= COALESCE(p."NEXT_WEEK_SEQ", p."AS_OF_WEEK_SEQ")
              THEN g."HOME_PREGAME_ELO" END AS "HOME_PREGAME_ELO",
         CASE WHEN g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ"
                OR (g."WEEK_SEQ" = p."NEXT_WEEK_SEQ" AND g."PREGAME_WP_FETCHED_AT" < p."CUTOFF_AT")
              THEN g."PREGAME_SPREAD" END AS "PREGAME_SPREAD"
    FROM "POINTS" p
    JOIN {games} g ON g."SEASON" = p."AS_OF_SEASON"
   WHERE (g."PARTICIPANTS_RULE" = 'FIXED'
      OR (g."PARTICIPANTS_RULE" = 'CONF_CHAMPIONSHIP'
          AND g."WEEK_SEQ" <= COALESCE(p."NEXT_WEEK_SEQ", p."AS_OF_WEEK_SEQ"))
      OR (g."PARTICIPANTS_RULE" = 'SELECTION'
          AND p."AS_OF_WEEK_SEQ" >= p."LAST_REGULAR_WEEK_SEQ")
      OR (g."PARTICIPANTS_RULE" = 'PREVIOUS_ROUND'
          AND g."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ"))
) WHERE "AS_OF_SEASON" = {int(season)}{week_filter}
""".strip()


def time_probe(
    connection: Any,
    probe: Probe,
    *,
    warmups: int = DEFAULT_WARMUPS,
    runs: int = DEFAULT_RUNS,
    clock: Clock = time.perf_counter,
) -> Timing:
    """Run `probe` `warmups` times unmeasured, then `runs` times measured (fetching the rows)."""
    for _ in range(warmups):
        connection.execute(probe.sql).fetchall()
    seconds: list[float] = []
    rows = 0
    for _ in range(runs):
        started = clock()
        rows = len(connection.execute(probe.sql).fetchall())
        seconds.append(clock() - started)
    return Timing(probe, statistics.median(seconds), max(seconds), rows)


def run_probes(
    connection: Any,
    to_run: Sequence[Probe],
    *,
    warmups: int = DEFAULT_WARMUPS,
    runs: int = DEFAULT_RUNS,
    clock: Clock = time.perf_counter,
    progress: Callable[[Timing], None] | None = None,
) -> list[Timing]:
    """Switch the query cache off for the session and time every probe in order."""
    connection.execute(QUERY_CACHE_OFF)
    timings = []
    for probe in to_run:
        timing = time_probe(connection, probe, warmups=warmups, runs=runs, clock=clock)
        timings.append(timing)
        if progress is not None:
            progress(timing)
    return timings


def verdict(timing: Timing) -> str:
    """PASS within the target, OVER up to twice the target, OVER 2X beyond."""
    target = timing.probe.target_seconds
    if timing.median <= target:
        return PASS
    return OVER if timing.median <= OVER_FACTOR * target else OVER_2X


def misses(timings: Sequence[Timing]) -> list[Timing]:
    """The timings whose median is over the target (OVER or OVER 2X)."""
    return [timing for timing in timings if verdict(timing) != PASS]


def report(timings: Sequence[Timing]) -> list[str]:
    """A table of the timings and a one-line summary."""
    header = ("probe", "view", "basis", "median s", "worst s", "target s", "rows", "verdict")
    body = [
        (
            t.probe.name,
            t.probe.view,
            t.probe.basis,
            f"{t.median:.3f}",
            f"{t.worst:.3f}",
            f"{t.probe.target_seconds:.1f}",
            str(t.rows),
            verdict(t),
        )
        for t in timings
    ]
    widths = [max(len(row[i]) for row in (header, *body)) for i in range(len(header))]
    lines = ["  ".join(cell.ljust(width) for cell, width in zip(header, widths, strict=True))]
    lines += [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in body
    ]
    verdicts = [verdict(t) for t in timings]
    lines.append(
        f"{len(timings)} probes: {verdicts.count(PASS)} pass, {verdicts.count(OVER)} over target, "
        f"{verdicts.count(OVER_2X)} over 2x target"
    )
    return lines
