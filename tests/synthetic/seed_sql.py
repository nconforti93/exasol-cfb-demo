"""Read the reference seeds of `sql/model/` as Python values (no database).

`seed_rows` parses the `INSERT ... VALUES (...), (...)` statement of a seed file into tuples
(`str`, `int`, `Decimal`, `bool` or `None`); `seed_columns` returns the declared column names.
"""

from __future__ import annotations

import functools
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from vs.host.sqlobjects import SqlObject, load

MODEL_DIR = Path(__file__).resolve().parents[2] / "sql" / "model"
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_WORD = re.compile(r"[A-Za-z_]+")


def seed_object(file: str) -> SqlObject:
    """The parsed object of one seed file, for example `010_era.sql`."""
    for item in load(MODEL_DIR):
        if item.file == file:
            return item
    raise FileNotFoundError(file)


def seed_columns(file: str) -> list[str]:
    return [column.name for column in seed_object(file).columns]


def seed_rows(file: str) -> list[tuple[Any, ...]]:
    """The rows of the seed's INSERT statement; an empty list if the file has none."""
    inserts = [s for s in seed_object(file).statements if s.startswith("INSERT")]
    if not inserts:
        return []
    return parse_values(inserts[0].split("VALUES", 1)[1])


def parse_values(text: str) -> list[tuple[Any, ...]]:
    """Parse `(a, 'b', NULL), (...)` into tuples."""
    rows: list[tuple[Any, ...]] = []
    position = 0
    size = len(text)
    current: list[Any] | None = None
    while position < size:
        char = text[position]
        if char == "(":
            current = []
            position += 1
        elif char == ")":
            assert current is not None
            rows.append(tuple(current))
            current = None
            position += 1
        elif char == "'":
            value, position = _string(text, position)
            assert current is not None
            current.append(value)
        elif char in ", \n\t":
            position += 1
        else:
            value, position = _bare(text, position)
            assert current is not None
            current.append(value)
    return rows


def _string(text: str, position: int) -> tuple[str, int]:
    out = []
    position += 1
    while True:
        if text[position] == "'":
            if text.startswith("''", position):
                out.append("'")
                position += 2
                continue
            return "".join(out), position + 1
        out.append(text[position])
        position += 1


def _bare(text: str, position: int) -> tuple[Any, int]:
    number = _NUMBER.match(text, position)
    if number:
        raw = number.group()
        return (Decimal(raw) if "." in raw else int(raw)), number.end()
    word = _WORD.match(text, position)
    assert word is not None, text[position : position + 20]
    return {"NULL": None, "TRUE": True, "FALSE": False}[word.group().upper()], word.end()


def power_conferences(season: int) -> set[str]:
    """Conference names whose tier in `season` is a power tier, from `012_conference_tier.sql`."""
    names = seed_columns("012_conference_tier.sql")
    found: set[str] = set()
    for row in seed_rows("012_conference_tier.sql"):
        cells = dict(zip(names, row, strict=True))
        last = cells["LAST_SEASON"] if cells["LAST_SEASON"] is not None else 9999
        covers = cells["FIRST_SEASON"] <= season <= last
        if covers and cells["IS_POWER_CONFERENCE"] and cells["CONFERENCE_NAME"] is not None:
            found.add(cells["CONFERENCE_NAME"])
    return found


@functools.cache
def championship_ranges() -> dict[str, list[tuple[int, int | None]]]:
    """Conference name -> season ranges with a championship game, from `013_...sql`."""
    names = seed_columns("013_conference_championship.sql")
    ranges: dict[str, list[tuple[int, int | None]]] = {}
    for row in seed_rows("013_conference_championship.sql"):
        cells = dict(zip(names, row, strict=True))
        ranges.setdefault(cells["CONFERENCE_NAME"], []).append(
            (cells["FIRST_SEASON"], cells["LAST_SEASON"])
        )
    return ranges
