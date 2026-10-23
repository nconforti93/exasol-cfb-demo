"""The pure arithmetic of the chat's budget (`chat/budget`): the cost of a call from the usage the
API reports, the context a call re-reads, the input estimate of the next call, its worst case, and
the cap decisions. Amounts are exact `Decimal` dollars.

- **Cost.** When `usage.iterations` holds entries (the refusal fallback ran), each entry is one
  attempt priced at its own model; the top-level usage then describes only the serving attempt
  and is not added again. Otherwise the top-level usage is priced at the model that served the
  call. A model outside the price table is priced at its highest row and reported. Pricing every
  attempt can count an unbilled pre-output decline, or ignore the fallback credit: both err high.
- **Input estimate** of the next call: the context of the previous call (its largest input of all
  kinds plus the output of every attempt, since earlier thinking stays in the context) plus the
  new messages at one token per 2 characters; for a conversation's first call, the whole request
  at that rate.
- **Worst case** of a call: the input estimate at the model's cache-write price plus `max_tokens`
  at its output price; with the refusal fallback on, one more hop at the highest row's prices.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.backend.chat import prices
from app.backend.chat.prices import ModelPrice

CHARS_PER_TOKEN = 2
DEFAULT_SESSION_MAX_USD = Decimal("2.00")
DEFAULT_DAILY_MAX_USD = Decimal("20.00")
DEFAULT_CALL_MAX_INPUT_TOKENS = 150_000
DAILY_CAP = "daily_cap"
CONVERSATION_CAP = "conversation_cap"
CONTEXT_CAP = "context_cap"
SPEND_LOG_UNWRITABLE = "spend_log_unwritable"

_PER_MILLION = Decimal(1_000_000)
_CENT = Decimal("0.01")
_COUNTS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)
_WRITE_5M = "ephemeral_5m_input_tokens"
_WRITE_1H = "ephemeral_1h_input_tokens"


@dataclass(frozen=True, slots=True)
class Attempt:
    """The tokens of one model attempt of a call; `model` is None when the entry names none."""

    model: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int

    @property
    def context_input(self) -> int:
        """Input tokens of all kinds: what the attempt read."""
        return (
            self.input_tokens
            + self.cache_read_tokens
            + self.cache_write_5m_tokens
            + self.cache_write_1h_tokens
        )


@dataclass(frozen=True, slots=True)
class CallCost:
    """The dollars of one call, and the served models that were priced at the highest row."""

    usd: Decimal
    unknown_models: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CapScope:
    """A dollar cap over a group of calls (a conversation, a rehearsal run or question): the key
    that groups them, the cap's name for messages, the cap and the reason code of a refusal."""

    key: str
    name: str
    cap_usd: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class CapRefusal:
    """A call that is not made: the reason code and a short message naming the cap."""

    reason: str
    message: str


def attempts(usage: Mapping[str, object]) -> tuple[Attempt, ...]:
    """The attempts of a call: the entries of `iterations`, or the top-level usage alone.
    Raises `ValueError` on counts that are not non-negative integers."""
    iterations = usage.get("iterations")
    if iterations is None or iterations == []:
        return (_attempt(usage, None),)
    if not isinstance(iterations, list):
        raise ValueError(f"usage: iterations must be a list, got {iterations!r}")
    return tuple(_attempt(_entry(item), _entry_model(item)) for item in iterations)


def call_cost(usage: Mapping[str, object], served_model: str) -> CallCost:
    """The cost of one call from its usage; attempts without a model are priced at
    `served_model`."""
    total = Decimal(0)
    unknown: list[str] = []
    for attempt in attempts(usage):
        model = attempt.model or served_model
        row, known = prices.lookup(model)
        if not known and model not in unknown:
            unknown.append(model)
        total += _priced(attempt, row)
    return CallCost(total, tuple(unknown))


def context_tokens(usage: Mapping[str, object]) -> int:
    """The context a call leaves for the next one: its largest input of all kinds plus the
    output of every attempt."""
    every = attempts(usage)
    largest = max(attempt.context_input for attempt in (*every, _attempt(usage, None)))
    return largest + sum(attempt.output_tokens for attempt in every)


