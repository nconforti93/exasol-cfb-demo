"""The turn loop (`chat/conversation`; decisions [3], [22], [25], [28]): one question, run in a
background thread, step by step until the model answers or the turn stops.

A step checks the stop cause, builds the request from the history and the pending question,
checks it against the API's rules, reserves its worst case, streams the response, settles it, and
acts on the stop reason: `end_turn` answers, `tool_use` runs the tools after the last `fallback`
block, and every other ending (`refusal`, `max_tokens`, an empty answer, any unknown reason)
withdraws the streamed text and runs nothing.

Invariants the loop keeps (each one tested in `tests/unit/test_chat_loop.py`):

- **The history is always a valid prefix.** A question enters it together with its first kept
  response; a response is kept only when it ends `end_turn` with text and no tool call, or
  `tool_use` with at least one tool call. A kept response with tool calls is committed in the same
  step as the user message that answers every call, in order, run or not ("turn stopped: ...").
  Commits are atomic and happen only while the turn is alive, so a turn that ends early, fails or
  is abandoned leaves nothing half-added.
- **Nothing earlier changes.** Responses are echoed with `model.echo_content`, the same function the
  scripted model records them with; the system prompt and tools are fixed per process. The only
  message that grows is the open tail (tool results no kept response answered yet), which takes the
  next question as a text block after the results.
- **Every call is paid for.** Each request is reserved before it is sent and then settled once:
  from its usage, at its worst case when no complete usage came, or released when it was refused
  before reaching the model. A late call of an abandoned turn is still settled when it returns.
- **No work starts after a stop.** The cancel token is checked before each model call and each
  tool; a stream is closed and a statement aborted through the token's callbacks.

Events of a turn, in order: `status` first; per step its `text` pieces, then per tool call
`tool_call`, `sql` (one per statement) and `app_action`; at the end `usage` (when a call was made),
then `answer` and/or `error`, and `done`. `text_reset` withdraws the turn's streamed text when a
response is not kept, and `status` names the model that finished an answer after a fallback.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from app.backend.chat import appstate, budget, conformance
from app.backend.chat.appstate import AppChange, AppState, StateLookups, ValidatedState
from app.backend.chat.budget import CapRefusal, CapScope
from app.backend.chat.model import (
    CancelToken,
    ChatModel,
    ChatModelError,
    ModelCancelled,
    ModelRequest,
    ModelResponse,
    RequestSettings,
    UnparseableResponse,
    build_request,
    echo_content,
    fallback_blocks,
    text_of,
    tool_calls,
)
from app.backend.chat.spend import CallPlan, Limits, Reservation, SpendLedger
from app.backend.chat.tools import (
    InvalidToolInput,
    ToolInput,
    ToolResult,
    error_result,
    invalid_input_result,
    stopped_result,
    tool_result_block,
    validate_input,
)
from app.backend.chat.turnlog import ActionRecord, CallRecord, ToolRecord

LOGGER = logging.getLogger("app.backend.chat.loop")
APP_STATE_TOOL = "app_state"
DECLINED = "The model declined to answer this."
# Model errors that mean the request was refused before the model ran: nothing to pay for.
NOT_SENT = frozenset(
    {
        "auth",
        "permission",
        "not_found",
        "bad_request",
        "rate_limit",
        "conflicting_credentials",
        "internal_history_error",
    }
)

# What the turn's thread is doing: a model call (or the loop itself), or statements on the chat
# connection (the app-state lookups and a tool). Stop abandons a statement later than a call.
CALL_PHASE, STATEMENT_PHASE = "call", "statement"

DONE, FAILED, REFUSED, CANCELLED_STATE, STOPPED = (
    "done",
    "failed",
    "refused",
    "cancelled",
    "stopped",
)


@dataclass(frozen=True)
class LoopSettings:
    """The settings of a turn: the request, the turn's limits and the caps (`chat/budget`)."""

    request: RequestSettings = field(default_factory=RequestSettings)
    max_steps: int = 8
    max_tools: int = 16
    max_seconds: float = 90.0
    session_max_usd: Decimal = budget.DEFAULT_SESSION_MAX_USD
    daily_max_usd: Decimal | None = budget.DEFAULT_DAILY_MAX_USD
    call_max_input_tokens: int | None = budget.DEFAULT_CALL_MAX_INPUT_TOKENS


