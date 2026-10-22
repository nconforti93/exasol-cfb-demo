import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from vs.cfbd_vs import naming
from vs.cfbd_vs.catalog import Column, ExcludedEndpoint, Rule, Table
from vs.gen.overrides import (
    ChildOverrides,
    EndpointOverrides,
    FieldOverride,
    Hoist,
    Overrides,
    ParamOverride,
)
from vs.gen.schema_walk import read_operations
from vs.gen.tables import MAX_FLATTENED_FIELDS, TablesError, derive_tables

FieldMap = dict[tuple[str, ...], FieldOverride]

SPEC_PATH = Path(__file__).resolve().parents[2] / "vs" / "openapi" / "cfbd-openapi-5.33.0.json"
RESERVED = frozenset({"DATE", "VALUE", "RESULT", "ORDER"})

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


def query(name: str, schema: Any, description: str = "", required: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "in": "query",
        "required": required,
        "description": description,
        "schema": schema,
    }


def get_op(schema: Any, *parameters: Any, description: str = "") -> dict[str, Any]:
    return {
        "get": {
            "description": description,
            "parameters": list(parameters),
            "responses": {"200": {"content": {"application/json": {"schema": schema}}}},
        }
    }


SCHEMAS: dict[str, Any] = {
    "SeasonType": {"type": "string", "enum": ["regular", "postseason", "both"]},
    "PollWeek": obj(season=INT, seasonType=ref("SeasonType"), week=INT, polls=arr(ref("Poll"))),
    "Poll": obj(poll=STR, isFinal=BOOL, ranks=arr(ref("PollRank"))),
    "PollRank": obj(rank=INT, school={**STR, "description": "School name"}, conference=STR),
    "TeamSP": obj(team=STR, offense=obj(rating=NUM), defense=obj(havoc=obj(total=NUM))),
    "GameTeams": obj(id=INT, teams=arr(ref("Side"))),
    "Side": obj(teamId=INT, team=STR, stats=arr(obj(category=STR, stat=STR))),
    "Matchup": obj(
        team1=STR,
        team2=STR,
        games=arr(obj(date=DATE_TIME, homeLineScores=arr(INT), venue=obj(name=STR))),
    ),
}

PATHS: dict[str, Any] = {
    "/rankings": get_op(
        arr(ref("PollWeek")),
        query("year", INT, "Season year", required=True),
        query("seasonType", ref("SeasonType"), "Season type"),
    ),
    "/ratings/sp": get_op(arr(ref("TeamSP")), query("year", INT), query("team", STR)),
    "/games/teams": get_op(arr(ref("GameTeams")), query("year", INT), query("id", INT)),
    "/teams/matchup": get_op(
        ref("Matchup"),
        query("team1", STR, required=True),
        query("team2", STR, required=True),
    ),
    "/stats/categories": get_op(arr(STR)),
}


def document(paths: Mapping[str, Any] | None = None, **schemas: Any) -> dict[str, Any]:
    return {
        "paths": dict(PATHS if paths is None else paths),
        "components": {"schemas": {**SCHEMAS, **schemas}},
    }


def derive(
    doc: dict[str, Any] | None = None,
    endpoints: Mapping[str, EndpointOverrides] | None = None,
    children: Mapping[str, tuple[tuple[str, ...], ...]] | None = None,
    excluded: tuple[str, ...] = (),
) -> dict[str, Table]:
    overrides = Overrides(
        endpoints=dict(endpoints or {}),
        children={name: ChildOverrides(name, keys) for name, keys in (children or {}).items()},
        acknowledged=(),
        excluded=tuple(ExcludedEndpoint(path, "test") for path in excluded),
    )
    operations = read_operations(doc if doc is not None else document())
    return {t.name: t for t in derive_tables(operations, overrides, RESERVED)}


def errors_of(**kwargs: Any) -> list[str]:
    with pytest.raises(TablesError) as caught:
        derive(**kwargs)
    return list(caught.value.errors)


