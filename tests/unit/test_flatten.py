import json
import math
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from vs.cfbd_vs import naming
from vs.cfbd_vs.catalog import Catalog, Column, Table
from vs.cfbd_vs.flatten import FlattenError, data_columns, flatten
from vs.gen.overrides import ChildOverrides, EndpointOverrides, FieldOverride, Hoist, Overrides
from vs.gen.schema_walk import ArrayNode, JsonValue, Node, ObjectNode, read_operations
from vs.gen.tables import derive_tables

INT = {"type": "integer", "format": "int32"}
NUM = {"type": "number", "format": "double"}
STR = {"type": "string"}
BOOL = {"type": "boolean"}
DATE_TIME = {"type": "string", "format": "date-time"}


def ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def obj(**properties: Any) -> dict[str, Any]:
    return {"type": "object", "properties": properties}


def arr(items: Any) -> dict[str, Any]:
    return {"type": "array", "items": items}


def get_op(schema: Any, *params: str) -> dict[str, Any]:
    parameters = [{"name": p, "in": "query", "schema": INT} for p in params]
    return {
        "get": {
            "parameters": parameters,
            "responses": {"200": {"content": {"application/json": {"schema": schema}}}},
        }
    }


SCHEMAS: dict[str, Any] = {
    "PollWeek": obj(season=INT, seasonType=STR, week=INT, polls=arr(ref("Poll"))),
    "Poll": obj(poll=STR, isFinal=BOOL, ranks=arr(ref("PollRank"))),
    "PollRank": obj(rank=INT, school=STR),
    "TeamSP": obj(team=STR, offense=obj(rating=NUM), defense=obj(havoc=obj(total=NUM))),
    "GameTeams": obj(id=INT, teams=arr(ref("Side"))),
    "Side": obj(team=STR, stats=arr(obj(category=STR, stat=STR))),
    "Matchup": obj(
        team1=STR,
        games=arr(obj(date=DATE_TIME, homeLineScores=arr(INT), winner=STR)),
    ),
    "Preview": obj(id=INT, home=obj(name=STR, rank=INT), notes=arr(obj(text=STR)), extra=STR),
}
PATHS: dict[str, Any] = {
    "/rankings": get_op(arr(ref("PollWeek")), "year"),
    "/ratings/sp": get_op(arr(ref("TeamSP")), "year"),
    "/games/teams": get_op(arr(ref("GameTeams")), "year"),
    "/teams/matchup": get_op(ref("Matchup")),
    "/stats/categories": get_op(arr(STR)),
    "/preview": get_op(ref("Preview")),
}
OVERRIDES = Overrides(
    endpoints={
        "/games/teams": EndpointOverrides(
            "/games/teams", hoist=Hoist(array=("teams",), carry={("id",): "GAME_ID"})
        ),
        "/preview": EndpointOverrides(
            "/preview", curated=(("id",), ("home",), ("notes",)), raw_json=True
        ),
        "/teams/matchup": EndpointOverrides(
            "/teams/matchup",
            fields={("games", "winner"): FieldOverride(type=None, size=5, rename=None)},
        ),
    },
    children={
        "RANKINGS_POLLS": ChildOverrides("RANKINGS_POLLS", (("season",), ("week",))),
        "RANKINGS_POLLS_RANKS": ChildOverrides(
            "RANKINGS_POLLS_RANKS", (("season",), ("seasonType",), ("week",), ("polls", "poll"))
        ),
        "GAMES_TEAMS_STATS": ChildOverrides("GAMES_TEAMS_STATS", (("id",), ("teams", "team"))),
    },
    acknowledged=(),
    excluded=(),
)


@pytest.fixture(scope="module")
def tables() -> dict[str, Table]:
    document = {"paths": PATHS, "components": {"schemas": SCHEMAS}}
    derived = derive_tables(read_operations(document), OVERRIDES, frozenset({"DATE", "VALUE"}))
    catalog = Catalog("h" * 64, "t", "1", (), derived)
    restored = Catalog.from_json(catalog.to_json(indent=None))
    return {t.name: t for t in restored.tables}


