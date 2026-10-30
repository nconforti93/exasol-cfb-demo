"""Unit tests of `app/backend/chat/scripted.py`: the steps, the templates, the question-keyed
scripts of the rehearsal, and the history check that every request goes through."""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from app.backend.chat import conformance, model, tools
from app.backend.chat.scripted import (
    Hold,
    Script,
    ScriptedCall,
    ScriptedChatModel,
    ScriptExhausted,
    ScriptStep,
    TemplateError,
    render_template,
    render_value,
    runtime_refs,
    script_from_spec,
    turn_view,
)

SETTINGS = model.RequestSettings()
SYSTEM = "You are the analyst."
STATE = '{"app_state": {"page": "odds", "focus_team": {"id": 12, "school": "Ashford"}}}'


def request_for(messages: list[dict[str, Any]]) -> model.ModelRequest:
    return model.build_request(SETTINGS, SYSTEM, messages)


def first_request(question: str = "Q1") -> model.ModelRequest:
    return request_for(
        [{"role": "user", "content": question}, {"role": "system", "content": STATE}]
    )


def call(
    scripted: ScriptedChatModel, req: model.ModelRequest, cancel: model.CancelToken | None = None
) -> tuple[model.ModelResponse, list[str]]:
    pieces: list[str] = []
    response = scripted.respond(req, pieces.append, cancel or model.CancelToken())
    return response, pieces


