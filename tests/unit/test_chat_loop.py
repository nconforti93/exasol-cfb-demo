"""Unit tests of `app/backend/chat/loop.py` (`chat/conversation`, `chat/budget`): the turn loop over
the scripted model, fake tools and fake lookups. Every kind of stop is followed by a next question
whose request passes the scripted model's full conformance check."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.backend.chat import budget
from app.backend.chat.loop import (
    CANCELLED,
    Conversation,
    LoopSettings,
    Turn,
    TurnDeps,
    run_turn,
    time_limit,
)
from app.backend.chat.model import (
    ChatModel,
    ChatModelError,
    ModelResponse,
    RequestSettings,
    UnparseableResponse,
    echo_content,
)
from app.backend.chat.scripted import Hold, ScriptedCall, ScriptedChatModel, ScriptStep
from app.backend.chat.spend import SpendLedger
from tests.unit.chat_loop_fakes import (
    ASHFORD,
    BROOKVALE,
    GAME,
    SYSTEM,
    DisclosingLookups,
    FailingLookups,
    HeadersWait,
    QueryTool,
    SetWhatIfTool,
    tool_table,
)

D = Decimal
SQL = {"sql": 'SELECT "SCHOOL" FROM "GAMES"', "purpose": "the game"}
FALLBACK = {
    "type": "fallback",
    "from": {"model": "claude-opus-5-5"},
    "to": {"model": "claude-opus-5"},
    "trigger": {"type": "refusal"},
}
THINKING = {"type": "thinking", "thinking": "", "signature": "sig"}
POINT = {"season": 2026, "week_seq": 10}


class Harness:
    """One conversation over a model, the fake tools and lookups, and optionally a ledger."""

    def __init__(
        self,
        steps: Sequence[ScriptStep] = (),
        *,
        model: ChatModel | None = None,
        settings: LoopSettings | None = None,
        ledger: SpendLedger | None = None,
        tools: dict[str, Any] | None = None,
        lookups: type[DisclosingLookups] = DisclosingLookups,
    ) -> None:
        self.scripted = ScriptedChatModel(list(steps))
        self.conversation = Conversation("c1", model or self.scripted)
        self.tools = tools if tools is not None else tool_table()
        self.ledger = ledger
        self.deps = TurnDeps(SYSTEM, lookups, self.tools, settings or LoopSettings(), ledger)
        self.count = 0

    def turn(self, question: str = "How did Ashford do?") -> Turn:
        self.count += 1
        return Turn(f"turn{self.count}", "c1", question)

    def ask(
        self,
        question: str = "How did Ashford do?",
        app_state: object = None,
        *,
        during: Callable[[Turn], None] | None = None,
    ) -> Turn:
        turn = self.turn(question)
        helper = None
        if during is not None:
            helper = threading.Thread(target=during, args=(turn,), daemon=True)
            helper.start()
        run_turn(self.conversation, question, app_state, turn, self.deps)
        if helper is not None:
            helper.join(5)
        return turn

    def tool(self, name: str) -> QueryTool:
        tool = self.tools[name]
        assert isinstance(tool, QueryTool)
        return tool

    def messages(self) -> list[dict[str, Any]]:
        return list(self.conversation.history.messages)

    def last_request(self) -> list[dict[str, Any]]:
        return self.scripted.requests[-1].messages

    def conformant(self) -> None:
        self.scripted.assert_conformant()
        assert self.scripted.violations == []


def kinds(turn: Turn) -> list[str]:
    return [event["type"] for event in turn.events()[1]]


def events_of(turn: Turn, kind: str) -> list[dict[str, Any]]:
    return [event for event in turn.events()[1] if event["type"] == kind]


def first_positions(turn: Turn, wanted: Sequence[str]) -> list[int]:
    seen = kinds(turn)
    return [seen.index(kind) for kind in wanted]


def result_texts(message: dict[str, Any]) -> list[str]:
    return [
        block["content"][0]["text"]
        for block in message["content"]
        if block["type"] == "tool_result"
    ]


def ledger_at(tmp_path: Path) -> SpendLedger:
    return SpendLedger(tmp_path / "chat-spend.jsonl", process="test")


def spend_lines(ledger: SpendLedger) -> list[dict[str, Any]]:
    return [json.loads(line) for line in ledger.path.read_text().splitlines()]


def stop_when(event: threading.Event, cause: Any = CANCELLED) -> Callable[[Turn], None]:
    def stopper(turn: Turn) -> None:
        assert event.wait(5)
        turn.stop(cause)

    return stopper


# --- a question, its steps, its answer ---------------------------------------------------------


def test_a_question_gets_an_answer_with_its_steps_shown() -> None:
    h = Harness([ScriptStep.call("run_sql", SQL), ScriptStep.say("Ashford won 21-14.")])
    turn = h.ask()
    assert turn.state == "done"
    wanted = ["status", "tool_call", "sql", "text", "usage", "answer", "done"]
    positions = first_positions(turn, wanted)
    assert positions == sorted(positions)
    assert kinds(turn)[-2:] == ["answer", "done"]
    (answer,) = events_of(turn, "answer")
    assert answer["text"] == "Ashford won 21-14."
    assert answer["sql_ids"] == ["S1"]
    (sql,) = events_of(turn, "sql")
    assert sql["sql"] == SQL["sql"]
    assert sql["tool"] == "run_sql"
    assert sql["purpose"] == "the game"
    assert [m["role"] for m in h.messages()] == ["user", "system", "assistant", "user", "assistant"]
    assert turn.in_history
    h.conformant()


def test_a_follow_up_question_extends_the_conversation_without_editing_it() -> None:
    h = Harness([ScriptStep.call("run_sql", SQL), ScriptStep.say("A1"), ScriptStep.say("A2")])
    h.ask("Q1")
    h.ask("Q2", {"page": "odds"})
    first, second, third = h.scripted.requests
    assert third.system == first.system and third.tools == first.tools
    assert third.messages[: len(second.messages)] == second.messages
    assert third.messages[len(second.messages)] == {
        "role": "assistant",
        "content": echo_content(h.scripted.responses[1].content),
    }
    assert third.messages[-2] == {"role": "user", "content": [{"type": "text", "text": "Q2"}]}
    assert third.messages[-1]["role"] == "system"
    h.conformant()


def test_statements_are_numbered_per_conversation() -> None:
    h = Harness([ScriptStep.call("run_sql", SQL), ScriptStep.say("A1")] * 2)
    h.ask("Q1")
    turn = h.ask("Q2")
    (answer,) = events_of(turn, "answer")
    assert answer["sql_ids"] == ["S2"]


def test_an_answer_without_sql_lists_no_statement() -> None:
    turn = Harness([ScriptStep.say("Hello")]).ask("Hi")
    (answer,) = events_of(turn, "answer")
    assert answer["sql_ids"] == []


# --- the app state -----------------------------------------------------------------------------


def test_the_app_state_follows_the_question_and_its_lookups_are_disclosed() -> None:
    h = Harness([ScriptStep.say("Brookvale is at 41.2%.")])
    turn = h.ask("What about this team?", {"point": POINT, "focus_team": BROOKVALE})
    first = h.scripted.requests[0].messages
    assert first[0] == {
        "role": "user",
        "content": [{"type": "text", "text": "What about this team?"}],
    }
    state = json.loads(first[1]["content"])["app_state"]
    assert state["focus_team"] == {"id": BROOKVALE, "school": "Brookvale"}
    sql = events_of(turn, "sql")
    assert [entry["tool"] for entry in sql] == ["app_state", "app_state"]
    (answer,) = events_of(turn, "answer")
    assert answer["sql_ids"] == ["S1", "S2"]
    assert turn.app_state == json.loads(first[1]["content"])


def test_a_lookup_failure_fails_the_turn_before_any_model_call() -> None:
    h = Harness([ScriptStep.say("never")], lookups=FailingLookups)
    turn = h.ask("Q", {"focus_team": ASHFORD})
    assert turn.state == "failed"
    assert turn.end is not None and turn.end.code == "app_state_failed"
    assert "the chat connection is gone" not in json.dumps(turn.events()[1])
    assert h.scripted.calls == 0
    assert h.messages() == []


def test_an_app_change_is_emitted_and_the_next_tool_sees_the_state_after() -> None:
    change = {"conditions": [{"game_id": GAME, "home_team_wins": False}], "mode": "replace"}
    h = Harness(
        [
            ScriptStep.calls(
                ScriptedCall("set_whatif", change), ScriptedCall("set_whatif", change)
            ),
            ScriptStep.say("Done."),
        ]
    )
    turn = h.ask("Show the what-if", {"point": POINT, "page": "predictions"})
    actions = events_of(turn, "app_action")
    assert len(actions) == 2
    assert actions[0]["state_before"]["page"] == "predictions"
    assert actions[0]["state_after"]["page"] == "odds"
    assert actions[0]["state_after"]["conditions"] == [{"game_id": GAME, "winner": "away"}]
    tool = h.tools["set_whatif"]
    assert isinstance(tool, SetWhatIfTool)
    assert tool.states[0].page == "predictions"
    assert tool.states[1].page == "odds"
    assert [action.tool for action in turn.actions] == ["set_whatif", "set_whatif"]
    h.conformant()


def test_tool_input_that_does_not_match_the_schema_is_sent_back_not_run() -> None:
    bad = {"conditions": "Brookvale wins", "mode": "replace"}
    h = Harness([ScriptStep.call("set_whatif", bad), ScriptStep.say("Sorry.")])
    turn = h.ask()
    assert turn.state == "done"
    (result,) = result_texts(h.messages()[3])
    body = json.loads(result)
    assert body["kind"] == "error"
    assert "Brookvale wins" in body["INVALID_INPUT"]
    assert "conditions" in body["problem"]
    assert h.messages()[3]["content"][0]["is_error"] is True
    assert events_of(turn, "app_action") == []
    tool = h.tools["set_whatif"]
    assert isinstance(tool, SetWhatIfTool) and tool.states == []
    (call,) = events_of(turn, "tool_call")
    assert call["invalid"]
    h.conformant()


def test_a_failing_tool_and_a_missing_tool_answer_with_an_error_and_the_turn_goes_on() -> None:
    tools = tool_table(run_sql=QueryTool(fail=True))
    del tools["rooting_guide"]
    h = Harness(
        [
            ScriptStep.calls(
                ScriptedCall("run_sql", SQL), ScriptedCall("rooting_guide", {"team_id": ASHFORD})
            ),
            ScriptStep.say("ok"),
        ],
        tools=tools,
    )
    turn = h.ask()
    assert turn.state == "done"
    failed, missing = (json.loads(text) for text in result_texts(h.messages()[3]))
    assert failed["kind"] == "error" and "hunter2" not in json.dumps(failed)
    assert missing["kind"] == "error" and "rooting_guide" in missing["error"]
    h.conformant()


# --- limits ------------------------------------------------------------------------------------


def test_a_turn_that_keeps_calling_tools_stops_at_the_step_limit() -> None:
    h = Harness([ScriptStep.call("run_sql", SQL)] * 8 + [ScriptStep.say("ok")])
    turn = h.ask("Q1")
    assert h.scripted.calls == 8
    assert turn.state == "stopped"
    assert turn.end is not None and turn.end.message == "step limit reached (8)"
    assert len(h.tool("run_sql").ran) == 7
    (last,) = result_texts(h.messages()[-1])
    assert "turn stopped: step limit" in last
    second = h.ask("Q2")
    assert second.state == "done"
    tail = h.last_request()[-2]
    assert tail["content"][0]["type"] == "tool_result"
    assert tail["content"][-1] == {"type": "text", "text": "Q2"}
    assert h.last_request()[-1]["role"] == "system"
    h.conformant()


def test_a_response_with_more_tool_calls_than_the_turn_allows_is_answered_in_full() -> None:
    calls = [ScriptedCall("run_sql", {"sql": f"SELECT {n}", "purpose": "p"}) for n in range(5)]
    h = Harness(
        [ScriptStep.calls(*calls), ScriptStep.say("ok")], settings=LoopSettings(max_tools=3)
    )
    turn = h.ask()
    assert turn.state == "stopped"
    assert turn.end is not None and turn.end.message == "tool limit reached (3)"
    assert len(h.tool("run_sql").ran) == 3
    texts = result_texts(h.messages()[-1])
    assert len(texts) == 5
    assert all("turn stopped: tool limit" in text for text in texts[3:])
    assert all("turn stopped" not in text for text in texts[:3])
    assert h.scripted.calls == 1
    assert h.ask("Q2").state == "done"
    h.conformant()


# --- stop reasons ------------------------------------------------------------------------------


def test_a_declined_request_runs_no_tool_and_withdraws_its_text() -> None:
    declined = ScriptStep(
        content=(
            {"type": "text", "text": "Brookvale's chances are"},
            {"type": "tool_use", "id": "t1", "name": "run_sql", "input": SQL},
        ),
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": "cyber"},
    )
    h = Harness([declined, ScriptStep.say("fine")])
    turn = h.ask("Q1")
    assert turn.state == "refused"
    assert turn.end is not None and turn.end.message is not None
    assert "The model declined to answer this." in turn.end.message
    assert "cyber" in turn.end.message
    seen = kinds(turn)
    assert seen.index("text_reset") > max(i for i, kind in enumerate(seen) if kind == "text")
    assert h.tool("run_sql").ran == []
    assert h.messages() == []
    h.ask("Q2")
    text = json.dumps(h.last_request())
    assert "Q1" not in text and "Brookvale's chances are" not in text
    h.conformant()


@pytest.mark.parametrize(
    ("stop", "message"),
    [
        ("max_tokens", "the answer was cut off"),
        ("something_new", "the answer stopped unexpectedly (something_new)"),
    ],
)
def test_a_cut_off_or_unexpected_response_runs_no_tool(stop: str, message: str) -> None:
    cut = ScriptStep(
        content=(
            {"type": "text", "text": "partial words"},
            {"type": "tool_use", "id": "t1", "name": "run_sql", "input": {"sql": "SELECT"}},
        ),
        stop_reason=stop,
    )
    h = Harness([cut, ScriptStep.say("ok")])
    turn = h.ask("Q1")
    assert turn.state == "failed"
    assert turn.end is not None and turn.end.message == message
    assert "text_reset" in kinds(turn)
    assert h.tool("run_sql").ran == []
    h.ask("Q2")
    assert "partial words" not in json.dumps(h.last_request())
    h.conformant()


@pytest.mark.parametrize("ending", ["refusal", "error", "cancel"])
def test_a_turn_that_ends_before_its_first_kept_response_leaves_the_history_unchanged(
    ending: str,
) -> None:
    hold = Hold()
    second = {
        "refusal": ScriptStep(stop_reason="refusal"),
        "error": ScriptStep(raises=ChatModelError("server", "the Anthropic API failed")),
        "cancel": ScriptStep.say("never shown", hold=hold),
    }[ending]
    h = Harness([ScriptStep.say("A1"), second, ScriptStep.say("A3")])
    h.ask("Q1")
    before = h.messages()
    turn = h.ask("Q2", during=stop_when(hold.reached) if ending == "cancel" else None)
    assert turn.state == {"refusal": "refused", "error": "failed", "cancel": "cancelled"}[ending]
    assert not turn.in_history
    assert h.messages() == before
    third = h.ask("Q3")
    assert third.state == "done"
    request = h.last_request()
    assert "Q2" not in json.dumps(request)
    assert [m["role"] for m in request] == ["user", "system", "assistant", "user", "system"]
    h.conformant()


def test_an_empty_answer_is_not_kept() -> None:
    for content in ((), (THINKING,), (THINKING, {"type": "text", "text": "  \n"})):
        h = Harness([ScriptStep(content=content, stop_reason="end_turn"), ScriptStep.say("ok")])
        turn = h.ask("Q1")
        assert turn.state == "failed"
        assert turn.end is not None and turn.end.code == "empty_answer"
        assert h.messages() == []
        assert h.ask("Q2").state == "done"
        h.conformant()


def test_a_blank_text_block_of_a_tool_step_is_not_echoed() -> None:
    step = ScriptStep(
        content=(
            THINKING,
            {"type": "text", "text": "\n\n"},
            {"type": "tool_use", "id": "t1", "name": "run_sql", "input": SQL},
        )
    )
    h = Harness([step, ScriptStep.say("ok")])
    assert h.ask().state == "done"
    assert [b["type"] for b in h.messages()[2]["content"]] == ["thinking", "tool_use"]
    h.conformant()


def test_a_tool_step_whose_calls_were_all_before_the_fallback_is_unexpected() -> None:
    step = ScriptStep(
        content=(
            {"type": "tool_use", "id": "t1", "name": "run_sql", "input": SQL},
            FALLBACK,
            {"type": "text", "text": "Let me see."},
        ),
        stop_reason="tool_use",
    )
    h = Harness([step, ScriptStep.say("ok")])
    turn = h.ask("Q1")
    assert turn.state == "failed"
    assert h.tool("run_sql").ran == []
    assert h.ask("Q2").state == "done"
    h.conformant()


def test_an_open_tail_takes_the_next_question_after_a_response_that_was_not_kept() -> None:
    h = Harness(
        [ScriptStep.call("run_sql", SQL), ScriptStep(stop_reason="refusal"), ScriptStep.say("ok")]
    )
    first = h.ask("Q1")
    assert first.state == "refused"
    assert first.in_history
    assert h.messages()[-1]["content"][0]["type"] == "tool_result"
    assert h.ask("Q2").state == "done"
    assert h.last_request()[3]["content"][-1] == {"type": "text", "text": "Q2"}
    h.conformant()


# --- the fallback ------------------------------------------------------------------------------


def test_a_response_finished_by_the_fallback_model_keeps_only_what_came_after_the_switch() -> None:
    switched = ScriptStep(
        content=(
            {"type": "text", "text": "Brookvale's chances are"},
            THINKING,
            {"type": "tool_use", "id": "t1", "name": "run_sql", "input": SQL},
            FALLBACK,
            {"type": "tool_use", "id": "t2", "name": "describe_view", "input": {"view": "TEAMS"}},
        ),
        model="claude-opus-5",
    )
    h = Harness([switched, ScriptStep.say("done")])
    turn = h.ask()
    assert turn.state == "done"
    assert h.tool("run_sql").ran == []
    assert len(h.tool("describe_view").ran) == 1
    echoed = h.scripted.requests[1].messages[2]
    assert [b["type"] for b in echoed["content"]] == ["text", "fallback", "tool_use"]
    assert echoed["content"][2]["id"] == "t2"
    statuses = [event["text"] for event in events_of(turn, "status")]
    assert any("claude-opus-5" in text for text in statuses)
    assert "text_reset" not in kinds(turn)
    assert turn.calls[0].fallback_blocks == (FALLBACK,)
    h.conformant()


# --- failures of the model ---------------------------------------------------------------------


def test_a_failing_model_fails_the_turn_with_a_short_message() -> None:
    h = Harness([ScriptStep(raises=ChatModelError("server", "the Anthropic API failed"))])
    turn = h.ask()
    assert turn.state == "failed"
    (error,) = events_of(turn, "error")
    assert (error["code"], error["message"]) == ("server", "the Anthropic API failed")
    assert "text_reset" in kinds(turn)


def test_tool_input_that_is_not_json_is_answered_and_settled_at_the_worst_case(
    tmp_path: Path,
) -> None:
    step = ScriptStep(
        content=(
            {"type": "text", "text": "Let me look."},
            {"type": "tool_use", "id": "bad", "name": "run_sql", "input": {}},
        ),
        stop_reason="tool_use",
        invalid_tool_ids=frozenset({"bad"}),
        usage_complete=False,
    )
    h = Harness([step, ScriptStep.say("ok")], ledger=ledger_at(tmp_path))
    assert h.ask().state == "done"
    (text,) = result_texts(h.messages()[3])
    body = json.loads(text)
    assert "INVALID_INPUT" in body and "JSON" in body["problem"]
    assert h.tool("run_sql").ran == []
    lines = spend_lines(h.ledger) if h.ledger else []
    assert [line["estimated"] for line in lines] == [True, False]
    h.conformant()


def test_an_unparseable_response_is_asked_again_as_a_step(tmp_path: Path) -> None:
    h = Harness(
        [ScriptStep(raises=UnparseableResponse()), ScriptStep.say("ok")],
        ledger=ledger_at(tmp_path),
    )
    turn = h.ask()
    assert turn.state == "done"
    assert h.scripted.calls == 2
    assert "text_reset" in kinds(turn)
    assert h.ledger is not None
    assert [line["estimated"] for line in spend_lines(h.ledger)] == [True, False]
    assert [call.kept for call in turn.calls] == [False, True]


def test_a_history_the_api_would_refuse_is_never_sent(tmp_path: Path) -> None:
    h = Harness([ScriptStep.say("A1"), ScriptStep.say("never")], ledger=ledger_at(tmp_path))
    h.ask("Q1")
    h.conversation.history._messages.append(
        {"role": "user", "content": [{"type": "text", "text": "x"}]}
    )
    turn = h.ask("Q2")
    assert turn.state == "failed"
    assert turn.end is not None and turn.end.code == "internal_history_error"
    assert h.scripted.calls == 1
    assert h.ledger is not None and len(spend_lines(h.ledger)) == 1
    assert h.ledger.reserved_total() == 0


def test_a_changed_earlier_message_is_caught_by_the_scripted_model(tmp_path: Path) -> None:
    h = Harness([ScriptStep.say("A1"), ScriptStep.say("never")], ledger=ledger_at(tmp_path))
    h.ask("Q1")
    h.conversation.history._messages[0] = {
        "role": "user",
        "content": [{"type": "text", "text": "Q0"}],
    }
    turn = h.ask("Q2")
    assert turn.state == "failed"
    assert turn.end is not None and turn.end.code == "internal_history_error"
    assert h.ledger is not None and h.ledger.reserved_total() == 0
    assert len(spend_lines(h.ledger)) == 1


# --- cancel and the time limit -----------------------------------------------------------------


def test_a_turn_cancelled_before_its_first_call_makes_no_call() -> None:
    h = Harness([ScriptStep.say("never")])
    turn = h.turn()
    turn.stop(CANCELLED)
    run_turn(h.conversation, "Q", None, turn, h.deps)
    assert turn.state == "cancelled"
    assert h.scripted.calls == 0
    assert h.messages() == []


def test_cancel_during_a_streaming_response_withdraws_its_text() -> None:
    hold = Hold()
    h = Harness([ScriptStep.say("Brookvale's chances", hold=hold), ScriptStep.say("ok")])
    turn = h.ask("Q1", during=stop_when(hold.reached))
    assert turn.state == "cancelled"
    seen = kinds(turn)
    assert "text" in seen and seen.index("text_reset") > seen.index("text")
    assert hold.closed.is_set()
    assert h.messages() == []
    second = h.ask("Q2")
    assert second.state == "done"
    assert "Q1" not in json.dumps(h.last_request())
    h.conformant()


def test_cancel_while_a_statement_runs_aborts_it_and_answers_the_rest(tmp_path: Path) -> None:
    slow = QueryTool(block=True)
    tools = tool_table(describe_view=slow)
    h = Harness(
        [
            ScriptStep.call("run_sql", SQL),
            ScriptStep.calls(
                ScriptedCall("describe_view", {"view": "WHATIF_OUTCOMES"}),
                ScriptedCall("run_sql", SQL),
            ),
            ScriptStep.call("run_sql", SQL),
            ScriptStep.say("ok"),
        ],
        tools=tools,
        ledger=ledger_at(tmp_path),
    )
    turn = h.ask("Q1", during=stop_when(slow.waiting))
    assert turn.state == "cancelled"
    assert slow.aborted.is_set()
    assert h.scripted.calls == 2
    sql = events_of(turn, "sql")
    assert [entry["error"] for entry in sql] == [None, "statement aborted"]
    aborted, rest = result_texts(h.messages()[-1])
    assert "statement aborted" in aborted
    assert "turn stopped: cancelled" in rest
    assert len(h.tool("run_sql").ran) == 1
    second = h.ask("Q2")
    assert second.state == "done"
    assert len(h.tool("run_sql").ran) == 2
    assert h.ledger is not None and h.ledger.reserved_total() == 0
    h.conformant()


def test_a_turn_stops_at_its_time_limit_like_a_cancel() -> None:
    hold = Hold()
    h = Harness([ScriptStep.say("thinking out loud", hold=hold), ScriptStep.say("ok")])
    turn = h.ask("Q1", during=stop_when(hold.reached, time_limit(2)))
    assert turn.state == "stopped"
    assert turn.end is not None and turn.end.message == "time limit reached (2 s)"
    assert turn.end.code == "time_limit"
    assert "text_reset" in kinds(turn)
    assert h.ask("Q2").state == "done"
    h.conformant()


def test_an_abandoned_call_is_settled_when_it_returns_and_changes_nothing(tmp_path: Path) -> None:
    late = ModelResponse(
        content=({"type": "text", "text": "late answer"},),
        stop_reason="end_turn",
        model="claude-opus-5-5",
        usage={"input_tokens": 10, "output_tokens": 5},
    )
    waiting = HeadersWait(ScriptedChatModel([ScriptStep.say("A3")]), late=late)
    h = Harness(model=waiting, ledger=ledger_at(tmp_path))
    turn = h.turn("Q1")
    runner = threading.Thread(
        target=run_turn, args=(h.conversation, "Q1", None, turn, h.deps), daemon=True
    )
    runner.start()
    assert waiting.waiting.wait(5)
    turn.stop(CANCELLED)
    assert turn.abandon()
    state, events = turn.events()
    assert state == "cancelled"
    assert [event["type"] for event in events][-3:] == ["text_reset", "error", "done"]
    waiting.release.set()
    runner.join(5)
    assert not runner.is_alive()
    assert turn.events()[1] == events
    assert h.messages() == []
    assert h.ledger is not None and h.ledger.reserved_total() == 0
    (line,) = spend_lines(h.ledger)
    assert line["estimated"] is False


# --- the budget --------------------------------------------------------------------------------

TEN_CENTS = {"input_tokens": 10_000, "output_tokens": 3_000}


def test_a_conversation_stops_before_the_call_that_could_pass_its_cost_cap(tmp_path: Path) -> None:
    settings = LoopSettings(request=RequestSettings(fallbacks=False), session_max_usd=D("0.80"))
    steps = [ScriptStep.say("A1", usage=TEN_CENTS)] + [
        ScriptStep.call("run_sql", SQL, usage=TEN_CENTS)
    ] * 10
    h = Harness(steps, settings=settings, ledger=ledger_at(tmp_path))
    h.ask("Q1")
    turn = h.ask("Q2")
    assert h.scripted.calls == 1 + 5
    assert turn.state == "stopped"
    assert turn.end is not None and turn.end.code == budget.CONVERSATION_CAP
    assert "conversation cost cap" in (turn.end.message or "")
    assert h.ledger is not None
    assert h.ledger.spent("conversation:c1") == D("0.60")
    assert h.ask("Q3").state == "stopped"
    assert h.scripted.calls == 6
    h.conformant()


def test_a_conversation_stops_at_its_context_cap(tmp_path: Path) -> None:
    settings = LoopSettings(call_max_input_tokens=18_000)
    steps = [ScriptStep.say("A1", usage={"input_tokens": 12_000, "output_tokens": 3_000})] + [
        ScriptStep.call("run_sql", SQL, usage={"input_tokens": 16_000, "output_tokens": 3_000})
    ] * 3
    h = Harness(steps, settings=settings, ledger=ledger_at(tmp_path))
    h.ask("Q1")
    turn = h.ask("Q2")
    assert h.scripted.calls == 2
    assert turn.state == "stopped"
    assert turn.end is not None and turn.end.code == budget.CONTEXT_CAP
    h.conformant()


def test_a_failed_spend_write_stops_live_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = ledger_at(tmp_path)

    def broken(_line: str) -> bool:
        ledger._fail("disk full")
        return False

    monkeypatch.setattr(ledger, "_append", broken)
    h = Harness(
        [ScriptStep.call("run_sql", SQL), ScriptStep.call("run_sql", SQL), ScriptStep.say("ok")],
        ledger=ledger,
    )
    turn = h.ask("Q1")
    assert h.scripted.calls == 1
    assert h.tool("run_sql").ran == []
    (text,) = result_texts(h.messages()[-1])
    assert "turn stopped: spend_log_unwritable" in text
    assert turn.state == "failed"
    assert turn.end is not None and turn.end.code == budget.SPEND_LOG_UNWRITABLE
    second = h.ask("Q2")
    assert second.state == "failed"
    assert second.end is not None and second.end.code == budget.SPEND_LOG_UNWRITABLE
    assert h.scripted.calls == 1
    h.conformant()


def test_the_usage_event_reports_the_spend_against_the_caps(tmp_path: Path) -> None:
    h = Harness([ScriptStep.say("A1", usage=TEN_CENTS)], ledger=ledger_at(tmp_path))
    turn = h.ask()
    (usage,) = events_of(turn, "usage")
    assert usage["cost_usd"] == pytest.approx(0.10)
    assert usage["conversation_usd"] == pytest.approx(0.10)
    assert usage["conversation_cap_usd"] == pytest.approx(2.0)
    assert usage["context_tokens"] == 13_000
    assert usage["context_cap_tokens"] == 150_000
    assert usage["estimated"] is False


def test_stub_mode_reserves_nothing_and_reports_no_cost() -> None:
    turn = Harness([ScriptStep.say("A1", usage=TEN_CENTS)]).ask()
    (usage,) = events_of(turn, "usage")
    assert usage["cost_usd"] == 0
    assert turn.calls[0].cost_usd == 0


def test_the_turn_records_its_calls_and_statements_for_the_log() -> None:
    h = Harness([ScriptStep.call("run_sql", SQL), ScriptStep.say("ok")])
    turn = h.ask("Q", {"point": POINT})
    assert [call.kept for call in turn.calls] == [True, True]
    assert [call.request_id for call in turn.calls] == ["req_script_1", "req_script_2"]
    assert [(tool.name, tool.sql_id) for tool in turn.tools] == [
        ("app_state", "S1"),
        ("run_sql", "S2"),
    ]
    assert turn.tools[1].sql == SQL["sql"]
    assert turn.tools[1].rows == ((1,),)