def names(table: Table, kind: str | None = None) -> list[str]:
    return [c.name for c in table.columns if kind is None or c.kind == kind]


def column(table: Table, name: str) -> Column:
    return next(c for c in table.columns if c.name == name)


def only(path: str, doc_paths: Mapping[str, Any] | None = None, **schemas: Any) -> dict[str, Any]:
    source = PATHS if doc_paths is None else doc_paths
    return document({path: source[path]}, **schemas)


META = list(naming.META_COLUMNS)


def test_endpoint_table_columns_follow_the_documented_order() -> None:
    sp = derive()["RATINGS_SP"]
    assert names(sp) == [
        "ROW_NO",
        "TEAM",
        "OFFENSE_RATING",
        "DEFENSE_HAVOC_TOTAL",
        "P_YEAR",
        "P_TEAM",
        *META,
    ]
    assert (sp.kind, sp.parent, sp.array_path, sp.hoisted) == ("endpoint", None, (), False)
    assert column(sp, "DEFENSE_HAVOC_TOTAL").path == ("defense", "havoc", "total")
    assert column(sp, "DEFENSE_HAVOC_TOTAL").sql_type == "DOUBLE"


def test_every_column_has_an_sql_type() -> None:
    for table in derive().values():
        for col in table.columns:
            assert col.sql_type, (table.name, col.name)
    sp = derive()["RATINGS_SP"]
    assert column(sp, "ROW_NO").sql_type == "DECIMAL(18,0)"
    assert column(sp, "P_TEAM").sql_type == "VARCHAR(2000) UTF8"
    assert {c.name: c.sql_type for c in sp.columns if c.kind == "meta"} == naming.META_COLUMNS


def test_row_no_path_marks_array_and_object_roots() -> None:
    tables = derive()
    assert column(tables["RATINGS_SP"], "ROW_NO").path == ()
    assert column(tables["TEAMS_MATCHUP"], "ROW_NO").path is None


def test_arrays_of_objects_become_child_tables_per_level() -> None:
    tables = derive(children={"RANKINGS_POLLS": (("season",), ("seasonType",), ("week",))})
    assert names(tables["RANKINGS"]) == [
        "ROW_NO",
        "SEASON",
        "SEASON_TYPE",
        "WEEK",
        "P_YEAR",
        "P_SEASON_TYPE",
        *META,
    ]
    polls = tables["RANKINGS_POLLS"]
    assert names(polls) == [
        "ROW_NO",
        "POLLS_NO",
        "SEASON",
        "SEASON_TYPE",
        "WEEK",
        "POLL",
        "IS_FINAL",
        "P_YEAR",
        "P_SEASON_TYPE",
        *META,
    ]
    assert (polls.kind, polls.parent, polls.array_path) == ("child", "RANKINGS", ("polls",))
    assert column(polls, "POLLS_NO").path == ("polls",)
    assert column(polls, "SEASON").kind == "parent_key"
    assert column(polls, "SEASON").path == ("season",)
    assert column(polls, "POLL").path == ("poll",)
    ranks = tables["RANKINGS_POLLS_RANKS"]
    assert names(ranks, "position") == ["ROW_NO", "POLLS_NO", "RANKS_NO"]
    assert (ranks.parent, ranks.array_path) == ("RANKINGS_POLLS", ("polls", "ranks"))
    assert column(ranks, "RANKS_NO").path == ("ranks",)


def test_parent_keys_can_come_from_any_ancestor_level() -> None:
    tables = derive(children={"RANKINGS_POLLS_RANKS": (("season",), ("polls", "poll"))})
    ranks = tables["RANKINGS_POLLS_RANKS"]
    assert names(ranks, "parent_key") == ["SEASON", "POLL"]
    assert column(ranks, "POLL").path == ("polls", "poll")
    assert column(ranks, "SEASON").sql_type == "DECIMAL(18,0)"
    assert "RANKINGS.SEASON" in (column(ranks, "SEASON").comment or "")
    assert "RANKINGS_POLLS.POLL" in (column(ranks, "POLL").comment or "")


