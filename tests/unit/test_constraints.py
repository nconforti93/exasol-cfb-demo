import random
import time
from decimal import Decimal
from typing import Any

import pytest

from tests.unit.filter_fixtures import (
    SEASON_TYPES,
    and_,
    between,
    boolean,
    col,
    date,
    dbl,
    eq,
    games_table,
    greater,
    greater_eq,
    in_list,
    is_not_null,
    is_null,
    less,
    less_eq,
    like,
    make_param,
    ne,
    not_,
    null,
    num,
    or_,
    text,
)
from vs.cfbd_vs.constraints import (
    ConstraintError,
    IntRanges,
    SolvedFilter,
    ValueSet,
    solve,
)
from vs.cfbd_vs.filters import (
    And,
    ColumnRef,
    Comparison,
    Literal,
    Or,
    PushdownError,
    parse_filter,
)

TABLE = games_table()
PREFIX = "CFBD_VS.GAMES: "
INTEGER_FORMS = "= value, IN (values), <, <=, >, >=, BETWEEN low AND high"
OTHER_FORMS = "= value, IN (values)"


def solved(node: Any) -> SolvedFilter:
    return solve(parse_filter(node, TABLE), TABLE)


def rejection(node: Any) -> str:
    with pytest.raises(ConstraintError) as raised:
        solved(node)
    assert isinstance(raised.value, PushdownError)
    return str(raised.value)


def values_of(result: SolvedFilter, param: str) -> list[Any]:
    constraint = result.constraint(param)
    assert constraint is not None
    return list(constraint.values())


def open_range(column: str) -> str:
    return (
        f"{PREFIX}{column} needs a bounded range or an IN list: give both a lower and an upper "
        f"bound (for example {column} BETWEEN <low> AND <high>) or list the values "
        f"({column} IN (...))"
    )


def cross_or(columns: str) -> str:
    return (
        f"{PREFIX}OR is supported only between predicates on one and the same parameter column, "
        f"but this OR involves {columns}; use UNION ALL of separate queries instead"
    )


def unsupported(form: str, column: str, forms: str) -> str:
    return (
        f"{PREFIX}unsupported predicate {form} on parameter column {column}; supported for "
        f"{column}: {forms}, combined with AND, or with OR on {column} only"
    )


# --- splitting and the empty filter ---


def test_no_filter_solves_to_no_constraints() -> None:
    result = solve(None, TABLE)
    assert result == SolvedFilter(contradiction=False, constraints=(), residual=())
    assert result.request_count() == 1


def test_equality_on_a_parameter_becomes_a_single_value() -> None:
    result = solved(eq(col("P_YEAR"), num(2024)))
    assert values_of(result, "year") == [2024]
    assert result.request_count() == 1
    assert result.residual == ()


def test_literal_on_the_left_is_accepted() -> None:
    assert values_of(solved(eq(num(2024), col("P_YEAR"))), "year") == [2024]


def test_response_predicates_are_residual_in_filter_order() -> None:
    venue = like(col("VENUE"), text("M%"))
    points = or_(greater(col("HOME_POINTS"), num(50)), greater(col("AWAY_POINTS"), num(50)))
    result = solved(and_(eq(col("P_YEAR"), num(2024)), points, venue))
    assert values_of(result, "year") == [2024]
    assert result.residual == (parse_filter(points, TABLE), parse_filter(venue, TABLE))


def test_position_and_meta_predicates_are_residual() -> None:
    stored = eq(col("REQ_SOURCE"), text("STORED"))
    row = less_eq(col("ROW_NO"), num(3))
    result = solved(and_(eq(col("P_YEAR"), num(2024)), stored, row))
    assert len(result.residual) == 2


def test_nested_top_level_ands_are_flattened() -> None:
    result = solved(and_(and_(eq(col("P_YEAR"), num(2024)), eq(col("P_WEEK"), num(8)))))
    assert values_of(result, "year") == [2024]
    assert values_of(result, "week") == [8]