def rows(table: Table, body: Any) -> list[dict[str, Any]]:
    names = [c.name for c in data_columns(table)]
    result = flatten(table, body)
    assert all(len(row) == len(names) for row in result)
    return [dict(zip(names, row, strict=True)) for row in result]


RANKINGS_BODY = [
    {
        "season": 2024,
        "seasonType": "regular",
        "week": 1,
        "polls": [
            {
                "poll": "AP Top 25",
                "isFinal": False,
                "ranks": [
                    {"rank": 1, "school": "Georgia"},
                    {"rank": 2, "school": "Ohio State"},
                ],
            },
            {"poll": "Coaches Poll", "isFinal": None, "ranks": [{"rank": 1, "school": "Georgia"}]},
        ],
    },
    {"season": 2024, "seasonType": "regular", "week": 2, "polls": []},
]


def test_data_columns_are_the_response_side_prefix(tables: Mapping[str, Table]) -> None:
    polls = tables["RANKINGS_POLLS"]
    assert [c.name for c in data_columns(polls)] == [
        "ROW_NO",
        "POLLS_NO",
        "SEASON",
        "WEEK",
        "POLL",
        "IS_FINAL",
    ]
    count = len(data_columns(polls))
    assert all(c.kind in ("param", "meta") for c in polls.columns[count:])


def test_endpoint_rows_omit_child_arrays(tables: Mapping[str, Table]) -> None:
    assert rows(tables["RANKINGS"], RANKINGS_BODY) == [
        {"ROW_NO": 1, "SEASON": 2024, "SEASON_TYPE": "regular", "WEEK": 1},
        {"ROW_NO": 2, "SEASON": 2024, "SEASON_TYPE": "regular", "WEEK": 2},
    ]


def test_child_rows_carry_positions_and_parent_keys(tables: Mapping[str, Table]) -> None:
    assert rows(tables["RANKINGS_POLLS"], RANKINGS_BODY) == [
        {
            "ROW_NO": 1,
            "POLLS_NO": 1,
            "SEASON": 2024,
            "WEEK": 1,
            "POLL": "AP Top 25",
            "IS_FINAL": False,
        },
        {
            "ROW_NO": 1,
            "POLLS_NO": 2,
            "SEASON": 2024,
            "WEEK": 1,
            "POLL": "Coaches Poll",
            "IS_FINAL": None,
        },
    ]
    assert rows(tables["RANKINGS_POLLS_RANKS"], RANKINGS_BODY) == [
        {
            "ROW_NO": 1,
            "POLLS_NO": 1,
            "RANKS_NO": 1,
            "SEASON": 2024,
            "SEASON_TYPE": "regular",
            "WEEK": 1,
            "POLL": "AP Top 25",
            "RANK": 1,
            "SCHOOL": "Georgia",
        },
        {
            "ROW_NO": 1,
            "POLLS_NO": 1,
            "RANKS_NO": 2,
            "SEASON": 2024,
            "SEASON_TYPE": "regular",
            "WEEK": 1,
            "POLL": "AP Top 25",
            "RANK": 2,
            "SCHOOL": "Ohio State",
        },
        {
            "ROW_NO": 1,
            "POLLS_NO": 2,
            "RANKS_NO": 1,
            "SEASON": 2024,
            "SEASON_TYPE": "regular",
            "WEEK": 1,
            "POLL": "Coaches Poll",
            "RANK": 1,
            "SCHOOL": "Georgia",
        },
    ]


def test_rank_rows_name_their_week_and_poll_without_the_polls_table(
    tables: Mapping[str, Table],
) -> None:
    body = [
        {
            "season": 2024,
            "seasonType": "regular",
            "week": week,
            "polls": [{"poll": "AP Top 25", "ranks": [{"rank": 1, "school": f"Team {week}"}]}],
        }
        for week in (3, 4)
    ]
    ranks = rows(tables["RANKINGS_POLLS_RANKS"], body)
    assert [(r["SEASON"], r["SEASON_TYPE"], r["WEEK"], r["POLL"], r["SCHOOL"]) for r in ranks] == [
        (2024, "regular", 3, "AP Top 25", "Team 3"),
        (2024, "regular", 4, "AP Top 25", "Team 4"),
    ]


