"""Unit tests of `app/backend/chat/sessions.py` (`chat/conversation`, `chat/safety`,
`chat/budget`): conversations per browser session, one running turn per conversation and two per
process, events after a sequence number, Stop in every phase, the turn deadline (with a fake clock
and with the real watchdog), the input check and the budget pre-check."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.backend.chat import budget
from app.backend.chat.loop import LoopSettings, Turn, TurnDeps
from app.backend.chat.model import (
    ChatModel,
    ChatModelError,
    ConflictingCredentials,
    ModelResponse,
    RequestSettings,
)
from app.backend.chat.scripted import Hold, ScriptedChatModel, ScriptStep
from app.backend.chat.sessions import ChatSessions, LogMeta, SessionSettings, TurnRefused
from app.backend.chat.spend import SpendLedger
from app.backend.chat.turnlog import TurnLog
from tests.unit.chat_loop_fakes import (
    BROOKVALE,
    SYSTEM,
    DisclosingLookups,
    HeadersWait,
    QueryTool,
    tool_table,
)

D = Decimal
SESSION, OTHER = "browser-a", "browser-b"
SECRET = "pw-CFBCHAT-0042"
SQL = {"sql": 'SELECT 1 FROM "TEAMS"', "purpose": "count"}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Models:
    """A model factory: one model per new conversation, in order; later ones answer 'ok'."""

    def __init__(self, *models: ChatModel) -> None:
        self.made: list[ChatModel] = []
        self._queue = list(models)

    def __call__(self) -> ChatModel:
        model = self._queue.pop(0) if self._queue else ScriptedChatModel([ScriptStep.say("ok")] * 5)
        self.made.append(model)
        return model


def scripted(*steps: ScriptStep) -> ScriptedChatModel:
    return ScriptedChatModel(list(steps))


def make(
    models: Models,
    *,
    clock: Callable[[], float] | None = None,
    watchdog: float | None = None,
    settings: LoopSettings | None = None,
    ledger: SpendLedger | None = None,
    tools: dict[str, Any] | None = None,
    turn_log: TurnLog | None = None,
    max_running: int = 2,
    grace: float = 0.3,
    statement_grace: float = 1.5,
) -> ChatSessions:
    deps = TurnDeps(
        SYSTEM, DisclosingLookups, tools or tool_table(), settings or LoopSettings(), ledger
    )
    return ChatSessions(
        deps,
        models,
        settings=SessionSettings(
            max_running=max_running,
            cancel_grace_seconds=grace,
            statement_grace_seconds=statement_grace,
            watchdog_seconds=watchdog,
        ),
        secrets=(SECRET, "sk-ant-test-CFBCHATMARKER-0000"),
        turn_log=turn_log,
        meta=LogMeta(target="local", namespace="TEST_", prompt_sha256="abc", stub=False),
        clock=clock or time.monotonic,
    )


@pytest.fixture
def closing() -> Iterator[list[ChatSessions]]:
    made: list[ChatSessions] = []
    yield made
    for sessions in made:
        sessions.close()


def wait_end(turn: Turn, seconds: float = 5.0) -> float:
    started = time.monotonic()
    while turn.end is None:
        assert time.monotonic() - started < seconds, f"turn still {turn.state}"
        time.sleep(0.002)
    return time.monotonic() - started


def log_lines(path: Path, count: int) -> list[dict[str, Any]]:
    """The log's lines once it holds `count` (it is written right after a turn's end)."""
    deadline = time.monotonic() + 5
    while True:
        lines = path.read_text().splitlines() if path.exists() else []
        if len(lines) >= count or time.monotonic() > deadline:
            return [json.loads(line) for line in lines]
        time.sleep(0.002)


def ask(sessions: ChatSessions, chat_id: str, question: str = "Q", **options: Any) -> Turn:
    return sessions.start_turn(SESSION, chat_id, question, options.get("app_state"))


def kinds(turn: Turn) -> list[str]:
    return [event["type"] for event in turn.events()[1]]


def refused(call: Callable[[], object]) -> TurnRefused:
    with pytest.raises(TurnRefused) as caught:
        call()
    return caught.value


# --- conversations and turns -------------------------------------------------------------------


def test_a_turn_runs_in_the_background_and_its_events_are_read_after_a_number(
    closing: list[ChatSessions],
) -> None:
    sessions = make(Models(scripted(ScriptStep.call("run_sql", SQL), ScriptStep.say("Done."))))
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    turn = ask(sessions, chat, "How many teams?")
    wait_end(turn)
    view = sessions.poll(SESSION, turn.turn_id, after=0)
    assert view["state"] == "done"
    assert [e["seq"] for e in view["events"]] == list(range(1, len(view["events"]) + 1))
    later = sessions.poll(SESSION, turn.turn_id, after=3)
    assert [e["seq"] for e in later["events"]] == list(range(4, len(view["events"]) + 1))
    assert sessions.running_count == 0


def test_a_conversation_of_another_browser_session_is_unknown(closing: list[ChatSessions]) -> None:
    sessions = make(Models())
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    assert refused(lambda: sessions.start_turn(OTHER, chat, "Q", None)).status == 404
    assert refused(lambda: sessions.transcript(OTHER, chat)).status == 404
    turn = ask(sessions, chat)
    wait_end(turn)
    assert refused(lambda: sessions.poll(OTHER, turn.turn_id, 0)).status == 404
    assert refused(lambda: sessions.cancel(OTHER, turn.turn_id)).status == 404
    assert refused(lambda: sessions.poll(SESSION, "no-such-turn", 0)).status == 404


@pytest.mark.parametrize("message", ["", "   ", "x" * 2001, None])
def test_a_message_is_one_to_two_thousand_characters(
    closing: list[ChatSessions], message: object
) -> None:
    sessions = make(Models())
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    error = refused(lambda: sessions.start_turn(SESSION, chat, message, None))
    assert error.status == 400


def test_one_turn_runs_at_a_time_in_a_conversation(closing: list[ChatSessions]) -> None:
    hold = Hold()
    sessions = make(Models(scripted(ScriptStep.say("first", hold=hold), ScriptStep.say("x"))))
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    turn = ask(sessions, chat)
    assert hold.reached.wait(5)
    error = refused(lambda: ask(sessions, chat, "again"))
    assert error.status == 409
    assert turn.turn_id in error.message
    other = sessions.new_conversation(SESSION)
    accepted = ask(sessions, other)
    wait_end(accepted)
    assert accepted.state == "done"
    hold.release.set()
    wait_end(turn)
    assert turn.state == "done"


def test_at_most_two_turns_run_in_the_process(closing: list[ChatSessions]) -> None:
    holds = [Hold(), Hold()]
    sessions = make(Models(*(scripted(ScriptStep.say("x", hold=h)) for h in holds)))
    closing.append(sessions)
    turns = [ask(sessions, sessions.new_conversation(SESSION)) for _ in holds]
    assert all(h.reached.wait(5) for h in holds)
    third = sessions.new_conversation(SESSION)
    error = refused(lambda: ask(sessions, third))
    assert (error.status, error.code) == (409, "chat_busy")
    for h in holds:
        h.release.set()
    for turn in turns:
        wait_end(turn)
    assert ask(sessions, third) is not None


# --- the input check ---------------------------------------------------------------------------


def test_a_question_that_contains_a_secret_is_refused_before_the_model(
    closing: list[ChatSessions], tmp_path: Path
) -> None:
    log = TurnLog(tmp_path / "chat-log.jsonl", secrets=(SECRET,))
    model = scripted(ScriptStep.say("never"))
    sessions = make(Models(model), turn_log=log)
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    error = refused(lambda: ask(sessions, chat, f"is the password {SECRET}?"))
    assert (error.status, error.message) == (400, "the question contains a configured secret")
    in_state = refused(lambda: ask(sessions, chat, "Q", app_state={"page": SECRET}))
    assert in_state.status == 400
    assert model.calls == 0
    lines = [json.loads(line) for line in (tmp_path / "chat-log.jsonl").read_text().splitlines()]
    assert [line["outcome"] for line in lines] == ["rejected", "rejected"]
    assert SECRET not in json.dumps(lines)
    assert "[REDACTED]" in lines[0]["question"]
    assert lines[0]["redactions"] >= 1


# --- Stop and the deadline ---------------------------------------------------------------------


def test_cancel_during_a_streaming_response_ends_the_turn_within_a_second(
    closing: list[ChatSessions],
) -> None:
    hold = Hold()
    model = scripted(ScriptStep.say("Brookvale's chances", hold=hold), ScriptStep.say("ok"))
    sessions = make(Models(model), watchdog=0.02)
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    turn = ask(sessions, chat, "Q1")
    assert hold.reached.wait(5)
    sessions.cancel(SESSION, turn.turn_id)
    assert wait_end(turn) < 1.0
    assert turn.state == "cancelled"
    seen = kinds(turn)
    assert seen.index("text_reset") > seen.index("text")
    assert hold.closed.is_set()
    second = ask(sessions, chat, "Q2")
    wait_end(second)
    assert second.state == "done"
    assert "Q1" not in json.dumps(model.requests[-1].messages)
    model.assert_conformant()


def test_cancel_while_a_call_waits_for_its_headers_abandons_it(
    closing: list[ChatSessions], tmp_path: Path
) -> None:
    ledger = SpendLedger(tmp_path / "chat-spend.jsonl", process="test")
    inner = scripted(ScriptStep.say("A1"), ScriptStep.say("A3"))
    late = ModelResponse(
        content=({"type": "text", "text": "late"},),
        stop_reason="end_turn",
        model="claude-opus-5-5",
        usage={"input_tokens": 100, "output_tokens": 10},
    )
    waiting = HeadersWait(inner, late=late)
    waiting._first = False
    sessions = make(Models(waiting), watchdog=0.02, ledger=ledger)
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    first = ask(sessions, chat, "Q1")
    wait_end(first)
    waiting._first = True
    second = ask(sessions, chat, "Q2")
    assert waiting.waiting.wait(5)
    sessions.cancel(SESSION, second.turn_id)
    assert wait_end(second) < 1.0
    assert second.state == "cancelled"
    assert second.end is not None and second.end.abandoned
    third = ask(sessions, chat, "Q3")
    wait_end(third)
    assert third.state == "done"
    request = inner.requests[-1].messages
    assert "Q2" not in json.dumps(request)
    assert [m["role"] for m in request] == ["user", "system", "assistant", "user", "system"]
    inner.assert_conformant()
    history = list(sessions.conversation(SESSION, chat).core.history.messages)
    waiting.release.set()
    assert waiting.returned.wait(5)
    deadline = time.monotonic() + 5
    while ledger.reserved_total() != 0:
        assert time.monotonic() < deadline
        time.sleep(0.002)
    assert list(sessions.conversation(SESSION, chat).core.history.messages) == history
    lines = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    assert len(lines) == 3
    assert sorted(line["usage"]["input_tokens"] for line in lines) == [100, 1000, 1000]
    assert not any(line["estimated"] for line in lines)


def test_cancel_while_a_statement_runs_aborts_it(closing: list[ChatSessions]) -> None:
    slow = QueryTool(block=True)
    model = scripted(
        ScriptStep.call("run_sql", SQL),
        ScriptStep.call("describe_view", {"view": "WHATIF_OUTCOMES"}),
        ScriptStep.call("run_sql", SQL),
        ScriptStep.say("ok"),
    )
    sessions = make(Models(model), watchdog=0.02, tools=tool_table(describe_view=slow))
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    turn = ask(sessions, chat, "Q1")
    assert slow.waiting.wait(5)
    sessions.cancel(SESSION, turn.turn_id)
    assert wait_end(turn) < 2.0
    assert turn.state == "cancelled"
    assert slow.aborted.is_set()
    assert model.calls == 2
    errors = [e["error"] for e in turn.events()[1] if e["type"] == "sql"]
    assert errors == [None, "statement aborted"]
    second = ask(sessions, chat, "Q2")
    wait_end(second)
    assert second.state == "done"
    model.assert_conformant()


def test_the_deadline_stops_a_turn_like_a_cancel(closing: list[ChatSessions]) -> None:
    clock = FakeClock()
    hold = Hold(seconds=60)
    model = scripted(ScriptStep.say("words", hold=hold), ScriptStep.say("ok"))
    sessions = make(Models(model), clock=clock, settings=LoopSettings(max_seconds=2))
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    turn = ask(sessions, chat, "Q1")
    assert hold.reached.wait(5)
    clock.now += 1.9
    sessions.tick()
    assert turn.end is None
    clock.now += 0.2
    sessions.tick()
    wait_end(turn)
    assert turn.state == "stopped"
    assert turn.end is not None and turn.end.message == "time limit reached (2 s)"
    assert not turn.end.abandoned
    assert "text_reset" in kinds(turn)
    second = ask(sessions, chat, "Q2")
    wait_end(second)
    assert second.state == "done"
    model.assert_conformant()


def test_a_call_that_ignores_the_deadline_is_abandoned_after_the_grace(
    closing: list[ChatSessions],
) -> None:
    clock = FakeClock()
    waiting = HeadersWait(scripted(ScriptStep.say("A2")))
    sessions = make(Models(waiting), clock=clock, settings=LoopSettings(max_seconds=2), grace=0.5)
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    turn = ask(sessions, chat, "Q1")
    assert waiting.waiting.wait(5)
    clock.now += 2.0
    sessions.tick()
    assert turn.end is None
    assert turn.stop_cause is not None
    clock.now += 0.4
    sessions.tick()
    assert turn.end is None
    clock.now += 0.2
    sessions.tick()
    assert turn.state == "stopped"
    assert turn.end is not None and turn.end.abandoned
    assert turn.end.message == "time limit reached (2 s)"
    assert kinds(turn)[-3:] == ["text_reset", "error", "done"]
    assert sessions.running_count == 0
    second = ask(sessions, chat, "Q2")
    wait_end(second)
    assert second.state == "done"
    waiting.release.set()
    assert waiting.returned.wait(5)


def test_a_statement_that_ignores_the_abort_gets_the_longer_grace(
    closing: list[ChatSessions],
) -> None:
    clock = FakeClock()
    stuck = QueryTool(stuck=True)
    model = scripted(ScriptStep.call("describe_view", {"view": "WHATIF_OUTCOMES"}))
    sessions = make(
        Models(model),
        clock=clock,
        tools=tool_table(describe_view=stuck),
        grace=0.6,
        statement_grace=1.5,
    )
    closing.append(sessions)
    turn = ask(sessions, sessions.new_conversation(SESSION))
    assert stuck.waiting.wait(5)
    assert turn.phase == "statement"
    sessions.cancel(SESSION, turn.turn_id)
    clock.now += 0.7
    sessions.tick()
    assert turn.end is None
    clock.now += 0.9
    sessions.tick()
    assert turn.state == "cancelled"
    assert turn.end is not None and turn.end.abandoned
    stuck.aborted.set()


def test_a_turn_whose_thread_cannot_start_is_refused_and_not_left_running(
    closing: list[ChatSessions],
) -> None:
    def no_threads(_target: Callable[[], None], _name: str) -> None:
        raise RuntimeError("can't start new thread")

    deps = TurnDeps(SYSTEM, DisclosingLookups, tool_table(), LoopSettings(), None)
    sessions = ChatSessions(
        deps, Models(), settings=SessionSettings(watchdog_seconds=None), spawn=no_threads
    )
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    error = refused(lambda: ask(sessions, chat))
    assert error.status == 503
    assert sessions.running_count == 0
    (turn,) = sessions.conversation(SESSION, chat).turns
    assert turn.state == "failed"


def test_the_real_watchdog_enforces_the_deadline(closing: list[ChatSessions]) -> None:
    hold = Hold(seconds=60)
    sessions = make(
        Models(scripted(ScriptStep.say("x", hold=hold))),
        watchdog=0.02,
        settings=LoopSettings(max_seconds=0.3),
    )
    closing.append(sessions)
    turn = ask(sessions, sessions.new_conversation(SESSION))
    assert wait_end(turn) < 2.0
    assert turn.state == "stopped"


# --- the turn log ------------------------------------------------------------------------------


def test_each_turn_writes_one_log_line_whatever_its_outcome(
    closing: list[ChatSessions], tmp_path: Path
) -> None:
    log_path = tmp_path / "chat-log.jsonl"
    hold = Hold()
    model = scripted(
        ScriptStep.call("run_sql", SQL),
        ScriptStep.say("Done."),
        ScriptStep.say("never", hold=hold),
        ScriptStep(stop_reason="refusal", stop_details={"type": "refusal", "category": "bio"}),
    )
    sessions = make(Models(model), turn_log=TurnLog(log_path), watchdog=0.02)
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    done = ask(sessions, chat, "Q1", app_state={"focus_team": BROOKVALE})
    wait_end(done)
    cancelled = ask(sessions, chat, "Q2")
    assert hold.reached.wait(5)
    sessions.cancel(SESSION, cancelled.turn_id)
    wait_end(cancelled)
    declined = ask(sessions, chat, "Q3")
    wait_end(declined)
    lines = log_lines(log_path, 3)
    assert [line["outcome"] for line in lines] == ["done", "cancelled", "refused"]
    first = lines[0]
    assert first["question"] == "Q1"
    assert first["in_history"] is True
    assert first["app_state"]["app_state"]["focus_team"]["school"] == "Brookvale"
    assert [tool["name"] for tool in first["tools"]] == ["app_state", "run_sql"]
    assert first["tools"][1]["sql"] == SQL["sql"]
    assert first["totals"]["model_calls"] == 2
    assert (first["target"], first["namespace"], first["prompt_sha256"]) == (
        "local",
        "TEST_",
        "abc",
    )
    assert lines[1]["in_history"] is False
    assert "bio" in lines[2]["reason"]


# --- the budget --------------------------------------------------------------------------------


def test_a_spent_conversation_refuses_the_next_question_and_a_new_one_is_accepted(
    closing: list[ChatSessions], tmp_path: Path
) -> None:
    ledger = SpendLedger(tmp_path / "chat-spend.jsonl", process="test")
    settings = LoopSettings(request=RequestSettings(fallbacks=False), session_max_usd=D("0.30"))
    ten = {"input_tokens": 10_000, "output_tokens": 3_000}
    sessions = make(
        Models(scripted(ScriptStep.say("A1", usage=ten))), settings=settings, ledger=ledger
    )
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    wait_end(ask(sessions, chat, "Q1"))
    error = refused(lambda: ask(sessions, chat, "Q2"))
    assert (error.status, error.code) == (409, "chat_budget_exhausted")
    fresh = ask(sessions, sessions.new_conversation(SESSION), "Q1")
    wait_end(fresh)
    assert fresh.state == "done"


def test_the_daily_cap_is_checked_before_any_model_call(
    closing: list[ChatSessions], tmp_path: Path
) -> None:
    path = tmp_path / "chat-spend.jsonl"
    today = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    path.write_text(
        json.dumps({"ts": "2026-10-23T08:00:00.000Z", "cost_usd": 0.98})
        + "\n"
        + json.dumps({"ts": "2026-10-22T08:00:00.000Z", "cost_usd": 5.0})
        + "\n"
    )
    now = [today]
    ledger = SpendLedger(path, process="test", clock=lambda: now[0])
    model = scripted(ScriptStep.say("A1"))
    sessions = make(Models(model), settings=LoopSettings(daily_max_usd=D("1.00")), ledger=ledger)
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    error = refused(lambda: ask(sessions, chat))
    assert (error.status, error.code) == (409, "chat_daily_budget_exhausted")
    assert model.calls == 0
    now[0] = today + timedelta(days=1)
    turn = ask(sessions, chat)
    wait_end(turn)
    assert turn.state == "done"


def test_conflicting_credentials_disable_the_chat(closing: list[ChatSessions]) -> None:
    guard = ConflictingCredentials("an Authorization header on a request")
    sessions = make(Models(scripted(ScriptStep(raises=guard))))
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    turn = ask(sessions, chat)
    wait_end(turn)
    assert turn.state == "failed"
    assert sessions.disabled_reason == "conflicting_credentials"
    error = refused(lambda: ask(sessions, chat))
    assert (error.status, error.code) == (503, "conflicting_credentials")


def test_a_failing_model_fails_only_its_turn(closing: list[ChatSessions]) -> None:
    sessions = make(
        Models(
            scripted(
                ScriptStep(raises=ChatModelError("server", "the Anthropic API failed")),
                ScriptStep.say("ok"),
            )
        )
    )
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    failed = ask(sessions, chat)
    wait_end(failed)
    assert failed.state == "failed"
    second = ask(sessions, chat)
    wait_end(second)
    assert second.state == "done"


# --- the transcript ----------------------------------------------------------------------------


def test_the_transcript_shows_every_question_with_its_outcome(
    closing: list[ChatSessions], tmp_path: Path
) -> None:
    ledger = SpendLedger(tmp_path / "chat-spend.jsonl", process="test")
    sessions = make(
        Models(
            scripted(
                ScriptStep.call("run_sql", SQL),
                ScriptStep.say("There are 2."),
                ScriptStep(stop_reason="refusal"),
            )
        ),
        ledger=ledger,
    )
    closing.append(sessions)
    chat = sessions.new_conversation(SESSION)
    wait_end(ask(sessions, chat, "How many teams?"))
    wait_end(ask(sessions, chat, "Something else"))
    transcript = sessions.transcript(SESSION, chat)
    assert transcript["chat_id"] == chat
    turns = transcript["turns"]
    assert [t["question"] for t in turns] == ["How many teams?", "Something else"]
    assert [t["state"] for t in turns] == ["done", "refused"]
    assert [t["in_history"] for t in turns] == [True, False]
    assert turns[0]["answer"] == "There are 2."
    assert [s["sql"] for s in turns[0]["statements"]] == [SQL["sql"]]
    assert transcript["spend_usd"] > 0
    assert transcript["cap_usd"] == pytest.approx(2.0)
    assert transcript["context_cap_tokens"] == 150_000


def test_the_daily_scope_names_its_reason() -> None:
    assert budget.daily_scope(D("1")).reason == "daily_cap"