def result_block(tool_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return tools.tool_result_block(tool_id, tools.ToolResult(json.dumps(body)))


def query_body(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {"kind": "query_result", "columns": columns, "rows": rows, "row_count": len(rows)}


# --- steps in order ----------------------------------------------------------------------------


def test_steps_are_played_in_order_and_requests_and_responses_are_recorded() -> None:
    scripted = ScriptedChatModel(
        [ScriptStep.call("run_sql", {"sql": "SELECT 1", "purpose": "x"}), ScriptStep.say("done")]
    )
    first, _ = call(scripted, first_request())
    assert first.stop_reason == "tool_use"
    second_messages = [
        *first_request().messages,
        {"role": "assistant", "content": list(first.content)},
        {"role": "user", "content": [result_block("toolu_1_1", query_body(["A"], [[1]]))]},
    ]
    second, pieces = call(scripted, request_for(second_messages))
    assert second.stop_reason == "end_turn"
    assert "".join(pieces) == "done"
    assert scripted.calls == 2
    assert len(scripted.requests) == 2
    assert scripted.responses == [first, second]
    scripted.assert_conformant()


def test_a_script_that_runs_out_fails_loudly() -> None:
    scripted = ScriptedChatModel([ScriptStep.say("only")])
    call(scripted, first_request())
    with pytest.raises(ScriptExhausted):
        call(scripted, request_for([*first_request().messages]))


def test_text_streams_in_pieces_that_join_to_the_text() -> None:
    text = "Brookvale has a 41.2 % chance to make the playoff in our simulations, up from 38 %."
    scripted = ScriptedChatModel([ScriptStep.say(text)])
    response, pieces = call(scripted, first_request())
    assert len(pieces) > 1
    assert "".join(pieces) == text
    assert model.text_of(response.content) == text


def test_content_has_a_thinking_block_and_tool_use_blocks_with_ids() -> None:
    step = ScriptStep.calls(
        ScriptedCall("describe_view", {"view": "TEAMS"}),
        ScriptedCall("rooting_guide", {"team_id": 12}, call_id="toolu_custom"),
        text="Let me check.",
    )
    response, pieces = call(ScriptedChatModel([step]), first_request())
    assert [block["type"] for block in response.content] == [
        "thinking",
        "text",
        "tool_use",
        "tool_use",
    ]
    assert [c["id"] for c in model.tool_calls(response.content)] == ["toolu_1_1", "toolu_custom"]
    assert "".join(pieces) == "Let me check."


def test_a_step_without_thinking_has_no_thinking_block() -> None:
    response, _ = call(ScriptedChatModel([ScriptStep.say("x", thinking=False)]), first_request())
    assert [block["type"] for block in response.content] == ["text"]


def test_usage_is_fixed_and_a_step_can_override_it() -> None:
    usage = {"input_tokens": 10, "output_tokens": 5}
    scripted = ScriptedChatModel(
        [ScriptStep.say("a"), ScriptStep.say("b", usage={"output_tokens": 9})], usage=usage
    )
    one, _ = call(scripted, first_request())
    assert one.usage == usage
    other = request_for(
        [
            *first_request().messages,
            {"role": "assistant", "content": list(one.content)},
            {"role": "user", "content": "Q2"},
            {"role": "system", "content": STATE},
        ]
    )
    two, _ = call(scripted, other)
    assert two.usage == {"output_tokens": 9}


def test_the_served_model_defaults_to_the_requested_one_and_can_be_changed() -> None:
    scripted = ScriptedChatModel([ScriptStep.say("a", model="claude-opus-5")])
    response, _ = call(scripted, first_request())
    assert response.model == "claude-opus-5"
    plain, _ = call(ScriptedChatModel([ScriptStep.say("a")]), first_request())
    assert plain.model == "claude-opus-5-5"


@pytest.mark.parametrize("stop", ["max_tokens", "something_new", "refusal"])
def test_the_stop_reason_can_be_overridden(stop: str) -> None:
    step = ScriptStep.call("run_sql", {"sql": "SELECT"}, stop_reason=stop)
    response, _ = call(ScriptedChatModel([step]), first_request())
    assert response.stop_reason == stop


def test_a_refusal_before_any_output_has_empty_content_and_a_category() -> None:
    step = ScriptStep(stop_reason="refusal", stop_details={"type": "refusal", "category": "cyber"})
    response, pieces = call(ScriptedChatModel([step]), first_request())
    assert response.content == ()
    assert pieces == []
    assert response.stop_details == {"type": "refusal", "category": "cyber"}


def test_a_raw_content_override_can_hold_a_fallback_block() -> None:
    fallback = {"type": "fallback", "from": {"model": "a"}, "to": {"model": "b"}}
    step = ScriptStep(
        content=(
            {"type": "text", "text": "partial"},
            {"type": "tool_use", "id": "t1", "name": "run_sql", "input": {}},
            fallback,
            {"type": "tool_use", "id": "t2", "name": "describe_view", "input": {"view": "TEAMS"}},
        ),
        model="claude-opus-5",
    )
    response, pieces = call(ScriptedChatModel([step]), first_request())
    assert "".join(pieces) == "partial"
    assert response.stop_reason == "tool_use"
    assert [c["id"] for c in model.tool_calls(model.echo_content(response.content))] == ["t2"]


def test_input_transformations_and_incomplete_usage_are_passed_through() -> None:
    entry = {"type": "thinking_dropped", "path": "messages.1", "reason": "prefix_binding_mismatch"}
    step = ScriptStep.say("a", input_transformations=(entry,), usage_complete=False)
    response, _ = call(ScriptedChatModel([step]), first_request())
    assert response.input_transformations == (entry,)
    assert response.usage_complete is False


def test_a_step_can_raise_the_models_error() -> None:
    boom = model.ChatModelError("server", "the Anthropic API failed")
    with pytest.raises(model.ChatModelError):
        call(ScriptedChatModel([ScriptStep(raises=boom)]), first_request())


def test_request_ids_are_made_up_per_call() -> None:
    response, _ = call(ScriptedChatModel([ScriptStep.say("a")]), first_request())
    assert response.request_id == "req_script_1"


# --- the history check -------------------------------------------------------------------------


def test_a_request_the_api_would_refuse_fails_the_call_and_is_recorded() -> None:
    scripted = ScriptedChatModel([ScriptStep.say("never")])
    bad = request_for(
        [
            {"role": "user", "content": "Q"},
            {"role": "system", "content": STATE},
            {"role": "user", "content": "Q again"},
        ]
    )
    with pytest.raises(model.HistoryError):
        call(scripted, bad)
    assert next(v.rule for v in scripted.violations) in (2, 3)
    with pytest.raises(conformance.ConformanceError):
        scripted.assert_conformant()


def test_an_edited_history_is_caught_by_the_stateful_rules() -> None:
    scripted = ScriptedChatModel(
        [ScriptStep.call("describe_view", {"view": "TEAMS"}), ScriptStep.say("ok")]
    )
    first, _ = call(scripted, first_request())
    edited_question: list[dict[str, Any]] = [
        {"role": "user", "content": "Q1, reworded"},
        {"role": "system", "content": STATE},
        {"role": "assistant", "content": list(first.content)},
        {"role": "user", "content": [result_block("toolu_1_1", query_body(["A"], [[1]]))]},
    ]
    with pytest.raises(model.HistoryError):
        call(scripted, request_for(edited_question))
    assert 9 in [v.rule for v in scripted.violations]


def test_a_changed_system_prompt_is_caught() -> None:
    scripted = ScriptedChatModel([ScriptStep.say("a"), ScriptStep.say("b")])
    first, _ = call(scripted, first_request())
    changed = model.build_request(
        SETTINGS,
        "A different prompt.",
        [
            *first_request().messages,
            {"role": "assistant", "content": list(first.content)},
            {"role": "user", "content": "Q2"},
            {"role": "system", "content": STATE},
        ],
    )
    with pytest.raises(model.HistoryError):
        call(scripted, changed)
    assert 8 in [v.rule for v in scripted.violations]


def test_checking_can_be_switched_off() -> None:
    scripted = ScriptedChatModel([ScriptStep.say("a")], check=False)
    bad = request_for(
        [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": [{"type": "text", "text": "x"}]},
        ]
    )
    response, _ = call(scripted, bad)
    assert response.stop_reason == "end_turn"
    assert scripted.violations == []


# --- cancel and holds --------------------------------------------------------------------------


def test_a_cancelled_token_stops_before_any_step_is_played() -> None:
    scripted = ScriptedChatModel([ScriptStep.say("never")])
    cancel = model.CancelToken()
    cancel.cancel()
    with pytest.raises(model.ModelCancelled):
        call(scripted, first_request(), cancel)
    assert scripted.responses == []


def test_a_held_stream_stays_open_until_released() -> None:
    hold = Hold()
    scripted = ScriptedChatModel([ScriptStep(answer="streamed text", hold=hold)])
    result: list[model.ModelResponse] = []
    thread = threading.Thread(
        target=lambda: result.append(call(scripted, first_request())[0]), daemon=True
    )
    thread.start()
    assert hold.reached.wait(5)
    assert result == []
    hold.release.set()
    thread.join(5)
    assert result[0].stop_reason == "end_turn"


def test_a_cancel_while_held_closes_the_stream_and_raises() -> None:
    hold = Hold()
    scripted = ScriptedChatModel([ScriptStep(answer="streamed text", hold=hold)])
    cancel = model.CancelToken()
    outcome: list[str] = []

    def run() -> None:
        try:
            call(scripted, first_request(), cancel)
            outcome.append("returned")
        except model.ModelCancelled:
            outcome.append("cancelled")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert hold.reached.wait(5)
    cancel.cancel()
    thread.join(5)
    assert outcome == ["cancelled"]
    assert hold.closed.is_set()
    assert scripted.responses == []


# --- templates ---------------------------------------------------------------------------------

RESULTS: dict[int, dict[str, Any]] = {
    1: {"kind": "app_change", "applied": "x"},
    2: query_body(
        ["TEAM_ID", "SCHOOL", "P_IN_CFP", "BASE_P_IN_CFP", "EFFECTIVE_SAMPLE", "DELTA"],
        [[12, "Brookvale", 0.4123, 0.38, 1530, -0.0215], [7, "Ashford", 0.1, 0.1, 99, 0]],
    ),
}
STATE_FLAT = {"focus_team_id": 12, "focus_team_school": "Ashford", "page": "odds"}


def test_templates_read_the_first_row_of_a_tool_result() -> None:
    template = (
        "{t2.row.SCHOOL}: {t2.row.P_IN_CFP|pct} (from {t2.row.BASE_P_IN_CFP|pct0}), "
        "n={t2.row.EFFECTIVE_SAMPLE|int}"
    )
    assert render_template(template, RESULTS, STATE_FLAT) == "Brookvale: 41.2% (from 38%), n=1530"


def test_templates_read_other_rows_and_the_row_count() -> None:
    assert render_template("{t2.rows.1.SCHOOL} of {t2.row_count}", RESULTS, {}) == "Ashford of 2"


def test_formats() -> None:
    assert render_template("{t2.row.DELTA|pp}", RESULTS, {}) == "-2.1 percentage points"
    assert render_template("{t2.row.P_IN_CFP|dec2}", RESULTS, {}) == "0.41"
    assert render_template("{t2.row.P_IN_CFP|dec1}", RESULTS, {}) == "0.4"
    assert render_template("{t2.row.EFFECTIVE_SAMPLE}", RESULTS, {}) == "1530"


def test_templates_read_the_app_state() -> None:
    assert (
        render_template("{state.focus_team_school} on {state.page}", {}, STATE_FLAT)
        == "Ashford on odds"
    )


def test_text_without_placeholders_and_other_braces_is_left_alone() -> None:
    assert render_template('{"a": 1} and {API}', RESULTS, {}) == '{"a": 1} and {API}'


@pytest.mark.parametrize(
    "template",
    [
        "{t9.row.X}",
        "{t2.row.NOPE}",
        "{t2.rows.5.SCHOOL}",
        "{t1.row.X}",
        "{state.missing}",
        "{t2.row.SCHOOL|pct}",
        "{t2.row.P_IN_CFP|weird}",
    ],
)
def test_a_placeholder_that_cannot_be_filled_raises(template: str) -> None:
    with pytest.raises((TemplateError, ValueError)):
        render_template(template, RESULTS, STATE_FLAT)


def test_a_whole_value_placeholder_keeps_its_native_type() -> None:
    value = {
        "team_ids": ["{state.focus_team_id}"],
        "sql": "WHERE ID = {state.focus_team_id}",
        "n": 3,
    }
    assert render_value(value, RESULTS, STATE_FLAT) == {
        "team_ids": [12],
        "sql": "WHERE ID = 12",
        "n": 3,
    }


def test_a_null_value_cannot_be_formatted() -> None:
    with pytest.raises(TemplateError, match="NULL"):
        render_template("{t1.row.A|pct}", {1: query_body(["A"], [[None]])}, {})


def test_runtime_refs_are_found_in_nested_values() -> None:
    value = {"a": ["{t1.row.X|pct}", "plain"], "b": {"c": "x {state.page} y"}}
    assert runtime_refs(value) == [("t1.row.X", "pct"), ("state.page", None)]


def test_script_from_spec_builds_steps() -> None:
    script = script_from_spec(
        [
            {
                "tools": [
                    {"name": "set_whatif", "input": {"mode": "replace"}},
                    {"name": "clear_whatif"},
                ]
            },
            {"answer": "done {t1.row_count}"},
        ]
    )
    assert isinstance(script, Script)
    assert [c.name for c in script.steps[0].tools] == ["set_whatif", "clear_whatif"]
    assert script.steps[0].tools[1].input == {}
    assert script.steps[1].answer == "done {t1.row_count}"
    with pytest.raises(ValueError, match="step 1"):
        script_from_spec([{"nope": 1}])


# --- reading the turn out of a request ----------------------------------------------------------


def test_the_turn_view_reads_question_state_steps_and_results() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "Old question"},
        {"role": "system", "content": STATE},
        {"role": "assistant", "content": [{"type": "text", "text": "Old answer"}]},
        {"role": "user", "content": "What are the odds?"},
        {"role": "system", "content": STATE},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "a", "name": "run_sql", "input": {}}],
        },
        {"role": "user", "content": [result_block("a", query_body(["X"], [[5]]))]},
    ]
    view = turn_view(messages)
    assert view.question == "What are the odds?"
    assert view.step_index == 1
    assert view.results == {1: query_body(["X"], [[5]])}
    assert view.state == {"page": "odds", "focus_team_id": 12, "focus_team_school": "Ashford"}