def test_every_child_row_joins_exactly_one_parent_row(tables: Mapping[str, Table]) -> None:
    polls = {(r["ROW_NO"], r["POLLS_NO"]) for r in rows(tables["RANKINGS_POLLS"], RANKINGS_BODY)}
    ranks = rows(tables["RANKINGS_POLLS_RANKS"], RANKINGS_BODY)
    assert all((r["ROW_NO"], r["POLLS_NO"]) in polls for r in ranks)
    per_poll = [sum(1 for r in ranks if r["POLLS_NO"] == n) for n in (1, 2)]
    assert per_poll == [2, 1]


def test_nested_objects_become_prefixed_columns(tables: Mapping[str, Table]) -> None:
    body = [
        {"team": "Michigan", "offense": {"rating": 31.5}, "defense": {"havoc": {"total": 0.2}}},
        {"team": "Navy", "offense": None, "defense": {}},
    ]
    assert rows(tables["RATINGS_SP"], body) == [
        {"ROW_NO": 1, "TEAM": "Michigan", "OFFENSE_RATING": 31.5, "DEFENSE_HAVOC_TOTAL": 0.2},
        {"ROW_NO": 2, "TEAM": "Navy", "OFFENSE_RATING": None, "DEFENSE_HAVOC_TOTAL": None},
    ]


def test_missing_and_null_fields_are_null(tables: Mapping[str, Table]) -> None:
    assert rows(tables["RATINGS_SP"], [{"team": None}]) == [
        {"ROW_NO": 1, "TEAM": None, "OFFENSE_RATING": None, "DEFENSE_HAVOC_TOTAL": None}
    ]


def test_hoisted_endpoint_rows_are_the_array_elements(tables: Mapping[str, Table]) -> None:
    body = [
        {
            "id": 401,
            "teams": [
                {"team": "Michigan", "stats": [{"category": "rushing", "stat": "200"}]},
                {
                    "team": "Ohio State",
                    "stats": [
                        {"category": "passing", "stat": "300"},
                        {"category": "rushing", "stat": "100"},
                    ],
                },
            ],
        },
        {"id": 402, "teams": None},
    ]
    assert rows(tables["GAMES_TEAMS"], body) == [
        {"ROW_NO": 1, "TEAMS_NO": 1, "GAME_ID": 401, "TEAM": "Michigan"},
        {"ROW_NO": 1, "TEAMS_NO": 2, "GAME_ID": 401, "TEAM": "Ohio State"},
    ]
    stats = rows(tables["GAMES_TEAMS_STATS"], body)
    assert [(r["TEAMS_NO"], r["STATS_NO"], r["GAME_ID"], r["TEAM"]) for r in stats] == [
        (1, 1, 401, "Michigan"),
        (2, 1, 401, "Ohio State"),
        (2, 2, 401, "Ohio State"),
    ]


def test_scalar_arrays_become_compact_json_text(tables: Mapping[str, Table]) -> None:
    body = {"team1": "Michigan", "games": [{"homeLineScores": [7, 0, 14, 3]}]}
    (row,) = rows(tables["TEAMS_MATCHUP_GAMES"], body)
    assert row["HOME_LINE_SCORES"] == "[7,0,14,3]"


def test_object_root_is_one_row_and_its_children_join_it(tables: Mapping[str, Table]) -> None:
    body = {"team1": "Michigan", "games": [{"winner": "Ohio"}, {"winner": "Mich"}]}
    assert rows(tables["TEAMS_MATCHUP"], body) == [{"ROW_NO": 1, "TEAM1": "Michigan"}]
    games = rows(tables["TEAMS_MATCHUP_GAMES"], body)
    assert [(g["ROW_NO"], g["GAMES_NO"], g["WINNER"]) for g in games] == [
        (1, 1, "Ohio"),
        (1, 2, "Mich"),
    ]


@pytest.mark.parametrize("body", [[], None])
def test_empty_responses_return_no_rows(tables: Mapping[str, Table], body: Any) -> None:
    for name in ("RANKINGS", "RANKINGS_POLLS", "RANKINGS_POLLS_RANKS"):
        assert flatten(tables[name], body) == []


def test_null_object_root_returns_no_rows(tables: Mapping[str, Table]) -> None:
    assert flatten(tables["TEAMS_MATCHUP"], None) == []


