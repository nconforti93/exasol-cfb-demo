import json
from pathlib import Path

import pytest

from vs.cfbd_vs import naming

SPEC_PATH = Path(__file__).resolve().parents[2] / "vs" / "openapi" / "cfbd-openapi-5.33.0.json"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("seasonType", "SEASON_TYPE"),
        ("gameId", "GAME_ID"),
        ("id", "ID"),
        ("year", "YEAR"),
        ("averagePPA", "AVERAGE_PPA"),
        ("homeCountyFIPS", "HOME_COUNTY_FIPS"),
        ("FIPSCode", "FIPS_CODE"),
        ("isStartTimeTBD", "IS_START_TIME_TBD"),
        ("team1", "TEAM1"),
        ("team1Wins", "TEAM1_WINS"),
        ("punts50Plus", "PUNTS50_PLUS"),
        ("inside20", "INSIDE20"),
        ("deep left", "DEEP_LEFT"),
        ("already_snake", "ALREADY_SNAKE"),
        ("ROW_NO", "ROW_NO"),
        ("a--b", "A_B"),
        ("_lead", "LEAD"),
    ],
)
def test_to_upper_snake(name: str, expected: str) -> None:
    assert naming.to_upper_snake(name) == expected


@pytest.mark.parametrize("name", ["", "___", "2pt", "-"])
def test_to_upper_snake_rejects_unusable_names(name: str) -> None:
    with pytest.raises(naming.NamingError):
        naming.to_upper_snake(name)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/games", "GAMES"),
        ("/games/{gameId}/preview", "GAMES_PREVIEW"),
        ("/games/{gameId}/preview/adjusted", "GAMES_PREVIEW_ADJUSTED"),
        ("/metrics/wp/pregame", "METRICS_WP_PREGAME"),
        ("/player/search", "PLAYER_SEARCH"),
        ("/stats/season/advanced", "STATS_SEASON_ADVANCED"),
        ("/game/box/advanced", "GAME_BOX_ADVANCED"),
        ("/games/{gameId}", "GAMES"),
        ("/coaches/profile", "COACHES_PROFILE"),
    ],
)
def test_table_name_from_path(path: str, expected: str) -> None:
    assert naming.table_name_from_path(path) == expected


@pytest.mark.parametrize("path", ["/", "", "/{id}", "games"])
def test_table_name_from_path_rejects_unusable_paths(path: str) -> None:
    with pytest.raises(naming.NamingError):
        naming.table_name_from_path(path)


def test_assign_table_names_maps_each_path() -> None:
    assert naming.assign_table_names(["/games", "/games/teams"]) == {
        "/games": "GAMES",
        "/games/teams": "GAMES_TEAMS",
    }


def test_colliding_table_names_name_both_paths() -> None:
    with pytest.raises(naming.NamingError) as error:
        naming.assign_table_names(["/games/{gameId}", "/games"])
    message = str(error.value)
    assert "/games/{gameId}" in message
    assert "/games" in message
    assert "GAMES" in message


def test_pinned_spec_paths_map_to_unique_table_names() -> None:
    paths = list(json.loads(SPEC_PATH.read_text())["paths"])
    names = naming.assign_table_names(paths)
    assert len(paths) == 93
    assert len(set(names.values())) == 93


def test_child_table_name_appends_array_path() -> None:
    assert naming.child_table_name("RANKINGS", ["polls"]) == "RANKINGS_POLLS"
    assert naming.child_table_name("RANKINGS_POLLS", ["ranks"]) == "RANKINGS_POLLS_RANKS"
    assert naming.child_table_name("GAMES_TEAMS", ["stats"]) == "GAMES_TEAMS_STATS"
    assert naming.child_table_name("X", ["fooBar", "items"]) == "X_FOO_BAR_ITEMS"


def test_nested_column_name_joins_path_segments() -> None:
    assert naming.column_name_from_path(["offense", "rating"]) == "OFFENSE_RATING"
    assert naming.column_name_from_path(["defense", "havoc", "total"]) == "DEFENSE_HAVOC_TOTAL"
    assert naming.column_name_from_path(["homeTeam"]) == "HOME_TEAM"


