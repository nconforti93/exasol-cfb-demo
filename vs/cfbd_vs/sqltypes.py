"""The one grammar of the Exasol column types the catalog may use (stdlib only).

Every module that reads a catalog type goes through `parse_sql_type`, so a type accepted by one
module can never be read differently by another. The grammar is strict and canonical: upper case,
no spaces inside `DECIMAL(p,s)`, one space before the VARCHAR character set, no leading zeros.
`normalize_sql_type` is the lenient counterpart for text reported by the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_DECIMAL_PRECISION = 36
MAX_VARCHAR_SIZE = 2_000_000
CHARACTER_SETS = ("UTF8", "ASCII")

_GRAMMAR = re.compile(
    r"DECIMAL\((?P<precision>[1-9][0-9]?),(?P<scale>0|[1-9][0-9]?)\)"
    r"|(?P<plain>DOUBLE|BOOLEAN|DATE|TIMESTAMP)"
    r"|VARCHAR\((?P<size>[1-9][0-9]{0,6})\) (?P<character_set>UTF8|ASCII)"
)
_DEFAULT_TIMESTAMP = re.compile(r"^TIMESTAMP\(3\)")
_DESCRIBE_LIMIT = 40


class SqlTypeError(ValueError):
    """A column type is outside the supported grammar or range."""


@dataclass(frozen=True)
class SqlType:
    """A parsed column type. Only the fields of its `kind` are set."""

    kind: str
    precision: int | None = None
    scale: int | None = None
    size: int | None = None
    character_set: str | None = None

    @property
    def is_integer(self) -> bool:
        """True for a DECIMAL without fractional digits."""
        return self.kind == "DECIMAL" and self.scale == 0

    def __str__(self) -> str:
        if self.kind == "DECIMAL":
            return f"DECIMAL({self.precision},{self.scale})"
        if self.kind == "VARCHAR":
            return f"VARCHAR({self.size}) {self.character_set}"
        return self.kind


def parse_sql_type(text: str) -> SqlType:
    """Parse the canonical text of a supported column type, or raise `SqlTypeError`."""
    match = _GRAMMAR.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise SqlTypeError(f"unsupported column type {_describe(text)}")
    if match["plain"]:
        return SqlType(match["plain"])
    if match["size"]:
        return _varchar(int(match["size"]), match["character_set"], text)
    return _decimal(int(match["precision"]), int(match["scale"]), text)


def decimal_type(precision: int, scale: int = 0) -> str:
    """The canonical text of `DECIMAL(precision,scale)`; raise when the shape is impossible."""
    return str(_decimal(precision, scale, f"DECIMAL({precision},{scale})"))


def varchar_type(size: int, character_set: str = "UTF8") -> str:
    """The canonical text of `VARCHAR(size) <character_set>`; raise when `size` is out of range."""
    return str(_varchar(size, character_set, f"VARCHAR({size}) {character_set}"))


def normalize_sql_type(text: str) -> str:
    """Upper case with single spaces; a database reports a plain `TIMESTAMP` as `TIMESTAMP(3)`."""
    normalized = " ".join(text.upper().split())
    return _DEFAULT_TIMESTAMP.sub("TIMESTAMP", normalized)


def _decimal(precision: int, scale: int, text: str) -> SqlType:
    if not 1 <= precision <= MAX_DECIMAL_PRECISION:
        raise SqlTypeError(f"unsupported column type {text!r}: precision out of range")
    if not 0 <= scale <= precision:
        raise SqlTypeError(f"unsupported column type {text!r}: scale exceeds precision")
    return SqlType("DECIMAL", precision=precision, scale=scale)


def _varchar(size: int, character_set: str, text: str) -> SqlType:
    if not 1 <= size <= MAX_VARCHAR_SIZE:
        raise SqlTypeError(f"VARCHAR size {size} is outside 1..{MAX_VARCHAR_SIZE}")
    if character_set not in CHARACTER_SETS:
        raise SqlTypeError(f"unsupported column type {text!r}: unknown character set")
    return SqlType("VARCHAR", size=size, character_set=character_set)


def _describe(value: object) -> str:
    return repr(str(value)[:_DESCRIBE_LIMIT])