def test_constraints_follow_catalog_parameter_order() -> None:
    result = solved(and_(eq(col("P_TEAM"), text("Michigan")), eq(col("P_YEAR"), num(2024))))
    assert [c.param.name for c in result.constraints] == ["year", "team"]
    assert result.constraint("week") is None


def test_true_constants_are_dropped() -> None:
    result = solved(and_(eq(col("P_YEAR"), num(2024)), boolean(True)))
    assert result.residual == ()
    assert result.contradiction is False


def test_constant_only_comparisons_stay_residual() -> None:
    result = solved(and_(eq(col("P_YEAR"), num(2024)), eq(num(1), num(1))))
    assert result.residual == (
        Comparison("=", Literal("exactnumeric", Decimal(1)), Literal("exactnumeric", Decimal(1))),
    )


def test_bare_boolean_response_column_is_residual() -> None:
    result = solved(and_(eq(col("P_YEAR"), num(2024)), col("NEUTRAL_SITE")))
    assert result.residual == (ColumnRef("NEUTRAL_SITE", "value"),)


# --- ranges, IN lists, OR ---


@pytest.mark.parametrize(
    "node",
    [
        between(col("P_YEAR"), num(2019), num(2021)),
        and_(greater_eq(col("P_YEAR"), num(2019)), less(col("P_YEAR"), num(2022))),
        and_(greater(col("P_YEAR"), num(2018)), less_eq(col("P_YEAR"), num(2021))),
        and_(greater(col("P_YEAR"), dbl("2.0185e+03")), less(col("P_YEAR"), num("2021.5"))),
        and_(greater_eq(col("P_YEAR"), num("2018.1")), less_eq(col("P_YEAR"), dbl("2021.9"))),
        between(col("P_YEAR"), num("2018.5"), num("2021.99")),
    ],
)
def test_bounded_integer_ranges_tighten_and_enumerate(node: Any) -> None:
    result = solved(node)
    assert values_of(result, "year") == [2019, 2020, 2021]
    constraint = result.constraint("year")
    assert constraint is not None
    assert constraint.domain == IntRanges(((2019, 2021),))
    assert (constraint.min_value, constraint.max_value) == (2019, 2021)


def test_in_list_produces_one_value_per_item() -> None:
    result = solved(in_list(col("P_TEAM"), text("Ohio State"), text("Michigan")))
    assert values_of(result, "team") == ["Michigan", "Ohio State"]
    assert result.request_count() == 2


def test_duplicate_values_are_requested_once() -> None:
    result = solved(
        and_(
            in_list(col("P_YEAR"), num(2020), num(2020), num(2021)),
            between(col("P_YEAR"), num(2019), num(2020)),
            in_list(col("P_WEEK"), num(8), num(8)),
        )
    )
    assert values_of(result, "year") == [2020]
    assert values_of(result, "week") == [8]
    assert result.request_count() == 1


def test_range_and_in_list_multiply_into_request_count() -> None:
    result = solved(
        and_(
            between(col("P_YEAR"), num(2019), num(2021)),
            in_list(col("P_WEEK"), num(8), num(9)),
        )
    )
    assert result.request_count() == 6


def test_same_parameter_or_acts_like_in() -> None:
    result = solved(or_(eq(col("P_YEAR"), num(2020)), between(col("P_YEAR"), num(2022), num(2023))))
    assert values_of(result, "year") == [2020, 2022, 2023]
    constraint = result.constraint("year")
    assert constraint is not None
    assert constraint.domain == IntRanges(((2020, 2020), (2022, 2023)))


def test_same_parameter_or_with_nested_and() -> None:
    node = or_(
        eq(col("P_YEAR"), num(2020)),
        and_(greater_eq(col("P_YEAR"), num(2022)), less_eq(col("P_YEAR"), num(2023))),
        or_(eq(col("P_YEAR"), num(2021))),
    )
    assert values_of(solved(node), "year") == [2020, 2021, 2022, 2023]


