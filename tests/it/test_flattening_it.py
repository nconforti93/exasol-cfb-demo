"""Integration tests of cfbd-vs/flattening: response bodies become typed rows in `TEST_CFBD_VS`.

Every test answers the request from a replay recording, so there is no network, no mock hit and
no CFBD call. Bodies are the synthetic responses of `tests.synthetic.fake`, changed where a
scenario needs a specific value. `stage` plans the query (`EXPLAIN VIRTUAL`, no call), records the
body under the planned slice key and switches the schema to replay.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple

import pyexasol
import pytest
import yaml

from tests.it.harness import Harness
from tests.it.support import split_slice_key, where_clause
from tests.synthetic import fake
from vs.cfbd_vs import naming
from vs.cfbd_vs.adapter import data_type
from vs.cfbd_vs.catalog import Column, Table
from vs.cfbd_vs.flatten import data_columns, flatten
from vs.cfbd_vs.rules import KNOWN_FEATURES
from vs.cfbd_vs.sqlrender import column_sql_types

pytestmark = pytest.mark.it

OVERRIDES_PATH = Path(__file__).resolve().parents[2] / "vs" / "catalog-overrides.yaml"
PREVIEW_PATH = "/games/{gameId}/preview"
WRONG_TYPED_TEXT = "not-a-valid-value"
TYPE_KEYS = ("type", "precision", "scale", "size")
UTC_NOON = datetime(2024, 8, 24, 16, 0, 0)
META_PREFIXES = ("P_", "REQ_")
POSITION_AND_KEY_COLUMNS = frozenset({"ROW_NO", "ID", "GAME_ID"})

Row = dict[str, Any]


class Staged(NamedTuple):
    where: str
    slice_key: str


def stage(
    harness: Harness,
    table: str,
    filters: dict[str, Any] | None = None,
    body: Any = None,
    edit: Callable[[Any], None] | None = None,
) -> Staged:
    """Record a body for the slice a filter plans, then use replay; plans without any call.

    The body is `body`, or the synthetic response of the slice, changed in place by `edit`.
    """
    chosen = fake.minimal_filter(table) if filters is None else filters
    where = where_clause(table, chosen)
    manifest = harness.explain(f"SELECT * FROM {harness.vs}.{table} WHERE {where}")
    assert len(manifest.slice_keys) == 1
    slice_key = manifest.slice_keys[0]
    recorded = response(slice_key) if body is None else body
    if edit is not None:
        edit(recorded)
    harness.write_recording(slice_key, recorded)
    harness.use_replay()
    return Staged(where, slice_key)


def as_datetime(value: Any) -> datetime:
    """A TIMESTAMP value; pyexasol hands timestamps over as text by default."""
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def declared_size(sql_type: str) -> int:
    found = re.search(r"\((\d+)\)", sql_type)
    assert found is not None, sql_type
    return int(found[1])


def response(slice_key: str) -> Any:
    """The synthetic response of a slice."""
    return fake.body_for(*split_slice_key(slice_key))


def rows_of(harness: Harness, table: str, staged: Staged, order: str = "ROW_NO") -> list[Row]:
    sql = f"SELECT * FROM {harness.vs}.{table} WHERE {staged.where} ORDER BY {order}"
    return harness.query_dicts(sql)


def database_column_names(harness: Harness, table: str) -> list[str]:
    rows = harness.query(
        "SELECT COLUMN_NAME FROM EXA_ALL_COLUMNS "
        "WHERE COLUMN_SCHEMA = {vs} AND COLUMN_TABLE = {table} ORDER BY COLUMN_ORDINAL_POSITION",
        {"vs": harness.vs, "table": table},
    )
    return [str(row[0]) for row in rows]


def test_nested_objects_become_prefixed_columns(harness: Harness) -> None:
    """Scenario flattening: Nested objects become prefixed columns."""
    staged = stage(harness, "RATINGS_SP", {"year": 2024})
    body = response(staged.slice_key)
    assert len(body) >= 2

    rows = harness.query_dicts(
        "SELECT ROW_NO, OFFENSE_RATING, DEFENSE_HAVOC_TOTAL "
        f"FROM {harness.vs}.RATINGS_SP WHERE P_YEAR = 2024 ORDER BY ROW_NO"
    )

    assert [row["ROW_NO"] for row in rows] == list(range(1, len(body) + 1))
    for row, element in zip(rows, body, strict=True):
        assert row["OFFENSE_RATING"] == pytest.approx(element["offense"]["rating"])
        assert row["DEFENSE_HAVOC_TOTAL"] == pytest.approx(element["defense"]["havoc"]["total"])


def test_arrays_of_objects_become_child_tables(harness: Harness) -> None:
    """Scenario flattening: Arrays of objects become child tables."""
    staged = stage(harness, "RANKINGS", {"year": 2024})
    body = response(staged.slice_key)

    weeks = rows_of(harness, "RANKINGS", staged)
    polls = rows_of(harness, "RANKINGS_POLLS", staged, "ROW_NO, POLLS_NO")
    ranks = rows_of(harness, "RANKINGS_POLLS_RANKS", staged, "ROW_NO, POLLS_NO, RANKS_NO")

    assert len(weeks) == len(body)
    assert not [name for name in weeks[0] if name.startswith("POLLS")]
    assert [(r["SEASON"], r["SEASON_TYPE"], r["WEEK"]) for r in weeks] == [
        (week["season"], week["seasonType"], week["week"]) for week in body
    ]
    expected_polls = [
        (week_no, poll_no, week, poll)
        for week_no, week in enumerate(body, 1)
        for poll_no, poll in enumerate(week["polls"], 1)
    ]
    assert len(polls) == len(expected_polls)
    for row, (week_no, poll_no, week, poll) in zip(polls, expected_polls, strict=True):
        assert (row["ROW_NO"], row["POLLS_NO"]) == (week_no, poll_no)
        assert (row["SEASON"], row["SEASON_TYPE"], row["WEEK"]) == (
            week["season"],
            week["seasonType"],
            week["week"],
        )
        assert row["POLL"] == poll["poll"]
    expected_ranks = [
        (week_no, poll_no, rank_no, poll, rank)
        for week_no, week in enumerate(body, 1)
        for poll_no, poll in enumerate(week["polls"], 1)
        for rank_no, rank in enumerate(poll["ranks"], 1)
    ]
    assert len(ranks) == len(expected_ranks)
    for row, (week_no, poll_no, rank_no, poll, rank) in zip(ranks, expected_ranks, strict=True):
        assert (row["ROW_NO"], row["POLLS_NO"], row["RANKS_NO"]) == (week_no, poll_no, rank_no)
        assert row["POLL"] == poll["poll"]
        assert row["TEAM_ID"] == rank["teamId"]


def test_child_rows_join_exactly_one_parent(harness: Harness) -> None:
    """Scenario flattening: Child rows join to exactly one parent row."""
    staged = stage(harness, "RANKINGS", {"year": 2024})
    body = response(staged.slice_key)
    polls = rows_of(harness, "RANKINGS_POLLS", staged)
    ranks = rows_of(harness, "RANKINGS_POLLS_RANKS", staged)

    parents = [(p["REQ_SLICE"], p["ROW_NO"], p["POLLS_NO"]) for p in polls]
    assert len(set(parents)) == len(parents)
    per_parent = dict.fromkeys(parents, 0)
    for rank in ranks:
        key = (rank["REQ_SLICE"], rank["ROW_NO"], rank["POLLS_NO"])
        assert parents.count(key) == 1
        per_parent[key] += 1
    for week_no, week in enumerate(body, 1):
        for poll_no, poll in enumerate(week["polls"], 1):
            assert per_parent[(staged.slice_key, week_no, poll_no)] == len(poll["ranks"])

    joined = harness.query(
        f"SELECT COUNT(*) FROM {harness.vs}.RANKINGS_POLLS_RANKS rk "
        f"JOIN {harness.vs}.RANKINGS_POLLS pl "
        "ON rk.REQ_SLICE = pl.REQ_SLICE AND rk.ROW_NO = pl.ROW_NO AND rk.POLLS_NO = pl.POLLS_NO "
        "WHERE rk.P_YEAR = 2024 AND pl.P_YEAR = 2024"
    )
    assert joined == [(len(ranks),)]


def test_hoisted_endpoint_exposes_array_elements_directly(harness: Harness) -> None:
    """Scenario flattening: Hoisted endpoints expose the array elements directly."""
    staged = stage(harness, "GAMES_TEAMS")
    body = response(staged.slice_key)

    teams = rows_of(harness, "GAMES_TEAMS", staged, "ROW_NO, TEAMS_NO")
    stats = rows_of(harness, "GAMES_TEAMS_STATS", staged, "ROW_NO, TEAMS_NO, STATS_NO")

    expected_teams = [
        (game_no, team_no, game["id"], team)
        for game_no, game in enumerate(body, 1)
        for team_no, team in enumerate(game["teams"], 1)
    ]
    assert len(teams) == len(expected_teams)
    for row, (game_no, team_no, game_id, team) in zip(teams, expected_teams, strict=True):
        assert (row["ROW_NO"], row["TEAMS_NO"], row["GAME_ID"]) == (game_no, team_no, game_id)
        assert row["TEAM_ID"] == team["teamId"]
    expected_stats = [
        (game_no, team_no, stat_no, game["id"], stat)
        for game_no, game in enumerate(body, 1)
        for team_no, team in enumerate(game["teams"], 1)
        for stat_no, stat in enumerate(team["stats"], 1)
    ]
    assert len(stats) == len(expected_stats)
    for row, (game_no, team_no, stat_no, game_id, stat) in zip(stats, expected_stats, strict=True):
        assert (row["ROW_NO"], row["TEAMS_NO"], row["STATS_NO"]) == (game_no, team_no, stat_no)
        assert row["GAME_ID"] == game_id
        assert row["CATEGORY"] == stat["category"]


def test_hoisted_endpoint_has_no_identifier_only_table(harness: Harness) -> None:
    """Scenario flattening: Hoisted endpoints expose the array elements directly (no id table)."""
    catalog = fake.catalog()
    hoisted = [table for table in catalog.tables if table.endpoint == "/games/teams"]
    rows = harness.query(
        "SELECT COLUMN_TABLE, COLUMN_NAME FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA = {vs}",
        {"vs": harness.vs},
    )
    names: dict[str, set[str]] = {}
    for table, column in rows:
        names.setdefault(str(table), set()).add(str(column))

    assert sorted(table.name for table in hoisted) == ["GAMES_TEAMS", "GAMES_TEAMS_STATS"]
    assert fake.catalog().table("GAMES_TEAMS").hoisted
    identifier_only = sorted(
        table
        for table, columns in names.items()
        if {c for c in columns if not c.startswith(META_PREFIXES)} <= POSITION_AND_KEY_COLUMNS
    )
    assert identifier_only == []


def test_scalar_arrays_become_json_text(harness: Harness) -> None:
    """Scenario flattening: Arrays of scalars become JSON text."""

    def edit(body: Any) -> None:
        body[0]["homeLineScores"] = [7, 0, 14, 3]
        body[1]["homeLineScores"] = []

    staged = stage(harness, "GAMES", {"year": 2024}, edit=edit)

    rows = rows_of(harness, "GAMES", staged)

    assert [row["HOME_LINE_SCORES"] for row in rows] == ["[7,0,14,3]", "[]"]


def test_large_objects_curated_plus_raw_json(harness: Harness) -> None:
    """Scenario flattening: Very large objects keep a curated subset plus the raw JSON."""
    catalog = fake.catalog()
    staged = stage(harness, "GAMES_PREVIEW", {"gameId": 1001})
    body = response(staged.slice_key)
    overrides = yaml.safe_load(OVERRIDES_PATH.read_text(encoding="utf-8"))
    curated = overrides["endpoints"][PREVIEW_PATH]["curated"]
    prefixes = [naming.column_name_from_path(entry.split(".")) for entry in curated]

    rows = rows_of(harness, "GAMES_PREVIEW", staged)
    names = database_column_names(harness, "GAMES_PREVIEW")

    assert len(rows) == 1
    assert names == [column.name for column in catalog.table("GAMES_PREVIEW").columns]
    assert json.loads(rows[0]["RAW_JSON"]) == body
    response_columns = [
        name
        for name in names
        if name not in ("ROW_NO", "RAW_JSON") and not name.startswith(META_PREFIXES)
    ]
    assert response_columns
    for name in response_columns:
        assert any(name == p or name.startswith(p + "_") for p in prefixes), name
    for prefix in prefixes:
        assert any(name == prefix or name.startswith(prefix + "_") for name in response_columns)
    on_endpoint = [table.name for table in catalog.tables if table.endpoint == PREVIEW_PATH]
    assert on_endpoint == ["GAMES_PREVIEW"]


def test_date_time_values_become_utc_timestamps(harness: Harness) -> None:
    """Scenario flattening: Date-time values become UTC timestamps."""

    def edit(body: Any) -> None:
        body[0]["startDate"] = "2024-08-24T16:00:00.000Z"
        body[1]["startDate"] = "2024-08-24T18:00:00+02:00"

    staged = stage(harness, "GAMES", {"year": 2024}, edit=edit)

    rows = rows_of(harness, "GAMES", staged)

    assert [as_datetime(row["START_DATE"]) for row in rows] == [UTC_NOON, UTC_NOON]


def test_missing_and_null_fields_are_null(harness: Harness) -> None:
    """Scenario flattening: Missing and null fields become NULL."""

    def edit(body: Any) -> None:
        del body[0]["attendance"]
        body[0]["venue"] = None

    staged = stage(harness, "GAMES", {"year": 2024}, edit=edit)

    rows = rows_of(harness, "GAMES", staged)

    assert len(rows) == len(response(staged.slice_key))
    assert rows[0]["ATTENDANCE"] is None
    assert rows[0]["VENUE"] is None
    assert rows[1]["ATTENDANCE"] is not None
    assert rows[1]["VENUE"] is not None


def test_object_root_response_is_one_row(harness: Harness) -> None:
    """Scenario flattening: Object-root responses become one row."""
    filters = {"team1": "Georgia", "team2": "Alabama"}
    staged = stage(harness, "TEAMS_MATCHUP", filters)
    body = response(staged.slice_key)

    matchups = rows_of(harness, "TEAMS_MATCHUP", staged)
    games = rows_of(harness, "TEAMS_MATCHUP_GAMES", staged, "GAMES_NO")

    assert len(matchups) == 1
    assert matchups[0]["ROW_NO"] == 1
    assert (matchups[0]["TEAM1"], matchups[0]["TEAM2"]) == ("Georgia", "Alabama")
    assert len(games) == len(body["games"]) >= 2
    assert [row["GAMES_NO"] for row in games] == list(range(1, len(games) + 1))
    assert {row["ROW_NO"] for row in games} == {1}
    assert [row["DATE_"] for row in games] == [game["date"] for game in body["games"]]


@pytest.mark.parametrize(
    "table, filters",
    [("CALENDAR", None), ("RANKINGS_POLLS_RANKS", {"year": 2024})],
    ids=["endpoint table", "child table"],
)
def test_empty_response_returns_no_rows(
    harness: Harness, table: str, filters: dict[str, Any] | None
) -> None:
    """Scenario flattening: Empty responses return no rows."""
    staged = stage(harness, table, filters, [])

    assert rows_of(harness, table, staged) == []
    count = harness.query(f"SELECT COUNT(*) FROM {harness.vs}.{table} WHERE {staged.where}")
    assert count == [(0,)]


def first_value_column(table: Table, *, varchar: bool) -> Column:
    for column in data_columns(table):
        wanted_type = not varchar or (column.sql_type or "").startswith("VARCHAR")
        if column.kind == "value" and not column.json and wanted_type:
            return column
    raise LookupError(table.name)


INVALID_VALUE_CASES = [
    ("GAMES", "oversized"),
    ("RATINGS_SP", "oversized"),
    ("RANKINGS_POLLS_RANKS", "oversized"),
    ("TEAMS_MATCHUP", "oversized"),
    ("GAMES", "invalid type"),
    ("RATINGS_SP", "invalid type"),
    ("RANKINGS_POLLS_RANKS", "invalid type"),
    ("CALENDAR", "invalid type"),
    ("TEAMS_MATCHUP", "invalid type"),
]
TYPE_MESSAGES = {
    "DECIMAL": "expected an integer, got string",
    "DOUBLE": "expected a number, got string",
    "BOOLEAN": "expected a boolean, got string",
    "TIMESTAMP": "is not an ISO-8601 date-time",
    "VARCHAR": "expected a string, got integer",
}


@pytest.mark.parametrize("table, kind", INVALID_VALUE_CASES)
def test_values_that_do_not_fit_fail_loudly(harness: Harness, table: str, kind: str) -> None:
    """Scenario flattening: Values that do not fit their column fail loudly."""
    catalog_table = fake.catalog().table(table)
    if kind == "oversized":
        column = first_value_column(catalog_table, varchar=True)
        body = fake.oversized_variant(table)
        size = declared_size(column.sql_type or "")
        expected = f"string of length {size + 1} exceeds {size} characters"
        leaked = "x" * (size + 1)
    else:
        column = first_value_column(catalog_table, varchar=False)
        body = fake.invalid_type_variant(table)
        base = (column.sql_type or "").split("(")[0].split(" ")[0]
        expected = TYPE_MESSAGES[base]
        leaked = WRONG_TYPED_TEXT
    staged = stage(harness, table, None, body)

    with pytest.raises(pyexasol.ExaError) as failure:
        rows_of(harness, table, staged)

    message = str(failure.value)
    assert f"CFBD_VS.{table}" in message
    assert f"column {column.name} " in message
    assert expected in message
    assert leaked not in message


def _is_timestamp_text(value: Any) -> bool:
    try:
        as_datetime(value)
    except ValueError:
        return False
    return True


def check_value(column: str, sql_type: str, value: Any) -> str | None:
    """A problem text when `value` does not fit the declared column type, else None."""
    if value is None:
        return None
    base = sql_type.split("(")[0].split(" ")[0]
    if base == "DECIMAL":
        fits = isinstance(value, int | Decimal) and not isinstance(value, bool)
    elif base == "DOUBLE":
        fits = isinstance(value, float)
    elif base == "BOOLEAN":
        fits = isinstance(value, bool)
    elif base == "TIMESTAMP":
        fits = isinstance(value, datetime) or _is_timestamp_text(value)
    else:
        fits = isinstance(value, str) and len(value) <= declared_size(sql_type)
    return None if fits else f"{column}: {type(value).__name__} does not fit {sql_type}"


def sweep_table(harness: Harness, table: Table) -> list[str]:
    """Query one table in replay mode; return everything that is not as the spec requires."""
    where = where_clause(table.name, fake.minimal_filter(table))
    slice_key = fake.slice_key_for(table)
    expected = len(flatten(table, response(slice_key)))
    problems: list[str] = []
    try:
        condition = f" WHERE {where}" if where else ""
        statement = harness.owner.execute(f"SELECT * FROM {harness.vs}.{table.name}{condition}")
        rows = statement.fetchall()
        actual = statement.columns()
    except pyexasol.ExaError as error:
        text = " ".join(str(error).split())
        return [f"{table.name}: query failed: {text[:300]} ... {text[-300:]}"]
    declared = column_sql_types(table)
    if list(actual) != [name for name, _ in declared]:
        return [f"{table.name}: columns {list(actual)[:3]}... differ from the catalog"]
    if expected < 1:
        problems.append(f"{table.name}: the synthetic response has no element for this table")
    if len(rows) != expected:
        problems.append(f"{table.name}: {len(rows)} rows, expected {expected}")
    for name, sql_type in declared:
        wanted, found = data_type(sql_type), actual[name]
        keys = [key for key in TYPE_KEYS if key in wanted]
        if {key: wanted[key] for key in keys} != {key: found.get(key) for key in keys}:
            problems.append(f"{table.name}.{name}: type {found} is not {sql_type}")
    for position, (name, sql_type) in enumerate(declared):
        for row in rows:
            problem = check_value(f"{table.name}.{name}", sql_type, row[position])
            if problem:
                problems.append(problem)
                break
    return problems


def test_all_tables_replay_sweep(harness: Harness) -> None:
    """Scenario flattening: Every table returns typed rows for a schema-conforming response."""
    tables = fake.catalog().tables
    harness.set_property("API_FEATURES", ",".join(KNOWN_FEATURES))
    harness.write_recordings(sorted({fake.slice_key_for(table) for table in tables}))
    harness.use_replay()

    problems = [problem for table in tables for problem in sweep_table(harness, table)]

    assert problems == []
