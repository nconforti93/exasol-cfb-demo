"""SQL rendering: exact output, validation, and injection-proofing property tests.

The property tests render random trees with hostile strings and identifiers, then re-read the SQL
with an independent reference lexer and parser. A hostile value that escaped its literal or
identifier would leave unexpected tokens (`;`, `--`, `/*`, keywords) or change the tree.
"""

import itertools
import random
import re
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from tests.unit.filter_fixtures import games_table, make_param
from vs.cfbd_vs.catalog import Column
from vs.cfbd_vs.filters import (
    And,
    Between,
    ColumnRef,
    Comparison,
    Expr,
    InList,
    IsNotNull,
    IsNull,
    Like,
    Literal,
    Not,
    Or,
)
from vs.cfbd_vs.sqlrender import (
    MAX_IDENTIFIER_LENGTH,
    RenderError,
    cast_literal,
    check_sql_type,
    column_sql_types,
    emits_clause,
    qualified_name,
    quote_identifier,
    render_conjunction,
    render_literal,
    render_predicate,
    sql_literal,
    string_literal,
    zero_row_select,
)

# --- reference lexer and parser (independent of the code under test) ---

_TOKEN = re.compile(
    r"""(?P<ws>[ ]+)
      | (?P<string>'(?:[^']|'')*')
      | (?P<ident>"(?:[^"]|"")*")
      | (?P<number>[0-9]+(?:\.[0-9]+)?(?:E-?[0-9]+)?)
      | (?P<word>[A-Z][A-Z0-9_]*)
      | (?P<op><>|<=|<|=|\(|\)|,|-|\.)""",
    re.VERBOSE,
)