def test_or_on_a_string_parameter_acts_like_in() -> None:
    node = or_(eq(col("P_TEAM"), text("Michigan")), eq(col("P_TEAM"), text("Ohio State")))
    assert values_of(solved(node), "team") == ["Michigan", "Ohio State"]


def test_open_branch_in_or_is_bounded_by_an_outer_range() -> None:
    node = and_(
        or_(less(col("P_YEAR"), num(2000)), eq(col("P_YEAR"), num(2020))),
        greater_eq(col("P_YEAR"), num(1997)),
    )
    assert values_of(solved(node), "year") == [1997, 1998, 1999, 2020]


def test_huge_ranges_are_counted_without_enumeration() -> None:
    started = time.monotonic()
    result = solved(between(col("P_YEAR"), num(0), num(2_000_000_000)))
    assert result.request_count() == 2_000_000_001
    assert time.monotonic() - started < 1


# --- open ranges ---


@pytest.mark.parametrize(
    "node",
    [
        greater_eq(col("P_YEAR"), num(2020)),
        less(col("P_YEAR"), num(2020)),
        or_(less(col("P_YEAR"), num(2000)), eq(col("P_YEAR"), num(2020))),
        and_(greater_eq(col("P_YEAR"), num(2020)), eq(col("P_WEEK"), num(1))),
    ],
)
def test_open_ranges_are_rejected(node: Any) -> None:
    assert rejection(node) == open_range("P_YEAR")


# --- contradictions ---


@pytest.mark.parametrize(
    "node",
    [
        and_(eq(col("P_YEAR"), num(2024)), eq(col("P_YEAR"), num(2025))),
        between(col("P_YEAR"), num(2025), num(2020)),
        and_(in_list(col("P_YEAR"), num(2019), num(2020)), greater(col("P_YEAR"), num(2021))),
        and_(greater(col("P_YEAR"), num(2024)), less(col("P_YEAR"), num(2025))),
        and_(eq(col("P_TEAM"), text("a")), eq(col("P_TEAM"), text("b"))),
        and_(eq(col("P_LATEST"), boolean(True)), eq(col("P_LATEST"), boolean(False))),
        eq(col("P_YEAR"), num("2024.5")),
        eq(col("P_YEAR"), null()),
        eq(col("P_TEAM"), null()),
        eq(col("P_THRESHOLD"), null()),
        eq(col("P_LATEST"), null()),
        in_list(col("P_YEAR"), null(), null()),
        less(col("P_YEAR"), null()),
        between(col("P_YEAR"), null(), num(2020)),
        eq(col("P_TEAM"), text("")),
        eq(col("P_TEAM"), text("x" * 2001)),
        eq(col("P_YEAR"), num(10**18)),
        greater(col("P_YEAR"), num(10**19)),
        and_(eq(col("P_YEAR"), num(2024)), boolean(False)),
        and_(eq(col("P_YEAR"), num(2024)), null()),
        and_(eq(col("P_THRESHOLD"), num("0.5")), eq(col("P_THRESHOLD"), num("0.25"))),
        eq(col("P_THRESHOLD"), dbl("1e+400")),
    ],
)
def test_contradictions_solve_to_zero_requests(node: Any) -> None:
    result = solved(node)
    assert result == SolvedFilter(contradiction=True, constraints=(), residual=())
    assert result.request_count() == 0


def test_contradiction_wins_over_an_open_range_on_another_parameter() -> None:
    node = and_(
        greater_eq(col("P_YEAR"), num(2020)),
        eq(col("P_WEEK"), num(1)),
        eq(col("P_WEEK"), num(2)),
    )
    assert solved(node).contradiction is True


def test_contradiction_ignores_residual_predicates() -> None:
    node = and_(eq(col("P_YEAR"), num(1)), eq(col("P_YEAR"), num(2)), col("NEUTRAL_SITE"))
    assert solved(node).residual == ()


def test_rejections_win_over_contradictions() -> None:
    node = and_(
        eq(col("P_YEAR"), num(1)),
        eq(col("P_YEAR"), num(2)),
        like(col("P_TEAM"), text("M%")),
    )
    assert rejection(node) == unsupported("LIKE", "P_TEAM", OTHER_FORMS)


