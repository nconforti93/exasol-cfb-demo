"""Gate A4 of task 1.1: the real Anthropic SDK (1.x, on `httpx2`) runs over an in-process transport.

No network: the transport is `httpx2.MockTransport`. The gate shows that

- `DefaultHttpxClient(transport=..., event_hooks=...)` is accepted by `anthropic.Anthropic` and the
  request hook runs before the transport;
- `client.beta.messages.stream(...)` sends every field the chat's request needs without
  `extra_body`: `fallbacks`, `output_config.effort`, `eager_input_streaming`, a `role: "system"`
  message, `thinking` with `block_binding`, `cache_control` on a system block and at the top level,
  and `betas`;
- the stream exposes `input_transformations` from `message_start`, and can be closed from another
  thread."""

from __future__ import annotations

import threading
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic import DefaultHttpxClient

from tests.unit import chat_sdk_fakes as fakes

BASE_URL = "https://api.anthropic.com"
TOOL: dict[str, Any] = {
    "name": "ping",
    "description": "Ping.",
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    "eager_input_streaming": True,
}
TRANSFORMATION = {
    "type": "thinking_dropped",
    "path": "messages.1.content.0",
    "reason": "prefix_binding_mismatch",
}


def make_client(recorder: fakes.Recorder) -> anthropic.Anthropic:
    http_client = DefaultHttpxClient(transport=recorder.transport())
    return anthropic.Anthropic(
        api_key=fakes.MARKER_KEY,
        base_url=BASE_URL,
        max_retries=1,
        timeout=anthropic.Timeout(60.0, connect=5.0),
        http_client=http_client,
    )


def chat_stream(client: anthropic.Anthropic) -> Any:
    return client.beta.messages.stream(
        model=fakes.MODEL,
        max_tokens=8000,
        system=[
            {
                "type": "text",
                "text": "You are the analyst.",
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {"role": "user", "content": "What are Brookvale's odds?"},
            {"role": "system", "content": '{"app_state": {"page": "odds"}}'},
        ],
        tools=[TOOL],  # type: ignore[list-item]
        thinking={"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "error"}},
        output_config={"effort": "medium"},
        tool_choice={"type": "auto"},
        cache_control={"type": "ephemeral"},
        fallbacks="default",
        betas=["server-side-fallback-2026-07-01", "thinking-binding-controls-2026-08-01"],
    )


def test_transport_is_accepted_and_the_hook_runs_before_it() -> None:
    order: list[str] = []
    recorder = fakes.Recorder([fakes.sse_response(fakes.text_events("ok"))])

    def transport_probe(request: httpx2.Request) -> httpx2.Response:
        order.append("transport")
        return recorder(request)

    client = anthropic.Anthropic(
        api_key=fakes.MARKER_KEY,
        base_url=BASE_URL,
        max_retries=0,
        http_client=DefaultHttpxClient(
            transport=httpx2.MockTransport(transport_probe),
            event_hooks={"request": [lambda _request: order.append("hook")]},
        ),
    )
    with chat_stream(client) as stream:
        message = stream.get_final_message()
    assert order == ["hook", "transport"]
    assert message.stop_reason == "end_turn"


def test_stream_sends_every_chat_field_without_extra_body() -> None:
    recorder = fakes.Recorder([fakes.sse_response(fakes.text_events("ok"))])
    client = make_client(recorder)
    with chat_stream(client) as stream:
        stream.get_final_message()
    request = recorder.requests[0]
    body = recorder.bodies()[0]
    assert str(request.url) == f"{BASE_URL}/v1/messages?beta=true"
    assert request.headers["x-api-key"] == fakes.MARKER_KEY
    assert "authorization" not in request.headers
    assert set(request.headers["anthropic-beta"].split(",")) >= {
        "server-side-fallback-2026-07-01",
        "thinking-binding-controls-2026-08-01",
    }
    assert body["model"] == fakes.MODEL
    assert body["stream"] is True
    assert body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "medium"}
    assert body["tools"][0]["eager_input_streaming"] is True
    assert body["messages"][1] == {"role": "system", "content": '{"app_state": {"page": "odds"}}'}
    assert body["thinking"] == {
        "type": "adaptive",
        "block_binding": {"prefix_mismatch_behavior": "error"},
    }
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["tool_choice"] == {"type": "auto"}


def test_stream_exposes_input_transformations_from_message_start() -> None:
    events = fakes.text_events("ok", input_transformations=[TRANSFORMATION])
    recorder = fakes.Recorder([fakes.sse_response(events)])
    with chat_stream(make_client(recorder)) as stream:
        message = stream.get_final_message()
    transformations = message.input_transformations
    assert transformations is not None
    assert [(item.type, item.reason, item.path) for item in transformations] == [
        ("thinking_dropped", "prefix_binding_mismatch", "messages.1.content.0")
    ]


def test_stream_can_be_closed_from_another_thread() -> None:
    held = fakes.HeldStream(fakes.text_events("partial")[:3])
    recorder = fakes.Recorder([fakes.held_response(held)])
    outcome: list[str] = []

    def reader() -> None:
        try:
            with chat_stream(make_client(recorder)) as stream:
                outcome.append("entered")
                for _ in stream.text_stream:
                    pass
                final = stream.get_final_message()
                outcome.append(f"final:{final.stop_reason}:{final.content[0].text}")
        except Exception as error:
            outcome.append(type(error).__name__)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    assert held.reached_hold.wait(10)
    held.close()
    thread.join(10)
    assert not thread.is_alive()
    assert outcome[0] == "entered"
    assert outcome[-1] == "final:None:partial"


def test_a_client_from_the_httpx_package_is_rejected() -> None:
    import httpx

    with pytest.raises((TypeError, ValueError, AttributeError)):
        client = anthropic.Anthropic(
            api_key=fakes.MARKER_KEY,
            base_url=BASE_URL,
            http_client=httpx.Client(),  # type: ignore[arg-type]
        )
        with chat_stream(client) as stream:
            stream.get_final_message()