@pytest.mark.parametrize(
    ("keys", "message"),
    [
        ((("nope",),), "nope"),
        ((("polls", "ranks", "rank"),), "polls.ranks.rank"),
        ((("polls",),), "polls"),
    ],
)
def test_parent_keys_must_be_ancestor_fields(
    keys: tuple[tuple[str, ...], ...], message: str
) -> None:
    errors = errors_of(children={"RANKINGS_POLLS_RANKS": keys})
    assert any(message in e and "RANKINGS_POLLS_RANKS" in e for e in errors)


def test_children_entries_without_a_derived_child_table_are_errors() -> None:
    errors = errors_of(children={"RANKINGS_NOPE": (("season",),), "RANKINGS": (("season",),)})
    assert any("RANKINGS_NOPE" in e for e in errors)
    assert any("'RANKINGS'" in e for e in errors)


def test_child_tables_share_params_rules_and_endpoint_settings() -> None:
    rule = Rule(id="rankings.r", type="one_of", params=("year", "seasonType"), message="m")
    options = EndpointOverrides(
        path="/rankings", rules=(rule,), gated_by="weather", read_through=False
    )
    tables = derive(endpoints={"/rankings": options})
    base = tables["RANKINGS"]
    for name in ("RANKINGS_POLLS", "RANKINGS_POLLS_RANKS"):
        child = tables[name]
        assert child.params == base.params
        assert child.rules == base.rules
        assert (child.gated_by, child.read_through) == ("weather", False)
        assert names(child, "param") == ["P_YEAR", "P_SEASON_TYPE"]


def test_read_through_defaults_to_true() -> None:
    assert all(t.read_through for t in derive().values())


def hoist_games_teams(**extra: Any) -> dict[str, EndpointOverrides]:
    hoist = Hoist(array=("teams",), carry={("id",): "GAME_ID"})
    return {"/games/teams": EndpointOverrides(path="/games/teams", hoist=hoist, **extra)}


def test_hoisted_endpoint_exposes_array_elements_with_carried_id() -> None:
    tables = derive(endpoints=hoist_games_teams())
    teams = tables["GAMES_TEAMS"]
    assert (teams.kind, teams.hoisted, teams.array_path, teams.parent) == (
        "endpoint",
        True,
        ("teams",),
        None,
    )
    assert names(teams) == [
        "ROW_NO",
        "TEAMS_NO",
        "GAME_ID",
        "TEAM_ID",
        "TEAM",
        "P_YEAR",
        "P_ID",
        *META,
    ]
    assert column(teams, "GAME_ID").kind == "parent_key"
    assert column(teams, "GAME_ID").path == ("id",)
    assert column(teams, "TEAMS_NO").path == ("teams",)
    stats = tables["GAMES_TEAMS_STATS"]
    assert names(stats, "position") == ["ROW_NO", "TEAMS_NO", "STATS_NO"]
    assert (stats.parent, stats.array_path) == ("GAMES_TEAMS", ("teams", "stats"))
    assert "GAMES_TEAMS_TEAMS" not in tables
    assert not any(names(t, "value") == ["ID"] for t in tables.values())


def test_children_of_hoisted_tables_can_take_the_carried_key() -> None:
    tables = derive(endpoints=hoist_games_teams(), children={"GAMES_TEAMS_STATS": (("id",),)})
    assert names(tables["GAMES_TEAMS_STATS"], "parent_key") == ["GAME_ID"]


def test_hoisting_must_not_drop_fields_or_arrays() -> None:
    doc = only(
        "/games/teams",
        GameTeams=obj(id=INT, venue=STR, teams=arr(ref("Side")), refs=arr(obj(name=STR))),
    )
    errors = errors_of(doc=doc, endpoints=hoist_games_teams())
    assert any("venue" in e and "carry" in e for e in errors)
    assert any("refs" in e for e in errors)