def test_null_in_an_in_list_is_ignored() -> None:
    assert values_of(solved(in_list(col("P_YEAR"), num(2020), null())), "year") == [2020]


def test_values_beyond_the_echo_column_size_are_dropped() -> None:
    result = solved(in_list(col("P_TEAM"), text("x" * 2001), text("y" * 2000)))
    assert values_of(result, "team") == ["y" * 2000]


def test_integer_ranges_are_clamped_to_the_echo_column_type() -> None:
    limit = 10**18 - 1
    node = between(col("P_YEAR"), num(limit - 1), num(10**30))
    assert values_of(solved(node), "year") == [limit - 1, limit]


# --- cross-parameter OR ---


@pytest.mark.parametrize(
    ("node", "columns"),
    [
        (
            or_(eq(col("P_YEAR"), num(2024)), eq(col("P_TEAM"), text("Michigan"))),
            "P_YEAR and P_TEAM",
        ),
        (
            or_(eq(col("P_YEAR"), num(2024)), greater(col("HOME_POINTS"), num(50))),
            "P_YEAR and HOME_POINTS",
        ),
        (
            or_(
                eq(col("P_YEAR"), num(2024)),
                and_(eq(col("P_WEEK"), num(1)), eq(col("VENUE"), text("x"))),
            ),
            "P_YEAR, P_WEEK and VENUE",
        ),
        (or_(eq(col("P_YEAR"), num(2024)), eq(num(1), num(1))), "P_YEAR and a constant"),
        (not_(or_(eq(col("P_YEAR"), num(1)), eq(col("P_WEEK"), num(1)))), "P_YEAR and P_WEEK"),
    ],
)
def test_or_across_columns_is_rejected(node: Any, columns: str) -> None:
    assert rejection(node) == cross_or(columns)


# --- filter shapes observed on nano 2026.2 (probe 1.4), as raw request JSON ---

P_YEAR_JSON = {"type": "column", "tableName": "GAMES", "columnNr": 9, "name": "P_YEAR"}
P_LATEST_JSON = {"type": "column", "tableName": "GAMES", "columnNr": 13, "name": "P_LATEST"}
YEAR_2024_JSON = {"type": "literal_exactnumeric", "value": "2024"}
NULL_JSON = {"type": "literal_null"}


def test_probe_shape_greater_arrives_as_swapped_less() -> None:
    node = {
        "type": "predicate_and",
        "expressions": [
            {"type": "predicate_less", "left": num(2018), "right": P_YEAR_JSON},
            {"type": "predicate_lessequal", "left": P_YEAR_JSON, "right": num(2021)},
            {"type": "predicate_lessequal", "left": num(2019), "right": P_YEAR_JSON},
        ],
    }
    assert values_of(solved(node), "year") == [2019, 2020, 2021]


def test_probe_shape_single_item_in_arrives_as_equal() -> None:
    node = {"type": "predicate_equal", "left": P_YEAR_JSON, "right": YEAR_2024_JSON}
    assert values_of(solved(node), "year") == [2024]


def test_probe_shape_folded_arithmetic_arrives_as_exactnumeric() -> None:
    node = {"type": "predicate_equal", "left": YEAR_2024_JSON, "right": P_YEAR_JSON}
    assert values_of(solved(node), "year") == [2024]


def test_probe_shape_quoted_number_stays_a_string_and_is_rejected() -> None:
    node = {
        "type": "predicate_equal",
        "left": P_YEAR_JSON,
        "right": {"type": "literal_string", "value": "2024"},
    }
    assert rejection(node) == (
        f"{PREFIX}P_YEAR is an integer parameter and cannot take the string literal '2024'"
    )


def test_probe_shape_unfolded_contradiction_solves_to_zero_requests() -> None:
    node = {
        "type": "predicate_and",
        "expressions": [
            {"type": "predicate_equal", "left": P_YEAR_JSON, "right": YEAR_2024_JSON},
            {"type": "predicate_equal", "left": P_YEAR_JSON, "right": num(2025)},
        ],
    }
    assert solved(node).contradiction is True