# --- tools -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Statement:
    """One statement the chat connection sent for a tool (or refused before the database), with the
    text exactly as the driver sent it (`chat/sql-disclosure`)."""

    sql: str
    rows: int | None = None
    more_rows: bool = False
    ms: int | None = None
    error: str | None = None
    refused_rule: str | None = None
    purpose: str | None = None
    first_rows: Sequence[Sequence[Any]] = ()
    truncated: bool = False


class ToolContext:
    """What a tool sees of its turn: its name and call, the app state as the turn has changed it so
    far, the cancel token (register the statement's abort on it while a statement runs) and the
    disclosure of each statement, which numbers it and returns its `sql_id`."""

    def __init__(
        self,
        tool: str,
        call_id: str | None,
        state: AppState,
        cancel: CancelToken,
        disclose: Callable[[ToolContext, Statement], str],
    ) -> None:
        self.tool = tool
        self.call_id = call_id
        self.state = state
        self.cancel = cancel
        self.disclosed = 0
        self._disclose = disclose

    def disclose(self, statement: Statement) -> str:
        """Disclose a statement that ran (or was refused); returns its `sql_id`."""
        self.disclosed += 1
        return self._disclose(self, statement)


@dataclass(frozen=True)
class ToolOutcome:
    """A tool's result, and the app change it made when it is an accepted app-state change."""

    result: ToolResult
    change: AppChange | None = None


class ToolExecutor(Protocol):
    """Runs one validated tool call (tasks 3.2 and 3.3 implement the eight tools)."""

    def __call__(self, tool_input: ToolInput, context: ToolContext) -> ToolOutcome: ...


LookupsFactory = Callable[[ToolContext], StateLookups]


@dataclass(frozen=True)
class TurnDeps:
    """What a turn needs: the frozen system prompt, the lookups that validate the posted app state,
    the dispatch table of the tools by name, the settings, and the spend ledger (None in stub mode,
    which reserves and spends nothing)."""

    system_text: str
    lookups: LookupsFactory
    tools: Mapping[str, ToolExecutor] = field(default_factory=dict)
    settings: LoopSettings = field(default_factory=LoopSettings)
    ledger: SpendLedger | None = None


# --- the history -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Pending:
    """A question that is not in the history yet: its user message (which extends the open tail
    when the history ends with unanswered tool results) and its app-state message."""

    messages: tuple[dict[str, Any], ...]
    extends_tail: bool


class History:
    """The messages every request of a conversation starts with; append-only."""

    def __init__(self) -> None:
        self._messages: list[dict[str, Any]] = []

    @property
    def messages(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._messages)

    def open_tail(self) -> bool:
        """True when the last message holds tool results that no kept response answered."""
        if not self._messages or self._messages[-1]["role"] != "user":
            return False
        return any(block.get("type") == "tool_result" for block in self._messages[-1]["content"])

    def pending(self, question: str, state_message: Mapping[str, Any]) -> Pending:
        """The messages of a new question and its app state."""
        block = {"type": "text", "text": question}
        if self.open_tail():
            tail = self._messages[-1]
            user = {"role": "user", "content": [*tail["content"], block]}
            return Pending((user, dict(state_message)), True)
        return Pending(({"role": "user", "content": [block]}, dict(state_message)), False)

    def request_messages(self, pending: Pending | None) -> list[dict[str, Any]]:
        """The messages of the next request."""
        if pending is None:
            return list(self._messages)
        base = self._messages[:-1] if pending.extends_tail else self._messages
        return [*base, *pending.messages]

    def commit(self, pending: Pending | None, new: Sequence[Mapping[str, Any]]) -> None:
        """Add the pending question (if any) and a kept response with what answers it."""
        added = copy.deepcopy([dict(message) for message in new])
        self._messages = [*self.request_messages(pending), *added]


@dataclass(frozen=True)
class CallContext:
    """The last call of a conversation, for the next input estimate: its context in tokens and the
    characters of the messages it covered (its request and its kept response)."""

    tokens: int
    covered_chars: int


class Conversation:
    """The model-facing state of one conversation: its model (one scripted model per conversation
    checks every request), the history, the last call's context, and the statement numbers."""

    def __init__(self, chat_id: str, model: ChatModel) -> None:
        self.chat_id = chat_id
        self.model = model
        self.history = History()
        self.context: CallContext | None = None
        self._statements = 0
        self._lock = threading.Lock()

    def next_sql_id(self) -> str:
        """`S1`, `S2`, …: statements are numbered per conversation."""
        with self._lock:
            self._statements += 1
            return f"S{self._statements}"


