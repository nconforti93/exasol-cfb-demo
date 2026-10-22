from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from tests.unit.filter_fixtures import (
    and_,
    between,
    boolean,
    col,
    date,
    dbl,
    eq,
    games_table,
    greater,
    in_list,
    is_not_null,
    is_null,
    less,
    less_eq,
    like,
    ne,
    not_,
    null,
    num,
    or_,
    text,
    timestamp,
)
from vs.cfbd_vs.catalog import Column
from vs.cfbd_vs.filters import (
    MAX_DEPTH,
    And,
    Between,
    ColumnRef,
    Comparison,
    FilterError,
    InList,
    IsNotNull,
    IsNull,
    Like,
    Literal,
    Not,
    Or,
    PushdownError,
    parse_filter,
)

TABLE = games_table()


def parse(node: Any) -> Any:
    return parse_filter(node, TABLE)


def test_missing_filter_is_none() -> None:
    assert parse(None) is None


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("P_YEAR", "param"),
        ("HOME_POINTS", "value"),
        ("ROW_NO", "position"),
        ("REQ_SOURCE", "meta"),
    ],
)
def test_columns_resolve_to_catalog_kind(name: str, kind: str) -> None:
    ref = parse(col(name))
    assert ref == ColumnRef(name=name, kind=kind)
    assert ref.is_param is (kind == "param")


def test_parameter_column_carries_its_param() -> None:
    ref = parse(col("P_SEASON_TYPE"))
    assert ref.param is not None
    assert ref.param.name == "seasonType"


def test_unknown_column_is_rejected_with_table_prefix() -> None:
    with pytest.raises(FilterError) as raised:
        parse(eq(col("NOPE"), num(1)))
    assert str(raised.value) == "CFBD_VS.GAMES: the filter references the unknown column NOPE"
    assert isinstance(raised.value, PushdownError)
    assert raised.value.table == "GAMES"


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        (null(), Literal("null", None)),
        (boolean(True), Literal("bool", True)),
        ({"type": "literal_bool", "value": "false"}, Literal("bool", False)),
        (num("2024"), Literal("exactnumeric", Decimal("2024"))),
        (num("-123.45"), Literal("exactnumeric", Decimal("-123.45"))),
        ({"type": "literal_exactnumeric", "value": 7}, Literal("exactnumeric", Decimal(7))),
        (dbl("1.2345000000000000e+02"), Literal("double", Decimal("123.45"))),
        ({"type": "literal_double", "value": 0.5}, Literal("double", Decimal("0.5"))),
        (text("Texas A&M"), Literal("string", "Texas A&M")),
        (text(""), Literal("string", "")),
        (date("2024-09-01"), Literal("date", "2024-09-01")),
        (
            timestamp("2024-09-01 12:01:01.123456789"),
            Literal("timestamp", "2024-09-01 12:01:01.123456789"),
        ),
        (timestamp("2024-09-01 12:01:01"), Literal("timestamp", "2024-09-01 12:01:01")),
    ],
)
def test_literals_parse_to_typed_values(node: dict[str, Any], expected: Literal) -> None:
    assert parse(node) == expected


@pytest.mark.parametrize(
    "node",
    [
        num("NaN"),
        num("Infinity"),
        num("1_000"),
        num(" 1"),
        num("1e999999"),
        num("1" * 101),
        num(""),
        num("0x10"),
        num("\u0661\u0662"),
        {"type": "literal_exactnumeric", "value": True},
        {"type": "literal_exactnumeric", "value": 1.5},
        {"type": "literal_exactnumeric"},
        dbl("-inf"),
        {"type": "literal_double", "value": float("nan")},
        {"type": "literal_bool", "value": 1},
        {"type": "literal_bool", "value": "yes"},
        {"type": "literal_string", "value": 5},
        text("nul\x00byte"),
        text("lone \ud800 surrogate"),
        date("2024-9-1"),
        date("2024-09-01'; DROP TABLE X; --"),
        date("\u0662\u0660\u0662\u0664-\u0660\u0669-\u0660\u0661"),
        timestamp("2024-09-01T12:00:00"),
        timestamp("2024-09-01 12:01:01.1234567890"),
    ],
)
def test_invalid_literal_values_are_rejected(node: dict[str, Any]) -> None:
    with pytest.raises(FilterError):
        parse(eq(col("VENUE"), node))


@pytest.mark.parametrize(
    "literal_type", ["literal_interval", "literal_timestamputc", "literal_geometry"]
)
def test_unadvertised_literal_types_are_rejected(literal_type: str) -> None:
    with pytest.raises(FilterError) as raised:
        parse(eq(col("START_DATE"), {"type": literal_type, "value": "x"}))
    assert literal_type in str(raised.value)