@pytest.mark.parametrize(
    "node",
    [
        NULL_JSON,
        {
            "type": "predicate_and",
            "expressions": [
                {"type": "predicate_equal", "left": P_YEAR_JSON, "right": YEAR_2024_JSON},
                NULL_JSON,
            ],
        },
    ],
)
def test_probe_shape_equals_null_arrives_as_literal_null(node: Any) -> None:
    assert solved(node).contradiction is True


def test_folded_null_inside_a_same_parameter_or_matches_nothing() -> None:
    node = {
        "type": "predicate_or",
        "expressions": [
            {"type": "predicate_equal", "left": P_YEAR_JSON, "right": YEAR_2024_JSON},
            NULL_JSON,
        ],
    }
    assert values_of(solved(node), "year") == [2024]


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        (or_(eq(col("P_YEAR"), num(2020)), boolean(False)), [2020]),
        (or_(and_(eq(col("P_YEAR"), num(1)), null()), eq(col("P_YEAR"), num(2))), [2]),
        (or_(and_(eq(col("P_YEAR"), num(1)), boolean(True)), eq(col("P_YEAR"), num(2))), [1, 2]),
    ],
)
def test_boolean_constants_inside_parameter_formulas(node: Any, expected: list[int]) -> None:
    assert values_of(solved(node), "year") == expected


def test_contradiction_through_a_folded_null_branch() -> None:
    node = and_(eq(col("P_YEAR"), num(2020)), or_(eq(col("P_YEAR"), num(2021)), null()))
    assert solved(node).contradiction is True


def test_true_branch_leaves_the_parameter_unconstrained() -> None:
    result = solved(and_(or_(eq(col("P_TEAM"), text("a")), boolean(True)), col("NEUTRAL_SITE")))
    assert result.contradiction is False
    assert result.constraint("team") is None
    assert result.request_count() == 1


def test_probe_shape_bare_boolean_column_is_true() -> None:
    node = {
        "type": "predicate_and",
        "expressions": [
            {"type": "predicate_equal", "left": P_YEAR_JSON, "right": YEAR_2024_JSON},
            P_LATEST_JSON,
        ],
    }
    result = solved(node)
    assert values_of(result, "latest") == [True]
    assert values_of(result, "year") == [2024]


# --- unsupported predicate forms ---


@pytest.mark.parametrize(
    ("node", "form", "column", "forms"),
    [
        (like(col("P_TEAM"), text("Mich%")), "LIKE", "P_TEAM", OTHER_FORMS),
        (ne(col("P_TEAM"), text("Michigan")), "<>", "P_TEAM", OTHER_FORMS),
        (is_null(col("P_TEAM")), "IS NULL", "P_TEAM", OTHER_FORMS),
        (is_not_null(col("P_YEAR")), "IS NOT NULL", "P_YEAR", INTEGER_FORMS),
        (not_(eq(col("P_WEEK"), num(3))), "NOT", "P_WEEK", INTEGER_FORMS),
        (less(col("P_TEAM"), text("M")), "<", "P_TEAM", OTHER_FORMS),
        (less_eq(col("P_TEAM"), text("M")), "<=", "P_TEAM", OTHER_FORMS),
        (greater(col("P_TEAM"), text("M")), ">", "P_TEAM", OTHER_FORMS),
        (greater_eq(col("P_TEAM"), text("M")), ">=", "P_TEAM", OTHER_FORMS),
        (between(col("P_TEAM"), text("A"), text("M")), "BETWEEN", "P_TEAM", OTHER_FORMS),
        (between(col("P_THRESHOLD"), num(0), num(1)), "BETWEEN", "P_THRESHOLD", OTHER_FORMS),
        (less(col("P_THRESHOLD"), num(1)), "<", "P_THRESHOLD", OTHER_FORMS),
        (between(num(2020), col("P_YEAR"), num(2025)), "BETWEEN", "P_YEAR", INTEGER_FORMS),
        (in_list(num(2020), col("P_YEAR")), "IN", "P_YEAR", INTEGER_FORMS),
        (
            eq(eq(col("P_YEAR"), num(1)), boolean(True)),
            "(nested comparison)",
            "P_YEAR",
            INTEGER_FORMS,
        ),
        (not_(col("P_LATEST")), "NOT", "P_LATEST", OTHER_FORMS),
    ],
)
def test_unsupported_parameter_predicates_are_rejected(
    node: Any, form: str, column: str, forms: str
) -> None:
    assert rejection(and_(eq(col("P_YEAR"), num(2024)), node)) == unsupported(form, column, forms)