def test_an_open_tail_question_is_the_text_after_the_results() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "Q1"},
        {"role": "system", "content": STATE},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "a", "name": "run_sql", "input": {}}],
        },
        {
            "role": "user",
            "content": [
                result_block("a", query_body(["X"], [[5]])),
                {"type": "text", "text": "Q2"},
            ],
        },
        {"role": "system", "content": STATE},
    ]
    view = turn_view(messages)
    assert view.question == "Q2"
    assert view.step_index == 0
    assert view.results == {}


def test_a_request_without_a_question_is_an_error() -> None:
    with pytest.raises(model.ChatModelError):
        turn_view([{"role": "system", "content": STATE}])


# --- the question-keyed model ------------------------------------------------------------------

QUESTION = "What are the odds for the team I'm looking at?"


def stub_model() -> ScriptedChatModel:
    script = script_from_spec(
        [
            {
                "tools": [
                    {
                        "name": "run_sql",
                        "input": {
                            "purpose": "odds",
                            "sql": (
                                'SELECT "SCHOOL", "P_IN_CFP" FROM "PLAYOFF_ODDS" '
                                'WHERE "TEAM_ID" = {state.focus_team_id}'
                            ),
                        },
                    }
                ]
            },
            {"answer": "{t1.row.SCHOOL} has a {t1.row.P_IN_CFP|pct} chance."},
        ]
    )
    return ScriptedChatModel.by_question({QUESTION: script})