def input_estimate(conversation: Conversation, request: ModelRequest) -> int:
    """The input estimate of a request (`chat/budget`): the last call's context plus the characters
    added since at one token per 2; for the first call, the whole request at that rate."""
    context = conversation.context
    if context is None:
        whole = {"system": request.system, "tools": request.tools, "messages": request.messages}
        return budget.input_estimate(None, budget.request_chars(whole))
    added = budget.request_chars(request.messages) - context.covered_chars
    return budget.input_estimate(context.tokens, max(0, added))


def call_plan(conversation: Conversation, request: ModelRequest) -> CallPlan:
    """The plan of a request: its input estimate and its worst case."""
    return CallPlan.for_request(
        request.model,
        input_estimate(conversation, request),
        max_tokens=request.max_tokens,
        fallbacks=request.fallbacks is not None,
    )


def conversation_scope(conversation: Conversation, settings: LoopSettings) -> CapScope:
    return budget.conversation_scope(conversation.chat_id, settings.session_max_usd)


def limits(settings: LoopSettings) -> Limits:
    return Limits(settings.daily_max_usd, settings.call_max_input_tokens)


def first_call_refusal(
    conversation: Conversation, question: str, deps: TurnDeps
) -> CapRefusal | None:
    """Whether the first call of a new question would be refused by a cap, with the smallest app
    state (the pre-check of `POST /api/chat/turns`; the reservation still decides)."""
    if deps.ledger is None:
        return None
    pending = conversation.history.pending(question, appstate.state_message(appstate.EMPTY))
    messages = conversation.history.request_messages(pending)
    request = build_request(deps.settings.request, deps.system_text, messages)
    plan = call_plan(conversation, request)
    scope = conversation_scope(conversation, deps.settings)
    return deps.ledger.check(plan, [scope], limits(deps.settings))


# --- the turn --------------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnEnd:
    """How a turn ended: its outcome, a code and a short message for any outcome but `done`, the
    answer when there is one, and whether the turn was abandoned while a call or tool still ran."""

    outcome: str
    code: str | None = None
    message: str | None = None
    answer: str | None = None
    abandoned: bool = False


@dataclass(frozen=True)
class StopCause:
    """Why a turn is being stopped from outside: a cancel or the time limit."""

    outcome: str
    code: str
    message: str
    result: str

    def end(self, *, abandoned: bool = False) -> TurnEnd:
        return TurnEnd(self.outcome, self.code, self.message, abandoned=abandoned)


CANCELLED = StopCause(CANCELLED_STATE, "cancelled", "cancelled", "cancelled")


def time_limit(seconds: float) -> StopCause:
    """The stop cause of the turn deadline (`CHAT_TURN_MAX_SECONDS`)."""
    shown = int(seconds) if float(seconds).is_integer() else seconds
    return StopCause(STOPPED, "time_limit", f"time limit reached ({shown} s)", "time limit")