# --- literals only ---


@pytest.mark.parametrize(
    ("node", "column", "operand"),
    [
        (eq(col("P_YEAR"), col("SEASON")), "P_YEAR", "column SEASON"),
        (eq(col("SEASON"), col("P_YEAR")), "P_YEAR", "column SEASON"),
        (eq(col("P_YEAR"), col("P_WEEK")), "P_YEAR", "column P_WEEK"),
        (eq(col("P_YEAR"), col("P_YEAR")), "P_YEAR", "column P_YEAR"),
        (in_list(col("P_YEAR"), num(2020), col("HOME_POINTS")), "P_YEAR", "column HOME_POINTS"),
        (between(col("P_YEAR"), col("SEASON"), num(2025)), "P_YEAR", "column SEASON"),
        (less(col("P_YEAR"), col("SEASON")), "P_YEAR", "column SEASON"),
        (eq(col("P_LATEST"), eq(num(1), num(1))), "P_LATEST", "an expression"),
    ],
)
def test_parameter_values_must_be_literals(node: Any, column: str, operand: str) -> None:
    assert rejection(node) == (
        f"{PREFIX}{column} can only be compared with literal values, not with {operand}"
    )


# --- enums and types ---


def test_enum_violations_list_the_allowed_values() -> None:
    node = and_(eq(col("P_YEAR"), num(2024)), eq(col("P_SEASON_TYPE"), text("Regular")))
    assert rejection(node) == (
        f"{PREFIX}'Regular' is not an allowed value for P_SEASON_TYPE; allowed values: "
        + ", ".join(SEASON_TYPES)
    )


def test_enum_violation_inside_an_in_list_is_rejected() -> None:
    message = rejection(in_list(col("P_SEASON_TYPE"), text("both"), text("O'Neil")))
    assert message.startswith(f"{PREFIX}'O''Neil' is not an allowed value for P_SEASON_TYPE")


def test_valid_enum_values_are_accepted() -> None:
    node = in_list(col("P_SEASON_TYPE"), text("regular"), text("both"))
    assert values_of(solved(node), "seasonType") == ["both", "regular"]


@pytest.mark.parametrize(
    ("node", "message"),
    [
        (
            eq(col("P_YEAR"), text("2024")),
            "P_YEAR is an integer parameter and cannot take the string literal '2024'",
        ),
        (
            eq(col("P_TEAM"), num(5)),
            "P_TEAM is a string parameter and cannot take the numeric literal 5",
        ),
        (
            eq(col("P_LATEST"), num(1)),
            "P_LATEST is a boolean parameter and cannot take the numeric literal 1",
        ),
        (
            eq(col("P_YEAR"), boolean(True)),
            "P_YEAR is an integer parameter and cannot take the boolean literal TRUE",
        ),
        (
            less(col("P_YEAR"), date("2024-01-01")),
            "P_YEAR is an integer parameter and cannot take the date literal DATE '2024-01-01'",
        ),
        (
            eq(col("P_THRESHOLD"), text("0.5")),
            "P_THRESHOLD is a number parameter and cannot take the string literal '0.5'",
        ),
        (col("P_YEAR"), "P_YEAR is an integer parameter and cannot take the boolean literal TRUE"),
    ],
)
def test_type_mismatches_are_rejected(node: Any, message: str) -> None:
    assert rejection(node) == PREFIX + message