def test_hoist_array_must_be_an_array_of_objects() -> None:
    hoist = Hoist(array=("id",), carry={})
    errors = errors_of(endpoints={"/games/teams": EndpointOverrides("/games/teams", hoist=hoist)})
    assert any("hoist" in e and "id" in e for e in errors)


def test_hoist_and_curated_cannot_be_combined() -> None:
    errors = errors_of(endpoints=hoist_games_teams(curated=(("id",),), raw_json=True))
    assert any("hoist" in e and "curated" in e for e in errors)


def test_scalar_arrays_and_free_form_objects_are_json_columns() -> None:
    games = derive()["TEAMS_MATCHUP_GAMES"]
    scores = column(games, "HOME_LINE_SCORES")
    assert (scores.sql_type, scores.json, scores.path) == (
        naming.JSON_TYPE,
        True,
        ("homeLineScores",),
    )
    assert column(games, "VENUE_NAME").json is False


def test_root_array_of_scalars_has_one_value_column() -> None:
    categories = derive()["STATS_CATEGORIES"]
    assert names(categories) == ["ROW_NO", "VALUE_", *META]
    value = column(categories, "VALUE_")
    assert (value.kind, value.path, value.sql_type) == ("value", (), "VARCHAR(2000) UTF8")


def test_object_root_children_have_a_single_row_root() -> None:
    tables = derive()
    assert names(tables["TEAMS_MATCHUP"], "value") == ["TEAM1", "TEAM2"]
    games = tables["TEAMS_MATCHUP_GAMES"]
    assert column(games, "ROW_NO").path is None
    assert names(games, "position") == ["ROW_NO", "GAMES_NO"]


def test_date_time_becomes_timestamp_and_reserved_words_get_a_suffix() -> None:
    games = derive()["TEAMS_MATCHUP_GAMES"]
    date = column(games, "DATE_")
    assert date.sql_type == "TIMESTAMP"
    assert "games.date" in (date.comment or "")


def test_value_comments_carry_the_description_and_api_field() -> None:
    tables = derive()
    comment = column(tables["RANKINGS_POLLS_RANKS"], "SCHOOL").comment or ""
    assert "School name" in comment
    assert "polls.ranks.school" in comment


def test_field_overrides_rename_retype_and_resize() -> None:
    fields: FieldMap = {
        ("games", "date"): FieldOverride(type="string", size=40, rename="GAME_DATE"),
        ("team1",): FieldOverride(type=None, size=100, rename=None),
        ("games", "venue"): FieldOverride(type="json", size=None, rename=None),
    }
    options = EndpointOverrides(path="/teams/matchup", fields=fields)
    tables = derive(endpoints={"/teams/matchup": options})
    games = tables["TEAMS_MATCHUP_GAMES"]
    assert column(games, "GAME_DATE").sql_type == "VARCHAR(40) UTF8"
    assert column(tables["TEAMS_MATCHUP"], "TEAM1").sql_type == "VARCHAR(100) UTF8"
    venue = column(games, "VENUE")
    assert (venue.json, venue.path, venue.sql_type) == (True, ("venue",), naming.JSON_TYPE)
    assert "VENUE_NAME" not in names(games)


def test_json_type_on_an_array_of_objects_replaces_the_child_table() -> None:
    fields: FieldMap = {("games",): FieldOverride(type="json", size=None, rename=None)}
    tables = derive(
        endpoints={"/teams/matchup": EndpointOverrides("/teams/matchup", fields=fields)}
    )
    assert "TEAMS_MATCHUP_GAMES" not in tables
    assert column(tables["TEAMS_MATCHUP"], "GAMES").json is True