def lex(sql: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(sql):
        match = _TOKEN.match(sql, position)
        assert match is not None, (
            f"unexpected text at {position}: {sql[position : position + 20]!r}"
        )
        kind = match.lastgroup
        assert kind is not None
        if kind != "ws":
            tokens.append((kind, match.group()))
        position = match.end()
    for (_, first), (_, second) in itertools.pairwise(tokens):
        assert (first, second) != ("-", "-"), "line comment outside a literal"
    return tokens


def decode_string(token: str) -> str:
    return token[1:-1].replace("''", "'")


def decode_identifier(token: str) -> str:
    return token[1:-1].replace('""', '"')


class Reader:
    def __init__(self, sql: str) -> None:
        self.tokens = lex(sql)
        self.position = 0

    def peek(self) -> tuple[str, str]:
        return self.tokens[self.position] if self.position < len(self.tokens) else ("end", "")

    def take(self, text: str | None = None) -> tuple[str, str]:
        token = self.peek()
        assert token[0] != "end", "unexpected end of SQL"
        assert text is None or token[1] == text, f"expected {text!r}, got {token!r}"
        self.position += 1
        return token

    def at(self, text: str) -> bool:
        return self.peek()[1] == text and self.peek()[0] in ("word", "op")

    def done(self) -> bool:
        return self.position == len(self.tokens)


def read_expr(reader: Reader) -> Expr:
    kind, text = reader.take()
    if kind == "ident":
        return ColumnRef(decode_identifier(text), "value")
    if kind == "string":
        return Literal("string", decode_string(text))
    if kind == "number":
        return Literal("double" if "E" in text else "exactnumeric", Decimal(text))
    if kind == "word" and text in ("NULL", "TRUE", "FALSE"):
        return Literal("null", None) if text == "NULL" else Literal("bool", text == "TRUE")
    if kind == "word" and text in ("DATE", "TIMESTAMP"):
        return Literal(text.lower(), decode_string(reader.take()[1]))
    assert text == "(", f"unexpected token {text!r}"
    return read_compound(reader)


def read_compound(reader: Reader) -> Expr:
    if reader.at("-"):
        reader.take("-")
        kind, text = reader.take()
        assert kind == "number"
        reader.take(")")
        return Literal("double" if "E" in text else "exactnumeric", -Decimal(text))
    if reader.at("NOT"):
        reader.take("NOT")
        result: Expr = Not(read_expr(reader))
    else:
        result = read_infix(reader, read_expr(reader))
    reader.take(")")
    return result


def read_infix(reader: Reader, first: Expr) -> Expr:
    _, text = reader.take()
    if text in ("AND", "OR"):
        items = [first, read_expr(reader)]
        while reader.at(text):
            reader.take(text)
            items.append(read_expr(reader))
        return And(tuple(items)) if text == "AND" else Or(tuple(items))
    if text in ("=", "<>", "<", "<="):
        return Comparison(text, first, read_expr(reader))
    if text == "LIKE":
        pattern = read_expr(reader)
        if reader.at("ESCAPE"):
            reader.take("ESCAPE")
            return Like(first, pattern, read_expr(reader))
        return Like(first, pattern, None)
    if text == "BETWEEN":
        low = read_expr(reader)
        reader.take("AND")
        return Between(first, low, read_expr(reader))
    if text == "IN":
        reader.take("(")
        items = [read_expr(reader)]
        while reader.at(","):
            reader.take(",")
            items.append(read_expr(reader))
        reader.take(")")
        return InList(first, tuple(items))
    assert text == "IS", f"unexpected operator {text!r}"
    if reader.at("NOT"):
        reader.take("NOT")
        reader.take("NULL")
        return IsNotNull(first)
    reader.take("NULL")
    return IsNull(first)


def reparse(sql: str) -> Expr:
    reader = Reader(sql)
    expr = read_expr(reader)
    assert reader.done(), f"trailing tokens: {reader.tokens[reader.position :]}"
    return expr


def read_type(reader: Reader) -> str:
    _, name = reader.take()
    text = name
    if reader.at("("):
        reader.take("(")
        sizes = [reader.take()[1]]
        while reader.at(","):
            reader.take(",")
            sizes.append(reader.take()[1])
        reader.take(")")
        text += "(" + ",".join(sizes) + ")"
    if reader.at("UTF8") or reader.at("ASCII"):
        text += " " + reader.take()[1]
    return text


# --- hostile generators (fixed seeds, stdlib random) ---

HOSTILE_PIECES = [
    "'",
    "''",
    '"',
    '""',
    ";",
    "--",
    "/*",
    "*/",
    "\\",
    "\n",
    "\r",
    "\t",
    " ",
    "%",
    "_",
    "?",
    ":",
    "$",
    "{}",
    "(",
    ")",
    ",",
    "=",
    "<>",
    "\u00e9",
    "\u00df",
    "\u20ac",
    "\U0001d11e",
    "\U0001f600",
    "\u2028",
    "\u200b",
    "\u202e",
    "\ufeff",
    "\x01",
    "\x7f",
    "DROP TABLE X",
    "' OR '1'='1",
    '"; DELETE FROM T; --',
    "E1",
    "a",
    "Z",
    "0",
]


def hostile(rng: random.Random, max_pieces: int = 10) -> str:
    return "".join(rng.choice(HOSTILE_PIECES) for _ in range(rng.randint(0, max_pieces)))


def hostile_identifier(rng: random.Random) -> str:
    name = hostile(rng, 8)[:MAX_IDENTIFIER_LENGTH] or "X"
    return name if name.encode("utf-8", "surrogatepass") else "X"


def random_decimal(rng: random.Random) -> Decimal:
    digits = tuple(rng.randint(0, 9) for _ in range(rng.randint(1, 20)))
    return Decimal((rng.randint(0, 1), digits, rng.randint(-30, 30)))


def random_literal(rng: random.Random) -> Literal:
    choice = rng.randrange(7)
    if choice == 0:
        return Literal("null", None)
    if choice == 1:
        return Literal("bool", rng.random() < 0.5)
    if choice == 2:
        return Literal("exactnumeric", random_decimal(rng))
    if choice == 3:
        return Literal("double", random_decimal(rng))
    if choice == 4:
        return Literal("date", f"{rng.randint(0, 9999):04d}-{rng.randint(1, 12):02d}-01")
    if choice == 5:
        fraction = "".join(str(rng.randint(0, 9)) for _ in range(rng.randint(0, 9)))
        suffix = f".{fraction}" if fraction else ""
        return Literal(
            "timestamp", f"2024-09-0{rng.randint(1, 9)} 23:59:5{rng.randint(0, 9)}{suffix}"
        )
    return Literal("string", hostile(rng))


def random_operand(rng: random.Random) -> Expr:
    if rng.random() < 0.4:
        return ColumnRef(hostile_identifier(rng), "value")
    return random_literal(rng)


def random_predicate(rng: random.Random, depth: int) -> Expr:
    if depth == 0:
        return random_operand(rng)
    choice = rng.randrange(9)

    def child() -> Expr:
        return random_predicate(rng, depth - 1)

    if choice == 0:
        return And(tuple(child() for _ in range(rng.randint(2, 3))))
    if choice == 1:
        return Or(tuple(child() for _ in range(rng.randint(2, 3))))
    if choice == 2:
        return Not(child())
    if choice == 3:
        return Comparison(rng.choice(["=", "<>", "<", "<="]), child(), child())
    if choice == 4:
        escape = random_operand(rng) if rng.random() < 0.5 else None
        return Like(random_operand(rng), random_operand(rng), escape)
    if choice == 5:
        return Between(random_operand(rng), random_operand(rng), random_operand(rng))
    if choice == 6:
        items = tuple(random_operand(rng) for _ in range(rng.randint(1, 4)))
        return InList(random_operand(rng), items)
    if choice == 7:
        return IsNull(child())
    return IsNotNull(child())


# --- identifiers ---


def test_identifiers_are_double_quoted() -> None:
    assert quote_identifier("P_YEAR") == '"P_YEAR"'
    assert quote_identifier('a"b') == '"a""b"'
    assert quote_identifier("x" * MAX_IDENTIFIER_LENGTH) == '"' + "x" * 128 + '"'


def test_qualified_names_join_quoted_parts() -> None:
    assert qualified_name("CFB_INFRA", "CFBD_FETCH") == '"CFB_INFRA"."CFBD_FETCH"'
    assert qualified_name("T") == '"T"'


@pytest.mark.parametrize("name", ["", "x" * (MAX_IDENTIFIER_LENGTH + 1), "a\x00b", "lone \ud800"])
def test_invalid_identifiers_are_refused(name: str) -> None:
    with pytest.raises(RenderError):
        quote_identifier(name)


def test_qualified_name_needs_a_part() -> None:
    with pytest.raises(RenderError):
        qualified_name()


@pytest.mark.parametrize("seed", range(300))
def test_hostile_identifiers_round_trip_as_one_token(seed: int) -> None:
    name = hostile_identifier(random.Random(seed))
    tokens = lex(quote_identifier(name))
    assert len(tokens) == 1
    assert tokens[0][0] == "ident"
    assert decode_identifier(tokens[0][1]) == name


# --- literals ---


def test_string_literal_doubles_quotes() -> None:
    assert string_literal("O'Brien; DROP TABLE X--") == "'O''Brien; DROP TABLE X--'"
    assert string_literal("Texas A&M") == "'Texas A&M'"
    assert string_literal("") == "''"
    assert string_literal("'") == "''''"


@pytest.mark.parametrize("value", ["nul\x00", "lone \udfff"])
def test_strings_that_cannot_be_sent_are_refused(value: str) -> None:
    with pytest.raises(RenderError):
        string_literal(value)


@pytest.mark.parametrize("seed", range(500))
def test_hostile_strings_round_trip_as_one_token(seed: int) -> None:
    value = hostile(random.Random(seed), 40)
    tokens = lex(string_literal(value))
    assert len(tokens) == 1
    assert tokens[0][0] == "string"
    assert decode_string(tokens[0][1]) == value


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "NULL"),
        (True, "TRUE"),
        (False, "FALSE"),
        (2024, "2024"),
        (-5, "(-5)"),
        (Decimal("1.50"), "1.50"),
        (Decimal("-2"), "(-2)"),
        (Decimal("1E+3"), "1000"),
        ("O'Brien", "'O''Brien'"),
    ],
)
def test_python_values_render_as_literals(value: Any, expected: str) -> None:
    assert sql_literal(value) == expected


