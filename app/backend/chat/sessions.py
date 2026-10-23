"""Conversations per browser session, turns in background threads, Stop and the turn deadline
(`chat/conversation`; decisions [13] and [25]). Thread-safe.

- **Conversations** belong to the browser session that started them (Phase 6's anonymous cookie);
  another session gets 404. Each one has its own model (one scripted model checks one
  conversation), history and caps, and lives in memory until the process ends.
- **Turns.** A question starts a turn in a thread of its own and returns at once; its events are
  read after a sequence number. One turn runs per conversation, and at most `max_running` per
  process (409 beyond). A turn counts as running exactly until its end is visible, so a client
  that sees `done` can post the next question at once. A question is checked first: 1 to 2,000
  characters, no configured secret (400, logged as `rejected`), and a first call that the caps
  allow (409, the budget pre-check).
- **Stop within a second, in every phase.** Cancel sets the cause and fires the turn's cancel token
  from the request thread: an open stream is closed, a running statement is aborted through the
  callback its tool registered, and the loop ends the turn itself. A call that cannot be
  interrupted (one still waiting for its response headers, or a statement whose abort does not
  return) is abandoned by the watchdog after a grace period that depends on the phase: the turn
  ends at once, and its thread, when the call returns, settles the reservation and changes nothing
  else (`loop.Turn` drops it). Abandoned threads are bounded by the client's timeout.
- **The deadline** (`CHAT_TURN_MAX_SECONDS`) is a stop with the cause "time limit"; the watchdog
  checks it every `watchdog_seconds` (tests pass None and call `tick()` with a fake clock).
"""

from __future__ import annotations

import logging
import secrets as random_ids
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.backend.chat import budget
from app.backend.chat.budget import CapRefusal
from app.backend.chat.loop import (
    CANCELLED,
    FAILED,
    STATEMENT_PHASE,
    Conversation,
    Turn,
    TurnDeps,
    TurnEnd,
    conversation_scope,
    first_call_refusal,
    run_turn,
    time_limit,
)
from app.backend.chat.model import ChatModel
from app.backend.chat.turnlog import MIN_SECRET_LENGTH, TurnLog, TurnRecord

LOGGER = logging.getLogger("app.backend.chat.sessions")
MAX_MESSAGE_CHARS = 2000
SECRET_IN_QUESTION = "the question contains a configured secret"
CONFLICTING_CREDENTIALS = "conflicting_credentials"


class TurnRefused(Exception):
    """A question that does not start a turn: the HTTP status, a code and a short message."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SessionSettings:
    """Concurrency and Stop (`CHAT_TURN_MAX_SECONDS` is in `LoopSettings`). A stopped turn whose
    thread does not end by itself is abandoned after `cancel_grace_seconds` while it waits on a
    model call (Stop within 1 s), and after `statement_grace_seconds` while a statement is being
    aborted (within 2 s, so that the aborted statement still reaches the transcript)."""

    max_running: int = 2
    max_message_chars: int = MAX_MESSAGE_CHARS
    cancel_grace_seconds: float = 0.6
    statement_grace_seconds: float = 1.5
    watchdog_seconds: float | None = 0.05


@dataclass(frozen=True)
class LogMeta:
    """What the turn log says about the process: the target, the namespace prefix, the system
    prompt's SHA-256, and whether the model is the stub."""

    target: str = "local"
    namespace: str = ""
    prompt_sha256: str = ""
    stub: bool = False


class ChatConversation:
    """One conversation of a browser session: its model-facing state and all of its turns."""

    def __init__(self, chat_id: str, session_id: str, core: Conversation) -> None:
        self.chat_id = chat_id
        self.session_id = session_id
        self.core = core
        self.turns: list[Turn] = []
        self.running: Turn | None = None


def _spawn(target: Callable[[], None], name: str) -> None:
    threading.Thread(target=target, name=name, daemon=True).start()