class Turn:
    """One question's turn: its numbered events, what it recorded for the turn log, its stop cause
    and its end. Thread-safe. The first end wins: after it, events, records and commits are
    dropped, so a turn abandoned by the watchdog cannot change the conversation any more."""

    def __init__(
        self,
        turn_id: str,
        chat_id: str,
        question: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_seconds: float | None = None,
        on_end: Callable[[Turn], None] | None = None,
    ) -> None:
        self.turn_id = turn_id
        self.chat_id = chat_id
        self.question = question
        self.cancel = CancelToken()
        self.started = clock()
        self.started_at = wall()
        self.ended_at: datetime | None = None
        self.deadline = None if max_seconds is None else self.started + max_seconds
        self.stop_requested_at: float | None = None
        self.app_state: Any = None
        self.phase = CALL_PHASE
        self._clock = clock
        self._wall = wall
        self._on_end = on_end
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._calls: list[CallRecord] = []
        self._tools: list[ToolRecord] = []
        self._actions: list[ActionRecord] = []
        self._sql_ids: list[str] = []
        self._in_history = False
        self._cause: StopCause | None = None
        self._end: TurnEnd | None = None

    # reading

    @property
    def state(self) -> str:
        end = self._end
        return "running" if end is None else end.outcome

    @property
    def end(self) -> TurnEnd | None:
        return self._end

    @property
    def stop_cause(self) -> StopCause | None:
        return self._cause

    @property
    def in_history(self) -> bool:
        return self._in_history

    @property
    def calls(self) -> tuple[CallRecord, ...]:
        with self._lock:
            return tuple(self._calls)

    @property
    def tools(self) -> tuple[ToolRecord, ...]:
        with self._lock:
            return tuple(self._tools)

    @property
    def actions(self) -> tuple[ActionRecord, ...]:
        with self._lock:
            return tuple(self._actions)

    @property
    def sql_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._sql_ids)

    def events(self, after: int = 0) -> tuple[str, list[dict[str, Any]]]:
        """The turn's state and its events with a sequence number above `after`."""
        with self._lock:
            return self.state, [dict(event) for event in self._events[max(0, after) :]]

    # stopping

    def stop(self, cause: StopCause) -> bool:
        """Ask the turn to stop: sets the cause, then fires the cancel token (closing a stream,
        aborting a statement). False when it already ended or was asked before."""
        with self._lock:
            if self._end is not None or self._cause is not None:
                return False
            self._cause = cause
            self.stop_requested_at = self._clock()
        self.cancel.cancel()
        return True

    def abandon(self) -> bool:
        """End the turn now, from outside, while its thread still waits on a call or a statement;
        whatever that thread does later is dropped."""
        cause = self._cause or CANCELLED
        with self._lock:
            if self._end is not None:
                return False
            self._append("text_reset", {"reason": cause.message})
            self._close(cause.end(abandoned=True))
        self._ended()
        return True

    # writing (dropped once the turn ended)

    def emit(self, kind: str, **data: Any) -> None:
        with self._lock:
            if self._end is None:
                self._append(kind, data)

    def record_call(self, record: CallRecord) -> None:
        with self._lock:
            if self._end is None:
                self._calls.append(record)

    def record_tool(self, record: ToolRecord) -> None:
        with self._lock:
            if self._end is None:
                self._tools.append(record)
                if record.sql_id is not None:
                    self._sql_ids.append(record.sql_id)

    def record_action(self, record: ActionRecord, event: Mapping[str, Any]) -> None:
        with self._lock:
            if self._end is None:
                self._actions.append(record)
                self._append("app_action", dict(event))

    def guard(self, apply: Callable[[], None]) -> bool:
        """Run `apply` while the turn is alive; False (not run) after its end."""
        with self._lock:
            if self._end is not None:
                return False
            apply()
            return True

    def commit(self, apply: Callable[[], None], finish: TurnEnd | None = None) -> bool:
        """Change the history while the turn is alive, and end it in the same moment if `finish`
        is given. False (nothing changed) after its end."""
        with self._lock:
            if self._end is not None:
                return False
            apply()
            self._in_history = True
            if finish is not None:
                self._close(finish)
        if finish is not None:
            self._ended()
        return True

    def finish(self, end: TurnEnd) -> bool:
        """End the turn; False when it had already ended."""
        with self._lock:
            if self._end is not None:
                return False
            self._close(end)
        self._ended()
        return True

    def _append(self, kind: str, data: Mapping[str, Any]) -> None:
        self._events.append({"seq": len(self._events) + 1, "type": kind, **data})

    def _close(self, end: TurnEnd) -> None:
        if end.answer is not None:
            self._append("answer", {"text": end.answer, "sql_ids": list(self._sql_ids)})
        if end.outcome != DONE:
            self._append("error", {"code": end.code, "message": end.message})
        self._append("done", {"state": end.outcome, "reason": end.message})
        self._end = end
        self.ended_at = self._wall()

    def _ended(self) -> None:
        if self._on_end is None:
            return
        try:
            self._on_end(self)
        except Exception as error:
            LOGGER.warning(
                "chat turn %s: the end hook failed (%s)", self.turn_id, type(error).__name__
            )


# --- the loop --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Halt:
    """Why the remaining tool calls of a step are not run, and how the turn then ends."""

    result: str
    end: TurnEnd


@dataclass(frozen=True)
class _Paid:
    cost: Decimal
    estimated: bool


@dataclass(frozen=True)
class _Exit:
    end: TurnEnd
    commit: Callable[[], None] | None = None


