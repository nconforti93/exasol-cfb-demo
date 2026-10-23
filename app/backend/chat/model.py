"""The model seam of the chat: the request the loop builds, the response it gets back, and the
Anthropic-backed implementation of `ChatModel.respond(request, on_text, cancel)`.

`build_request` is pure and holds every field of the Messages request (decision [4]); the scripted
model (`chat/scripted.py`) implements the same protocol, so the loop's caps, history and tools are
tested without the SDK. `AnthropicChatModel` is the only code that talks to the API: the key goes
in one header to one host, the client is built only with an explicit key, and every error is mapped
to a short message that carries no HTTP detail.
"""

from __future__ import annotations

import copy
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

import anthropic
import httpx2

from app.backend.chat import conformance
from app.backend.chat.tools import tool_definitions

DEFAULT_MODEL = "claude-opus-5-5"
BASE_URL = "https://api.anthropic.com"
API_HOST = "api.anthropic.com"
BETA_BINDING = "thinking-binding-controls-2026-08-01"
BETA_FALLBACK = "server-side-fallback-2026-07-01"
CONNECT_TIMEOUT = 5.0

Effort = Literal["low", "medium", "high", "xhigh", "max"]
PrefixMismatch = Literal["drop_block", "error"]


@dataclass(frozen=True)
class RequestSettings:
    """The settings that shape a request (`CHAT_MODEL`, `CHAT_EFFORT`, `CHAT_MAX_TOKENS`,
    `CHAT_FALLBACKS`, `CHAT_PREFIX_MISMATCH`)."""

    model: str = DEFAULT_MODEL
    effort: Effort = "medium"
    max_tokens: int = 8000
    fallbacks: bool = True
    prefix_mismatch: PrefixMismatch = "drop_block"


@dataclass(frozen=True)
class ModelRequest:
    """One Messages request, as plain JSON-able values in the order the SDK sends them."""

    model: str
    max_tokens: int
    system: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    thinking: dict[str, Any]
    output_config: dict[str, Any]
    tool_choice: dict[str, Any]
    cache_control: dict[str, Any]
    betas: list[str]
    fallbacks: str | None

    def kwargs(self) -> dict[str, Any]:
        """The keyword arguments of `client.beta.messages.stream(...)`."""
        arguments: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system,
            "messages": self.messages,
            "tools": self.tools,
            "thinking": self.thinking,
            "output_config": self.output_config,
            "tool_choice": self.tool_choice,
            "cache_control": self.cache_control,
            "betas": self.betas,
        }
        if self.fallbacks is not None:
            arguments["fallbacks"] = self.fallbacks
        return arguments

    def conformance_view(self) -> dict[str, Any]:
        """The parts the history rules look at."""
        return {"system": self.system, "tools": self.tools, "messages": self.messages}


def build_request(
    settings: RequestSettings, system_text: str, messages: Sequence[Mapping[str, Any]]
) -> ModelRequest:
    """The request for `messages` under the frozen system prompt (pure; copies its inputs)."""
    betas = [BETA_BINDING]
    if settings.fallbacks:
        betas.append(BETA_FALLBACK)
    return ModelRequest(
        model=settings.model,
        max_tokens=settings.max_tokens,
        system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
        messages=copy.deepcopy([dict(message) for message in messages]),
        tools=tool_definitions(),
        thinking={
            "type": "adaptive",
            "block_binding": {"prefix_mismatch_behavior": settings.prefix_mismatch},
        },
        output_config={"effort": settings.effort},
        tool_choice={"type": "auto"},
        cache_control={"type": "ephemeral"},
        betas=betas,
        fallbacks="default" if settings.fallbacks else None,
    )


# --- responses -------------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelResponse:
    """What one call returned: the content blocks as the API wrote them (so they can be echoed back
    unchanged), why it stopped, which model served it, its usage and the dropped thinking blocks."""

    content: tuple[dict[str, Any], ...]
    stop_reason: str | None
    model: str
    usage: Mapping[str, Any]
    stop_details: Mapping[str, Any] | None = None
    input_transformations: tuple[dict[str, Any], ...] = ()
    request_id: str | None = None
    usage_complete: bool = True
    invalid_tool_ids: frozenset[str] = frozenset()