def test_rename_applies_to_parent_keys_copied_from_that_field() -> None:
    fields: FieldMap = {("polls", "poll"): FieldOverride(type=None, size=None, rename="POLL_NAME")}
    tables = derive(
        endpoints={"/rankings": EndpointOverrides("/rankings", fields=fields)},
        children={"RANKINGS_POLLS_RANKS": (("polls", "poll"),)},
    )
    assert "POLL_NAME" in names(tables["RANKINGS_POLLS"], "value")
    assert names(tables["RANKINGS_POLLS_RANKS"], "parent_key") == ["POLL_NAME"]


def test_size_on_a_timestamp_column_is_an_error() -> None:
    fields: FieldMap = {("games", "date"): FieldOverride(type=None, size=40, rename=None)}
    errors = errors_of(
        endpoints={"/teams/matchup": EndpointOverrides("/teams/matchup", fields=fields)}
    )
    assert any("games.date" in e and "size" in e for e in errors)


@pytest.mark.parametrize(
    "override",
    [
        FieldOverride(type=None, size=None, rename="X"),
        FieldOverride(type="integer", size=None, rename=None),
    ],
)
def test_unused_field_overrides_are_errors(override: FieldOverride) -> None:
    fields: FieldMap = {("games", "venue"): override}
    errors = errors_of(
        endpoints={"/teams/matchup": EndpointOverrides("/teams/matchup", fields=fields)}
    )
    assert any("games.venue" in e and "not used" in e for e in errors)


def test_rename_to_a_reserved_word_is_an_error() -> None:
    fields: FieldMap = {("team1",): FieldOverride(type=None, size=None, rename="ORDER")}
    errors = errors_of(
        endpoints={"/teams/matchup": EndpointOverrides("/teams/matchup", fields=fields)}
    )
    assert any("ORDER" in e and "reserved" in e for e in errors)


def test_column_collisions_name_table_column_and_both_sources() -> None:
    doc = only("/ratings/sp", TeamSP=obj(deepLeft=NUM, **{"deep left": NUM}))
    errors = errors_of(doc=doc)
    assert any(
        "RATINGS_SP" in e and "DEEP_LEFT" in e and "deepLeft" in e and "deep left" in e
        for e in errors
    )


def test_response_columns_may_not_shadow_position_param_or_meta_columns() -> None:
    doc = only("/ratings/sp", TeamSP=obj(rowNo=INT, pYear=INT, reqSlice=STR))
    errors = errors_of(doc=doc)
    assert any("ROW_NO" in e for e in errors)
    assert any("P_YEAR" in e and "parameter year" in e for e in errors)
    assert any("REQ_SLICE" in e for e in errors)


def test_a_rename_resolves_a_collision() -> None:
    doc = only("/ratings/sp", TeamSP=obj(deepLeft=NUM, **{"deep left": NUM}))
    fields: FieldMap = {("deep left",): FieldOverride(type=None, size=None, rename="DEEP_LEFT_2")}
    tables = derive(
        doc=doc, endpoints={"/ratings/sp": EndpointOverrides("/ratings/sp", fields=fields)}
    )
    assert names(tables["RATINGS_SP"], "value") == ["DEEP_LEFT", "DEEP_LEFT_2"]


def test_table_name_collisions_name_both_sources() -> None:
    paths = {
        "/games": get_op(arr(obj(id=INT, teams=arr(obj(team=STR))))),
        "/games/teams": PATHS["/games/teams"],
    }
    errors = errors_of(doc=document(paths))
    assert any("GAMES_TEAMS" in e and "/games/teams" in e and "/games" in e for e in errors)


def test_rename_on_an_array_renames_its_child_table_and_position_column() -> None:
    fields: FieldMap = {("polls",): FieldOverride(type=None, size=None, rename="POLL_LIST")}
    tables = derive(endpoints={"/rankings": EndpointOverrides("/rankings", fields=fields)})
    assert "RANKINGS_POLLS" not in tables
    assert names(tables["RANKINGS_POLL_LIST"], "position") == ["ROW_NO", "POLL_LIST_NO"]
    assert tables["RANKINGS_POLL_LIST_RANKS"].parent == "RANKINGS_POLL_LIST"
    assert names(tables["RANKINGS_POLL_LIST_RANKS"], "position") == [
        "ROW_NO",
        "POLL_LIST_NO",
        "RANKS_NO",
    ]


