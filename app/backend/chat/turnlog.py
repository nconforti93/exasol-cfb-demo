"""The turn log: one JSON line per chat turn, whatever its outcome (`chat/turn-log`).

The log is local (`cache/` is git-ignored) and never holds a held secret: every string is checked
before the line is written. A failing write is one warning that never repeats the line's content,
and never fails the turn. The spend file, not this log, feeds the daily cap."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.backend.chat.tools import json_value

LOGGER = logging.getLogger("app.backend.chat.turnlog")
REDACTED = "[REDACTED]"
MIN_SECRET_LENGTH = 4
LOGGED_ROWS = 20
PREFIX_MISMATCH = "prefix_binding_mismatch"


@dataclass(frozen=True)
class CallRecord:
    """One model call: how it ended, what served it, what it used and cost, and whether its
    response entered the history."""

    stop_reason: str | None
    model: str
    usage: Mapping[str, Any]
    cost_usd: float
    request_id: str | None
    kept: bool
    fallback_blocks: Sequence[Mapping[str, Any]] = ()
    input_transformations: Sequence[Mapping[str, Any]] = ()
    estimated: bool = False


@dataclass(frozen=True)
class ToolRecord:
    """One tool call: the validated input and, for a statement, exactly what was sent."""

    name: str
    input: Any
    sql_id: str | None = None
    sql: str | None = None
    row_count: int | None = None
    truncated: bool = False
    rows: Sequence[Sequence[Any]] = ()
    ms: int | None = None
    error: str | None = None
    refused_rule: str | None = None


@dataclass(frozen=True)
class ActionRecord:
    """One accepted app change with the state before and after."""

    tool: str
    input: Any
    state_before: Mapping[str, Any]
    state_after: Mapping[str, Any]


@dataclass(frozen=True)
class TurnRecord:
    """Everything one turn leaves in the log."""

    started_at: datetime
    ended_at: datetime
    chat_id: str
    turn_id: str
    target: str
    namespace: str
    model: str
    effort: str
    fallbacks: str
    prompt_sha256: str
    stub: bool
    question: str
    app_state: Any
    outcome: str
    in_history: bool
    answer: str = ""
    reason: str | None = None
    calls: Sequence[CallRecord] = ()
    tools: Sequence[ToolRecord] = ()
    app_actions: Sequence[ActionRecord] = ()


@dataclass
class _Redactor:
    secrets: tuple[str, ...]
    count: int = field(default=0)

    def clean(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._text(value)
        if isinstance(value, Mapping):
            return {self._text(str(key)): self.clean(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self.clean(item) for item in value]
        return value

    def _text(self, text: str) -> str:
        for secret in self.secrets:
            hits = text.count(secret)
            if hits:
                self.count += hits
                text = text.replace(secret, REDACTED)
        return text


class TurnLog:
    """Appends turn lines to `path` under a lock (in the process and between processes)."""

    def __init__(
        self,
        path: Path,
        *,
        secrets: Sequence[str] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._path = path
        self._secrets = tuple(s for s in secrets if len(s) >= MIN_SECRET_LENGTH)
        self._clock = clock
        self._lock = threading.Lock()

    def write(self, record: TurnRecord) -> bool:
        """Append the turn's line; False (after one warning) when the file cannot be written."""
        self._warn_about_dropped_thinking(record)
        redactor = _Redactor(self._secrets)
        body = redactor.clean(_line(record, self._clock()))
        body["redactions"] = redactor.count
        data = (json.dumps(body, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        try:
            self._append(data)
        except OSError as error:
            LOGGER.warning(
                "chat log write failed (%s); the turn is not affected", type(error).__name__
            )
            return False
        return True

    def _append(self, data: bytes) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self._path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                os.write(descriptor, data)
            finally:
                os.close(descriptor)

    def _warn_about_dropped_thinking(self, record: TurnRecord) -> None:
        for index, call in enumerate(record.calls, start=1):
            for entry in call.input_transformations:
                if entry.get("reason") == PREFIX_MISMATCH:
                    LOGGER.warning(
                        "chat turn %s call %d: %s at %s: history changed under a thinking block",
                        record.turn_id,
                        index,
                        PREFIX_MISMATCH,
                        entry.get("path"),
                    )


def _line(record: TurnRecord, now: datetime) -> dict[str, Any]:
    calls = [_call(call) for call in record.calls]
    input_tokens = sum(_input_tokens(call.usage) for call in record.calls)
    output_tokens = sum(int(call.usage.get("output_tokens") or 0) for call in record.calls)
    return {
        "logged_at": now.isoformat(),
        "started_at": record.started_at.isoformat(),
        "ended_at": record.ended_at.isoformat(),
        "chat_id": record.chat_id,
        "turn_id": record.turn_id,
        "target": record.target,
        "namespace": record.namespace,
        "model": record.model,
        "effort": record.effort,
        "fallbacks": record.fallbacks,
        "prompt_sha256": record.prompt_sha256,
        "stub": record.stub,
        "question": record.question,
        "app_state": record.app_state,
        "calls": calls,
        "tools": [_tool(tool) for tool in record.tools],
        "app_actions": [
            {
                "tool": action.tool,
                "input": action.input,
                "state_before": action.state_before,
                "state_after": action.state_after,
            }
            for action in record.app_actions
        ],
        "answer": record.answer,
        "outcome": record.outcome,
        "reason": record.reason,
        "in_history": record.in_history,
        "totals": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": sum(call.cost_usd for call in record.calls),
            "model_calls": len(record.calls),
            "tool_calls": len(record.tools),
        },
    }


def _input_tokens(usage: Mapping[str, Any]) -> int:
    keys = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return sum(int(usage.get(key) or 0) for key in keys)


def _call(call: CallRecord) -> dict[str, Any]:
    return {
        "stop_reason": call.stop_reason,
        "model": call.model,
        "usage": dict(call.usage),
        "cost_usd": call.cost_usd,
        "estimated": call.estimated,
        "request_id": call.request_id,
        "kept": call.kept,
        "fallback_blocks": [dict(block) for block in call.fallback_blocks],
        "input_transformations": [dict(entry) for entry in call.input_transformations],
    }


def _tool(tool: ToolRecord) -> dict[str, Any]:
    rows = [[json_value(value) for value in row] for row in tool.rows[:LOGGED_ROWS]]
    body: dict[str, Any] = {
        "name": tool.name,
        "input": tool.input,
        "sql_id": tool.sql_id,
        "sql": tool.sql,
        "row_count": tool.row_count,
        "truncated": tool.truncated,
        "rows": rows,
        "ms": tool.ms,
        "error": tool.error,
    }
    if tool.refused_rule is not None:
        body["refused_rule"] = tool.refused_rule
    return body
