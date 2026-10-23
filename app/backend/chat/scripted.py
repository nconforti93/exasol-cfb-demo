"""The scripted model: a deterministic stand-in for Claude behind the same seam (`ChatModel`).

It drives every loop, tool, budget, app-state and rehearsal test, the CI rehearsal and stub mode, at
zero cost and with no network. A step is one response: text, tool calls, a stop reason, usage, and
the things a real API does that tests must reach (a refusal with empty content, a `fallback` block,
dropped thinking blocks, a stream that stays open until a signal, an error).

Two ways to choose the step:

- `ScriptedChatModel(steps)` plays its steps in order, one per call, whatever the request holds.
- `QuestionScriptedModel(scripts)` reads the question and the turn so far from the request, and
  plays the script of that question: the step is the number of assistant messages after the
  question. Answers and tool inputs may read the tool results of the turn (`{t2.row.P_IN_CFP|pct}`)
  and the app state (`{state.focus_team_id}`). Questions without a script get a fixed answer.

One model object serves one conversation: it runs the whole history check (`chat/conformance.py`,
all nine rules) on every request it receives, so every test also shows that the API would accept it.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from app.backend.chat import conformance
from app.backend.chat.model import (
    CancelToken,
    ChatModelError,
    HistoryError,
    ModelCancelled,
    ModelRequest,
    ModelResponse,
    echo_content,
)

DEFAULT_USAGE: Mapping[str, Any] = {
    "input_tokens": 1000,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "output_tokens": 200,
}
PIECE_LENGTH = 40
HOLD_SECONDS = 30.0
STUB_NOTICE = (
    "Stub mode: I only answer the rehearsed questions. Pick one of the suggestions, or add an "
    "Anthropic key to ask anything."
)
FREEZE_LABELS = {
    "ALL": "reconstructed at the as-of point (leak-free, made after kickoff)",
    "IN_ADVANCE": "made in advance",
    "RECONSTRUCTED": "reconstructed after kickoff",
}
FORMATS = frozenset({"raw", "pct", "pct0", "pp", "int", "dec1", "dec2", "freeze"})
RUNTIME_REF = re.compile(
    r"\{(?P<ref>t\d+\.[A-Za-z_][\w.]*|state\.[A-Za-z_]\w*)(?:\|(?P<fmt>[a-z0-9_]+))?\}"
)


class ScriptExhausted(AssertionError):
    """The model was called more often than its script has steps."""


class TemplateError(ValueError):
    """A placeholder of a script cannot be filled from the turn so far."""


@dataclass(frozen=True)
class Hold:
    """A stream that stays open until a signal: `reached` is set when the model waits, `release`
    lets it finish, `closed` is set when a cancel closed it."""

    reached: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    closed: threading.Event = field(default_factory=threading.Event)
    seconds: float = HOLD_SECONDS


@dataclass(frozen=True)
class ScriptedCall:
    """One tool call of a step; `call_id` is made up when left out."""

    name: str
    input: Any
    call_id: str | None = None


@dataclass(frozen=True)
class ScriptStep:
    """One response of the model."""

    answer: str | None = None
    tools: tuple[ScriptedCall, ...] = ()
    text: str | None = None
    stop_reason: str | None = None
    stop_details: Mapping[str, Any] | None = None
    usage: Mapping[str, Any] | None = None
    model: str | None = None
    thinking: bool = True
    content: tuple[dict[str, Any], ...] | None = None
    input_transformations: tuple[dict[str, Any], ...] = ()
    hold: Hold | None = None
    raises: Exception | None = None
    usage_complete: bool = True
    invalid_tool_ids: frozenset[str] = frozenset()

    @staticmethod
    def say(text: str, **options: Any) -> ScriptStep:
        """A final answer."""
        return ScriptStep(answer=text, **options)

    @staticmethod
    def call(name: str, tool_input: Any, **options: Any) -> ScriptStep:
        """One tool call."""
        return ScriptStep(tools=(ScriptedCall(name, tool_input),), **options)

    @staticmethod
    def calls(*calls: ScriptedCall, **options: Any) -> ScriptStep:
        """Several tool calls in one response."""
        return ScriptStep(tools=calls, **options)


@dataclass(frozen=True)
class Script:
    """The steps of one question."""

    steps: tuple[ScriptStep, ...]


# --- templates -------------------------------------------------------------------------------


def render_template(
    template: str,
    results: Mapping[int, Mapping[str, Any]] | None = None,
    state: Mapping[str, Any] | None = None,
) -> str:
    """Fill the runtime placeholders of `template`: `{tN.row.COL|fmt}`, `{tN.rows.I.COL|fmt}`,
    `{tN.row_count}` read the N-th tool result of the turn (1-based), `{state.NAME}` the state."""

    def fill(match: re.Match[str]) -> str:
        value = _lookup(match.group("ref"), results or {}, state or {})
        return _format(value, match.group("fmt") or "raw")

    return RUNTIME_REF.sub(fill, template)


def render_value(
    value: Any, results: Mapping[int, Mapping[str, Any]], state: Mapping[str, Any]
) -> Any:
    """Fill placeholders anywhere in a JSON-like value. A string that is exactly one placeholder
    without a format becomes the native value (an id stays an integer)."""
    if isinstance(value, str):
        whole = RUNTIME_REF.fullmatch(value)
        if whole is not None and whole.group("fmt") is None:
            return _lookup(whole.group("ref"), results, state)
        return render_template(value, results, state)
    if isinstance(value, Mapping):
        return {key: render_value(item, results, state) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [render_value(item, results, state) for item in value]
    return value


def runtime_refs(value: Any) -> list[tuple[str, str | None]]:
    """Every runtime placeholder in a JSON-like value as (reference, format)."""
    found: list[tuple[str, str | None]] = []
    if isinstance(value, str):
        found += [(m.group("ref"), m.group("fmt")) for m in RUNTIME_REF.finditer(value)]
    elif isinstance(value, Mapping):
        for item in value.values():
            found += runtime_refs(item)
    elif isinstance(value, list | tuple):
        for item in value:
            found += runtime_refs(item)
    return found


def _lookup(ref: str, results: Mapping[int, Mapping[str, Any]], state: Mapping[str, Any]) -> Any:
    head, _, rest = ref.partition(".")
    if head == "state":
        if rest not in state:
            raise TemplateError(f"the app state has no {rest!r}")
        return state[rest]
    number = int(head[1:])
    result = results.get(number)
    if result is None:
        raise TemplateError(f"tool call {number} has no result")
    if result.get("kind") == "error":
        raise TemplateError(
            f"tool call {number} failed: {result.get('error') or result.get('problem')}"
        )
    if rest == "row_count":
        return result.get("row_count")
    parts = rest.split(".")
    if parts[0] == "row" and len(parts) == 2:
        return _cell(result, 0, parts[1], number)
    if parts[0] == "rows" and len(parts) == 3 and parts[1].isdigit():
        return _cell(result, int(parts[1]), parts[2], number)
    raise TemplateError(f"cannot read {ref!r}")


def _cell(result: Mapping[str, Any], row: int, column: str, number: int) -> Any:
    columns = [str(name).upper() for name in result.get("columns", [])]
    rows = result.get("rows", [])
    if column.upper() not in columns:
        raise TemplateError(f"tool call {number} has no column {column!r}")
    if row >= len(rows):
        raise TemplateError(f"tool call {number} has no row {row}")
    return rows[row][columns.index(column.upper())]


def _format(value: Any, fmt: str) -> str:
    if fmt not in FORMATS:
        raise TemplateError(f"unknown format {fmt!r}")
    if value is None:
        raise TemplateError("the value is NULL")
    if fmt == "raw":
        return _plain(value)
    if fmt == "freeze":
        return FREEZE_LABELS.get(str(value), str(value).lower())
    number = float(value)
    return {
        "pct": f"{number * 100:.1f}%",
        "pct0": f"{number * 100:.0f}%",
        "pp": f"{number * 100:+.1f} percentage points",
        "int": str(round(number)),
        "dec1": f"{number:.1f}",
        "dec2": f"{number:.2f}",
    }[fmt]


def _plain(value: Any) -> str:
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def script_from_spec(spec: Sequence[Mapping[str, Any]]) -> Script:
    """The script of a rehearsed question, from its YAML steps: `{tools: [{name, input}], text?}`
    or `{answer}` (placeholders stay in place until the step is played)."""
    steps = []
    for index, item in enumerate(spec, start=1):
        if "tools" in item:
            calls = tuple(
                ScriptedCall(str(call["name"]), call.get("input", {})) for call in item["tools"]
            )
            steps.append(ScriptStep(tools=calls, text=item.get("text")))
        elif "answer" in item:
            steps.append(ScriptStep(answer=str(item["answer"])))
        else:
            raise ValueError(f"script step {index} has neither tools nor answer")
    return Script(tuple(steps))


# --- reading the turn out of a request -------------------------------------------------------


@dataclass(frozen=True)
class TurnView:
    """The question of the request, and what the turn has done so far."""

    question: str
    step_index: int
    results: Mapping[int, Mapping[str, Any]]
    state: Mapping[str, Any]


def _blocks(message: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return list(content or [])


def turn_view(messages: Sequence[Mapping[str, Any]]) -> TurnView:
    """The pending question (the last user message with something other than tool results), the
    number of assistant messages after it, the results in call order and the flat app state."""
    index = next(
        (
            i
            for i in range(len(messages) - 1, -1, -1)
            if messages[i].get("role") == "user"
            and any(b.get("type") != "tool_result" for b in _blocks(messages[i]))
        ),
        None,
    )
    if index is None:
        raise ChatModelError("scripted", "the request holds no question")
    question = "".join(
        str(b.get("text", "")) for b in _blocks(messages[index]) if b.get("type") == "text"
    )
    state: Mapping[str, Any] = {}
    after = messages[index + 1] if index + 1 < len(messages) else None
    if after is not None and after.get("role") == "system":
        state = _flat_state(after.get("content"))
    steps = 0
    order: list[str] = []
    outcomes: dict[str, Mapping[str, Any]] = {}
    for message in messages[index + 1 :]:
        role = message.get("role")
        if role == "assistant":
            steps += 1
            order += [str(b["id"]) for b in _blocks(message) if b.get("type") == "tool_use"]
        elif role == "user":
            for block in _blocks(message):
                if block.get("type") == "tool_result":
                    outcomes[str(block.get("tool_use_id"))] = _result_json(block)
    results = {
        n: outcomes[call_id] for n, call_id in enumerate(order, start=1) if call_id in outcomes
    }
    return TurnView(question, steps, results, state)


def _result_json(block: Mapping[str, Any]) -> Mapping[str, Any]:
    content = block.get("content")
    text = (
        content
        if isinstance(content, str)
        else "".join(
            str(part.get("text", "")) for part in content or [] if isinstance(part, Mapping)
        )
    )
    try:
        value = json.loads(text)
    except ValueError:
        return {"kind": "error", "error": text}
    return value if isinstance(value, dict) else {"kind": "error", "error": text}


def _flat_state(content: Any) -> Mapping[str, Any]:
    text = content if isinstance(content, str) else ""
    try:
        body = json.loads(text)
    except ValueError:
        return {}
    app_state = body.get("app_state") if isinstance(body, dict) else None
    flat: dict[str, Any] = {}

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                walk(f"{prefix}_{key}" if prefix else str(key), item)
        else:
            flat[prefix] = value

    if isinstance(app_state, dict):
        walk("", app_state)
    return flat


# --- the models ------------------------------------------------------------------------------


class StepSource(Protocol):
    """Chooses the step for a request."""

    def step_for(self, request: ModelRequest, call_index: int) -> ScriptStep: ...


@dataclass
class _Sequence:
    steps: Sequence[ScriptStep]

    def step_for(self, request: ModelRequest, call_index: int) -> ScriptStep:
        if call_index >= len(self.steps):
            raise ScriptExhausted(
                f"the model was called {call_index + 1} times; the script has {len(self.steps)}"
            )
        return self.steps[call_index]


@dataclass
class _ByQuestion:
    scripts: Mapping[str, Script]
    other: Script

    def step_for(self, request: ModelRequest, call_index: int) -> ScriptStep:
        view = turn_view(request.messages)
        script = self.scripts.get(view.question, self.other)
        if view.step_index >= len(script.steps):
            raise ScriptExhausted(f"the script of {view.question!r} has {len(script.steps)} steps")
        step = script.steps[view.step_index]
        try:
            return _render_step(step, view)
        except TemplateError as error:
            return ScriptStep(
                answer=f"The rehearsed answer could not be built from the data: {error}"
            )


def _render_step(step: ScriptStep, view: TurnView) -> ScriptStep:
    answer = None if step.answer is None else render_template(step.answer, view.results, view.state)
    text = None if step.text is None else render_template(step.text, view.results, view.state)
    tools = tuple(
        replace(call, input=render_value(call.input, view.results, view.state))
        for call in step.tools
    )
    return replace(step, answer=answer, text=text, tools=tools)


class ScriptedChatModel:
    """A `ChatModel` that plays steps. Records every request and response, and runs the whole
    history check on every request (`violations` collects what it found)."""

    def __init__(
        self,
        steps: Sequence[ScriptStep] | StepSource,
        *,
        usage: Mapping[str, Any] = DEFAULT_USAGE,
        model: str | None = None,
        check: bool = True,
    ) -> None:
        self._source: StepSource = _Sequence(steps) if isinstance(steps, Sequence) else steps
        self._usage = dict(usage)
        self._model = model
        self._check = check
        self._conformance = conformance.Conformance()
        self._lock = threading.Lock()
        self.requests: list[ModelRequest] = []
        self.responses: list[ModelResponse] = []
        self.violations: list[conformance.Violation] = []
        self.held: list[Hold] = []

    @classmethod
    def by_question(
        cls, scripts: Mapping[str, Script], *, other: str = STUB_NOTICE, **options: Any
    ) -> ScriptedChatModel:
        """A model that plays the script of the pending question, and `other` for any other."""
        fallback = Script((ScriptStep(answer=other),))
        return cls(_ByQuestion(scripts, fallback), **options)

    @property
    def calls(self) -> int:
        return len(self.requests)

    def assert_conformant(self) -> None:
        """Fail when any request broke a rule of the history."""
        conformance.require(self.violations)

    def respond(
        self, request: ModelRequest, on_text: Callable[[str], None], cancel: CancelToken
    ) -> ModelResponse:
        if cancel.cancelled:
            raise ModelCancelled
        with self._lock:
            index = len(self.requests)
            self.requests.append(request)
            violations = self._conformance.check(request.conformance_view()) if self._check else ()
            self.violations.extend(violations)
        if violations:
            raise HistoryError("; ".join(str(violation) for violation in violations))
        step = self._source.step_for(request, index)
        if step.raises is not None:
            raise step.raises
        response = self._play(step, index, request, on_text, cancel)
        with self._lock:
            self.responses.append(response)
            if self._check:
                self._conformance.record(request.conformance_view(), echo_content(response.content))
        return response

    def _play(
        self,
        step: ScriptStep,
        index: int,
        request: ModelRequest,
        on_text: Callable[[str], None],
        cancel: CancelToken,
    ) -> ModelResponse:
        content = list(step.content) if step.content is not None else self._blocks(step, index)
        for block in content:
            if block.get("type") == "text":
                for piece in _pieces(str(block["text"])):
                    if cancel.cancelled:
                        raise ModelCancelled
                    on_text(piece)
        if step.hold is not None:
            self._wait(step.hold, cancel)
        invalid = step.invalid_tool_ids
        tool_use = any(block.get("type") == "tool_use" for block in content)
        stop = step.stop_reason or ("tool_use" if tool_use else "end_turn")
        return ModelResponse(
            content=tuple(content),
            stop_reason=stop,
            model=step.model or self._model or request.model,
            usage=dict(step.usage if step.usage is not None else self._usage),
            stop_details=step.stop_details,
            input_transformations=step.input_transformations,
            request_id=f"req_script_{index + 1}",
            usage_complete=step.usage_complete,
            invalid_tool_ids=invalid,
        )

    def _blocks(self, step: ScriptStep, index: int) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        if not step.tools and step.answer is None:
            return blocks
        spoken = step.text if step.tools else step.answer
        if step.thinking:
            blocks.append({"type": "thinking", "thinking": "", "signature": f"sig-{index + 1}"})
        if spoken:
            blocks.append({"type": "text", "text": spoken})
        for position, call in enumerate(step.tools, start=1):
            blocks.append(
                {
                    "type": "tool_use",
                    "id": call.call_id or f"toolu_{index + 1}_{position}",
                    "name": call.name,
                    "input": call.input,
                }
            )
        return blocks

    def _wait(self, hold: Hold, cancel: CancelToken) -> None:
        self.held.append(hold)
        forget = cancel.on_cancel(hold.closed.set)
        hold.reached.set()
        try:
            deadline = hold.seconds
            while deadline > 0 and not hold.release.is_set() and not cancel.cancelled:
                cancel.wait(0.01)
                deadline -= 0.01
        finally:
            forget()
        if cancel.cancelled:
            raise ModelCancelled


def _pieces(text: str) -> list[str]:
    return [text[i : i + PIECE_LENGTH] for i in range(0, len(text), PIECE_LENGTH)] or [""]
