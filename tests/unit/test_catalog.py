import dataclasses
import hashlib
import json
from typing import Any

import pytest

from vs.cfbd_vs.catalog import (
    Catalog,
    CatalogError,
    Column,
    ExcludedEndpoint,
    Param,
    Rule,
    Table,
)


def make_param(name: str = "year", column: str = "P_YEAR") -> Param:
    return Param(
        name=name,
        column=column,
        location="query",
        type="integer",
        sql_type="DECIMAL(18,0)",
        enum=None,
    )


def make_table(name: str = "RANKINGS_POLLS") -> Table:
    return Table(
        name=name,
        endpoint="/rankings",
        kind="child",
        parent="RANKINGS",
        array_path=("polls",),
        hoisted=False,
        read_through=True,
        gated_by=None,
        params=(make_param(),),
        columns=(
            Column(name="ROW_NO", kind="position"),
            Column(name="POLLS_NO", kind="position"),
            Column(name="SEASON", kind="parent_key", path=("season",), sql_type="DECIMAL(18,0)"),
            Column(
                name="POLL",
                kind="value",
                path=("poll",),
                sql_type="VARCHAR(2000) UTF8",
                comment="Poll name",
            ),
            Column(name="P_YEAR", kind="param"),
            Column(name="REQ_SLICE", kind="meta"),
        ),
        rules=(
            Rule(
                id="rankings.required_year",
                type="required",
                params=("year",),
                message="year is required",
            ),
        ),
    )


def make_catalog(tables: tuple[Table, ...] | None = None) -> Catalog:
    return Catalog(
        catalog_hash="a" * 64,
        spec_version="5.33.0",
        generator_version="1",
        excluded=(ExcludedEndpoint(path="/live/plays", reason="streaming"),),
        tables=tables if tables is not None else (make_table(),),
    )


def test_to_dict_follows_documented_key_order() -> None:
    data = make_catalog().to_dict()
    assert list(data) == [
        "catalog_hash",
        "spec_version",
        "generator_version",
        "excluded",
        "tables",
    ]
    table = data["tables"][0]
    assert list(table) == [
        "name",
        "endpoint",
        "kind",
        "parent",
        "array_path",
        "hoisted",
        "read_through",
        "gated_by",
        "params",
        "columns",
        "rules",
    ]
    assert list(table["params"][0]) == [
        "name",
        "column",
        "in",
        "type",
        "sql_type",
        "enum",
    ]


def test_columns_omit_unset_optional_fields() -> None:
    columns = make_catalog().to_dict()["tables"][0]["columns"]
    assert columns[0] == {"name": "ROW_NO", "kind": "position"}
    assert columns[3] == {
        "name": "POLL",
        "kind": "value",
        "path": ["poll"],
        "sql_type": "VARCHAR(2000) UTF8",
        "comment": "Poll name",
    }


def test_json_round_trip_is_lossless() -> None:
    original = make_catalog()
    restored = Catalog.from_json(original.to_json())
    assert restored == original


def test_serialization_is_byte_stable() -> None:
    assert make_catalog().to_json() == make_catalog().to_json()
    assert make_catalog().to_json().endswith("\n")


def test_serialization_is_independent_of_table_order() -> None:
    first = make_table("AAA")
    second = make_table("BBB")
    forward = make_catalog((first, second)).to_json()
    backward = make_catalog((second, first)).to_json()
    assert forward == backward
    assert [t["name"] for t in json.loads(forward)["tables"]] == ["AAA", "BBB"]


def test_serialization_keeps_column_order() -> None:
    names = [c["name"] for c in json.loads(make_catalog().to_json())["tables"][0]["columns"]]
    assert names == ["ROW_NO", "POLLS_NO", "SEASON", "POLL", "P_YEAR", "REQ_SLICE"]


def test_compact_json_is_smaller_and_equivalent() -> None:
    original = make_catalog()
    compact = original.to_json(indent=None)
    assert len(compact) < len(original.to_json())
    assert Catalog.from_json(compact) == original


