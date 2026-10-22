"""Shared helpers of the pushdown, rule, gating, fan-out and property integration tests.

Pure SQL text and response bodies, plus small wrappers around the `Harness`; nothing here creates
database state. A test that needs the mock requests the `mock` fixture itself.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

import pyexasol

from tests.it.harness import OWNER, Harness
from tests.synthetic import fake
from vs.host import fetchlog

GAMES = "GAMES"
RULE_ID = re.compile(r"rule ([a-z0-9_.]+):")
ERROR_LINE = re.compile(r"CFBD_VS\.[A-Z_]+:[^\n]*")

GAMES_2024_ROWS = (
    # (home points, away points, venue, week)
    (55, 10, "Michigan Stadium", 1),
    (20, 56, "Mercedes-Benz Stadium", 1),
    (60, 52, "Miller Park", 2),
    (31, 28, "Michigan Stadium", 2),
    (51, 3, "Ohio Stadium", 3),
)
GAMES_FIRST_ID = 401000


def table_query(harness: Harness, table: str, where: str | None = None, columns: str = "*") -> str:
    """`SELECT <columns> FROM <vs>.<table> [WHERE <where>]`."""
    sql = f"SELECT {columns} FROM {harness.vs}.{table}"
    return f"{sql} WHERE {where}" if where else sql


def error_of(harness: Harness, sql: str, *, user: str = OWNER) -> str:
    """The error text of a statement that must fail; fails the test when it succeeds."""
    try:
        harness.query(sql, user=user)
    except pyexasol.ExaError as error:
        return str(getattr(error, "message", error))
    raise AssertionError(f"expected an error from: {sql}")


def explain_error_of(harness: Harness, sql: str) -> str:
    """The error text of `EXPLAIN VIRTUAL <sql>`, which must fail."""
    return error_of(harness, f"EXPLAIN VIRTUAL {sql}")


def keys_of(harness: Harness, sql: str) -> tuple[str, ...]:
    """The slice keys `EXPLAIN VIRTUAL` plans for a query (no API call)."""
    return harness.explain(sql).slice_keys


def adapter_line(message: str) -> str:
    """The first `CFBD_VS.<TABLE>: ...` line of an error text."""
    found = ERROR_LINE.search(message)
    if found is None:
        raise AssertionError(f"no CFBD_VS error line in: {message}")
    return found.group(0)


def rule_ids(message: str) -> list[str]:
    """The ids of the rules an error text names."""
    return RULE_ID.findall(message)


def explain_manifests(harness: Harness, sql: str) -> list[fetchlog.Manifest]:
    """One manifest per pushdown of `EXPLAIN VIRTUAL <sql>`; a statement may read several tables."""
    statement = harness.owner.execute(f"EXPLAIN VIRTUAL {sql}")
    position = list(statement.columns()).index("PUSHDOWN_SQL")
    return [fetchlog.parse_manifest(str(row[position])) for row in statement.fetchall()]


def games_slice_key() -> str:
    """The slice key of the 2024 games request with the explicit `seasonType` default."""
    return fake.slice_key_for(GAMES)


def games_2024_body() -> list[dict[str, Any]]:
    """A `/games` response of five games with varied points, venues and weeks."""
    template = fake.body_for("/games", {"year": "2024", "seasonType": "both"})[0]
    games = []
    for index, (home, away, venue, week) in enumerate(GAMES_2024_ROWS):
        game = dict(template)
        game.update(
            id=GAMES_FIRST_ID + index,
            week=week,
            homePoints=home,
            awayPoints=away,
            venue=venue,
        )
        games.append(game)
    return games


def replay_games_2024(harness: Harness) -> None:
    """Record the five-game 2024 response and switch the virtual schema to replay."""
    harness.write_recording(games_slice_key(), games_2024_body())
    harness.use_replay()


def games_keys(years: Sequence[int], weeks: Sequence[int] | None = None) -> set[str]:
    """The `/games` slice keys of a year x week product with `seasonType=both`."""
    if weeks is None:
        return {f"/games?seasonType=both&year={year}" for year in years}
    return {f"/games?seasonType=both&week={week}&year={year}" for year in years for week in weeks}
