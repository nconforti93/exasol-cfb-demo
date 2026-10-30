"""Fakes for the Anthropic SDK layer: server-sent events for a streamed message, an in-process
transport that records each request, and a stream that stays open until it is closed.

Nothing here opens a socket. The transport is `httpx2.MockTransport`, the client library of the
SDK's 1.x line (`anthropic` runs on `httpx2`, not on `httpx`)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx2

MARKER_KEY = "sk-ant-test-CFBCHATMARKER-0000"
MODEL = "claude-opus-5-5"


def message_skeleton(
    *,
    model: str = MODEL,
    usage: dict[str, Any] | None = None,
    input_transformations: Sequence[dict[str, Any]] | None = (),
    content: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "msg_test_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": list(content),
        "stop_reason": None,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 100, "output_tokens": 1},
    }
    if input_transformations is not None:
        body["input_transformations"] = list(input_transformations)
    return body


def sse_event(payload: dict[str, Any]) -> bytes:
    return f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n".encode()


def text_events(
    text: str,
    *,
    stop_reason: str = "end_turn",
    usage: dict[str, Any] | None = None,
    input_transformations: Sequence[dict[str, Any]] | None = (),
    pieces: Sequence[str] | None = None,
) -> list[bytes]:
    """The events of a message with one text block; `pieces` splits the text into deltas."""
    chunks = list(pieces) if pieces is not None else [text]
    final_usage = usage or {"output_tokens": 7}
    events: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": message_skeleton(input_transformations=input_transformations),
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
    ]
    events += [
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": chunk},
        }
        for chunk in chunks
    ]
    events += [
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": final_usage,
        },
        {"type": "message_stop"},
    ]
    return [sse_event(event) for event in events]


def tool_use_events(
    name: str,
    tool_input: dict[str, Any],
    *,
    tool_id: str = "toolu_1",
    usage: dict[str, Any] | None = None,
) -> list[bytes]:
    """The events of a message with one `tool_use` block whose input streams as partial JSON."""
    raw = json.dumps(tool_input)
    half = len(raw) // 2
    events: list[dict[str, Any]] = [
        {"type": "message_start", "message": message_skeleton()},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": tool_id, "name": name, "input": {}},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": raw[:half]},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": raw[half:]},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": usage or {"output_tokens": 9},
        },
        {"type": "message_stop"},
    ]
    return [sse_event(event) for event in events]


class HeldStream(httpx2.SyncByteStream):
    """Yields its first chunks, then waits until `close()` is called (from any thread)."""

    def __init__(self, chunks: Sequence[bytes], *, wait_seconds: float = 30.0) -> None:
        self._chunks = list(chunks)
        self._wait_seconds = wait_seconds
        self.closed = threading.Event()
        self.reached_hold = threading.Event()

    def __iter__(self) -> Iterator[bytes]:
        yield from self._chunks
        self.reached_hold.set()
        self.closed.wait(self._wait_seconds)

    def close(self) -> None:
        self.closed.set()


@dataclass
class Recorder:
    """An in-process transport: records every request, answers from a queue of responders."""

    responders: list[Callable[[httpx2.Request], httpx2.Response]] = field(default_factory=list)
    requests: list[httpx2.Request] = field(default_factory=list)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        request.read()
        self.requests.append(request)
        if not self.responders:
            raise AssertionError("the recorder has no response left for this request")
        responder = self.responders.pop(0) if len(self.responders) > 1 else self.responders[0]
        return responder(request)

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


def sse_response(events: Sequence[bytes]) -> Callable[[httpx2.Request], httpx2.Response]:
    def respond(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=b"".join(events)
        )

    return respond


def held_response(stream: HeldStream) -> Callable[[httpx2.Request], httpx2.Response]:
    def respond(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    return respond


def error_response(
    status: int, error_type: str = "api_error", message: str = "boom"
) -> Callable[[httpx2.Request], httpx2.Response]:
    def respond(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status,
            headers={"request-id": "req_test"},
            json={"type": "error", "error": {"type": error_type, "message": message}},
        )

    return respond


def refusal_events(
    *,
    partial_text: str | None = None,
    category: str | None = "cyber",
    usage: dict[str, Any] | None = None,
) -> list[bytes]:
    """A refusal: before any output (no content) or after `partial_text` was streamed."""
    events: list[dict[str, Any]] = [{"type": "message_start", "message": message_skeleton()}]
    if partial_text is not None:
        events += [
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": partial_text},
            },
            {"type": "content_block_stop", "index": 0},
        ]
    events += [
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": "refusal",
                "stop_sequence": None,
                "stop_details": {"type": "refusal", "category": category, "explanation": None},
            },
            "usage": usage or {"output_tokens": 3},
        },
        {"type": "message_stop"},
    ]
    return [sse_event(event) for event in events]


def invalid_tool_events(raw: str) -> list[bytes]:
    """A text block and then a `tool_use` block whose streamed input is `raw`."""
    events: list[dict[str, Any]] = [
        {"type": "message_start", "message": message_skeleton()},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Let me look."},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_bad",
                "name": "run_sql",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": raw},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 9},
        },
        {"type": "message_stop"},
    ]
    return [sse_event(event) for event in events]