def test_non_ascii_text_is_kept_verbatim() -> None:
    table = make_table()
    column = Column(name="TEAM", kind="value", path=("team",), comment="Saint-Étienne")
    table = Table(**{**vars(table), "columns": (*table.columns, column)})
    text = make_catalog((table,)).to_json()
    assert "Saint-Étienne" in text
    assert Catalog.from_json(text) == make_catalog((table,))


def test_a_row_cap_rule_round_trips_with_its_details() -> None:
    rule = Rule(
        id="games.row_cap",
        type="row_cap",
        params=("gameId",),
        message="m",
        details={"rows": 2000, "complete_with": ["gameId"]},
    )
    table = Table(**{**vars(make_table()), "rules": (rule,)})
    data = make_catalog((table,)).to_dict()["tables"][0]
    assert "row_cap" not in data
    assert data["rules"][0]["details"] == {"rows": 2000, "complete_with": ["gameId"]}
    assert Catalog.from_json(make_catalog((table,)).to_json()).tables[0].rules == (rule,)


def test_rule_details_round_trip() -> None:
    rule = Rule(
        id="games_teams.week_team_or_conference_with_year",
        type="requires_any",
        params=(),
        message="needs week, team or conference",
        details={"if": "year", "any_of": ["week", "team", "conference"]},
    )
    table = Table(**{**vars(make_table()), "rules": (rule,)})
    restored = Catalog.from_json(make_catalog((table,)).to_json())
    assert restored.tables[0].rules == (rule,)


def test_rule_without_details_omits_the_key() -> None:
    rule = make_catalog().to_dict()["tables"][0]["rules"][0]
    assert rule == {
        "id": "rankings.required_year",
        "type": "required",
        "params": ["year"],
        "message": "year is required",
    }


def test_table_lookup_by_name() -> None:
    assert make_catalog().table("RANKINGS_POLLS").endpoint == "/rankings"


def test_unknown_table_lookup_names_the_table() -> None:
    with pytest.raises(CatalogError, match="NOPE"):
        make_catalog().table("NOPE")


def test_duplicate_table_names_are_rejected() -> None:
    with pytest.raises(CatalogError, match="RANKINGS_POLLS"):
        make_catalog((make_table(), make_table()))


def test_unknown_rule_type_is_rejected() -> None:
    with pytest.raises(CatalogError, match="bogus"):
        Rule(id="x.y", type="bogus", params=(), message="m")


def test_every_documented_rule_type_is_accepted() -> None:
    for rule_type in (
        "required",
        "one_of",
        "requires_any",
        "excludes",
        "requires_value",
        "bounds",
        "default",
        "row_cap",
    ):
        assert Rule(id="x.y", type=rule_type, params=(), message="m").type == rule_type


def test_unknown_column_kind_is_rejected() -> None:
    with pytest.raises(CatalogError, match="weird"):
        Column(name="X", kind="weird")


def test_unknown_table_kind_is_rejected() -> None:
    with pytest.raises(CatalogError, match="weird"):
        Table(**{**vars(make_table()), "kind": "weird"})


def test_from_json_rejects_missing_keys() -> None:
    data = make_catalog().to_dict()
    del data["spec_version"]
    with pytest.raises(CatalogError, match="spec_version"):
        Catalog.from_dict(data)


def test_from_json_rejects_invalid_text() -> None:
    with pytest.raises(CatalogError):
        Catalog.from_json("not json")


def test_from_json_rejects_non_object() -> None:
    with pytest.raises(CatalogError):
        Catalog.from_json("[]")


def test_content_hash_is_hex_sha256_of_the_compact_json_with_a_blank_hash() -> None:
    value = make_catalog().content_hash()
    blank = dataclasses.replace(make_catalog(), catalog_hash="")
    assert value == hashlib.sha256(blank.to_json(indent=None).encode("utf-8")).hexdigest()
    assert len(value) == 64


def test_content_hash_ignores_the_stored_hash_field() -> None:
    other = dataclasses.replace(make_catalog(), catalog_hash="b" * 64)
    assert other.content_hash() == make_catalog().content_hash()