@pytest.mark.parametrize("value", [1.5, object(), Decimal("NaN"), Decimal("Infinity"), b"x"])
def test_unsupported_python_values_are_refused(value: Any) -> None:
    with pytest.raises(RenderError):
        sql_literal(value)


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        (Literal("null", None), "NULL"),
        (Literal("bool", True), "TRUE"),
        (Literal("exactnumeric", Decimal("2024")), "2024"),
        (Literal("exactnumeric", Decimal("-0.001")), "(-0.001)"),
        (Literal("double", Decimal("123.45")), "1.2345E2"),
        (Literal("double", Decimal("1.2345000000000000e+02")), "1.2345000000000000E2"),
        (Literal("double", Decimal("-0.5")), "(-5E-1)"),
        (Literal("double", Decimal("0")), "0E0"),
        (Literal("double", Decimal("7")), "7E0"),
        (Literal("string", "it's"), "'it''s'"),
        (Literal("date", "2024-09-01"), "DATE '2024-09-01'"),
        (Literal("timestamp", "2024-09-01 12:01:01.123"), "TIMESTAMP '2024-09-01 12:01:01.123'"),
    ],
)
def test_filter_literals_render_with_their_sql_type(literal: Literal, expected: str) -> None:
    assert render_literal(literal) == expected