def echo_content(content: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The blocks of a response in the form that enters the history.

    After a fallback the blocks before the last `fallback` block are left out, except text blocks:
    `thinking`, `redacted_thinking`, `tool_use` and any other model-internal block there belong to
    the model that declined. The `fallback` blocks themselves are markers and are left out too."""
    blocks = [dict(block) for block in content]
    last = max((i for i, block in enumerate(blocks) if block.get("type") == "fallback"), default=-1)
    if last < 0:
        return blocks
    before = [block for block in blocks[:last] if block.get("type") == "text"]
    after = [block for block in blocks[last + 1 :] if block.get("type") != "fallback"]
    return before + after


def fallback_blocks(content: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The `fallback` blocks of a response, in order."""
    return [dict(block) for block in content if block.get("type") == "fallback"]


def tool_calls(content: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The `tool_use` blocks of (echoed) content, in order."""
    return [dict(block) for block in content if block.get("type") == "tool_use"]


def text_of(content: Sequence[Mapping[str, Any]]) -> str:
    """The text blocks of content, joined."""
    return "".join(str(block.get("text", "")) for block in content if block.get("type") == "text")


# --- cancel and errors -----------------------------------------------------------------------


class CancelToken:
    """A flag with callbacks: set once, from any thread, to stop what the model call is doing."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            _quietly(callback)

    def on_cancel(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Run `callback` on cancel (at once if already cancelled); returns a way to remove it."""
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return lambda: self._remove(callback)
        _quietly(callback)
        return lambda: None

    def _remove(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def wait(self, seconds: float) -> bool:
        return self._event.wait(seconds)


def _quietly(callback: Callable[[], None]) -> None:
    try:
        callback()
    except Exception:
        logging.getLogger(__name__).debug("a cancel callback failed", exc_info=True)


class ModelCancelled(Exception):
    """The turn was cancelled while the call was running."""


class ChatModelError(Exception):
    """A failed model call: a short `message` for the presenter, a `code` for the logs and the
    API's request id when there is one. It never carries HTTP detail."""

    def __init__(self, code: str, message: str, request_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id


class HistoryError(ChatModelError):
    """The request would break the API's rules for the history; nothing was sent."""

    def __init__(self, violations: str) -> None:
        super().__init__("internal_history_error", f"internal history error: {violations}")


class UnparseableResponse(ChatModelError):
    """The response held tool input that is not JSON and no tool call to answer: ask again."""

    def __init__(self, request_id: str | None = None) -> None:
        super().__init__(
            "unparseable_response", "the model's tool call was not valid JSON", request_id
        )


class ConflictingCredentials(ChatModelError):
    """A request was stopped by the guard: another host, or an `Authorization` header."""

    def __init__(self, detail: str) -> None:
        super().__init__("conflicting_credentials", f"conflicting credentials: {detail}")


class ChatModel(Protocol):
    """The seam below the loop: one streamed model call."""

    def respond(
        self, request: ModelRequest, on_text: Callable[[str], None], cancel: CancelToken
    ) -> ModelResponse: ...


# --- the Anthropic client --------------------------------------------------------------------


def request_guard(request: httpx2.Request) -> None:
    """Stop, before the network, a request to another host or one that carries `Authorization`."""
    host = request.url.host
    if host != API_HOST:
        raise ConflictingCredentials(f"a request to {host!r} instead of {API_HOST}")
    if "authorization" in request.headers:
        raise ConflictingCredentials("an Authorization header on a request")


def _default_http_client_factory() -> anthropic.DefaultHttpxClient:
    return anthropic.DefaultHttpxClient(event_hooks={"request": [request_guard]})


# The seam the test suite replaces so that no test can reach the network (tests/conftest.py).
http_client_factory: Callable[[], anthropic.DefaultHttpxClient] = _default_http_client_factory


def quiet_loggers() -> None:
    """Hold the SDK's and the HTTP libraries' loggers at WARNING, whatever `ANTHROPIC_LOG` says, so
    that no request header or body reaches the server log."""
    for name in ("anthropic", "httpx2", "httpcore2", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


quiet_loggers()


def build_client(
    api_key: str, *, timeout: float = 60.0, max_retries: int = 1
) -> anthropic.Anthropic:
    """The pinned client: the explicit key (never the credential chain), the API host, one retry,
    a 60 s read timeout and the request guard."""
    quiet_loggers()
    return anthropic.Anthropic(
        api_key=api_key,
        base_url=BASE_URL,
        max_retries=max_retries,
        timeout=anthropic.Timeout(timeout, connect=CONNECT_TIMEOUT),
        http_client=http_client_factory(),
    )


def map_api_error(error: Exception) -> ChatModelError:
    """A short message for an SDK error: no status line, header or body."""
    request_id = getattr(error, "request_id", None)
    guard = _guard_cause(error)
    if guard is not None:
        return guard
    mapping: tuple[tuple[type[Exception], str, str], ...] = (
        (anthropic.AuthenticationError, "auth", "the Anthropic API key was refused"),
        (anthropic.PermissionDeniedError, "permission", "the API key may not use this model"),
        (anthropic.NotFoundError, "not_found", "the model was not found"),
        (anthropic.RateLimitError, "rate_limit", "the Anthropic API is rate limiting requests"),
        (anthropic.BadRequestError, "bad_request", "the Anthropic API refused the request"),
        (anthropic.APITimeoutError, "timeout", "the Anthropic API did not answer in time"),
        (anthropic.APIConnectionError, "connection", "the Anthropic API cannot be reached"),
        (anthropic.APIStatusError, "server", "the Anthropic API failed"),
    )
    for kind, code, message in mapping:
        if isinstance(error, kind):
            return ChatModelError(code, message, request_id)
    return ChatModelError("model_error", "the model call failed", request_id)


def _guard_cause(error: BaseException) -> ConflictingCredentials | None:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, ConflictingCredentials):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def response_from_message(
    message: Any, *, usage_complete: bool = True, invalid: frozenset[str] = frozenset()
) -> ModelResponse:
    """A `ModelResponse` from an SDK message (final, or the snapshot of an interrupted stream)."""
    dump = message.model_dump(mode="json", by_alias=True, exclude_none=True)
    transformations = tuple(dict(entry) for entry in dump.get("input_transformations") or ())
    return ModelResponse(
        content=tuple(dump.get("content") or ()),
        stop_reason=dump.get("stop_reason"),
        model=str(dump.get("model") or ""),
        usage=dict(dump.get("usage") or {}),
        stop_details=dump.get("stop_details"),
        input_transformations=transformations,
        request_id=getattr(message, "_request_id", None),
        usage_complete=usage_complete,
        invalid_tool_ids=invalid,
    )


@dataclass
class AnthropicChatModel:
    """`ChatModel` over the Anthropic SDK. Built only with an explicit key."""

    api_key: str = field(repr=False)
    timeout: float = 60.0
    max_retries: int = 1
    _client: anthropic.Anthropic | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = build_client(
            self.api_key, timeout=self.timeout, max_retries=self.max_retries
        )

    def respond(
        self, request: ModelRequest, on_text: Callable[[str], None], cancel: CancelToken
    ) -> ModelResponse:
        """Stream one response. Raises `ModelCancelled` after a cancel, `HistoryError` before
        sending a request the API would refuse, `UnparseableResponse` for a response without a
        held tool call to answer, and `ChatModelError` for everything else."""
        assert self._client is not None
        _check_history(request)
        if cancel.cancelled:
            raise ModelCancelled
        try:
            return self._stream(self._client, request, on_text, cancel)
        except (ModelCancelled, ChatModelError):
            raise
        except Exception as error:
            if cancel.cancelled:
                raise ModelCancelled from None
            raise map_api_error(error) from None

    def _stream(
        self,
        client: anthropic.Anthropic,
        request: ModelRequest,
        on_text: Callable[[str], None],
        cancel: CancelToken,
    ) -> ModelResponse:
        with client.beta.messages.stream(**request.kwargs()) as stream:
            forget = cancel.on_cancel(stream.close)
            try:
                for piece in stream.text_stream:
                    if cancel.cancelled:
                        break
                    on_text(piece)
                message = stream.get_final_message()
            except ValueError:
                return self._interrupted(stream, cancel)
            finally:
                forget()
        if cancel.cancelled:
            raise ModelCancelled
        if message.stop_reason is None:
            raise ChatModelError(
                "stream_ended",
                "the response ended before it was complete",
                getattr(message, "_request_id", None),
            )
        return response_from_message(message)

    def _interrupted(self, stream: Any, cancel: CancelToken) -> ModelResponse:
        """The SDK raised `ValueError` on tool input that is not JSON. When the failing call is a
        held `tool_use` block it is answered with an error result; otherwise ask again."""
        if cancel.cancelled:
            raise ModelCancelled
        snapshot = stream.current_message_snapshot
        blocks = list(snapshot.content)
        if blocks and blocks[-1].type == "tool_use":
            response = response_from_message(snapshot, usage_complete=False)
            return replace(
                response, stop_reason="tool_use", invalid_tool_ids=frozenset({blocks[-1].id})
            )
        raise UnparseableResponse(getattr(snapshot, "_request_id", None))


def _check_history(request: ModelRequest) -> None:
    violations = conformance.check_stateless(request.messages)
    if violations:
        raise HistoryError("; ".join(str(violation) for violation in violations))