def test_root_array_of_scalars_has_one_row_per_element(tables: Mapping[str, Table]) -> None:
    assert rows(tables["STATS_CATEGORIES"], ["passing", "rushing"]) == [
        {"ROW_NO": 1, "VALUE_": "passing"},
        {"ROW_NO": 2, "VALUE_": "rushing"},
    ]


def test_curated_rows_have_curated_columns_and_complete_raw_json(
    tables: Mapping[str, Table],
) -> None:
    body = {
        "id": 7,
        "home": {"name": "Michigan", "rank": 3},
        "notes": [{"text": "Rivalry ✓"}],
        "extra": "kept only in RAW_JSON",
    }
    (row,) = rows(tables["PREVIEW"], body)
    assert row["ID"] == 7
    assert row["HOME_NAME"] == "Michigan"
    assert row["NOTES"] == '[{"text":"Rivalry ✓"}]'
    assert json.loads(row["RAW_JSON"]) == body
    assert "EXTRA" not in row


@pytest.mark.parametrize(
    "value",
    ["2024-08-24T16:00:00.000Z", "2024-08-24T18:00:00+02:00", "2024-08-24T12:00:00-04:00"],
)
def test_date_times_become_naive_utc_timestamps(tables: Mapping[str, Table], value: str) -> None:
    (row,) = rows(tables["TEAMS_MATCHUP_GAMES"], {"games": [{"date": value}]})
    assert row["DATE_"] == datetime(2024, 8, 24, 16, 0, 0)
    assert row["DATE_"].tzinfo is None


def test_date_time_without_offset_is_taken_as_utc(tables: Mapping[str, Table]) -> None:
    (row,) = rows(tables["TEAMS_MATCHUP_GAMES"], {"games": [{"date": "2024-08-24T16:00:00.123"}]})
    assert row["DATE_"] == datetime(2024, 8, 24, 16, 0, 0, 123000)


def failure(table: Table, body: Any) -> str:
    with pytest.raises(FlattenError) as caught:
        flatten(table, body)
    return str(caught.value)


def test_overlong_strings_fail_naming_table_column_and_length(
    tables: Mapping[str, Table],
) -> None:
    message = failure(tables["TEAMS_MATCHUP_GAMES"], {"games": [{"winner": "Michigan"}]})
    assert "TEAMS_MATCHUP_GAMES" in message
    assert "WINNER" in message
    assert "8" in message and "5" in message
    assert "Michigan" not in message


@pytest.mark.parametrize(
    ("body", "column", "found"),
    [
        ([{"season": 2024.5, "polls": []}], "SEASON", "number"),
        ([{"season": "2024", "polls": []}], "SEASON", "string"),
        ([{"season": True, "polls": []}], "SEASON", "boolean"),
        ([{"season": 10**18, "polls": []}], "SEASON", "out of range"),
        ([{"week": [1], "polls": []}], "WEEK", "array"),
    ],
)
def test_type_mismatches_fail_naming_column_and_value_type(
    tables: Mapping[str, Table], body: Any, column: str, found: str
) -> None:
    message = failure(tables["RANKINGS"], body)
    assert "RANKINGS" in message and column in message and found in message


def test_integral_floats_are_accepted_as_integers(tables: Mapping[str, Table]) -> None:
    (row,) = rows(tables["RANKINGS"], [{"season": 2024.0, "polls": []}])
    assert row["SEASON"] == 2024
    assert isinstance(row["SEASON"], int)


@pytest.mark.parametrize(
    ("value", "found"),
    [(True, "boolean"), ("1.5", "string"), (math.inf, "not finite"), (math.nan, "not finite")],
)
def test_double_columns_reject_non_numbers(
    tables: Mapping[str, Table], value: Any, found: str
) -> None:
    message = failure(tables["RATINGS_SP"], [{"offense": {"rating": value}}])
    assert "OFFENSE_RATING" in message and found in message


def test_double_columns_accept_integers(tables: Mapping[str, Table]) -> None:
    (row,) = rows(tables["RATINGS_SP"], [{"offense": {"rating": 31}}])
    assert row["OFFENSE_RATING"] == 31.0
    assert isinstance(row["OFFENSE_RATING"], float)