@pytest.mark.parametrize(
    "literal",
    [
        Literal("null", "x"),
        Literal("bool", "yes"),
        Literal("exactnumeric", "2024"),
        Literal("exactnumeric", Decimal("NaN")),
        Literal("double", Decimal("-Infinity")),
        Literal("double", 1.5),  # type: ignore[arg-type]
        Literal("string", 5),  # type: ignore[arg-type]
        Literal("string", "nul\x00"),
        Literal("date", "2024-09-01' OR '1'='1"),
        Literal("timestamp", "2024-09-01"),
        Literal("interval", "+2-01"),
    ],
)
def test_invalid_literals_are_refused(literal: Literal) -> None:
    with pytest.raises(RenderError):
        render_literal(literal)


@pytest.mark.parametrize(
    ("value", "sql_type", "expected"),
    [
        ("STORED", "VARCHAR(8) UTF8", "CAST('STORED' AS VARCHAR(8) UTF8)"),
        (None, "DECIMAL(3,0)", "CAST(NULL AS DECIMAL(3,0))"),
        (0, "DECIMAL(3,0)", "CAST(0 AS DECIMAL(3,0))"),
        (-1, "DECIMAL(9,0)", "CAST((-1) AS DECIMAL(9,0))"),
        (True, "BOOLEAN", "CAST(TRUE AS BOOLEAN)"),
        (Decimal("0.5"), "DOUBLE", "CAST(0.5 AS DOUBLE)"),
        (
            "/games?year=2024",
            "VARCHAR(2000) UTF8",
            "CAST('/games?year=2024' AS VARCHAR(2000) UTF8)",
        ),
    ],
)
def test_cast_literal_types_a_value_exactly(value: Any, sql_type: str, expected: str) -> None:
    assert cast_literal(value, sql_type) == expected


