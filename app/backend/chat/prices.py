"""The price table of the chat's model calls (`chat/budget`), in US dollars per million tokens.

Source: the pricing page (`SOURCE`), read on `CHECKED`; it agrees with the claude-api skill's model
table (cached 2026-10-06). The rows are the chat's model and the two models the refusal fallback
(`fallbacks: "default"`) is expected to route `claude-opus-5-5` to. Anthropic does not publish
that routing per model, so a served model outside the table is priced at `HIGHEST`, the largest
price of every column, and reported for a warning. Cache writes cost 1.25 x input for the
5-minute lifetime and 2 x for 1 hour; cache reads are 0.05 x input on Opus 5.5, 0.1 x elsewhere.
The chat sets neither fast mode nor a regional inference location, so neither surcharge applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
CHECKED = "2026-10-23"


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Dollars per million tokens of one model."""

    model: str
    input: Decimal
    output: Decimal
    cache_write_5m: Decimal
    cache_write_1h: Decimal
    cache_read: Decimal


TABLE: tuple[ModelPrice, ...] = (
    ModelPrice(
        "claude-opus-5-5", Decimal("4.00"), Decimal("20.00"), Decimal("5.00"), Decimal("8.00"),
        Decimal("0.20"),
    ),
    ModelPrice(
        "claude-opus-5", Decimal("5.00"), Decimal("25.00"), Decimal("6.25"), Decimal("10.00"),
        Decimal("0.50"),
    ),
    ModelPrice(
        "claude-opus-4-8", Decimal("5.00"), Decimal("25.00"), Decimal("6.25"), Decimal("10.00"),
        Decimal("0.50"),
    ),
)  # fmt: skip

HIGHEST = ModelPrice(
    "highest row",
    max(row.input for row in TABLE),
    max(row.output for row in TABLE),
    max(row.cache_write_5m for row in TABLE),
    max(row.cache_write_1h for row in TABLE),
    max(row.cache_read for row in TABLE),
)

_BY_MODEL = {row.model: row for row in TABLE}


def lookup(model: str) -> tuple[ModelPrice, bool]:
    """The prices of `model` and whether it is in the table; `HIGHEST` when it is not."""
    row = _BY_MODEL.get(model)
    return (HIGHEST, False) if row is None else (row, True)