def test_child_and_endpoint_table_collision_is_resolved_by_an_array_rename() -> None:
    paths = {
        "/games": get_op(arr(obj(id=INT, teams=arr(obj(team=STR))))),
        "/games/teams": PATHS["/games/teams"],
    }
    fields: FieldMap = {("teams",): FieldOverride(type=None, size=None, rename="SIDES")}
    tables = derive(
        document(paths), endpoints={"/games": EndpointOverrides("/games", fields=fields)}
    )
    assert tables["GAMES_SIDES"].parent == "GAMES"
    assert tables["GAMES_TEAMS"].kind == "endpoint"


def wide(count: int) -> dict[str, Any]:
    return obj(**{f"f{i}": INT for i in range(count)}, nested=arr(obj(x=INT)))


def preview_paths() -> dict[str, Any]:
    gid = {"name": "gameId", "in": "path", "required": True, "schema": INT}
    return {"/games/{gameId}/preview": get_op(ref("Preview"), gid)}


def test_more_than_500_fields_without_curated_is_an_error() -> None:
    doc = document(preview_paths(), Preview=wide(MAX_FLATTENED_FIELDS + 1))
    errors = errors_of(doc=doc)
    assert any("/games/{gameId}/preview" in e and "501" in e and "curated" in e for e in errors)
    tables = derive(document(preview_paths(), Preview=wide(MAX_FLATTENED_FIELDS)))
    assert len(names(tables["GAMES_PREVIEW"], "value")) == MAX_FLATTENED_FIELDS


def test_curated_table_has_the_curated_columns_raw_json_and_no_children() -> None:
    preview = obj(
        id=INT,
        homeTeam=obj(name=STR, rank=INT, broadcasts=arr(obj(network=STR))),
        notes=arr(obj(text=STR)),
        **{f"f{i}": INT for i in range(600)},
    )
    curated = (("notes",), ("homeTeam",), ("id",))
    options = EndpointOverrides("/games/{gameId}/preview", curated=curated, raw_json=True)
    tables = derive(
        document(preview_paths(), Preview=preview),
        endpoints={"/games/{gameId}/preview": options},
    )
    assert set(tables) == {"GAMES_PREVIEW"}
    table = tables["GAMES_PREVIEW"]
    assert names(table) == [
        "ROW_NO",
        "ID",
        "HOME_TEAM_NAME",
        "HOME_TEAM_RANK",
        "HOME_TEAM_BROADCASTS",
        "NOTES",
        "RAW_JSON",
        "P_GAME_ID",
        *META,
    ]
    assert column(table, "ROW_NO").path is None
    assert column(table, "NOTES").json is True
    assert column(table, "HOME_TEAM_BROADCASTS").json is True
    raw = column(table, "RAW_JSON")
    assert (raw.kind, raw.path, raw.sql_type, raw.json) == (
        "raw_json",
        None,
        naming.JSON_TYPE,
        True,
    )


def test_curated_without_raw_json_flag_has_no_raw_json_column() -> None:
    options = EndpointOverrides("/teams/matchup", curated=(("team1",),))
    tables = derive(endpoints={"/teams/matchup": options})
    assert names(tables["TEAMS_MATCHUP"], "value") == ["TEAM1"]
    assert "RAW_JSON" not in names(tables["TEAMS_MATCHUP"])


@pytest.mark.parametrize(
    ("curated", "message"),
    [
        ((("team1",), ("team1",)), "twice"),
        ((("games",), ("games", "date")), "games"),
        ((("nope",),), "nope"),
    ],
)
def test_invalid_curated_entries_are_errors(
    curated: tuple[tuple[str, ...], ...], message: str
) -> None:
    options = EndpointOverrides("/teams/matchup", curated=curated, raw_json=True)
    errors = errors_of(endpoints={"/teams/matchup": options})
    assert any(message in e and "curated" in e for e in errors)