@pytest.mark.parametrize(
    ("value", "sql_type"),
    [
        ("STORED", "VARCHAR(8)"),
        ("STORED", "INT"),
        ("STORED", "VARCHAR(8) UTF8) AS X; --"),
        (1.5, "DOUBLE"),
        ("nul\x00", "VARCHAR(8) UTF8"),
    ],
)
def test_cast_literal_refuses_unsafe_values_and_types(value: Any, sql_type: str) -> None:
    with pytest.raises(RenderError):
        cast_literal(value, sql_type)


@pytest.mark.parametrize("seed", range(200))
def test_hostile_strings_stay_one_literal_inside_a_cast(seed: int) -> None:
    value = hostile(random.Random(seed), 30)
    reader = Reader(cast_literal(value, "VARCHAR(2000) UTF8"))
    reader.take("CAST")
    reader.take("(")
    kind, token = reader.take()
    assert kind == "string"
    assert decode_string(token) == value
    reader.take("AS")
    assert read_type(reader) == "VARCHAR(2000) UTF8"
    reader.take(")")
    assert reader.done()


# --- predicates ---

YEAR = ColumnRef("P_YEAR", "param")
POINTS = ColumnRef("HOME_POINTS", "value")
VENUE = ColumnRef("VENUE", "value")


def num(value: str) -> Literal:
    return Literal("exactnumeric", Decimal(value))


def text(value: str) -> Literal:
    return Literal("string", value)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        (POINTS, '"HOME_POINTS"'),
        (Comparison("<", num("50"), POINTS), '(50 < "HOME_POINTS")'),
        (Comparison("<>", VENUE, text("x")), "(\"VENUE\" <> 'x')"),
        (Like(VENUE, text("M%"), None), "(\"VENUE\" LIKE 'M%')"),
        (Like(VENUE, text("M!%"), text("!")), "(\"VENUE\" LIKE 'M!%' ESCAPE '!')"),
        (Between(POINTS, num("1"), num("-3")), '("HOME_POINTS" BETWEEN 1 AND (-3))'),
        (InList(VENUE, (text("a"), text("b"))), "(\"VENUE\" IN ('a', 'b'))"),
        (IsNull(VENUE), '("VENUE" IS NULL)'),
        (IsNotNull(VENUE), '("VENUE" IS NOT NULL)'),
        (Not(IsNull(VENUE)), '(NOT ("VENUE" IS NULL))'),
        (
            Or((Comparison("<", num("50"), POINTS), IsNull(VENUE))),
            '((50 < "HOME_POINTS") OR ("VENUE" IS NULL))',
        ),
        (
            And((IsNull(VENUE), Comparison("=", POINTS, num("1")))),
            '(("VENUE" IS NULL) AND ("HOME_POINTS" = 1))',
        ),
        (And((IsNull(VENUE),)), '("VENUE" IS NULL)'),
        (Or((IsNull(VENUE),)), '("VENUE" IS NULL)'),
    ],
)
def test_predicates_render_fully_parenthesized(expr: Expr, expected: str) -> None:
    assert render_predicate(expr) == expected


def test_residual_conjunction() -> None:
    assert render_conjunction([]) == "TRUE"
    assert render_conjunction([IsNull(VENUE)]) == '("VENUE" IS NULL)'
    assert render_conjunction([IsNull(VENUE), POINTS]) == '("VENUE" IS NULL) AND "HOME_POINTS"'


@pytest.mark.parametrize(
    "expr",
    [
        Comparison("; DROP TABLE X; --", POINTS, num("1")),
        Comparison(">", POINTS, num("1")),
        And(()),
        Or(()),
        InList(VENUE, ()),
        "P_YEAR",
        Comparison("=", POINTS, "1"),  # type: ignore[arg-type]
    ],
)
def test_invalid_predicates_are_refused(expr: Any) -> None:
    with pytest.raises(RenderError):
        render_predicate(expr)


@pytest.mark.parametrize("seed", range(600))
def test_hostile_predicates_render_to_exactly_their_tree(seed: int) -> None:
    expr = random_predicate(random.Random(seed), 4)
    assert reparse(render_predicate(expr)) == expr