def test_boolean_columns_reject_non_booleans(tables: Mapping[str, Table]) -> None:
    message = failure(tables["RANKINGS_POLLS"], [{"polls": [{"isFinal": 1}]}])
    assert "IS_FINAL" in message and "integer" in message


@pytest.mark.parametrize(
    ("value", "found"),
    [
        ("yesterday", "not an ISO-8601"),
        (1724515200, "integer"),
        ("2024-08-24T16:00:00.1234Z", "sub-millisecond"),
    ],
)
def test_bad_date_times_fail(tables: Mapping[str, Table], value: Any, found: str) -> None:
    message = failure(tables["TEAMS_MATCHUP_GAMES"], {"games": [{"date": value}]})
    assert "DATE_" in message and found in message


def test_wrong_root_kind_fails(tables: Mapping[str, Table]) -> None:
    assert "array" in failure(tables["RANKINGS"], {"message": "oops"})
    assert "object" in failure(tables["TEAMS_MATCHUP"], [{"team1": "x"}])


def test_non_object_elements_fail(tables: Mapping[str, Table]) -> None:
    assert "object" in failure(tables["RANKINGS"], [None])
    assert "object" in failure(tables["RANKINGS_POLLS"], [{"polls": ["AP"]}])
    assert "polls" in failure(tables["RANKINGS_POLLS"], [{"polls": {"poll": "AP"}}])


def test_a_scalar_where_an_object_is_expected_fails(tables: Mapping[str, Table]) -> None:
    message = failure(tables["RATINGS_SP"], [{"offense": 5}])
    assert "OFFENSE_RATING" in message and "offense" in message


def test_json_columns_reject_non_finite_numbers(tables: Mapping[str, Table]) -> None:
    body = {"games": [{"homeLineScores": [math.nan]}]}
    assert "HOME_LINE_SCORES" in failure(tables["TEAMS_MATCHUP_GAMES"], body)


def test_json_column_holding_null_is_null(tables: Mapping[str, Table]) -> None:
    (row,) = rows(tables["TEAMS_MATCHUP_GAMES"], {"games": [{"homeLineScores": None}]})
    assert row["HOME_LINE_SCORES"] is None


def table_with(columns: tuple[Column, ...]) -> Table:
    meta = Column("REQ_SLICE", "meta", sql_type="VARCHAR(2000) UTF8")
    return Table("T", "/t", "endpoint", None, (), False, True, None, (), (*columns, meta), ())


def test_oversized_json_text_fails() -> None:
    small_json = Column("J", "value", ("j",), "VARCHAR(10) UTF8", json=True)
    table = table_with((Column("ROW_NO", "position", (), naming.DECIMAL_TYPE), small_json))
    assert "J" in failure(table, [{"j": [1, 2, 3, 4, 5, 6]}])


@pytest.mark.parametrize("sql_type", ["DATE", "VARCHAR(10)", "DECIMAL(10, 0)", "CHAR(3) UTF8", ""])
def test_column_types_outside_the_shared_grammar_are_unsupported(sql_type: str) -> None:
    columns = (
        Column("ROW_NO", "position", (), naming.DECIMAL_TYPE),
        Column("X", "value", ("x",), sql_type),
    )
    assert "unsupported type" in failure(table_with(columns), [{"x": "a"}])


def test_a_json_column_must_be_a_varchar() -> None:
    columns = (
        Column("ROW_NO", "position", (), naming.DECIMAL_TYPE),
        Column("J", "value", ("j",), "DOUBLE", json=True),
    )
    assert "must be a VARCHAR" in failure(table_with(columns), [{"j": 1}])


@pytest.mark.parametrize(
    "columns",
    [
        (Column("X", "value", ("x",), "VARCHAR(10) UTF8"),),
        (
            Column("ROW_NO", "position", (), naming.DECIMAL_TYPE),
            Column("X", "value", ("x",), "DECIMAL(10,2)"),
        ),
        (
            Column("ROW_NO", "position", (), naming.DECIMAL_TYPE),
            Column("P_X", "param", sql_type=naming.DECIMAL_TYPE),
            Column("X", "value", ("x",), "VARCHAR(10) UTF8"),
        ),
        (
            Column("ROW_NO", "position", (), naming.DECIMAL_TYPE),
            Column("K", "parent_key", ("x",), "VARCHAR(10) UTF8"),
        ),
    ],
)
def test_inconsistent_tables_are_refused(columns: tuple[Column, ...]) -> None:
    with pytest.raises(FlattenError):
        flatten(table_with(columns), [])