def test_parameter_column_names_are_prefixed() -> None:
    assert naming.param_column_name("year") == "P_YEAR"
    assert naming.param_column_name("seasonType") == "P_SEASON_TYPE"
    assert naming.param_column_name("gameId") == "P_GAME_ID"
    assert naming.param_column_name("team1") == "P_TEAM1"
    assert naming.param_column_name("limit") == "P_LIMIT"


def test_position_column_names() -> None:
    assert naming.ROW_NO == "ROW_NO"
    assert naming.position_column_name(["polls"]) == "POLLS_NO"
    assert naming.position_column_name(["ranks"]) == "RANKS_NO"
    assert naming.position_column_name(["fooBar", "items"]) == "FOO_BAR_ITEMS_NO"


def test_request_metadata_columns_in_documented_order() -> None:
    assert list(naming.META_COLUMNS) == [
        "REQ_SLICE",
        "REQ_SOURCE",
        "REQ_STATUS",
        "REQ_ATTEMPTS",
        "REQ_MS",
        "REQ_ROWS",
        "REQ_AT",
    ]
    assert naming.META_COLUMNS["REQ_SLICE"] == "VARCHAR(2000) UTF8"
    assert naming.META_COLUMNS["REQ_STATUS"] == "DECIMAL(3,0)"
    assert naming.META_COLUMNS["REQ_MS"] == "DECIMAL(9,0)"
    assert naming.META_COLUMNS["REQ_AT"] == "TIMESTAMP"


def test_reserved_word_gets_trailing_underscore() -> None:
    reserved = frozenset({"DATE", "ORDER"})
    assert naming.safe_column_name("DATE", reserved) == "DATE_"
    assert naming.safe_column_name("ORDER", reserved) == "ORDER_"
    assert naming.safe_column_name("SEASON", reserved) == "SEASON"


def test_reserved_check_is_case_insensitive() -> None:
    assert naming.safe_column_name("date", frozenset({"DATE"})) == "date_"


def test_empty_reserved_set_changes_nothing() -> None:
    assert naming.safe_column_name("DATE", frozenset()) == "DATE"


@pytest.mark.parametrize(
    ("spec_type", "fmt", "expected"),
    [
        ("integer", None, "DECIMAL(18,0)"),
        ("integer", "int32", "DECIMAL(18,0)"),
        ("number", None, "DOUBLE"),
        ("number", "double", "DOUBLE"),
        ("boolean", None, "BOOLEAN"),
        ("string", None, "VARCHAR(2000) UTF8"),
        ("string", "date", "VARCHAR(2000) UTF8"),
        ("string", "date-time", "TIMESTAMP"),
        ("json", None, "VARCHAR(2000000) UTF8"),
    ],
)
def test_column_sql_type(spec_type: str, fmt: str | None, expected: str) -> None:
    assert naming.column_sql_type(spec_type, fmt) == expected


def test_string_size_override() -> None:
    assert naming.column_sql_type("string", None, varchar_size=500) == "VARCHAR(500) UTF8"


def test_size_override_does_not_change_other_types() -> None:
    assert naming.column_sql_type("integer", None, varchar_size=500) == "DECIMAL(18,0)"
    assert naming.column_sql_type("string", "date-time", varchar_size=500) == "TIMESTAMP"


@pytest.mark.parametrize("size", [0, -1, 2_000_001])
def test_invalid_varchar_sizes_are_rejected(size: int) -> None:
    with pytest.raises(naming.NamingError):
        naming.column_sql_type("string", None, varchar_size=size)


@pytest.mark.parametrize("spec_type", ["array", "object", "", "null"])
def test_unmappable_spec_types_are_rejected(spec_type: str) -> None:
    with pytest.raises(naming.NamingError) as error:
        naming.column_sql_type(spec_type)
    assert spec_type in str(error.value)


@pytest.mark.parametrize(
    ("spec_type", "expected"),
    [
        ("integer", "DECIMAL(18,0)"),
        ("number", "DOUBLE"),
        ("boolean", "BOOLEAN"),
        ("string", "VARCHAR(2000) UTF8"),
    ],
)
def test_param_sql_type(spec_type: str, expected: str) -> None:
    assert naming.param_sql_type(spec_type) == expected


def test_param_sql_type_ignores_date_time_format() -> None:
    assert naming.param_sql_type("string", "date-time") == "VARCHAR(2000) UTF8"


def test_param_sql_type_rejects_unknown_type() -> None:
    with pytest.raises(naming.NamingError):
        naming.param_sql_type("array")