def test_the_question_script_is_played_step_by_step_with_state_and_results() -> None:
    scripted = stub_model()
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": QUESTION},
        {"role": "system", "content": STATE},
    ]
    one, _ = call(scripted, request_for(messages))
    (tool_call,) = model.tool_calls(one.content)
    assert tool_call["input"]["sql"].endswith('"TEAM_ID" = 12')
    messages += [
        {"role": "assistant", "content": list(one.content)},
        {
            "role": "user",
            "content": [
                result_block(
                    tool_call["id"], query_body(["SCHOOL", "P_IN_CFP"], [["Ashford", 0.1]])
                )
            ],
        },
    ]
    two, pieces = call(scripted, request_for(messages))
    assert "".join(pieces) == "Ashford has a 10.0% chance."
    assert two.stop_reason == "end_turn"
    scripted.assert_conformant()


def test_an_unknown_question_gets_the_fixed_stub_answer() -> None:
    scripted = stub_model()
    response, pieces = call(scripted, first_request("Tell me a joke"))
    assert "rehearsed questions" in "".join(pieces)
    assert response.stop_reason == "end_turn"


def test_a_follow_up_question_is_matched_by_its_own_text() -> None:
    scripted = stub_model()
    one, _ = call(scripted, first_request("Tell me a joke"))
    follow_up = request_for(
        [
            *first_request("Tell me a joke").messages,
            {"role": "assistant", "content": list(one.content)},
            {"role": "user", "content": QUESTION},
            {"role": "system", "content": STATE},
        ]
    )
    two, _ = call(scripted, follow_up)
    assert two.stop_reason == "tool_use"


def test_a_script_that_cannot_be_filled_answers_with_the_reason() -> None:
    script = script_from_spec([{"answer": "{t1.row.X}"}])
    scripted = ScriptedChatModel.by_question({"Q1": script})
    response, pieces = call(scripted, first_request("Q1"))
    assert "could not be built from the data" in "".join(pieces)
    assert response.stop_reason == "end_turn"


def test_the_freeze_format_names_how_the_predictions_were_made() -> None:
    results = {1: query_body(["K"], [["ALL"], ["IN_ADVANCE"], ["RECONSTRUCTED"], ["NEW_KIND"]])}
    text = (
        "{t1.rows.0.K|freeze} / {t1.rows.1.K|freeze} / {t1.rows.2.K|freeze} / {t1.rows.3.K|freeze}"
    )
    rendered = render_template(text, results, {})
    assert rendered.startswith(
        "reconstructed at the as-of point (leak-free, made after kickoff) / "
    )
    assert "made in advance" in rendered
    assert rendered.endswith("new_kind")