def test_params_become_typed_echo_columns() -> None:
    options = EndpointOverrides(
        "/rankings",
        params={"year": ParamOverride("number")},
        defaults={"seasonType": "both"},
    )
    rankings = derive(endpoints={"/rankings": options})["RANKINGS"]
    year, season_type = rankings.params
    assert (year.name, year.column, year.location, year.type, year.sql_type) == (
        "year",
        "P_YEAR",
        "query",
        "number",
        "DOUBLE",
    )
    assert "Season year" in (column(rankings, "P_YEAR").comment or "")
    assert season_type.enum == ("regular", "postseason", "both")
    assert column(rankings, "P_YEAR").sql_type == "DOUBLE"


def test_path_parameters_are_path_located() -> None:
    doc = document(preview_paths(), Preview=obj(id=INT))
    param = derive(doc)["GAMES_PREVIEW"].params[0]
    assert (param.name, param.column, param.location) == ("gameId", "P_GAME_ID", "path")


def test_non_scalar_parameters_are_errors() -> None:
    paths = {"/x": get_op(arr(obj(a=INT)), query("ids", arr(INT)))}
    errors = errors_of(doc=document(paths))
    assert any("ids" in e for e in errors)


def test_spec_required_params_become_a_required_rule() -> None:
    tables = derive()
    rule = tables["TEAMS_MATCHUP"].rules[0]
    assert (rule.id, rule.type, rule.params) == (
        "teams_matchup.spec_required",
        "required",
        ("team1", "team2"),
    )
    assert tables["TEAMS_MATCHUP_GAMES"].rules == tables["TEAMS_MATCHUP"].rules
    assert tables["RATINGS_SP"].rules == ()


def test_spec_required_rule_skips_params_an_override_already_requires() -> None:
    own = Rule(id="teams_matchup.need_team1", type="required", params=("team1",), message="")
    options = EndpointOverrides("/teams/matchup", rules=(own,))
    rules = derive(endpoints={"/teams/matchup": options})["TEAMS_MATCHUP"].rules
    assert [(r.id, r.params) for r in rules] == [
        ("teams_matchup.spec_required", ("team2",)),
        ("teams_matchup.need_team1", ("team1",)),
    ]


def test_override_rules_are_passed_through_unchanged() -> None:
    rule = Rule(
        id="ratings_sp.year_or_team",
        type="one_of",
        params=("year", "team"),
        message="pick one",
        details={"x": [1]},
    )
    options = EndpointOverrides("/ratings/sp", rules=(rule,))
    assert derive(endpoints={"/ratings/sp": options})["RATINGS_SP"].rules == (rule,)


def test_param_comments_carry_description_rules_values_and_gating() -> None:
    rule = Rule(
        id="rankings.year_or_type", type="one_of", params=("year", "seasonType"), message="pick one"
    )
    default = Rule(
        id="rankings.default_season_type",
        type="default",
        params=("seasonType",),
        message="",
        details={"value": "both"},
    )
    options = EndpointOverrides(
        "/rankings", rules=(rule, default), gated_by="weather", defaults={"seasonType": "both"}
    )
    rankings = derive(endpoints={"/rankings": options})["RANKINGS"]
    comment = column(rankings, "P_SEASON_TYPE").comment or ""
    for text in (
        "Season type",
        "rankings.year_or_type",
        "pick one",
        "rankings.default_season_type",
        "both",
        "regular, postseason, both",
        "weather",
    ):
        assert text in comment
    year = column(rankings, "P_YEAR").comment or ""
    assert "rankings.spec_required" in year
    assert "rankings.default_season_type" not in year


def test_comments_are_clamped_to_the_exasol_limit() -> None:
    long = {**STR, "description": "x" * 5000}
    doc = only("/ratings/sp", TeamSP=obj(team=long))
    comment = column(derive(doc)["RATINGS_SP"], "TEAM").comment or ""
    assert len(comment) == 2000
    assert comment.endswith("…")