def run_turn(
    conversation: Conversation, question: str, app_state: object, turn: Turn, deps: TurnDeps
) -> TurnEnd:
    """Run one question to its end (in the turn's thread). The turn is finished on return, unless
    it was already finished from outside (abandoned), in which case nothing of this run is kept."""
    runner = _Runner(conversation, question, app_state, turn, deps)
    try:
        exit_ = runner.run()
    except Exception as error:
        LOGGER.error("chat turn %s failed: %s", turn.turn_id, type(error).__name__)
        runner.withdraw("the turn failed")
        exit_ = _Exit(TurnEnd(FAILED, "internal_error", "the turn failed"))
    return runner.close(exit_)


class _Runner:
    def __init__(
        self,
        conversation: Conversation,
        question: str,
        app_state: object,
        turn: Turn,
        deps: TurnDeps,
    ) -> None:
        self.conversation = conversation
        self.question = question
        self.posted = app_state
        self.turn = turn
        self.deps = deps
        self.settings = deps.settings
        self.history = conversation.history
        self.pending: Pending | None = None
        self.state = AppState()
        self.tools_run = 0
        self.spend_failed = False
        self.calls_made = 0
        self.cost = Decimal(0)
        self.input_tokens = 0
        self.output_tokens = 0
        self.estimated = False

    # the turn

    def run(self) -> _Exit:
        self.turn.emit("status", text="thinking")
        validated = self.validate_state()
        if isinstance(validated, TurnEnd):
            return _Exit(validated)
        self.turn.app_state = json.loads(appstate.render(validated))
        self.pending = self.history.pending(self.question, appstate.state_message(validated))
        self.state = validated.state
        steps = self.settings.max_steps
        for step in range(1, steps + 1):
            outcome = self.step(step)
            if outcome is not None:
                return outcome
        return _Exit(TurnEnd(STOPPED, "step_limit", f"step limit reached ({steps})"))

    def close(self, exit_: _Exit) -> TurnEnd:
        if self.calls_made:
            self.emit_usage()
        if exit_.commit is not None:
            self.turn.commit(exit_.commit, finish=exit_.end)
        else:
            self.turn.finish(exit_.end)
        return exit_.end

    def validate_state(self) -> ValidatedState | TurnEnd:
        context = self.context(APP_STATE_TOOL, None, None)
        self.turn.phase = STATEMENT_PHASE
        try:
            return appstate.validate(self.posted, self.deps.lookups(context))
        except Exception as error:
            if self.turn.cancel.cancelled:
                return self.stop_end()
            LOGGER.warning(
                "chat turn %s: the app state lookups failed (%s)",
                self.turn.turn_id,
                type(error).__name__,
            )
            return TurnEnd(FAILED, "app_state_failed", "the app state could not be checked")
        finally:
            self.turn.phase = CALL_PHASE

    def step(self, step: int) -> _Exit | None:
        if self.turn.cancel.cancelled:
            return _Exit(self.stop_end())
        if step > 1:
            self.turn.emit("status", text=f"step {step} of at most {self.settings.max_steps}")
        messages = self.history.request_messages(self.pending)
        violations = conformance.check_stateless(messages)
        if violations:
            detail = "; ".join(str(violation) for violation in violations)
            return _Exit(
                TurnEnd(FAILED, "internal_history_error", f"internal history error: {detail}")
            )
        request = build_request(self.settings.request, self.deps.system_text, messages)
        plan = call_plan(self.conversation, request)
        reservation = self.reserve(plan)
        if isinstance(reservation, TurnEnd):
            return _Exit(reservation)
        called = self.call(request, plan, reservation)
        if called is None or isinstance(called, _Exit):
            return called
        return self.act(step, request, plan, called)

    # the model call

    def reserve(self, plan: CallPlan) -> Reservation | TurnEnd | None:
        ledger = self.deps.ledger
        if ledger is None:
            return None
        scope = conversation_scope(self.conversation, self.settings)
        granted = ledger.reserve(
            plan, [scope], limits(self.settings), conversation=self.conversation.chat_id
        )
        if isinstance(granted, CapRefusal):
            outcome = FAILED if granted.reason == budget.SPEND_LOG_UNWRITABLE else STOPPED
            return TurnEnd(outcome, granted.reason, granted.message)
        return granted

    def call(
        self, request: ModelRequest, plan: CallPlan, reservation: Reservation | None
    ) -> tuple[ModelResponse, _Paid] | _Exit | None:
        def on_text(piece: str) -> None:
            if piece:
                self.turn.emit("text", text=piece)

        try:
            response = self.conversation.model.respond(request, on_text, self.turn.cancel)
        except ModelCancelled:
            self.failed_call(request, self.settle(reservation, None, request.model), None)
            self.withdraw((self.turn.stop_cause or CANCELLED).message)
            return _Exit(self.stop_end())
        except UnparseableResponse as error:
            self.failed_call(request, self.settle(reservation, None, request.model), error)
            self.withdraw("the model's tool call was not valid JSON; asking again")
            return _Exit(self.spend_end()) if self.spend_failed else None
        except ChatModelError as error:
            if error.code in NOT_SENT:
                self.release(reservation)
            else:
                self.failed_call(request, self.settle(reservation, None, request.model), error)
            self.withdraw(error.message)
            return _Exit(TurnEnd(FAILED, error.code, error.message))
        except Exception as error:
            LOGGER.warning(
                "chat turn %s: the model call raised %s", self.turn.turn_id, type(error).__name__
            )
            self.failed_call(request, self.settle(reservation, None, request.model), None)
            self.withdraw("the model call failed")
            return _Exit(TurnEnd(FAILED, "model_error", "the model call failed"))
        usage = response.usage if response.usage_complete else None
        paid = self.settle(reservation, usage, response.model)
        self.count_usage(usage)
        return response, paid

    def settle(
        self, reservation: Reservation | None, usage: Mapping[str, Any] | None, model: str
    ) -> _Paid:
        self.calls_made += 1
        ledger = self.deps.ledger
        if reservation is None or ledger is None:
            return _Paid(Decimal(0), False)
        settlement = ledger.settle(reservation, usage, model)
        if not settlement.written:
            self.spend_failed = True
        if settlement.unknown_models:
            LOGGER.warning(
                "chat: priced unknown models at the highest row: %s", settlement.unknown_models
            )
        self.cost += settlement.cost_usd
        self.estimated = self.estimated or settlement.estimated
        return _Paid(settlement.cost_usd, settlement.estimated)

    def release(self, reservation: Reservation | None) -> None:
        if reservation is not None and self.deps.ledger is not None:
            self.deps.ledger.release(reservation)

    def count_usage(self, usage: Mapping[str, Any] | None) -> None:
        if usage is None:
            return
        try:
            every = budget.attempts(usage)
        except ValueError:
            return
        self.input_tokens += sum(attempt.context_input for attempt in every)
        self.output_tokens += sum(attempt.output_tokens for attempt in every)

    # the response

    def act(
        self, step: int, request: ModelRequest, plan: CallPlan, called: tuple[ModelResponse, _Paid]
    ) -> _Exit | None:
        response, paid = called
        if fallback_blocks(response.content):
            self.turn.emit("status", text=f"answer continued by {response.model}")
        kept = echo_content(response.content)
        calls = tool_calls(kept)
        stop = response.stop_reason
        if self.turn.cancel.cancelled:
            return self.not_kept(request, plan, response, paid, self.stop_end())
        if stop == "end_turn" and not calls and text_of(kept).strip():
            return self.answer(request, plan, response, paid, kept)
        if stop == "tool_use" and calls:
            return self.run_tools(step, request, plan, response, paid, kept, calls)
        if stop == "refusal":
            end = TurnEnd(REFUSED, "refusal", self.declined(response))
        elif stop == "max_tokens":
            end = TurnEnd(FAILED, "max_tokens", "the answer was cut off")
        elif stop == "end_turn" and not calls:
            end = TurnEnd(FAILED, "empty_answer", "the answer was empty")
        else:
            end = TurnEnd(FAILED, "unexpected_stop", f"the answer stopped unexpectedly ({stop})")
        return self.not_kept(request, plan, response, paid, end)

    def not_kept(
        self,
        request: ModelRequest,
        plan: CallPlan,
        response: ModelResponse,
        paid: _Paid,
        end: TurnEnd,
    ) -> _Exit:
        self.record_call(response, paid, kept=False)
        self.remember(request, plan, response, None)
        self.withdraw(end.message or "the response was not kept")
        return _Exit(self.spend_end() if self.spend_failed else end)

    def answer(
        self,
        request: ModelRequest,
        plan: CallPlan,
        response: ModelResponse,
        paid: _Paid,
        kept: list[dict[str, Any]],
    ) -> _Exit:
        message = {"role": "assistant", "content": kept}
        self.record_call(response, paid, kept=True)
        self.remember(request, plan, response, message)
        text = text_of(kept)
        if self.spend_failed:
            end = TurnEnd(FAILED, budget.SPEND_LOG_UNWRITABLE, self.spend_message(), answer=text)
        else:
            end = TurnEnd(DONE, answer=text)
        pending = self.pending
        return _Exit(end, lambda: self.history.commit(pending, [message]))

    def run_tools(
        self,
        step: int,
        request: ModelRequest,
        plan: CallPlan,
        response: ModelResponse,
        paid: _Paid,
        kept: list[dict[str, Any]],
        calls: list[dict[str, Any]],
    ) -> _Exit | None:
        message = {"role": "assistant", "content": kept}
        self.record_call(response, paid, kept=True)
        self.remember(request, plan, response, message)
        halt = None
        if self.spend_failed:
            halt = _Halt(budget.SPEND_LOG_UNWRITABLE, self.spend_end())
        elif step >= self.settings.max_steps:
            limit = self.settings.max_steps
            halt = _Halt(
                "step limit", TurnEnd(STOPPED, "step_limit", f"step limit reached ({limit})")
            )
        blocks = []
        for call in calls:
            if halt is None and self.turn.cancel.cancelled:
                halt = _Halt((self.turn.stop_cause or CANCELLED).result, self.stop_end())
            if halt is None and self.tools_run >= self.settings.max_tools:
                limit = self.settings.max_tools
                halt = _Halt(
                    "tool limit", TurnEnd(STOPPED, "tool_limit", f"tool limit reached ({limit})")
                )
            if halt is not None:
                result = stopped_result(halt.result)
                self.turn.record_tool(
                    ToolRecord(
                        str(call.get("name")),
                        call.get("input"),
                        error=f"turn stopped: {halt.result}",
                    )
                )
            else:
                self.tools_run += 1
                result = self.run_tool(call, response)
            blocks.append(tool_result_block(str(call["id"]), result))
        results = {"role": "user", "content": blocks}
        pending = self.pending
        if not self.turn.commit(lambda: self.history.commit(pending, [message, results])):
            return _Exit(self.stop_end())
        self.pending = None
        return None if halt is None else _Exit(halt.end)

    # tools

    def run_tool(self, call: Mapping[str, Any], response: ModelResponse) -> ToolResult:
        call_id, name, raw = str(call.get("id")), str(call.get("name")), call.get("input")
        if call_id in response.invalid_tool_ids:
            problem = "the tool input was not valid JSON"
            return self.invalid(name, call_id, raw, invalid_input_result(name, raw, problem))
        try:
            tool_input = validate_input(name, raw)
        except InvalidToolInput as error:
            return self.invalid(name, call_id, raw, error.result)
        shown = tool_input.model_dump(mode="json")
        self.turn.emit("tool_call", tool=name, call_id=call_id, input=shown)
        context = self.context(name, call_id, shown)
        outcome = self.dispatch(name, tool_input, context)
        if outcome.change is not None:
            self.apply(outcome.change)
        if not context.disclosed:
            failed = _error_text(outcome.result)
            self.turn.record_tool(ToolRecord(name, shown, error=failed))
        return outcome.result

    def dispatch(self, name: str, tool_input: ToolInput, context: ToolContext) -> ToolOutcome:
        executor = self.deps.tools.get(name)
        if executor is None:
            return ToolOutcome(error_result(f"the tool {name} is not available"))
        self.turn.phase = STATEMENT_PHASE
        try:
            return executor(tool_input, context)
        except Exception as error:
            LOGGER.warning("chat tool %s failed: %s", name, type(error).__name__)
            return ToolOutcome(error_result(f"the tool {name} failed"))
        finally:
            self.turn.phase = CALL_PHASE

    def invalid(self, name: str, call_id: str, raw: object, result: ToolResult) -> ToolResult:
        problem = _problem(result)
        self.turn.emit("tool_call", tool=name, call_id=call_id, input=raw, invalid=problem)
        self.turn.record_tool(ToolRecord(name, raw, error=problem))
        return result

    def apply(self, change: AppChange) -> None:
        self.state = change.after
        record = ActionRecord(
            change.tool, dict(change.input), change.before.as_json(), change.after.as_json()
        )
        self.turn.record_action(record, change.event())

    def context(self, tool: str, call_id: str | None, shown: Any) -> ToolContext:
        def disclose(context: ToolContext, statement: Statement) -> str:
            sql_id = self.conversation.next_sql_id()
            self.turn.emit(
                "sql",
                sql_id=sql_id,
                tool=context.tool,
                purpose=statement.purpose,
                sql=statement.sql,
                rows=statement.rows,
                more_rows=statement.more_rows,
                ms=statement.ms,
                error=statement.error,
                refused=statement.refused_rule is not None,
                rule=statement.refused_rule,
            )
            self.turn.record_tool(
                ToolRecord(
                    context.tool,
                    shown,
                    sql_id=sql_id,
                    sql=statement.sql,
                    row_count=statement.rows,
                    truncated=statement.truncated or statement.more_rows,
                    rows=statement.first_rows,
                    ms=statement.ms,
                    error=statement.error,
                    refused_rule=statement.refused_rule,
                )
            )
            return sql_id

        return ToolContext(tool, call_id, self.state, self.turn.cancel, disclose)

    # records and endings

    def record_call(self, response: ModelResponse, paid: _Paid, *, kept: bool) -> None:
        self.turn.record_call(
            CallRecord(
                stop_reason=response.stop_reason,
                model=response.model,
                usage=dict(response.usage),
                cost_usd=float(paid.cost),
                request_id=response.request_id,
                kept=kept,
                fallback_blocks=tuple(fallback_blocks(response.content)),
                input_transformations=response.input_transformations,
                estimated=paid.estimated,
            )
        )

    def failed_call(self, request: ModelRequest, paid: _Paid, error: ChatModelError | None) -> None:
        self.turn.record_call(
            CallRecord(
                stop_reason=None,
                model=request.model,
                usage={},
                cost_usd=float(paid.cost),
                request_id=None if error is None else error.request_id,
                kept=False,
                estimated=paid.estimated,
            )
        )

    def remember(
        self,
        request: ModelRequest,
        plan: CallPlan,
        response: ModelResponse,
        kept: Mapping[str, Any] | None,
    ) -> None:
        tokens = plan.input_tokens + request.max_tokens
        if response.usage_complete:
            with contextlib.suppress(ValueError):
                tokens = budget.context_tokens(response.usage)
        covered = budget.request_chars(request.messages)
        if kept is not None:
            covered += budget.request_chars(kept) + 1
        context = CallContext(tokens, covered)

        def store() -> None:
            self.conversation.context = context

        self.turn.guard(store)

    def emit_usage(self) -> None:
        ledger = self.deps.ledger
        scope = conversation_scope(self.conversation, self.settings)
        spent = Decimal(0) if ledger is None else ledger.spent(scope.key)
        context = self.conversation.context
        self.turn.emit(
            "usage",
            calls=self.calls_made,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cost_usd=float(self.cost),
            estimated=self.estimated,
            conversation_usd=float(spent),
            conversation_cap_usd=float(self.settings.session_max_usd),
            context_tokens=None if context is None else context.tokens,
            context_cap_tokens=self.settings.call_max_input_tokens,
        )

    def withdraw(self, reason: str) -> None:
        self.turn.emit("text_reset", reason=reason)

    def stop_end(self) -> TurnEnd:
        return (self.turn.stop_cause or CANCELLED).end()

    def spend_message(self) -> str:
        ledger = self.deps.ledger
        detail = None if ledger is None else ledger.unwritable_reason
        return f"live calls are off because the spend file is not usable: {detail}"

    def spend_end(self) -> TurnEnd:
        return TurnEnd(FAILED, budget.SPEND_LOG_UNWRITABLE, self.spend_message())

    @staticmethod
    def declined(response: ModelResponse) -> str:
        category = (response.stop_details or {}).get("category")
        return f"{DECLINED} Category: {category}." if category else DECLINED


def _problem(result: ToolResult) -> str:
    try:
        body = json.loads(result.text)
    except ValueError:
        return "invalid input"
    return str(body.get("problem") or body.get("error") or "invalid input")


def _error_text(result: ToolResult) -> str | None:
    if not result.is_error:
        return None
    return _problem(result)
