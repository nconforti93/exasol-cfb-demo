import pytest

from vs.cfbd_vs import sqltypes
from vs.cfbd_vs.sqltypes import SqlType, SqlTypeError, parse_sql_type

SUPPORTED = [
    ("DECIMAL(18,0)", SqlType("DECIMAL", precision=18, scale=0)),
    ("DECIMAL(1,0)", SqlType("DECIMAL", precision=1, scale=0)),
    ("DECIMAL(36,36)", SqlType("DECIMAL", precision=36, scale=36)),
    ("DOUBLE", SqlType("DOUBLE")),
    ("BOOLEAN", SqlType("BOOLEAN")),
    ("DATE", SqlType("DATE")),
    ("TIMESTAMP", SqlType("TIMESTAMP")),
    ("VARCHAR(1) UTF8", SqlType("VARCHAR", size=1, character_set="UTF8")),
    ("VARCHAR(2000000) ASCII", SqlType("VARCHAR", size=2_000_000, character_set="ASCII")),
]

UNSUPPORTED = [
    "",
    "INT",
    "double",
    "DOUBLE PRECISION",
    "DECIMAL(37,0)",
    "DECIMAL(5,6)",
    "DECIMAL(0,0)",
    "DECIMAL(018,0)",
    "DECIMAL(18, 0)",
    "DECIMAL(18)",
    "VARCHAR(0) UTF8",
    "VARCHAR(2000001) UTF8",
    "VARCHAR(10)",
    "VARCHAR(10)  UTF8",
    "VARCHAR(10) UTF8; DROP TABLE X",
    "TIMESTAMP(3)",
    "TIMESTAMP WITH LOCAL TIME ZONE",
    "DECIMAL(18,0)\n",
]


@pytest.mark.parametrize(("text", "expected"), SUPPORTED)
def test_supported_types_parse_into_their_parts(text: str, expected: SqlType) -> None:
    assert parse_sql_type(text) == expected


@pytest.mark.parametrize(("text", "parsed"), SUPPORTED)
def test_a_parsed_type_renders_back_to_the_same_text(text: str, parsed: SqlType) -> None:
    assert str(parse_sql_type(text)) == text
    assert str(parsed) == text


@pytest.mark.parametrize("text", UNSUPPORTED)
def test_unsupported_types_are_refused(text: str) -> None:
    with pytest.raises(SqlTypeError):
        parse_sql_type(text)


@pytest.mark.parametrize("value", [None, 18, b"DOUBLE"])
def test_non_text_is_refused(value: object) -> None:
    with pytest.raises(SqlTypeError):
        parse_sql_type(value)  # type: ignore[arg-type]


def test_hostile_text_is_quoted_and_truncated_in_the_error() -> None:
    with pytest.raises(SqlTypeError) as caught:
        parse_sql_type("X" * 500)
    assert len(str(caught.value)) < 100


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DECIMAL(18,0)", True),
        ("DECIMAL(3,0)", True),
        ("DECIMAL(18,2)", False),
        ("DOUBLE", False),
        ("VARCHAR(10) UTF8", False),
    ],
)
def test_is_integer_means_a_decimal_without_scale(text: str, expected: bool) -> None:
    assert parse_sql_type(text).is_integer is expected


def test_builders_render_canonical_text() -> None:
    assert sqltypes.decimal_type(18, 0) == "DECIMAL(18,0)"
    assert sqltypes.varchar_type(2000) == "VARCHAR(2000) UTF8"
    assert sqltypes.varchar_type(8, "ASCII") == "VARCHAR(8) ASCII"


@pytest.mark.parametrize("size", [0, -1, 2_000_001])
def test_the_varchar_builder_refuses_sizes_outside_the_range(size: int) -> None:
    with pytest.raises(SqlTypeError, match=r"outside 1\.\.2000000"):
        sqltypes.varchar_type(size)


@pytest.mark.parametrize(("precision", "scale"), [(0, 0), (37, 0), (5, 6), (5, -1)])
def test_the_decimal_builder_refuses_impossible_shapes(precision: int, scale: int) -> None:
    with pytest.raises(SqlTypeError):
        sqltypes.decimal_type(precision, scale)


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("DECIMAL(18,0)", "DECIMAL(18,0)"),
        ("decimal(18,0)", "DECIMAL(18,0)"),
        ("VARCHAR(10)   UTF8", "VARCHAR(10) UTF8"),
        ("TIMESTAMP(3)", "TIMESTAMP"),
        ("timestamp(3)", "TIMESTAMP"),
        ("TIMESTAMP", "TIMESTAMP"),
        ("TIMESTAMP(6)", "TIMESTAMP(6)"),
        (" DOUBLE ", "DOUBLE"),
    ],
)
def test_normalize_matches_how_the_database_reports_a_type(reported: str, expected: str) -> None:
    assert sqltypes.normalize_sql_type(reported) == expected