@pytest.mark.parametrize("seed", range(100))
def test_hostile_conjunctions_render_to_exactly_their_trees(seed: int) -> None:
    rng = random.Random(seed)
    exprs = [random_predicate(rng, 3) for _ in range(rng.randint(2, 4))]
    rendered = render_conjunction(exprs)
    assert reparse(f"({rendered})") == And(tuple(exprs))


# --- column types, EMITS and the zero-row SELECT ---


def small_table(columns: tuple[Column, ...]) -> Any:
    return replace(
        games_table(),
        name="SMALL",
        params=(make_param("year", "P_YEAR", "integer", "DECIMAL(18,0)"),),
        columns=columns,
    )


SMALL_COLUMNS = (
    Column(name="ROW_NO", kind="position"),
    Column(name="POLLS_NO", kind="position"),
    Column(name="SEASON", kind="parent_key", path=("season",), sql_type="DECIMAL(18,0)"),
    Column(name="POLL", kind="value", path=("poll",), sql_type="VARCHAR(100) UTF8"),
    Column(name="RAW_JSON", kind="raw_json"),
    Column(name="P_YEAR", kind="param"),
    Column(name="REQ_SLICE", kind="meta"),
    Column(name="REQ_AT", kind="meta"),
)


def test_column_types_resolve_every_column_kind() -> None:
    assert column_sql_types(small_table(SMALL_COLUMNS)) == (
        ("ROW_NO", "DECIMAL(18,0)"),
        ("POLLS_NO", "DECIMAL(18,0)"),
        ("SEASON", "DECIMAL(18,0)"),
        ("POLL", "VARCHAR(100) UTF8"),
        ("RAW_JSON", "VARCHAR(2000000) UTF8"),
        ("P_YEAR", "DECIMAL(18,0)"),
        ("REQ_SLICE", "VARCHAR(2000) UTF8"),
        ("REQ_AT", "TIMESTAMP"),
    )


def test_every_games_column_has_a_type() -> None:
    table = games_table()
    names = [name for name, _ in column_sql_types(table)]
    assert names == [column.name for column in table.columns]


def test_explicit_types_win_when_consistent() -> None:
    columns = (
        Column(name="ROW_NO", kind="position", sql_type="DECIMAL(9,0)"),
        Column(name="P_YEAR", kind="param", sql_type="DECIMAL(18,0)"),
        Column(name="REQ_AT", kind="meta", sql_type="TIMESTAMP"),
    )
    assert column_sql_types(small_table(columns)) == (
        ("ROW_NO", "DECIMAL(9,0)"),
        ("P_YEAR", "DECIMAL(18,0)"),
        ("REQ_AT", "TIMESTAMP"),
    )


@pytest.mark.parametrize(
    "column",
    [
        Column(name="P_YEAR", kind="param", sql_type="DOUBLE"),
        Column(name="REQ_AT", kind="meta", sql_type="DATE"),
        Column(name="REQ_OTHER", kind="meta"),
        Column(name="P_WEEK", kind="param"),
        Column(name="SEASON", kind="value"),
        Column(name="SEASON", kind="value", sql_type="VARCHAR(10)); DROP TABLE X; --"),
    ],
)
def test_untypable_columns_are_refused(column: Column) -> None:
    with pytest.raises(RenderError):
        column_sql_types(small_table((column,)))


@pytest.mark.parametrize(
    "sql_type",
    [
        "DECIMAL(18,0)",
        "DECIMAL(1,0)",
        "DECIMAL(36,36)",
        "DOUBLE",
        "BOOLEAN",
        "DATE",
        "TIMESTAMP",
        "VARCHAR(1) UTF8",
        "VARCHAR(2000000) ASCII",
    ],
)
def test_supported_sql_types_pass(sql_type: str) -> None:
    assert check_sql_type(sql_type) == sql_type