def test_excluded_endpoints_are_skipped() -> None:
    tables = derive(excluded=("/rankings",))
    assert not any(name.startswith("RANKINGS") for name in tables)


def test_errors_from_all_endpoints_are_listed_together() -> None:
    paths = {
        "/a": get_op(arr(obj(deepLeft=NUM, **{"deep left": NUM}))),
        "/b": get_op(arr(obj(rowNo=INT))),
    }
    errors = errors_of(doc=document(paths))
    assert any("/a" in e for e in errors)
    assert any("/b" in e for e in errors)


def test_derivation_is_deterministic() -> None:
    first = [t.to_dict() for t in derive().values()]
    second = [t.to_dict() for t in derive().values()]
    assert json.dumps(first) == json.dumps(second)


REAL_CURATED = {
    "/games/{gameId}/preview": (("assembledAt",), ("availability",)),
    "/teams/season/overview": (("record",),),
}
REAL_COLLISION_FIXES: dict[str, FieldMap] = {
    "/coaches": {("seasons",): FieldOverride(type=None, size=None, rename="SEASON_HISTORY")},
    "/playoffs/cfp": {("participants",): FieldOverride(type="json", size=None, rename=None)},
}


def test_real_spec_derives_with_curated_large_endpoints() -> None:
    document_ = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    endpoints = {
        path: EndpointOverrides(path, curated=curated, raw_json=True)
        for path, curated in REAL_CURATED.items()
    }
    for path, fields in REAL_COLLISION_FIXES.items():
        endpoints[path] = EndpointOverrides(path, fields=fields)
    overrides = Overrides(endpoints=endpoints, children={}, acknowledged=(), excluded=())
    tables = derive_tables(read_operations(document_), overrides, RESERVED)
    endpoint_tables = [t for t in tables if t.kind == "endpoint"]
    assert len(endpoint_tables) == 93
    by_name = {t.name: t for t in tables}
    assert "RANKINGS_POLLS_RANKS" in by_name
    assert "GAMES_TEAMS_TEAMS_STATS" in by_name
    assert names(by_name["GAMES_PREVIEW"], "value") == ["ASSEMBLED_AT", "AVAILABILITY"]
    assert by_name["COACHES_SEASON_HISTORY"].parent == "COACHES"
    assert by_name["COACHES_SEASONS"].kind == "endpoint"
    assert column(by_name["PLAYOFFS_CFP"], "PARTICIPANTS").json is True
    assert all(len(names(t, "value")) <= MAX_FLATTENED_FIELDS for t in tables)


def test_real_spec_child_tables_collide_with_two_endpoints() -> None:
    document_ = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    endpoints = {
        path: EndpointOverrides(path, curated=curated, raw_json=True)
        for path, curated in REAL_CURATED.items()
    }
    overrides = Overrides(endpoints=endpoints, children={}, acknowledged=(), excluded=())
    with pytest.raises(TablesError) as caught:
        derive_tables(read_operations(document_), overrides, RESERVED)
    assert sorted(caught.value.errors) == [
        "table name COACHES_SEASONS is produced by both /coaches (array seasons) "
        "and /coaches/seasons",
        "table name PLAYOFFS_CFP_PARTICIPANTS is produced by both /playoffs/cfp "
        "(array participants) and /playoffs/cfp/participants",
    ]


def test_real_spec_without_curation_fails_on_the_large_endpoints() -> None:
    document_ = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    overrides = Overrides(endpoints={}, children={}, acknowledged=(), excluded=())
    with pytest.raises(TablesError) as caught:
        derive_tables(read_operations(document_), overrides, RESERVED)
    failing = [e for e in caught.value.errors if "curated" in e]
    assert len(failing) == 2
    assert any("/games/{gameId}/preview" in e for e in failing)
    assert any("/teams/season/overview" in e for e in failing)