def tokens_for_chars(chars: int) -> int:
    """Tokens for a number of characters at one token per 2 characters, rounded up."""
    if chars < 0:
        raise ValueError(f"a character count cannot be negative: {chars}")
    return -(-chars // CHARS_PER_TOKEN)


def input_estimate(previous_context: int | None, new_chars: int) -> int:
    """The input estimate of a call: the previous call's context (None for a conversation's first
    call) plus the characters added since, or of the whole first request."""
    if previous_context is not None and previous_context < 0:
        raise ValueError(f"a context size cannot be negative: {previous_context}")
    return (previous_context or 0) + tokens_for_chars(new_chars)


def request_chars(payload: object) -> int:
    """Characters of a request part as compact JSON, the unit of the input estimate."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    return len(text)


def worst_case(input_tokens: int, max_tokens: int, model: str, *, fallbacks: bool) -> Decimal:
    """The most one call can cost: the input at the cache-write price and `max_tokens` at the
    output price, plus one fallback hop at the highest row's prices when fallbacks are on."""
    if input_tokens < 0 or max_tokens < 0:
        raise ValueError("token counts of a worst case cannot be negative")
    row, _known = prices.lookup(model)
    total = _hop(input_tokens, max_tokens, row)
    if fallbacks:
        total += _hop(input_tokens, max_tokens, prices.HIGHEST)
    return total


def conversation_scope(chat_id: str, cap_usd: Decimal) -> CapScope:
    """The cost cap of one conversation (`CHAT_SESSION_MAX_USD`)."""
    return CapScope(f"conversation:{chat_id}", "conversation cost cap", cap_usd, CONVERSATION_CAP)


def daily_scope(cap_usd: Decimal) -> CapScope:
    """The cost cap of the UTC day over every process that writes the spend file."""
    return CapScope("day", "daily cost cap", cap_usd, DAILY_CAP)


def money_check(scope: CapScope, *, committed: Decimal, worst: Decimal) -> CapRefusal | None:
    """Refuse a call when what is spent or reserved plus its worst case passes the cap."""
    if committed + worst <= scope.cap_usd:
        return None
    return CapRefusal(
        scope.reason,
        f"{scope.name} reached ({format_usd(scope.cap_usd)}): {format_usd(committed)} spent or "
        f"reserved, next call up to {format_usd(worst)}",
    )


def context_check(input_tokens: int, cap_tokens: int) -> CapRefusal | None:
    """Refuse a call whose input estimate passes the context cap."""
    if input_tokens <= cap_tokens:
        return None
    return CapRefusal(
        CONTEXT_CAP,
        f"context cap reached ({cap_tokens:,} tokens): the next call would read about "
        f"{input_tokens:,}",
    )


def usage_record(usage: Mapping[str, object]) -> dict[str, object]:
    """The usage as the spend file keeps it: the token counts, the cache-write breakdown and the
    attempts when present. Raises `ValueError` like `attempts`."""
    record: dict[str, object] = {name: _count(usage, name) for name in _COUNTS}
    breakdown = usage.get("cache_creation")
    if breakdown is not None:
        detail = _mapping(breakdown, "cache_creation")
        record["cache_creation"] = {name: _count(detail, name) for name in (_WRITE_5M, _WRITE_1H)}
    iterations = usage.get("iterations")
    if iterations:
        attempts(usage)
        assert isinstance(iterations, list)
        record["iterations"] = [
            {"type": _entry(item).get("type"), "model": _entry_model(item)}
            | {name: _count(_entry(item), name) for name in _COUNTS}
            for item in iterations
        ]
    return record


def as_usd(value: object) -> Decimal:
    """A dollar amount from a setting (number or text) without binary floating-point noise."""
    if isinstance(value, bool) or not isinstance(value, int | float | str | Decimal):
        raise ValueError(f"not a dollar amount: {value!r}")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError(f"not a dollar amount: {value!r}") from None
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"not a dollar amount: {value!r}")
    return amount


def format_usd(amount: Decimal) -> str:
    """An amount in dollars and cents, rounded half up."""
    return f"${amount.quantize(_CENT, rounding=ROUND_HALF_UP)}"


def _hop(input_tokens: int, max_tokens: int, row: ModelPrice) -> Decimal:
    return (input_tokens * row.cache_write_5m + max_tokens * row.output) / _PER_MILLION


def _priced(attempt: Attempt, row: ModelPrice) -> Decimal:
    dollars_per_million = (
        attempt.input_tokens * row.input
        + attempt.output_tokens * row.output
        + attempt.cache_read_tokens * row.cache_read
        + attempt.cache_write_5m_tokens * row.cache_write_5m
        + attempt.cache_write_1h_tokens * row.cache_write_1h
    )
    return dollars_per_million / _PER_MILLION


def _attempt(data: Mapping[str, object], model: str | None) -> Attempt:
    counts = {name: _count(data, name) for name in _COUNTS}
    five_min = one_hour = 0
    breakdown = data.get("cache_creation")
    if breakdown is not None:
        detail = _mapping(breakdown, "cache_creation")
        five_min, one_hour = _count(detail, _WRITE_5M), _count(detail, _WRITE_1H)
    writes = max(counts["cache_creation_input_tokens"], five_min + one_hour)
    return Attempt(
        model,
        counts["input_tokens"],
        counts["output_tokens"],
        counts["cache_read_input_tokens"],
        writes - one_hour,
        one_hour,
    )


def _count(data: Mapping[str, object], name: str) -> int:
    value = data.get(name)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"usage: {name} must be a non-negative integer, got {value!r}")
    return value


def _mapping(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"usage: {what} must be an object, got {value!r}")
    return value


def _entry(item: object) -> Mapping[str, object]:
    return _mapping(item, "an iterations entry")


def _entry_model(item: object) -> str | None:
    model = _entry(item).get("model")
    if model is not None and not isinstance(model, str):
        raise ValueError(f"usage: an iterations entry's model must be text, got {model!r}")
    return model or None