SPEC_PATH = Path(__file__).resolve().parents[2] / "vs" / "openapi" / "cfbd-openapi-5.33.0.json"
PYTHON_TYPES = {
    naming.DECIMAL_TYPE: int,
    naming.DOUBLE_TYPE: float,
    naming.BOOLEAN_TYPE: bool,
    naming.TIMESTAMP_TYPE: datetime,
}


def synthetic(node: Node) -> Any:
    if isinstance(node, ObjectNode):
        return {f.name: synthetic(f.node) for f in node.fields}
    if isinstance(node, ArrayNode):
        return [synthetic(node.items), synthetic(node.items)]
    if isinstance(node, JsonValue):
        return {"free": [1, "form"]}
    if node.enum:
        return node.enum[0]
    if node.format == "date-time":
        return "2024-08-24T18:00:00.000+02:00"
    return {"integer": 7, "number": 1.5, "boolean": True, "string": "s"}[node.type]


def real_overrides() -> Overrides:
    endpoints = {
        "/games/{gameId}/preview": EndpointOverrides(
            "/games/{gameId}/preview", curated=(("game",), ("assembledAt",)), raw_json=True
        ),
        "/teams/season/overview": EndpointOverrides(
            "/teams/season/overview", curated=(("record",),), raw_json=True
        ),
        "/coaches": EndpointOverrides(
            "/coaches", fields={("seasons",): FieldOverride(None, None, "SEASON_HISTORY")}
        ),
        "/playoffs/cfp": EndpointOverrides(
            "/playoffs/cfp", fields={("participants",): FieldOverride("json", None, None)}
        ),
        "/games/teams": EndpointOverrides(
            "/games/teams", hoist=Hoist(array=("teams",), carry={("id",): "GAME_ID"})
        ),
        "/games/players": EndpointOverrides(
            "/games/players",
            hoist=Hoist(array=("teams",), carry={("id",): "GAME_ID"}),
            fields={("teams", "categories", "name"): FieldOverride(None, None, "CATEGORY")},
        ),
    }
    children = {
        "RANKINGS_POLLS": ChildOverrides("RANKINGS_POLLS", (("season",), ("week",))),
        "RANKINGS_POLLS_RANKS": ChildOverrides(
            "RANKINGS_POLLS_RANKS", (("season",), ("seasonType",), ("week",), ("polls", "poll"))
        ),
        "GAMES_PLAYERS_CATEGORIES_TYPES_ATHLETES": ChildOverrides(
            "GAMES_PLAYERS_CATEGORIES_TYPES_ATHLETES",
            (("id",), ("teams", "team"), ("teams", "categories", "name")),
        ),
    }
    return Overrides(endpoints=endpoints, children=children, acknowledged=(), excluded=())


def test_every_real_table_flattens_a_schema_conforming_response() -> None:
    document = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    operations = {op.path: op for op in read_operations(document)}
    derived = derive_tables(list(operations.values()), real_overrides(), frozenset({"DATE"}))
    catalog = Catalog.from_json(Catalog("h" * 64, "t", "1", (), derived).to_json(indent=None))
    assert len(catalog.tables) > 93
    for table in catalog.tables:
        body = synthetic(operations[table.endpoint].response)
        result = flatten(table, body)
        assert result, table.name
        for row in result:
            for column, value in zip(data_columns(table), row, strict=True):
                expected = PYTHON_TYPES.get(column.sql_type or "", str)
                assert value is None or type(value) is expected, (table.name, column.name)
    athletes = catalog.table("GAMES_PLAYERS_CATEGORIES_TYPES_ATHLETES")
    body = synthetic(operations["/games/players"].response)
    assert [c.name for c in data_columns(athletes) if c.kind == "parent_key"] == [
        "GAME_ID",
        "TEAM",
        "CATEGORY",
    ]
    assert len(flatten(athletes, body)) == 2 * 2 * 2 * 2 * 2