def test_long_values_are_shortened_in_messages() -> None:
    message = rejection(eq(col("P_YEAR"), text("x" * 500)))
    assert message.endswith("'" + "x" * 60 + "...'")


# --- typed values ---


def test_boolean_parameter_values() -> None:
    assert values_of(solved(eq(col("P_LATEST"), boolean(True))), "latest") == [True]
    assert values_of(solved(col("P_LATEST")), "latest") == [True]
    both = solved(in_list(col("P_LATEST"), boolean(True), boolean(False)))
    assert values_of(both, "latest") == [False, True]


def test_number_parameter_values_are_normalized_floats() -> None:
    result = solved(in_list(col("P_THRESHOLD"), num("0.50"), dbl("5.0000000000000000e-01")))
    assert values_of(result, "threshold") == [0.5]
    integral = solved(eq(col("P_THRESHOLD"), dbl("8.0000000000000000e+00")))
    assert values_of(integral, "threshold") == [8.0]
    zero = solved(in_list(col("P_THRESHOLD"), num("-0"), num("0.0")))
    [value] = values_of(zero, "threshold")
    assert str(value) == "0.0"


def test_integer_parameter_accepts_integral_doubles() -> None:
    assert values_of(solved(eq(col("P_YEAR"), dbl("2.0240000000000000e+03"))), "year") == [2024]


def test_unknown_parameter_types_are_rejected() -> None:
    odd = make_param("ids", "P_IDS", "array", "VARCHAR(2000) UTF8")
    table = games_table(params=(odd,))
    with pytest.raises(ConstraintError) as raised:
        solve(parse_filter(eq(col("P_IDS"), text("1")), table), table)
    assert "P_IDS has the unsupported parameter type array" in str(raised.value)


@pytest.mark.parametrize(
    "param",
    [
        make_param("year", "P_YEAR", "integer", "DOUBLE"),
        make_param("year", "P_YEAR", "string", "CLOB"),
    ],
)
def test_parameter_with_unexpected_column_type_is_a_catalog_error(param: Any) -> None:
    table = games_table(params=(param,))
    with pytest.raises(ConstraintError) as raised:
        solve(parse_filter(eq(col("P_YEAR"), num(1)), table), table)
    assert "unexpected echo column type" in str(raised.value)


# --- result types ---


def test_int_ranges_membership_and_iteration() -> None:
    ranges = IntRanges(((1, 3), (7, 7)))
    assert ranges.count() == 4
    assert list(ranges) == [1, 2, 3, 7]
    assert 2 in ranges
    assert 7 in ranges
    assert 5 not in ranges
    assert True not in ranges
    assert "2" not in ranges


@pytest.mark.parametrize(
    "intervals", [((3, 1),), ((1, 3), (2, 5)), ((1, 3), (4, 5)), ((5, 6), (1, 2))]
)
def test_int_ranges_reject_non_canonical_intervals(intervals: Any) -> None:
    with pytest.raises(ValueError):
        IntRanges(intervals)


def test_value_set_membership_and_iteration() -> None:
    values = ValueSet(("a", "b"))
    assert values.count() == 2
    assert list(values) == ["a", "b"]
    assert "a" in values
    assert "c" not in values
    assert (values.min_value, values.max_value) == ("a", "b")


def test_constraint_membership_delegates_to_its_domain() -> None:
    constraint = solved(between(col("P_YEAR"), num(1), num(3))).constraint("year")
    assert constraint is not None
    assert 2 in constraint
    assert 4 not in constraint
    assert constraint.count() == 3


def test_value_sets_hand_single_intervals_over_as_lazy_ranges() -> None:
    result = solved(
        and_(
            between(col("P_YEAR"), num(0), num(2_000_000_000)),
            in_list(col("P_WEEK"), num(10), num(8)),
            eq(col("P_TEAM"), text("Michigan")),
        )
    )
    sets = result.value_sets()
    assert sets["year"] == range(0, 2_000_000_001)
    assert sets["week"] == (8, 10)
    assert sets["team"] == ("Michigan",)
    assert list(sets) == ["year", "week", "team"]


