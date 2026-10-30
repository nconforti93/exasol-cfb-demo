"""The API's rules for the message history, as one check (pure).

Every request of the chat must pass it before it is sent. Seven rules need no earlier request and
run before every live call; two more compare the request with the conversation's earlier requests
and responses and run on every request of the scripted model, so that every test also shows that the
API would accept what the loop builds. A violation names the rule and the position of the message
(0-based), so a failing test says what the API would have objected to.

1. `messages` is not empty and starts with a `user` message.
2. A `system` message follows a `user` message, and is the last message or is followed by an
   `assistant` message.
3. `user` and `assistant` messages alternate, system messages aside.
4. No message has empty content, and no text block is empty or blank.
5. Every `assistant` message with `tool_use` blocks is followed by a `user` message that starts with
   exactly one `tool_result` per call, in the same order, before any other block; a `tool_result`
   appears nowhere else.
6. The last message is a `user` or `system` message (no assistant prefill).
7. No `assistant` message holds a `thinking`, `redacted_thinking`, `tool_use` or other
   model-internal block before its last `fallback` block: only text and `fallback` blocks may come
   before it. The `fallback` blocks themselves stay where they appeared (decision [28]).
8. `system` and `tools` are byte-identical to those of the conversation's first request.
9. Every `assistant` message equals a response the model returned in this conversation (after the
   fallback echo rule), and every message before it equals, byte for byte, the messages of the
   request that produced that response.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

BEFORE_FALLBACK = frozenset({"text", "fallback"})

Message = Mapping[str, Any]


@dataclass(frozen=True)
class Violation:
    """One broken rule at one message (`position` is None for a rule about the whole request)."""

    rule: int
    position: int | None
    message: str

    def __str__(self) -> str:
        where = "" if self.position is None else f" (message {self.position})"
        return f"rule {self.rule}{where}: {self.message}"


class ConformanceError(ValueError):
    """A request that the API would refuse; the message lists every violation."""


def require(violations: Sequence[Violation]) -> None:
    """Raise `ConformanceError` naming every violation, or return when there are none."""
    if violations:
        raise ConformanceError("; ".join(str(violation) for violation in violations))


def check_stateless(messages: Sequence[Message]) -> tuple[Violation, ...]:
    """Rules 1 to 7 on `messages`."""
    found: list[Violation] = []
    _roles(messages, found)
    _content(messages, found)
    _tool_calls(messages, found)
    _fallback_blocks(messages, found)
    return tuple(sorted(found, key=lambda v: (v.position is None, v.position or 0, v.rule)))


class Conformance:
    """The check for one conversation: rules 1 to 9, remembering what the model returned.

    `check` runs before a request is sent; `record` after its response came back, with the content
    in the form that will be echoed into the history (`chat.model.echo_content`)."""

    def __init__(self) -> None:
        self._first: tuple[str, str] | None = None
        self._responses: list[tuple[str, tuple[str, ...]]] = []

    def check(self, request: Mapping[str, Any]) -> tuple[Violation, ...]:
        messages: Sequence[Message] = request["messages"]
        found = list(check_stateless(messages))
        found += self._pinned(request)
        found += self._replayed(messages)
        return tuple(found)

    def record(
        self, request: Mapping[str, Any], echoed_content: Sequence[Mapping[str, Any]]
    ) -> None:
        if self._first is None:
            self._first = (_canon(request.get("system")), _canon(request.get("tools")))
        prefixes = tuple(_canon(message) for message in request["messages"])
        self._responses.append((_canon(list(echoed_content)), prefixes))

    def _pinned(self, request: Mapping[str, Any]) -> list[Violation]:
        if self._first is None:
            return []
        system, tools = self._first
        found = []
        if _canon(request.get("system")) != system:
            found.append(Violation(8, None, "the system prompt differs from the first request's"))
        if _canon(request.get("tools")) != tools:
            found.append(Violation(8, None, "the tool definitions differ from the first request's"))
        return found

    def _replayed(self, messages: Sequence[Message]) -> list[Violation]:
        if not self._responses and not any(m.get("role") == "assistant" for m in messages):
            return []
        found = []
        canon = [_canon(message) for message in messages]
        for index, message in enumerate(messages):
            if message.get("role") != "assistant":
                continue
            content = _canon(_blocks(message))
            producers = [prefixes for echoed, prefixes in self._responses if echoed == content]
            if not producers:
                found.append(
                    Violation(
                        9, index, "the assistant message is not a response of this conversation"
                    )
                )
            elif tuple(canon[:index]) not in producers:
                found.append(
                    Violation(
                        9,
                        index,
                        "the messages before this response differ from its request",
                    )
                )
        return found


def _canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _blocks(message: Message) -> list[Any]:
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return list(content or [])


def _roles(messages: Sequence[Message], found: list[Violation]) -> None:
    if not messages:
        found.append(Violation(1, None, "messages is empty"))
        return
    if messages[0].get("role") != "user":
        found.append(Violation(1, 0, "the first message is not a user message"))
    previous_turn: str | None = None
    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "system":
            before = messages[index - 1].get("role") if index else None
            after = messages[index + 1].get("role") if index + 1 < len(messages) else None
            if before != "user":
                found.append(Violation(2, index, "a system message must follow a user message"))
            if after not in (None, "assistant"):
                found.append(
                    Violation(
                        2,
                        index,
                        "a system message must be last or be followed by an assistant message",
                    )
                )
        elif role in ("user", "assistant"):
            if role == previous_turn:
                found.append(Violation(3, index, f"two {role} messages in a row"))
            previous_turn = role
        else:
            found.append(Violation(3, index, f"unknown role {role!r}"))
    if messages[-1].get("role") == "assistant":
        found.append(Violation(6, len(messages) - 1, "the last message is an assistant message"))


def _content(messages: Sequence[Message], found: list[Violation]) -> None:
    for index, message in enumerate(messages):
        content = message.get("content")
        if content is None or (isinstance(content, str | list) and len(content) == 0):
            found.append(Violation(4, index, "the message has empty content"))
            continue
        for block in _blocks(message):
            if block.get("type") == "text" and not str(block.get("text", "")).strip():
                found.append(Violation(4, index, "a text block is empty or blank"))
        if isinstance(content, str) and not content.strip():
            found.append(Violation(4, index, "the message has blank content"))


def _tool_calls(messages: Sequence[Message], found: list[Violation]) -> None:
    for index, message in enumerate(messages):
        blocks = _blocks(message)
        role = message.get("role")
        calls = [b["id"] for b in blocks if role == "assistant" and b.get("type") == "tool_use"]
        results = [b for b in blocks if b.get("type") == "tool_result"]
        if role == "assistant":
            if results:
                found.append(Violation(5, index, "an assistant message holds a tool_result"))
            if calls:
                _answered(messages, index, calls, found)
        elif role == "user":
            owner = messages[index - 1] if index else None
            owned = (
                owner is not None
                and owner.get("role") == "assistant"
                and any(block.get("type") == "tool_use" for block in _blocks(owner))
            )
            if results and not owned:
                found.append(Violation(5, index, "a tool_result without a tool call before it"))
        elif results:
            found.append(Violation(5, index, "a system message holds a tool_result"))


def _answered(
    messages: Sequence[Message], index: int, calls: list[str], found: list[Violation]
) -> None:
    following = messages[index + 1] if index + 1 < len(messages) else None
    if following is None or following.get("role") != "user":
        found.append(Violation(5, index, f"tool calls {calls} have no user message after them"))
        return
    blocks = _blocks(following)
    leading = []
    for block in blocks:
        if block.get("type") != "tool_result":
            break
        leading.append(block.get("tool_use_id"))
    total = sum(1 for block in blocks if block.get("type") == "tool_result")
    if leading != calls or total != len(calls):
        missing = [call for call in calls if call not in leading]
        detail = (
            f"tool call {missing[0]} has no result in the next message"
            if missing
            else ("the results are not exactly one per call, first and in the order of the calls")
        )
        found.append(Violation(5, index + 1, detail))


def _fallback_blocks(messages: Sequence[Message], found: list[Violation]) -> None:
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        types = [block.get("type") for block in _blocks(message)]
        last = max((i for i, kind in enumerate(types) if kind == "fallback"), default=0)
        early = [kind for kind in types[:last] if kind not in BEFORE_FALLBACK]
        if early:
            found.append(
                Violation(7, index, f"a {early[0]} block comes before the last fallback block")
            )
