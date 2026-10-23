"""Unit tests of `app/backend/chat/conformance.py`: the API's nine rules for the history, with and
without per-conversation state."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from app.backend.chat import conformance
from app.backend.chat.conformance import Conformance, Violation

SYSTEM = [{"type": "text", "text": "You are the analyst.", "cache_control": {"type": "ephemeral"}}]
TOOLS = [{"name": "run_sql", "description": "d", "input_schema": {"type": "object"}}]


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def tool_use(tool_id: str, name: str = "run_sql") -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": {"sql": "SELECT 1"}}


def tool_result(tool_id: str, body: str = "{}") -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": tool_id, "content": [text(body)]}


def user(*blocks: dict[str, Any] | str) -> dict[str, Any]:
    only = blocks[0] if len(blocks) == 1 else None
    return {"role": "user", "content": only if isinstance(only, str) else list(blocks)}


def assistant(*blocks: dict[str, Any]) -> dict[str, Any]:
    return {"role": "assistant", "content": list(blocks)}


def system_message(body: str = '{"app_state": {}}') -> dict[str, Any]:
    return {"role": "system", "content": body}


def request(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {"system": copy.deepcopy(SYSTEM), "tools": copy.deepcopy(TOOLS), "messages": messages}


def valid_two_turns() -> list[dict[str, Any]]:
    return [
        user("What are Brookvale's odds?"),
        system_message(),
        assistant({"type": "thinking", "thinking": "", "signature": "sig"}, tool_use("t1")),
        user(tool_result("t1")),
        assistant(text("41.2 %")),
        user("And Ashford?"),
        system_message(),
    ]


def rules_of(violations: tuple[Violation, ...]) -> list[int]:
    return [violation.rule for violation in violations]


# --- the rules without state -------------------------------------------------------------------


def test_a_valid_history_passes() -> None:
    assert conformance.check_stateless(valid_two_turns()) == ()


def test_a_first_request_with_one_question_and_its_state_passes() -> None:
    assert conformance.check_stateless([user("Q"), system_message()]) == ()


def test_rule_1_empty_messages_and_a_first_assistant_message() -> None:
    assert rules_of(conformance.check_stateless([])) == [1]
    broken = [assistant(text("hi")), user("Q")]
    assert 1 in rules_of(conformance.check_stateless(broken))


def test_rule_2_a_system_message_must_follow_a_user_message() -> None:
    messages = valid_two_turns()
    messages.insert(3, system_message())
    violations = conformance.check_stateless(messages)
    assert 2 in rules_of(violations)
    assert any(violation.position == 3 for violation in violations if violation.rule == 2)


def test_rule_2_a_system_message_followed_by_a_user_message_is_refused() -> None:
    messages = [user("Q"), system_message(), user("Q2")]
    violations = conformance.check_stateless(messages)
    assert 2 in rules_of(violations)
    assert [v.position for v in violations if v.rule == 2] == [1]


def test_rule_2_a_leading_system_message_is_refused() -> None:
    assert 2 in rules_of(conformance.check_stateless([system_message(), user("Q")]))


def test_rule_3_roles_alternate_ignoring_system_messages() -> None:
    messages = [user("Q"), system_message(), user("Q2")]
    assert 3 in rules_of(conformance.check_stateless(messages))
    twice = [user("Q"), assistant(text("a")), assistant(text("b")), user("Q2")]
    assert 3 in rules_of(conformance.check_stateless(twice))


def test_rule_4_empty_content_and_blank_text_blocks() -> None:
    assert 4 in rules_of(conformance.check_stateless([user("Q"), assistant(), user("Q2")]))
    assert 4 in rules_of(conformance.check_stateless([user("  ")]))
    blank = [user("Q"), assistant(text("  ")), user("Q2")]
    assert 4 in rules_of(conformance.check_stateless(blank))
    assert 4 in rules_of(conformance.check_stateless([user("Q"), system_message("")]))


def test_rule_4_does_not_refuse_an_empty_thinking_block() -> None:
    messages = [
        user("Q"),
        assistant({"type": "thinking", "thinking": "", "signature": "s"}, text("A")),
        user("Q2"),
    ]
    assert conformance.check_stateless(messages) == ()


def test_rule_5_a_tool_call_needs_its_result_in_the_next_message() -> None:
    messages = [user("Q"), assistant(tool_use("t1")), user("next question")]
    violations = conformance.check_stateless(messages)
    assert 5 in rules_of(violations)
    assert any("t1" in violation.message for violation in violations)


def test_rule_5_results_come_first_in_the_order_of_the_calls() -> None:
    swapped = [
        user("Q"),
        assistant(tool_use("t1"), tool_use("t2")),
        user(tool_result("t2"), tool_result("t1")),
        assistant(text("done")),
        user("Q2"),
    ]
    assert 5 in rules_of(conformance.check_stateless(swapped))


def test_rule_5_a_tool_result_after_a_text_block_is_refused() -> None:
    messages = [
        user("Q"),
        assistant(tool_use("t1")),
        user(text("a note"), tool_result("t1")),
    ]
    assert 5 in rules_of(conformance.check_stateless(messages))


def test_rule_5_a_tool_result_without_a_call_is_refused() -> None:
    messages = [user("Q"), assistant(text("A")), user(tool_result("t9"))]
    assert 5 in rules_of(conformance.check_stateless(messages))
    first = [user(tool_result("t9"))]
    assert 5 in rules_of(conformance.check_stateless(first))


def test_rule_5_an_extra_result_is_refused() -> None:
    messages = [
        user("Q"),
        assistant(tool_use("t1")),
        user(tool_result("t1"), tool_result("t2")),
    ]
    assert 5 in rules_of(conformance.check_stateless(messages))


def test_an_open_tail_is_valid() -> None:
    messages = [
        user("Q"),
        system_message(),
        assistant(tool_use("t1")),
        user(tool_result("t1"), text("Next question")),
        system_message(),
    ]
    assert conformance.check_stateless(messages) == ()


def test_rule_6_the_last_message_is_not_an_assistant_message() -> None:
    messages = [user("Q"), assistant(text("A"))]
    assert 6 in rules_of(conformance.check_stateless(messages))


def test_rule_7_no_thinking_or_tool_call_before_a_fallback_block() -> None:
    fallback = {"type": "fallback", "from": {"model": "a"}, "to": {"model": "b"}}
    thinking = {"type": "thinking", "thinking": "", "signature": "s"}
    for early in (thinking, {"type": "redacted_thinking", "data": "x"}, tool_use("t1")):
        messages = [user("Q"), assistant(early, fallback, text("A")), user("Q2")]
        assert 7 in rules_of(conformance.check_stateless(messages))


def test_rule_7_no_fallback_block_at_all_in_the_history() -> None:
    fallback = {"type": "fallback", "from": {"model": "a"}, "to": {"model": "b"}}
    messages = [user("Q"), assistant(fallback, text("A")), user("Q2")]
    assert 7 in rules_of(conformance.check_stateless(messages))


def test_a_fallback_echoed_response_is_valid() -> None:
    messages = [
        user("Q"),
        assistant(text("partial"), tool_use("t2")),
        user(tool_result("t2")),
    ]
    assert conformance.check_stateless(messages) == ()


def test_every_violation_names_the_rule_and_the_position() -> None:
    violations = conformance.check_stateless([user("Q"), assistant(text("A"))])
    text_form = str(violations[0])
    assert "rule 6" in text_form
    assert "message 1" in text_form


# --- the rules with state ----------------------------------------------------------------------


def run_two_turns() -> tuple[Conformance, list[dict[str, Any]]]:
    record = Conformance()
    first = [user("Q1"), system_message()]
    assert record.check(request(first)) == ()
    reply_one = [{"type": "thinking", "thinking": "", "signature": "sig"}, tool_use("t1")]
    record.record(request(first), reply_one)
    second = [*first, assistant(*reply_one), user(tool_result("t1"))]
    assert record.check(request(second)) == ()
    reply_two = [text("41.2 %")]
    record.record(request(second), reply_two)
    third = [*second, assistant(*reply_two), user("Q2"), system_message()]
    return record, third


def test_a_valid_two_turn_conversation_passes_with_state() -> None:
    record, third = run_two_turns()
    assert record.check(request(third)) == ()


def test_rule_8_a_changed_system_prompt_is_refused() -> None:
    record, third = run_two_turns()
    changed = request(third)
    changed["system"][0]["text"] = "You are a different analyst."
    assert rules_of(record.check(changed)) == [8]


def test_rule_8_a_changed_tool_definition_is_refused() -> None:
    record, third = run_two_turns()
    changed = request(third)
    changed["tools"][0]["description"] = "changed"
    assert rules_of(record.check(changed)) == [8]


def test_rule_9_a_changed_earlier_message_is_refused() -> None:
    record, third = run_two_turns()
    third[0] = user("Q1, reworded")
    violations = record.check(request(third))
    assert 9 in rules_of(violations)


def test_rule_9_an_assistant_message_that_was_never_returned_is_refused() -> None:
    record, third = run_two_turns()
    third[4] = assistant(text("something else"))
    assert 9 in rules_of(record.check(request(third)))


def test_rule_9_an_edited_thinking_block_is_refused() -> None:
    record, third = run_two_turns()
    third[2]["content"][0]["signature"] = "other"
    assert 9 in rules_of(record.check(request(third)))


def test_rule_9_a_message_inserted_before_a_kept_response_is_refused() -> None:
    record, third = run_two_turns()
    third.insert(1, user("sneaked in"))
    assert 9 in rules_of(record.check(request(third)))


def test_state_survives_a_turn_that_was_not_kept() -> None:
    record, third = run_two_turns()
    declined = [*third]
    assert record.check(request(declined)) == ()
    record.record(request(declined), [])
    next_question = [*third[:-2], user("Q3"), system_message()]
    assert record.check(request(next_question)) == ()


def test_the_stateful_check_includes_the_stateless_rules() -> None:
    record, third = run_two_turns()
    broken = [*third, assistant(text("prefill"))]
    assert 6 in rules_of(record.check(request(broken)))


def test_a_first_request_without_state_only_runs_the_stateless_rules() -> None:
    assert Conformance().check(request([user("Q"), system_message()])) == ()


def test_the_check_does_not_change_the_request() -> None:
    record, third = run_two_turns()
    body = request(third)
    before = json.dumps(body)
    record.check(body)
    assert json.dumps(body) == before


def test_raise_on_violation_names_every_rule() -> None:
    with pytest.raises(conformance.ConformanceError) as caught:
        conformance.require(conformance.check_stateless([user("Q"), assistant(text("A"))]))
    assert "rule 6" in str(caught.value)
    conformance.require(())