def test_content_hash_is_deterministic_across_a_json_round_trip() -> None:
    original = make_catalog()
    assert Catalog.from_json(original.to_json()).content_hash() == original.content_hash()


def test_with_content_hash_stamps_the_hash_and_is_idempotent() -> None:
    stamped = make_catalog().with_content_hash()
    assert stamped.catalog_hash == stamped.content_hash()
    assert stamped.with_content_hash() == stamped


def changed_column_type(base: Catalog) -> Catalog:
    (table,) = base.tables
    columns = tuple(
        dataclasses.replace(c, sql_type="DECIMAL(9,0)") if c.name == "SEASON" else c
        for c in table.columns
    )
    return dataclasses.replace(base, tables=(dataclasses.replace(table, columns=columns),))


def changed_parent_key_path(base: Catalog) -> Catalog:
    (table,) = base.tables
    columns = tuple(
        dataclasses.replace(c, path=("year",)) if c.name == "SEASON" else c for c in table.columns
    )
    return dataclasses.replace(base, tables=(dataclasses.replace(table, columns=columns),))


def changed_rule_message(base: Catalog) -> Catalog:
    (table,) = base.tables
    rule = dataclasses.replace(table.rules[0], message="year is mandatory")
    return dataclasses.replace(base, tables=(dataclasses.replace(table, rules=(rule,)),))


@pytest.mark.parametrize(
    "mutate",
    [
        changed_column_type,
        changed_parent_key_path,
        changed_rule_message,
        lambda c: dataclasses.replace(c, spec_version="5.34.0"),
        lambda c: dataclasses.replace(c, generator_version="2"),
        lambda c: dataclasses.replace(c, excluded=()),
        lambda c: dataclasses.replace(c, tables=(make_table(), make_table("OTHER"))),
    ],
)
def test_content_hash_depends_on_the_generated_content(mutate: Any) -> None:
    base = make_catalog()
    assert mutate(base).content_hash() != base.content_hash()


def with_column_type(sql_type: str) -> Catalog:
    table = make_table()
    columns = tuple(
        dataclasses.replace(c, sql_type=sql_type) if c.name == "SEASON" else c
        for c in table.columns
    )
    return make_catalog((dataclasses.replace(table, columns=columns),))


@pytest.mark.parametrize("sql_type", ["INT", "DECIMAL(18, 0)", "VARCHAR(10)", "TIMESTAMP(3)"])
def test_a_column_type_outside_the_shared_grammar_is_refused(sql_type: str) -> None:
    with pytest.raises(CatalogError, match=r"RANKINGS_POLLS\.SEASON.*unsupported column type"):
        with_column_type(sql_type)


def test_a_parameter_type_outside_the_shared_grammar_is_refused() -> None:
    table = make_table()
    param = dataclasses.replace(make_param(), sql_type="NUMBER")
    with pytest.raises(CatalogError, match=r"RANKINGS_POLLS\.year.*unsupported column type"):
        make_catalog((dataclasses.replace(table, params=(param,)),))


def test_a_malformed_type_in_the_json_is_a_catalog_error() -> None:
    text = make_catalog().to_json().replace("DECIMAL(18,0)", "DECIMAL(18, 0)")
    with pytest.raises(CatalogError, match="unsupported column type"):
        Catalog.from_json(text)


def test_json_flag_is_omitted_when_false_and_round_trips_when_true() -> None:
    plain = Column(name="POLL", kind="value", path=("poll",), sql_type="VARCHAR(2000) UTF8")
    scores = Column(
        name="HOME_LINE_SCORES",
        kind="value",
        path=("homeLineScores",),
        sql_type="VARCHAR(2000000) UTF8",
        json=True,
    )
    assert "json" not in plain.to_dict()
    assert scores.to_dict()["json"] is True
    assert Column.from_dict(scores.to_dict()) == scores
    assert Column.from_dict(plain.to_dict()).json is False