@pytest.mark.parametrize(
    "sql_type",
    [
        "",
        "INT",
        "double",
        "DOUBLE PRECISION",
        "DECIMAL(37,0)",
        "DECIMAL(5,6)",
        "DECIMAL(0,0)",
        "DECIMAL(18, 0)",
        "VARCHAR(0) UTF8",
        "VARCHAR(2000001) UTF8",
        "VARCHAR(10)",
        "VARCHAR(10) UTF8; DROP TABLE X",
        "TIMESTAMP WITH LOCAL TIME ZONE",
        "DECIMAL(18,0)\n",
    ],
)
def test_unsupported_sql_types_are_refused(sql_type: str) -> None:
    with pytest.raises(RenderError):
        check_sql_type(sql_type)


def test_emits_clause_lists_typed_columns_in_catalog_order() -> None:
    table = small_table(SMALL_COLUMNS[:4])
    assert emits_clause(table) == (
        'EMITS ("ROW_NO" DECIMAL(18,0), "POLLS_NO" DECIMAL(18,0), '
        '"SEASON" DECIMAL(18,0), "POLL" VARCHAR(100) UTF8)'
    )


def test_zero_row_select_has_typed_null_columns() -> None:
    table = small_table((SMALL_COLUMNS[0], SMALL_COLUMNS[3], SMALL_COLUMNS[7]))
    assert zero_row_select(table) == (
        'SELECT CAST(NULL AS DECIMAL(18,0)) AS "ROW_NO", '
        'CAST(NULL AS VARCHAR(100) UTF8) AS "POLL", '
        'CAST(NULL AS TIMESTAMP) AS "REQ_AT" FROM DUAL WHERE FALSE'
    )


def test_table_without_columns_is_refused() -> None:
    with pytest.raises(RenderError):
        emits_clause(small_table(()))
    with pytest.raises(RenderError):
        zero_row_select(small_table(()))


def hostile_table(seed: int) -> tuple[Any, list[tuple[str, str]]]:
    rng = random.Random(seed)
    types = ["DECIMAL(18,0)", "DOUBLE", "BOOLEAN", "TIMESTAMP", "VARCHAR(2000) UTF8", "DATE"]
    expected: list[tuple[str, str]] = []
    for index in range(rng.randint(1, 6)):
        name = f"{hostile_identifier(rng)}{index}"
        expected.append((name, rng.choice(types)))
    columns = tuple(
        Column(name=name, kind="value", path=("x",), sql_type=sql_type)
        for name, sql_type in expected
    )
    return small_table(columns), expected


@pytest.mark.parametrize("seed", range(150))
def test_hostile_column_names_stay_identifiers_in_emits(seed: int) -> None:
    table, expected = hostile_table(seed)
    reader = Reader(emits_clause(table))
    reader.take("EMITS")
    reader.take("(")
    found: list[tuple[str, str]] = []
    while True:
        kind, token = reader.take()
        assert kind == "ident"
        found.append((decode_identifier(token), read_type(reader)))
        if not reader.at(","):
            break
        reader.take(",")
    reader.take(")")
    assert reader.done()
    assert found == expected


@pytest.mark.parametrize("seed", range(150))
def test_hostile_column_names_stay_identifiers_in_zero_row_select(seed: int) -> None:
    table, expected = hostile_table(seed)
    reader = Reader(zero_row_select(table))
    reader.take("SELECT")
    found: list[tuple[str, str]] = []
    while True:
        reader.take("CAST")
        reader.take("(")
        reader.take("NULL")
        reader.take("AS")
        sql_type = read_type(reader)
        reader.take(")")
        reader.take("AS")
        kind, token = reader.take()
        assert kind == "ident"
        found.append((decode_identifier(token), sql_type))
        if not reader.at(","):
            break
        reader.take(",")
    for word in ("FROM", "DUAL", "WHERE", "FALSE"):
        reader.take(word)
    assert reader.done()
    assert found == expected