def test_value_sets_materialize_multiple_intervals() -> None:
    node = or_(eq(col("P_YEAR"), num(2020)), between(col("P_YEAR"), num(2022), num(2023)))
    assert solved(node).value_sets() == {"year": (2020, 2022, 2023)}


def test_value_sets_of_a_contradiction_are_empty() -> None:
    assert solved(between(col("P_YEAR"), num(2), num(1))).value_sets() == {}


# --- property: interval algebra equals brute-force evaluation ---

UNIVERSE = range(-5, 36)


def _random_literal(rng: random.Random) -> tuple[Any, Decimal | None]:
    if rng.random() < 0.08:
        return null(), None
    value = Decimal(rng.randint(-6, 66)) / 2
    if rng.random() < 0.5:
        return num(str(value)), value
    return dbl(f"{value:.16e}"), value


def _random_leaf(rng: random.Random) -> tuple[Any, Any]:
    if rng.random() < 0.1:
        constant = rng.choice([True, False, None])
        node = null() if constant is None else boolean(constant)
        return node, lambda x: constant is True
    column = col("P_YEAR")
    kind = rng.choice(["eq", "in", "lt", "le", "gt", "ge", "between"])
    if kind == "in":
        pairs = [_random_literal(rng) for _ in range(rng.randint(1, 4))]
        values = [v for _, v in pairs if v is not None]
        return in_list(column, *(n for n, _ in pairs)), lambda x: any(x == v for v in values)
    if kind == "between":
        (low_node, low), (high_node, high) = _random_literal(rng), _random_literal(rng)
        if low is None or high is None:
            return between(column, low_node, high_node), lambda x: False
        return between(column, low_node, high_node), lambda x: low <= x <= high
    node, value = _random_literal(rng)
    if value is None:
        builders = {"eq": eq, "lt": less, "le": less_eq, "gt": greater, "ge": greater_eq}
        return builders[kind](column, node), lambda x: False
    table = {
        "eq": (eq, lambda x: x == value),
        "lt": (less, lambda x: x < value),
        "le": (less_eq, lambda x: x <= value),
        "gt": (greater, lambda x: x > value),
        "ge": (greater_eq, lambda x: x >= value),
    }
    builder, predicate = table[kind]
    return builder(column, node), predicate


def _random_formula(rng: random.Random, depth: int) -> tuple[Any, Any]:
    if depth == 0 or rng.random() < 0.4:
        return _random_leaf(rng)
    parts = [_random_formula(rng, depth - 1) for _ in range(rng.randint(2, 3))]
    nodes = [n for n, _ in parts]
    tests = [t for _, t in parts]
    if rng.random() < 0.5:
        return and_(*nodes), lambda x: all(t(x) for t in tests)
    return or_(*nodes), lambda x: any(t(x) for t in tests)


@pytest.mark.parametrize("seed", range(400))
def test_solver_matches_brute_force_on_random_formulas(seed: int) -> None:
    rng = random.Random(seed)
    formula, predicate = _random_formula(rng, 3)
    node = and_(formula, between(col("P_YEAR"), num(0), num(30)))
    expected = [x for x in UNIVERSE if 0 <= x <= 30 and predicate(Decimal(x))]
    result = solved(node)
    if result.contradiction or not all(_constant_holds(item) for item in result.residual):
        assert expected == []
    else:
        assert values_of(result, "year") == expected
        constraint = result.constraint("year")
        assert constraint is not None
        assert constraint.count() == len(expected)


def _constant_holds(expr: Any) -> bool:
    if isinstance(expr, Literal):
        return expr.value is True
    if isinstance(expr, And):
        return all(_constant_holds(item) for item in expr.items)
    assert isinstance(expr, Or), f"unexpected residual {expr!r}"
    return any(_constant_holds(item) for item in expr.items)