class ChatSessions:
    """The chat's conversations and turns for the whole process."""

    def __init__(
        self,
        deps: TurnDeps,
        model_factory: Callable[[], ChatModel],
        *,
        settings: SessionSettings | None = None,
        secrets: Sequence[str] = (),
        turn_log: TurnLog | None = None,
        meta: LogMeta | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = lambda: datetime.now(UTC),
        spawn: Callable[[Callable[[], None], str], None] = _spawn,
    ) -> None:
        self.deps = deps
        self.settings = settings or SessionSettings()
        self.disabled_reason: str | None = None
        self._model_factory = model_factory
        self._secrets = tuple(s for s in secrets if len(s) >= MIN_SECRET_LENGTH)
        self._turn_log = turn_log
        self._meta = meta or LogMeta()
        self._clock = clock
        self._wall = wall
        self._spawn = spawn
        self._lock = threading.Lock()
        self._conversations: dict[str, ChatConversation] = {}
        self._turns: dict[str, tuple[ChatConversation, Turn]] = {}
        self._running: dict[str, Turn] = {}
        self._closed = threading.Event()
        if self.settings.watchdog_seconds is not None:
            _spawn(self._watch, "chat-watchdog")

    # conversations

    def new_conversation(self, session_id: str) -> str:
        """Start a conversation for a browser session; returns its id."""
        chat_id = random_ids.token_hex(12)
        conversation = ChatConversation(
            chat_id, session_id, Conversation(chat_id, self._model_factory())
        )
        with self._lock:
            self._conversations[chat_id] = conversation
        return chat_id

    def conversation(self, session_id: str, chat_id: str) -> ChatConversation:
        """The conversation, if it belongs to this browser session (404 otherwise)."""
        with self._lock:
            conversation = self._conversations.get(chat_id)
        if conversation is None or conversation.session_id != session_id:
            raise TurnRefused(404, "not_found", "unknown conversation")
        return conversation

    def transcript(self, session_id: str, chat_id: str) -> dict[str, Any]:
        """Every question of the conversation (also those that never reached the history), with
        its outcome, answer, statements and app changes, and the spend against the caps."""
        conversation = self.conversation(session_id, chat_id)
        with self._lock:
            turns = list(conversation.turns)
        ledger = self.deps.ledger
        loop_settings = self.deps.settings
        key = conversation_scope(conversation.core, loop_settings).key
        spent = Decimal(0) if ledger is None else ledger.spent(key)
        context = conversation.core.context
        return {
            "chat_id": chat_id,
            "turns": [_summary(turn) for turn in turns],
            "spend_usd": float(spent),
            "cap_usd": float(loop_settings.session_max_usd),
            "context_tokens": None if context is None else context.tokens,
            "context_cap_tokens": loop_settings.call_max_input_tokens,
        }

    # turns

    @property
    def running_count(self) -> int:
        with self._lock:
            return self._live()

    def start_turn(self, session_id: str, chat_id: str, message: object, app_state: object) -> Turn:
        """Start a turn in the background (the route answers 202 with its id)."""
        conversation = self.conversation(session_id, chat_id)
        question = self._message(message)
        self._check_secrets(conversation, question, app_state)
        if self.disabled_reason is not None:
            raise TurnRefused(
                503, self.disabled_reason, f"the chat is disabled: {self.disabled_reason}"
            )
        with self._lock:
            self._check_free(conversation)
        refusal = first_call_refusal(conversation.core, question, self.deps)
        if refusal is not None:
            raise _budget_refusal(refusal)
        with self._lock:
            self._check_free(conversation)
            turn = Turn(
                random_ids.token_hex(12),
                chat_id,
                question,
                clock=self._clock,
                wall=self._wall,
                max_seconds=self.deps.settings.max_seconds,
                on_end=self._ended,
            )
            conversation.running = turn
            conversation.turns.append(turn)
            self._turns[turn.turn_id] = (conversation, turn)
            self._running[turn.turn_id] = turn
        try:
            self._spawn(
                lambda: self._run(conversation, turn, app_state), f"chat-turn-{turn.turn_id[:8]}"
            )
        except Exception as error:
            LOGGER.error("chat: a turn thread could not start (%s)", type(error).__name__)
            turn.finish(TurnEnd(FAILED, "not_started", "the turn could not start"))
            raise TurnRefused(503, "not_started", "the turn could not start") from None
        return turn

    def turn(self, session_id: str, turn_id: str) -> Turn:
        """The turn, if it belongs to this browser session (404 otherwise)."""
        with self._lock:
            found = self._turns.get(turn_id)
        if found is None or found[0].session_id != session_id:
            raise TurnRefused(404, "not_found", "unknown turn")
        return found[1]

    def poll(self, session_id: str, turn_id: str, after: int = 0) -> dict[str, Any]:
        """The turn's state and its events after sequence number `after`."""
        turn = self.turn(session_id, turn_id)
        state, events = turn.events(after)
        return {"turn_id": turn_id, "chat_id": turn.chat_id, "state": state, "events": events}

    def cancel(self, session_id: str, turn_id: str) -> Turn:
        """Stop the turn (a no-op when it has ended)."""
        turn = self.turn(session_id, turn_id)
        turn.stop(CANCELLED)
        return turn

    # the watchdog

    def tick(self) -> None:
        """Stop turns past their deadline, and abandon stopped turns past the grace period."""
        now = self._clock()
        with self._lock:
            running = list(self._running.values())
        for turn in running:
            if turn.end is not None:
                continue
            requested = turn.stop_requested_at
            if requested is None:
                if turn.deadline is not None and now >= turn.deadline:
                    turn.stop(time_limit(self.deps.settings.max_seconds))
            elif now >= requested + self._grace(turn):
                turn.abandon()

    def _grace(self, turn: Turn) -> float:
        if turn.phase == STATEMENT_PHASE:
            return self.settings.statement_grace_seconds
        return self.settings.cancel_grace_seconds

    def close(self) -> None:
        """Stop the watchdog (turn threads are daemons and end with their calls)."""
        self._closed.set()

    def _watch(self) -> None:
        interval = self.settings.watchdog_seconds or 0.05
        while not self._closed.wait(interval):
            try:
                self.tick()
            except Exception as error:
                LOGGER.warning("chat watchdog failed: %s", type(error).__name__)

    # inside

    def _run(self, conversation: ChatConversation, turn: Turn, app_state: object) -> None:
        run_turn(conversation.core, turn.question, app_state, turn, self.deps)

    def _ended(self, turn: Turn) -> None:
        with self._lock:
            self._running.pop(turn.turn_id, None)
            found = self._turns.get(turn.turn_id)
            if found is not None and found[0].running is turn:
                found[0].running = None
        end = turn.end
        if end is not None and end.code == CONFLICTING_CREDENTIALS:
            self.disabled_reason = CONFLICTING_CREDENTIALS
        self._log(turn)

    def _live(self) -> int:
        return sum(1 for turn in self._running.values() if turn.end is None)

    def _check_free(self, conversation: ChatConversation) -> None:
        running = conversation.running
        if running is not None and running.end is None:
            raise TurnRefused(
                409, "turn_running", f"a turn of this conversation is running ({running.turn_id})"
            )
        if self._live() >= self.settings.max_running:
            limit = self.settings.max_running
            raise TurnRefused(409, "chat_busy", f"at most {limit} chat turns run at once")

    def _message(self, message: object) -> str:
        limit = self.settings.max_message_chars
        if not isinstance(message, str) or not message.strip() or len(message) > limit:
            raise TurnRefused(400, "invalid_message", f"a message is 1 to {limit:,} characters")
        return message

    def _check_secrets(
        self, conversation: ChatConversation, question: str, app_state: object
    ) -> None:
        texts = [question, *_strings(app_state)]
        if not any(secret in text for secret in self._secrets for text in texts):
            return
        now = self._wall()
        self._write(
            TurnRecord(
                **self._record_meta(),
                started_at=now,
                ended_at=now,
                chat_id=conversation.chat_id,
                turn_id=random_ids.token_hex(12),
                question=question,
                app_state=None,
                outcome="rejected",
                in_history=False,
                reason=SECRET_IN_QUESTION,
            )
        )
        raise TurnRefused(400, "secret_in_question", SECRET_IN_QUESTION)

    def _log(self, turn: Turn) -> None:
        end = turn.end
        if self._turn_log is None or end is None:
            return
        self._write(
            TurnRecord(
                **self._record_meta(),
                started_at=turn.started_at,
                ended_at=turn.ended_at or self._wall(),
                chat_id=turn.chat_id,
                turn_id=turn.turn_id,
                question=turn.question,
                app_state=turn.app_state,
                outcome=end.outcome,
                in_history=turn.in_history,
                answer=end.answer or "",
                reason=end.message,
                calls=turn.calls,
                tools=turn.tools,
                app_actions=turn.actions,
            )
        )

    def _record_meta(self) -> dict[str, Any]:
        request = self.deps.settings.request
        return {
            "target": self._meta.target,
            "namespace": self._meta.namespace,
            "model": request.model,
            "effort": request.effort,
            "fallbacks": "default" if request.fallbacks else "off",
            "prompt_sha256": self._meta.prompt_sha256,
            "stub": self._meta.stub,
        }

    def _write(self, record: TurnRecord) -> None:
        if self._turn_log is None:
            return
        try:
            self._turn_log.write(record)
        except Exception as error:
            LOGGER.warning(
                "chat log write failed (%s); the turn is not affected", type(error).__name__
            )


def _budget_refusal(refusal: CapRefusal) -> TurnRefused:
    if refusal.reason == budget.DAILY_CAP:
        return TurnRefused(409, "chat_daily_budget_exhausted", refusal.message)
    if refusal.reason in (budget.CONVERSATION_CAP, budget.CONTEXT_CAP):
        return TurnRefused(409, "chat_budget_exhausted", refusal.message)
    return TurnRefused(503, refusal.reason, refusal.message)


def _strings(value: object) -> Iterable[str]:
    """Every string in a JSON-like value, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def _summary(turn: Turn) -> dict[str, Any]:
    state, events = turn.events()
    end = turn.end
    return {
        "turn_id": turn.turn_id,
        "question": turn.question,
        "state": state,
        "reason": None if end is None else end.message,
        "answer": None if end is None else end.answer,
        "in_history": turn.in_history,
        "statements": [
            _without(event, "seq", "type") for event in events if event["type"] == "sql"
        ],
        "app_actions": [
            _without(event, "seq", "type") for event in events if event["type"] == "app_action"
        ],
    }


def _without(event: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key not in keys}
