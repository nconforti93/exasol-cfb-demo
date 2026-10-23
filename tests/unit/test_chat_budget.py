"""Unit tests of `app/backend/chat/prices.py` and `app/backend/chat/budget.py` (`chat/budget`): the
price table, the cost of a call from its usage, the input estimate, the worst case and the cap
decisions. Every expected amount is worked out by hand in the comments.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.backend.chat import budget, prices
from app.backend.chat.budget import CapRefusal, CapScope
from app.backend.chat.prices import ModelPrice

D = Decimal
OPUS_55 = "claude-opus-5-5"
OPUS_5 = "claude-opus-5"
OPUS_48 = "claude-opus-4-8"
SPEC_USAGE: dict[str, Any] = {
    "input_tokens": 1_200,
    "cache_read_input_tokens": 15_000,
    "cache_creation_input_tokens": 2_000,
    "output_tokens": 900,
}


# --- prices ---


def test_price_table_has_its_source_date_and_the_fallback_rows() -> None:
    assert prices.SOURCE == "https://platform.claude.com/docs/en/about-claude/pricing"
    assert prices.CHECKED == "2026-10-23"
    rows = {row.model: row for row in prices.TABLE}
    assert set(rows) == {OPUS_55, OPUS_5, OPUS_48}
    assert rows[OPUS_55] == ModelPrice(
        OPUS_55, D("4.00"), D("20.00"), D("5.00"), D("8.00"), D("0.20")
    )
    assert rows[OPUS_5] == ModelPrice(
        OPUS_5, D("5.00"), D("25.00"), D("6.25"), D("10.00"), D("0.50")
    )
    assert rows[OPUS_48] == ModelPrice(
        OPUS_48, D("5.00"), D("25.00"), D("6.25"), D("10.00"), D("0.50")
    )
    for row in rows.values():
        assert row.cache_write_5m == row.input * D("1.25")
        assert row.cache_write_1h == row.input * 2


def test_highest_row_is_the_maximum_of_every_column() -> None:
    top = prices.HIGHEST
    assert (top.input, top.output, top.cache_write_5m, top.cache_write_1h, top.cache_read) == (
        D("5.00"),
        D("25.00"),
        D("6.25"),
        D("10.00"),
        D("0.50"),
    )


def test_lookup_flags_a_model_outside_the_table() -> None:
    row, known = prices.lookup(OPUS_55)
    assert known and row.model == OPUS_55
    row, known = prices.lookup("claude-fable-5-1")
    assert not known and row == prices.HIGHEST


# --- cost of a call ---


def test_price_per_call() -> None:
    # 1,200 x $4 + 15,000 x $0.20 + 2,000 x $5 + 900 x $20, per million tokens
    first = budget.call_cost(SPEC_USAGE, OPUS_55)
    assert first.usd == D("0.0048") + D("0.0030") + D("0.0100") + D("0.0180") == D("0.0358")
    assert first.unknown_models == ()

    # each attempt at its own model; the top-level usage (the serving attempt) is not added again
    # opus-5-5: 3,000 x $4 + 400 x $20 + 6,000 x $0.20 + 1,000 x $5   = 0.0262
    # opus-4-8: 9,000 x $5 + 700 x $25                                 = 0.0625
    second_usage = {
        "input_tokens": 9_000,
        "output_tokens": 700,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "iterations": [
            {
                "type": "message",
                "model": OPUS_55,
                "input_tokens": 3_000,
                "output_tokens": 400,
                "cache_read_input_tokens": 6_000,
                "cache_creation_input_tokens": 1_000,
            },
            {
                "type": "fallback_message",
                "model": OPUS_48,
                "input_tokens": 9_000,
                "output_tokens": 700,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        ],
    }
    second = budget.call_cost(second_usage, OPUS_48)
    assert second.usd == D("0.0262") + D("0.0625") == D("0.0887")
    assert second.unknown_models == ()

    # the highest row: 1,200 x $5 + 15,000 x $0.50 + 2,000 x $6.25 + 900 x $25 = 0.0485
    third = budget.call_cost(SPEC_USAGE, "claude-unknown-1")
    assert third.usd == D("0.0485")
    assert third.unknown_models == ("claude-unknown-1",)


def test_an_iteration_without_a_model_is_priced_at_the_served_model() -> None:
    usage = {"iterations": [{"type": "compaction", "input_tokens": 1_000, "output_tokens": 0}]}
    assert budget.call_cost(usage, OPUS_5).usd == D("0.005")


def test_an_empty_iterations_list_prices_the_top_level_usage() -> None:
    assert budget.call_cost({**SPEC_USAGE, "iterations": []}, OPUS_55).usd == D("0.0358")


def test_one_hour_cache_writes_are_priced_at_their_own_rate() -> None:
    # 1,000 at $5 (5-minute) + 2,000 at $8 (1-hour) = 0.005 + 0.016
    usage = {
        "cache_creation_input_tokens": 3_000,
        "cache_creation": {"ephemeral_5m_input_tokens": 1_000, "ephemeral_1h_input_tokens": 2_000},
    }
    assert budget.call_cost(usage, OPUS_55).usd == D("0.021")


def test_missing_and_null_counts_are_zero() -> None:
    usage = {"input_tokens": 1_000, "output_tokens": None, "cache_creation": None}
    assert budget.call_cost(usage, OPUS_55).usd == D("0.004")
    assert budget.call_cost({}, OPUS_55).usd == D("0")


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": -1},
        {"output_tokens": 1.5},
        {"output_tokens": "10"},
        {"output_tokens": True},
        {"iterations": "x"},
        {"iterations": [{"model": OPUS_55, "input_tokens": -5}]},
        {"iterations": [7]},
        {"cache_creation": {"ephemeral_1h_input_tokens": -1}},
    ],
)
def test_malformed_usage_is_rejected(usage: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="usage"):
        budget.call_cost(usage, OPUS_55)


# --- context size and the input estimate ---


def test_context_of_a_call_is_its_input_of_all_kinds_plus_its_output() -> None:
    assert budget.context_tokens(SPEC_USAGE) == 1_200 + 15_000 + 2_000 + 900


def test_context_after_a_fallback_takes_the_largest_input_and_every_output() -> None:
    usage = {
        "input_tokens": 9_000,
        "output_tokens": 700,
        "iterations": [
            {"model": OPUS_55, "input_tokens": 3_000, "cache_read_input_tokens": 7_000},
            {"model": OPUS_48, "input_tokens": 9_000, "output_tokens": 700},
            {"model": OPUS_55, "output_tokens": 400},
        ],
    }
    assert budget.context_tokens(usage) == 10_000 + 700 + 400


def test_tokens_for_characters_round_up_at_two_characters_per_token() -> None:
    assert [budget.tokens_for_chars(n) for n in (0, 1, 2, 3, 1_001)] == [0, 1, 1, 2, 501]
    with pytest.raises(ValueError):
        budget.tokens_for_chars(-1)


def test_input_estimate_of_a_first_and_a_later_call() -> None:
    assert budget.input_estimate(None, 30_001) == 15_001
    assert budget.input_estimate(10_000 + 3_000, 600) == 13_300


def test_request_chars_count_compact_json_characters() -> None:
    assert budget.request_chars({"a": "\N{LATIN SMALL LETTER E WITH ACUTE}", "b": [1, 2]}) == len(
        '{"a":"e","b":[1,2]}'
    )


# --- the worst case ---


def test_worst_case_fallback_hop() -> None:
    # 10,000 x $5 (cache write) + 8,000 x $20; the hop: 10,000 x $6.25 + 8,000 x $25
    without = budget.worst_case(10_000, 8_000, OPUS_55, fallbacks=False)
    assert without == D("0.0500") + D("0.1600") == D("0.2100")
    with_hop = budget.worst_case(10_000, 8_000, OPUS_55, fallbacks=True)
    assert with_hop == D("0.2100") + D("0.0625") + D("0.2000") == D("0.4725")


def test_worst_case_of_an_unknown_model_uses_the_highest_row() -> None:
    assert budget.worst_case(10_000, 8_000, "claude-x", fallbacks=False) == D("0.2625")


def test_worst_case_bounds_any_call_within_its_input_and_max_tokens() -> None:
    worst = budget.worst_case(13_000, 8_000, OPUS_55, fallbacks=True)
    for usage in (
        {"input_tokens": 13_000, "output_tokens": 8_000},
        {"cache_creation_input_tokens": 13_000, "output_tokens": 8_000},
        {
            "iterations": [
                {"model": OPUS_55, "cache_creation_input_tokens": 13_000, "output_tokens": 8_000},
                {"model": OPUS_5, "cache_creation_input_tokens": 13_000, "output_tokens": 8_000},
            ]
        },
    ):
        assert budget.call_cost(usage, OPUS_55).usd <= worst


# --- cap decisions ---


def test_money_cap_admits_a_call_that_reaches_the_cap_exactly() -> None:
    scope = budget.conversation_scope("c1", D("0.80"))
    assert scope == CapScope(
        "conversation:c1", "conversation cost cap", D("0.80"), "conversation_cap"
    )
    assert budget.money_check(scope, committed=D("0.50"), worst=D("0.30")) is None
    refusal = budget.money_check(scope, committed=D("0.60"), worst=D("0.2265"))
    assert refusal == CapRefusal(
        "conversation_cap",
        "conversation cost cap reached ($0.80): $0.60 spent or reserved, next call up to $0.23",
    )


def test_daily_cap_names_itself() -> None:
    refusal = budget.money_check(
        budget.daily_scope(D("1.00")), committed=D("0.98"), worst=D("0.21")
    )
    assert refusal is not None and refusal.reason == "daily_cap"
    assert refusal.message.startswith("daily cost cap reached ($1.00)")


def test_context_cap_admits_an_estimate_at_the_cap_exactly() -> None:
    assert budget.context_check(18_000, 18_000) is None
    assert budget.context_check(19_000, 18_000) == CapRefusal(
        "context_cap",
        "context cap reached (18,000 tokens): the next call would read about 19,000",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [(2, D("2")), (2.0, D("2.0")), (0.1, D("0.1")), ("20.00", D("20.00")), (D("1.5"), D("1.5"))],
)
def test_as_usd_converts_settings_without_binary_noise(value: object, expected: Decimal) -> None:
    assert budget.as_usd(value) == expected


@pytest.mark.parametrize("value", [-1, "nan", "inf", "abc", None, True])
def test_as_usd_rejects_values_that_are_not_amounts(value: object) -> None:
    with pytest.raises(ValueError):
        budget.as_usd(value)


def test_format_usd_rounds_half_up_to_cents() -> None:
    assert [budget.format_usd(D(x)) for x in ("0.2265", "0.005", "2", "0.0048")] == [
        "$0.23",
        "$0.01",
        "$2.00",
        "$0.00",
    ]


def test_default_caps_are_the_plans() -> None:
    assert D("2.00") == budget.DEFAULT_SESSION_MAX_USD
    assert D("20.00") == budget.DEFAULT_DAILY_MAX_USD
    assert budget.DEFAULT_CALL_MAX_INPUT_TOKENS == 150_000


def test_usage_record_keeps_counts_breakdown_and_attempts() -> None:
    usage = {
        "input_tokens": 9_000,
        "output_tokens": None,
        "service_tier": "standard",
        "cache_creation": {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 0},
        "iterations": [{"type": "fallback_message", "model": OPUS_48, "input_tokens": 9_000}],
    }
    assert budget.usage_record(usage) == {
        "input_tokens": 9_000,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_creation": {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 0},
        "iterations": [
            {
                "type": "fallback_message",
                "model": OPUS_48,
                "input_tokens": 9_000,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            }
        ],
    }
    with pytest.raises(ValueError, match="usage"):
        budget.usage_record({"iterations": [{"model": 5}]})
    with pytest.raises(ValueError, match="usage"):
        budget.usage_record({"cache_creation": [1]})


def test_negative_sizes_are_rejected() -> None:
    with pytest.raises(ValueError):
        budget.input_estimate(-1, 0)
    with pytest.raises(ValueError):
        budget.worst_case(-1, 8_000, OPUS_55, fallbacks=False)
    with pytest.raises(ValueError):
        budget.worst_case(1, -8_000, OPUS_55, fallbacks=False)
