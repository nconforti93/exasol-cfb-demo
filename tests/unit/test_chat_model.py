"""Unit tests of `app/backend/chat/model.py`: the request, the echo rule, and the real SDK over an
in-process transport (the key, the host, the fields, errors without HTTP detail, cancel)."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest

from app.backend.chat import model, tools
from tests.unit import chat_sdk_fakes as fakes

SYSTEM_TEXT = "You are the analyst."
QUESTION: list[dict[str, Any]] = [
    {"role": "user", "content": "What are Brookvale's odds?"},
    {"role": "system", "content": '{"app_state": {"page": "odds"}}'},
]


def request(**overrides: Any) -> model.ModelRequest:
    settings = model.RequestSettings(**overrides)
    return model.build_request(settings, SYSTEM_TEXT, QUESTION)


def install(monkeypatch: pytest.MonkeyPatch, recorder: fakes.Recorder) -> None:
    def factory() -> anthropic.DefaultHttpxClient:
        return anthropic.DefaultHttpxClient(
            transport=recorder.transport(), event_hooks={"request": [model.request_guard]}
        )

    monkeypatch.setattr(model, "http_client_factory", factory)


def respond(
    recorder: fakes.Recorder,
    monkeypatch: pytest.MonkeyPatch,
    *,
    on_text: Callable[[str], None] | None = None,
    cancel: model.CancelToken | None = None,
    **overrides: Any,
) -> model.ModelResponse:
    install(monkeypatch, recorder)
    client = model.AnthropicChatModel(fakes.MARKER_KEY)
    return client.respond(
        request(**overrides), on_text or (lambda _text: None), cancel or model.CancelToken()
    )


# --- build_request -----------------------------------------------------------------------------


def test_the_request_has_every_field_of_the_design() -> None:
    built = request()
    kwargs = built.kwargs()
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["max_tokens"] == 8000
    assert kwargs["thinking"] == {
        "type": "adaptive",
        "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }
    assert kwargs["output_config"] == {"effort": "medium"}
    assert kwargs["tool_choice"] == {"type": "auto"}
    assert kwargs["cache_control"] == {"type": "ephemeral"}
    assert kwargs["system"] == [
        {"type": "text", "text": SYSTEM_TEXT, "cache_control": {"type": "ephemeral"}}
    ]
    assert kwargs["betas"] == [model.BETA_BINDING, model.BETA_FALLBACK]
    assert kwargs["fallbacks"] == "default"
    assert kwargs["messages"] == QUESTION


def test_the_eight_tools_are_sent_in_order_with_eager_input_streaming() -> None:
    sent = request().kwargs()["tools"]
    assert [item["name"] for item in sent] == list(tools.TOOL_NAMES)
    assert all(item["eager_input_streaming"] is True for item in sent)


def test_thinking_is_never_disabled_or_budgeted_and_the_choice_is_never_forced() -> None:
    kwargs = request().kwargs()
    assert kwargs["thinking"]["type"] == "adaptive"
    assert "budget_tokens" not in kwargs["thinking"]
    assert kwargs["tool_choice"]["type"] == "auto"
    for sampling in ("temperature", "top_p", "top_k"):
        assert sampling not in kwargs


def test_fallbacks_off_removes_the_field_and_its_beta() -> None:
    kwargs = request(fallbacks=False).kwargs()
    assert "fallbacks" not in kwargs
    assert kwargs["betas"] == [model.BETA_BINDING]


def test_effort_and_prefix_behavior_follow_the_settings() -> None:
    kwargs = request(effort="low", prefix_mismatch="error", max_tokens=4000).kwargs()
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "error"}
    assert kwargs["max_tokens"] == 4000


def test_the_request_is_deterministic_and_copies_its_inputs() -> None:
    first, second = request(), request()
    assert json.dumps(first.kwargs()) == json.dumps(second.kwargs())
    first.messages[0]["content"] = "changed"
    assert QUESTION[0]["content"] == "What are Brookvale's odds?"


# --- the echo rule -----------------------------------------------------------------------------

FALLBACK = {
    "type": "fallback",
    "from": {"model": "claude-opus-5-5"},
    "to": {"model": "claude-opus-5"},
    "trigger": {"type": "refusal"},
}
THINKING = {"type": "thinking", "thinking": "", "signature": "sig"}


def test_without_a_fallback_the_content_is_echoed_unchanged() -> None:
    content: list[dict[str, Any]] = [
        THINKING,
        {"type": "text", "text": "a"},
        {"type": "tool_use", "id": "t", "name": "x", "input": {}},
    ]
    assert model.echo_content(content) == content


def test_after_a_fallback_text_before_it_the_block_in_place_and_everything_after_are_kept() -> None:
    content: list[dict[str, Any]] = [
        {"type": "text", "text": "Brookvale's chances are"},
        THINKING,
        {"type": "tool_use", "id": "t1", "name": "run_sql", "input": {"sql": "SELECT 1"}},
        FALLBACK,
        {"type": "thinking", "thinking": "", "signature": "sig2"},
        {"type": "tool_use", "id": "t2", "name": "describe_view", "input": {"view": "TEAMS"}},
    ]
    echoed = model.echo_content(content)
    assert [block["type"] for block in echoed] == ["text", "fallback", "thinking", "tool_use"]
    assert echoed[0]["text"] == "Brookvale's chances are"
    assert echoed[1] == FALLBACK
    assert echoed[3]["id"] == "t2"
    assert [call["id"] for call in model.tool_calls(echoed)] == ["t2"]
    assert model.fallback_blocks(content) == [FALLBACK]


def test_with_two_fallbacks_both_stay_in_place_and_everything_before_the_last_is_judged() -> None:
    content: list[dict[str, Any]] = [
        {"type": "text", "text": "one"},
        FALLBACK,
        {"type": "text", "text": "two"},
        THINKING,
        {"type": "redacted_thinking", "data": "x"},
        FALLBACK,
        {"type": "tool_use", "id": "t3", "name": "run_sql", "input": {}},
    ]
    echoed = model.echo_content(content)
    assert [block["type"] for block in echoed] == [
        "text",
        "fallback",
        "text",
        "fallback",
        "tool_use",
    ]


def test_a_fallback_before_any_output_keeps_every_block() -> None:
    content: list[dict[str, Any]] = [FALLBACK, THINKING, {"type": "text", "text": "ok"}]
    assert model.echo_content(content) == content


def test_an_unknown_internal_block_before_the_last_fallback_is_dropped() -> None:
    content: list[dict[str, Any]] = [
        {"type": "server_tool_use", "id": "s"},
        FALLBACK,
        {"type": "text", "text": "x"},
    ]
    assert model.echo_content(content) == [FALLBACK, {"type": "text", "text": "x"}]


def test_a_blank_text_block_is_not_echoed() -> None:
    content: list[dict[str, Any]] = [
        THINKING,
        {"type": "text", "text": "\n\n"},
        {"type": "text", "text": ""},
        {"type": "tool_use", "id": "t", "name": "x", "input": {}},
    ]
    assert [block["type"] for block in model.echo_content(content)] == ["thinking", "tool_use"]


def test_text_of_joins_text_blocks() -> None:
    content: list[dict[str, Any]] = [
        {"type": "text", "text": "a"},
        THINKING,
        {"type": "text", "text": "b"},
    ]
    assert model.text_of(content) == "ab"


# --- the SDK over an in-process transport -------------------------------------------------------


def test_one_question_goes_to_the_pinned_host_with_the_key_in_one_header(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    markers = {
        "ANTHROPIC_BASE_URL": "https://evil.example/marker-base-url",
        "ANTHROPIC_AUTH_TOKEN": "marker-auth-token-123",
        "ANTHROPIC_PROFILE": "marker-profile",
        "ANTHROPIC_FEDERATION_RULE_ID": "marker-rule-id",
        "ANTHROPIC_ORGANIZATION_ID": "marker-org-id",
        "ANTHROPIC_SERVICE_ACCOUNT_ID": "marker-service-account",
        "ANTHROPIC_IDENTITY_TOKEN": "marker-identity-token",
        "ANTHROPIC_LOG": "debug",
    }
    for name, value in markers.items():
        monkeypatch.setenv(name, value)
    logging.getLogger("anthropic").setLevel(logging.DEBUG)
    recorder = fakes.Recorder([fakes.sse_response(fakes.text_events("41.2 %"))])
    with caplog.at_level(logging.DEBUG):
        response = respond(recorder, monkeypatch)
    (sent,) = recorder.requests
    assert sent.url.host == "api.anthropic.com"
    assert sent.url.scheme == "https"
    assert sent.headers["x-api-key"] == fakes.MARKER_KEY
    assert "authorization" not in sent.headers
    everything = str(sent.headers) + sent.content.decode()
    assert all(value not in everything for value in markers.values())
    assert everything.count(fakes.MARKER_KEY) == 1
    body = recorder.bodies()[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"] == {"effort": "medium"}
    assert body["fallbacks"] == "default"
    assert body["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["cache_control"] == {"type": "ephemeral"}
    assert all(item["eager_input_streaming"] is True for item in body["tools"])
    assert set(sent.headers["anthropic-beta"].split(",")) >= {
        model.BETA_FALLBACK,
        model.BETA_BINDING,
    }
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert fakes.MARKER_KEY not in logged
    assert "x-api-key" not in logged.lower()
    assert "What are Brookvale" not in logged
    assert response.stop_reason == "end_turn"


def test_an_authorization_header_is_stopped_before_the_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "Authorization: Bearer marker-bearer")
    recorder = fakes.Recorder([fakes.sse_response(fakes.text_events("never sent"))])
    with pytest.raises(model.ConflictingCredentials) as caught:
        respond(recorder, monkeypatch)
    assert caught.value.code == "conflicting_credentials"
    assert "marker-bearer" not in str(caught.value)
    assert recorder.requests == []


def test_the_guard_stops_another_host() -> None:
    other = httpx2.Request("POST", "https://example.org/v1/messages", headers={"x-api-key": "k"})
    with pytest.raises(model.ConflictingCredentials):
        model.request_guard(other)
    good = httpx2.Request("POST", f"{model.BASE_URL}/v1/messages", headers={"x-api-key": "k"})
    model.request_guard(good)


def test_the_client_is_built_only_from_the_explicit_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-the-environment")
    recorder = fakes.Recorder([fakes.sse_response(fakes.text_events("ok"))])
    respond(recorder, monkeypatch)
    assert recorder.requests[0].headers["x-api-key"] == fakes.MARKER_KEY


def test_logger_levels_are_held_at_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    logging.getLogger("anthropic").setLevel(logging.DEBUG)
    logging.getLogger("httpx2").setLevel(logging.DEBUG)
    install(monkeypatch, fakes.Recorder())
    model.AnthropicChatModel(fakes.MARKER_KEY)
    assert logging.getLogger("anthropic").level == logging.WARNING
    assert logging.getLogger("httpx2").level == logging.WARNING


def test_text_streams_to_the_callback_and_the_response_carries_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = fakes.text_events(
        "41.2 %", pieces=["41", ".2 %"], usage={"output_tokens": 77}, input_transformations=[]
    )
    pieces: list[str] = []
    response = respond(
        fakes.Recorder([fakes.sse_response(events)]), monkeypatch, on_text=pieces.append
    )
    assert pieces == ["41", ".2 %"]
    assert model.text_of(response.content) == "41.2 %"
    assert response.stop_reason == "end_turn"
    assert response.usage["output_tokens"] == 77
    assert response.usage["input_tokens"] == 100
    assert response.model == "claude-opus-5-5"
    assert response.usage_complete is True
    assert response.invalid_tool_ids == frozenset()


def test_a_tool_call_is_returned_with_its_parsed_input(monkeypatch: pytest.MonkeyPatch) -> None:
    events = fakes.tool_use_events("run_sql", {"sql": "SELECT 1", "purpose": "x"})
    response = respond(fakes.Recorder([fakes.sse_response(events)]), monkeypatch)
    assert response.stop_reason == "tool_use"
    (call,) = model.tool_calls(response.content)
    assert call["input"] == {"sql": "SELECT 1", "purpose": "x"}
    assert call["name"] == "run_sql"


def test_content_can_be_sent_back_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    events = fakes.tool_use_events("run_sql", {"sql": "SELECT 1", "purpose": "x"})
    first = respond(fakes.Recorder([fakes.sse_response(events)]), monkeypatch)
    echoed = model.echo_content(first.content)
    assert echoed == list(first.content)
    assert json.loads(json.dumps(echoed)) == echoed


def test_input_transformations_are_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = {
        "type": "thinking_dropped",
        "path": "messages.1.content.0",
        "reason": "prefix_binding_mismatch",
    }
    events = fakes.text_events("ok", input_transformations=[entry])
    response = respond(fakes.Recorder([fakes.sse_response(events)]), monkeypatch)
    assert response.input_transformations == (entry,)


def test_a_refusal_before_any_output_has_empty_content(monkeypatch: pytest.MonkeyPatch) -> None:
    response = respond(fakes.Recorder([fakes.sse_response(fakes.refusal_events())]), monkeypatch)
    assert response.stop_reason == "refusal"
    assert response.content == ()
    assert response.stop_details is not None
    assert response.stop_details["category"] == "cyber"


def test_a_refusal_after_partial_output_keeps_the_partial_for_the_caller_to_discard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pieces: list[str] = []
    events = fakes.refusal_events(partial_text="Brookvale's chances are")
    response = respond(
        fakes.Recorder([fakes.sse_response(events)]), monkeypatch, on_text=pieces.append
    )
    assert response.stop_reason == "refusal"
    assert pieces == ["Brookvale's chances are"]
    assert model.text_of(response.content) == "Brookvale's chances are"


def test_a_cut_off_response_is_surfaced_with_its_stop_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = fakes.text_events("The answer is", stop_reason="max_tokens")
    response = respond(fakes.Recorder([fakes.sse_response(events)]), monkeypatch)
    assert response.stop_reason == "max_tokens"


def test_an_unknown_stop_reason_is_surfaced_as_written(monkeypatch: pytest.MonkeyPatch) -> None:
    events = fakes.text_events("x", stop_reason="something_new")
    response = respond(fakes.Recorder([fakes.sse_response(events)]), monkeypatch)
    assert response.stop_reason == "something_new"


def test_a_fallback_block_arrives_as_content_and_the_model_that_served(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": fakes.message_skeleton(model="claude-opus-5"),
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "fallback",
                "from": {"model": "claude-opus-5-5"},
                "to": {"model": "claude-opus-5"},
                "trigger": {"type": "refusal"},
            },
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "ok"}},
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 4},
        },
        {"type": "message_stop"},
    ]
    response = respond(
        fakes.Recorder([fakes.sse_response([fakes.sse_event(event) for event in events])]),
        monkeypatch,
    )
    assert response.model == "claude-opus-5"
    (marker,) = model.fallback_blocks(response.content)
    assert marker["from"] == {"model": "claude-opus-5-5"}
    assert marker["to"] == {"model": "claude-opus-5"}
    assert model.echo_content(response.content) == [marker, {"type": "text", "text": "ok"}]


@pytest.mark.parametrize("raw", ['{"sql": "SELECT 1", ]', "not json at all"])
def test_tool_input_that_is_not_json_becomes_a_held_invalid_call(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    response = respond(
        fakes.Recorder([fakes.sse_response(fakes.invalid_tool_events(raw))]), monkeypatch
    )
    assert response.stop_reason == "tool_use"
    assert response.invalid_tool_ids == frozenset({"toolu_bad"})
    assert response.usage_complete is False
    assert [block["type"] for block in response.content] == ["text", "tool_use"]


def test_an_interrupted_stream_without_a_held_call_asks_again() -> None:
    from types import SimpleNamespace

    snapshot = SimpleNamespace(content=[SimpleNamespace(type="text")])
    stream = SimpleNamespace(current_message_snapshot=snapshot)
    client = model.AnthropicChatModel.__new__(model.AnthropicChatModel)
    with pytest.raises(model.UnparseableResponse):
        client._interrupted(stream, model.CancelToken())


# --- the history guard -------------------------------------------------------------------------


def test_a_request_the_api_would_refuse_is_not_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = fakes.Recorder([fakes.sse_response(fakes.text_events("never"))])
    install(monkeypatch, recorder)
    client = model.AnthropicChatModel(fakes.MARKER_KEY)
    settings = model.RequestSettings()
    bad = model.build_request(
        settings,
        SYSTEM_TEXT,
        [*QUESTION, {"role": "assistant", "content": [{"type": "text", "text": "prefill"}]}],
    )
    with pytest.raises(model.HistoryError) as caught:
        client.respond(bad, lambda _text: None, model.CancelToken())
    assert caught.value.code == "internal_history_error"
    assert "rule 6" in caught.value.message
    assert recorder.requests == []


# --- errors ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "error_type", "code"),
    [
        (401, "authentication_error", "auth"),
        (403, "permission_error", "permission"),
        (404, "not_found_error", "not_found"),
        (400, "invalid_request_error", "bad_request"),
        (429, "rate_limit_error", "rate_limit"),
        (500, "api_error", "server"),
        (529, "overloaded_error", "server"),
    ],
)
def test_api_errors_become_short_messages_without_http_detail(
    monkeypatch: pytest.MonkeyPatch, status: int, error_type: str, code: str
) -> None:
    secret_body = "SECRET-BODY-TEXT: your key sk-ant-xxx is bad"
    recorder = fakes.Recorder([fakes.error_response(status, error_type, secret_body)])
    with pytest.raises(model.ChatModelError) as caught:
        respond(recorder, monkeypatch)
    error = caught.value
    assert error.code == code
    text = error.message
    assert secret_body not in text
    assert str(status) not in text
    for forbidden in ("HTTP", "header", "request-id", "Traceback", "sk-ant"):
        assert forbidden not in text
    assert error.request_id == "req_test"


def test_a_server_error_is_retried_once_then_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = fakes.Recorder([fakes.error_response(500)])
    with pytest.raises(model.ChatModelError) as caught:
        respond(recorder, monkeypatch)
    assert caught.value.code == "server"
    assert len(recorder.requests) == 2


def test_a_connection_failure_is_a_short_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused at 10.0.0.1:443")

    recorder = fakes.Recorder([refuse])
    with pytest.raises(model.ChatModelError) as caught:
        respond(recorder, monkeypatch)
    assert caught.value.code == "connection"
    assert "10.0.0.1" not in caught.value.message


def test_a_timeout_is_a_short_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(_request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("read timed out")

    with pytest.raises(model.ChatModelError) as caught:
        respond(fakes.Recorder([slow]), monkeypatch)
    assert caught.value.code == "timeout"


def test_a_stream_that_ends_early_is_an_error_not_a_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    short = fakes.text_events("partial")[:3]
    with pytest.raises(model.ChatModelError) as caught:
        respond(fakes.Recorder([fakes.sse_response(short)]), monkeypatch)
    assert caught.value.code == "stream_ended"


# --- cancel ------------------------------------------------------------------------------------


def test_a_cancel_before_the_call_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = fakes.Recorder([fakes.sse_response(fakes.text_events("never"))])
    cancel = model.CancelToken()
    cancel.cancel()
    with pytest.raises(model.ModelCancelled):
        respond(recorder, monkeypatch, cancel=cancel)
    assert recorder.requests == []


def test_a_cancel_during_the_stream_closes_it_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    held = fakes.HeldStream(fakes.text_events("partial")[:3])
    recorder = fakes.Recorder([fakes.held_response(held)])
    cancel = model.CancelToken()
    pieces: list[str] = []
    outcome: list[str] = []

    def run() -> None:
        try:
            respond(recorder, monkeypatch, on_text=pieces.append, cancel=cancel)
            outcome.append("returned")
        except model.ModelCancelled:
            outcome.append("cancelled")
        except BaseException as error:
            outcome.append(type(error).__name__)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert held.reached_hold.wait(10)
    cancel.cancel()
    thread.join(5)
    assert not thread.is_alive()
    assert outcome == ["cancelled"]
    assert held.closed.is_set()
    assert pieces == ["partial"]


def test_cancel_callbacks_run_once_and_late_ones_run_at_once() -> None:
    cancel = model.CancelToken()
    calls: list[str] = []
    remove = cancel.on_cancel(lambda: calls.append("a"))
    cancel.on_cancel(lambda: calls.append("b"))
    remove()
    cancel.cancel()
    cancel.cancel()
    cancel.on_cancel(lambda: calls.append("late"))
    assert calls == ["b", "late"]
    assert cancel.cancelled is True


def test_a_failing_cancel_callback_does_not_stop_the_others() -> None:
    cancel = model.CancelToken()
    calls: list[str] = []

    def broken() -> None:
        raise RuntimeError("boom")

    cancel.on_cancel(broken)
    cancel.on_cancel(lambda: calls.append("ran"))
    cancel.cancel()
    assert calls == ["ran"]


def test_wait_returns_when_cancelled() -> None:
    cancel = model.CancelToken()
    assert cancel.wait(0.01) is False
    cancel.cancel()
    assert cancel.wait(0.01) is True


def test_the_suite_refuses_real_network_access() -> None:
    client = model.build_client(fakes.MARKER_KEY)
    with pytest.raises(anthropic.APIConnectionError):
        client.beta.messages.create(
            model="claude-opus-5-5", max_tokens=5, messages=[{"role": "user", "content": "x"}]
        )


def test_input_transformations_of_the_final_message_delta_reach_the_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = {
        "type": "thinking_dropped",
        "path": "messages.1.content.0",
        "reason": "model_binding_mismatch",
    }
    events: list[dict[str, Any]] = [
        {"type": "message_start", "message": fakes.message_skeleton(input_transformations=[])},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 4},
            "input_transformations": [entry],
        },
        {"type": "message_stop"},
    ]
    recorder = fakes.Recorder([fakes.sse_response([fakes.sse_event(e) for e in events])])
    response = respond(recorder, monkeypatch)
    assert response.input_transformations == (entry,)