def test_comparisons_parse_with_operator_and_operands() -> None:
    assert parse(eq(col("P_YEAR"), num(2024))) == Comparison(
        "=", ColumnRef("P_YEAR", "param"), Literal("exactnumeric", Decimal(2024))
    )
    assert parse(ne(col("VENUE"), text("x"))).op == "<>"
    assert parse(less(col("P_YEAR"), num(1))).op == "<"
    assert parse(less_eq(col("P_YEAR"), num(1))).op == "<="


def test_greater_than_arrives_as_reversed_less() -> None:
    parsed = parse(greater(col("P_YEAR"), num(2019)))
    assert parsed == Comparison(
        "<", Literal("exactnumeric", Decimal(2019)), ColumnRef("P_YEAR", "param")
    )


def test_connectives_parse_recursively() -> None:
    parsed = parse(
        and_(
            eq(col("P_YEAR"), num(2024)),
            or_(greater(col("HOME_POINTS"), num(50)), greater(col("AWAY_POINTS"), num(50))),
            not_(is_null(col("VENUE"))),
        )
    )
    assert isinstance(parsed, And)
    assert len(parsed.items) == 3
    assert isinstance(parsed.items[1], Or)
    assert isinstance(parsed.items[2], Not)
    assert isinstance(parsed.items[2].item, IsNull)


def test_like_with_and_without_escape() -> None:
    plain = parse(like(col("VENUE"), text("M%")))
    assert plain == Like(ColumnRef("VENUE", "value"), Literal("string", "M%"), None)
    escaped = parse(like(col("VENUE"), text("M!%%"), text("!")))
    assert escaped.escape == Literal("string", "!")


def test_between_in_and_null_tests() -> None:
    assert parse(between(col("P_YEAR"), num(2019), num(2021))) == Between(
        ColumnRef("P_YEAR", "param"),
        Literal("exactnumeric", Decimal(2019)),
        Literal("exactnumeric", Decimal(2021)),
    )
    parsed = parse(in_list(col("P_TEAM"), text("Michigan"), text("Ohio State")))
    assert parsed == InList(
        ColumnRef("P_TEAM", "param"),
        (Literal("string", "Michigan"), Literal("string", "Ohio State")),
    )
    assert parse(is_null(col("VENUE"))) == IsNull(ColumnRef("VENUE", "value"))
    assert parse(is_not_null(col("VENUE"))) == IsNotNull(ColumnRef("VENUE", "value"))


@pytest.mark.parametrize(
    "node_type",
    [
        "predicate_like_regexp",
        "predicate_is_json",
        "function_scalar",
        "function_aggregate",
        "sub_select",
    ],
)
def test_unadvertised_elements_are_rejected_by_name(node_type: str) -> None:
    with pytest.raises(FilterError) as raised:
        parse({"type": node_type, "expression": col("VENUE")})
    assert str(raised.value) == (
        f"CFBD_VS.GAMES: the filter contains the unsupported element {node_type}"
    )


@pytest.mark.parametrize(
    "node",
    [
        "predicate_and",
        ["predicate_and"],
        {"expressions": []},
        {"type": 5},
        and_(),
        or_(),
        {"type": "predicate_and", "expressions": "x"},
        {"type": "predicate_equal", "left": col("P_YEAR")},
        {"type": "predicate_between", "expression": col("P_YEAR"), "left": num(1)},
        {"type": "predicate_in_constlist", "expression": col("P_YEAR"), "arguments": []},
        {"type": "predicate_in_constlist", "expression": col("P_YEAR"), "arguments": num(1)},
        {"type": "predicate_not"},
        {"type": "predicate_like", "expression": col("VENUE")},
        {"type": "column"},
        {"type": "column", "name": 5},
    ],
)
def test_malformed_filters_are_rejected(node: Any) -> None:
    with pytest.raises(FilterError):
        parse(node)


def test_nesting_beyond_the_depth_limit_is_rejected() -> None:
    node: dict[str, Any] = eq(col("P_YEAR"), num(1))
    for _ in range(MAX_DEPTH):
        node = not_(node)
    with pytest.raises(FilterError) as raised:
        parse(node)
    assert "nested deeper than" in str(raised.value)


def test_nesting_at_the_depth_limit_is_accepted() -> None:
    node: dict[str, Any] = col("NEUTRAL_SITE")
    for _ in range(MAX_DEPTH - 1):
        node = not_(node)
    assert isinstance(parse(node), Not)


def test_parameter_column_without_catalog_param_is_a_catalog_error() -> None:
    table = games_table(params=())
    broken = replace(table, columns=(*table.columns, Column(name="P_YEAR", kind="param")))
    with pytest.raises(FilterError) as raised:
        parse_filter(col("P_YEAR"), broken)
    assert "no parameter" in str(raised.value)
