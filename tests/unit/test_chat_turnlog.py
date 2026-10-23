"""Unit tests of `app/backend/chat/turnlog.py`: one JSONL line per turn, redaction of held secrets,
write failures as one warning, and the warning for a dropped thinking block."""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.backend.chat import turnlog
from app.backend.chat.turnlog import (
    ActionRecord,
    CallRecord,
    ToolRecord,
    TurnLog,
    TurnRecord,
)

KEY = "sk-ant-test-CFBCHATMARKER-0000"
PASSWORD = "chat-pw-8c1f-test"
NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)
USAGE = {
    "input_tokens": 1200,
    "cache_creation_input_tokens": 2000,
    "cache_read_input_tokens": 15000,
    "output_tokens": 900,
}


def call(**overrides: Any) -> CallRecord:
    values: dict[str, Any] = {
        "stop_reason": "tool_use",
        "model": "claude-opus-5-5",
        "usage": USAGE,
        "cost_usd": 0.0358,
        "request_id": "req_1",
        "kept": True,
    }
    values.update(overrides)
    return CallRecord(**values)


def tool(**overrides: Any) -> ToolRecord:
    values: dict[str, Any] = {
        "name": "run_sql",
        "input": {"sql": 'SELECT "SCHOOL" FROM "TEAMS"', "purpose": "teams"},
        "sql_id": "S1",
        "sql": 'SELECT "SCHOOL" FROM "TEAMS"',
        "row_count": 2,
        "truncated": False,
        "rows": [["Ashford"], ["Brookvale"]],
        "ms": 12,
        "error": None,
    }
    values.update(overrides)
    return ToolRecord(**values)


def record(**overrides: Any) -> TurnRecord:
    values: dict[str, Any] = {
        "started_at": NOW,
        "ended_at": NOW,
        "chat_id": "c1",
        "turn_id": "t1",
        "target": "local",
        "namespace": "TEST_",
        "model": "claude-opus-5-5",
        "effort": "medium",
        "fallbacks": "default",
        "prompt_sha256": "ab" * 32,
        "stub": False,
        "question": "What if Brookvale wins at Ashford?",
        "app_state": {"page": "odds"},
        "calls": [
            call(),
            call(stop_reason="end_turn", cost_usd=0.02, usage={"output_tokens": 100}),
        ],
        "tools": [tool()],
        "app_actions": [
            ActionRecord("set_whatif", {"mode": "replace"}, {"whatif": []}, {"whatif": [401]})
        ],
        "answer": "41.2 %",
        "outcome": "done",
        "reason": None,
        "in_history": True,
    }
    values.update(overrides)
    return TurnRecord(**values)


def make_log(tmp_path: Path, **overrides: Any) -> tuple[TurnLog, Path]:
    path = tmp_path / "cache" / "chat-log.jsonl"
    values: dict[str, Any] = {"secrets": (KEY, PASSWORD), "clock": lambda: NOW}
    values.update(overrides)
    return TurnLog(path, **values), path


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_finished_turn_is_one_line_with_its_question_sql_rows_and_tokens(tmp_path: Path) -> None:
    log, path = make_log(tmp_path)
    assert log.write(record()) is True
    (line,) = lines(path)
    assert line["question"] == "What if Brookvale wins at Ashford?"
    assert line["app_state"] == {"page": "odds"}
    assert len(line["calls"]) == 2
    assert line["calls"][0]["usage"] == USAGE
    assert line["calls"][0]["cost_usd"] == 0.0358
    assert line["calls"][0]["request_id"] == "req_1"
    assert line["calls"][0]["kept"] is True
    assert line["tools"][0]["sql"] == 'SELECT "SCHOOL" FROM "TEAMS"'
    assert line["tools"][0]["rows"] == [["Ashford"], ["Brookvale"]]
    assert line["tools"][0]["row_count"] == 2
    assert line["app_actions"][0]["state_after"] == {"whatif": [401]}
    assert line["outcome"] == "done"
    assert line["in_history"] is True
    assert line["logged_at"] == "2026-10-23T12:00:00+00:00"


def test_the_totals_equal_the_sums_of_the_calls(tmp_path: Path) -> None:
    log, path = make_log(tmp_path)
    log.write(record())
    (line,) = lines(path)
    assert line["totals"]["model_calls"] == 2
    assert line["totals"]["tool_calls"] == 1
    assert line["totals"]["cost_usd"] == pytest.approx(0.0558)
    assert line["totals"]["input_tokens"] == 1200 + 2000 + 15000
    assert line["totals"]["output_tokens"] == 900 + 100


def test_lines_are_appended_not_replaced(tmp_path: Path) -> None:
    log, path = make_log(tmp_path)
    log.write(record(turn_id="t1"))
    log.write(record(turn_id="t2"))
    assert [line["turn_id"] for line in lines(path)] == ["t1", "t2"]


def test_turns_that_do_not_finish_keep_their_outcome_and_statements(tmp_path: Path) -> None:
    log, path = make_log(tmp_path)
    for outcome, reason in (
        ("cancelled", "cancelled by the presenter"),
        ("stopped", "step limit reached (8)"),
        ("failed", "the model API failed"),
        ("refused", "The model declined to answer this."),
    ):
        log.write(record(outcome=outcome, reason=reason, answer="", in_history=False))
    result = lines(path)
    assert [(line["outcome"], line["reason"]) for line in result][2] == (
        "failed",
        "the model API failed",
    )
    assert [line["outcome"] for line in result] == ["cancelled", "stopped", "failed", "refused"]
    assert all(line["tools"][0]["sql"] for line in result)
    assert all(line["in_history"] is False for line in result)


def test_a_refused_statement_is_logged_with_its_rule(tmp_path: Path) -> None:
    log, path = make_log(tmp_path)
    refused = tool(sql="DROP VIEW X", refused_rule="select_only", rows=[], row_count=0, error="no")
    log.write(record(tools=[refused]))
    (line,) = lines(path)
    assert line["tools"][0]["refused_rule"] == "select_only"
    assert line["tools"][0]["sql"] == "DROP VIEW X"


def test_only_the_first_20_rows_are_logged_and_values_become_json(tmp_path: Path) -> None:
    log, path = make_log(tmp_path)
    rows = [[n, datetime(2026, 1, 1, tzinfo=UTC)] for n in range(50)]
    log.write(record(tools=[tool(rows=rows, row_count=50, truncated=True)]))
    (line,) = lines(path)
    assert len(line["tools"][0]["rows"]) == 20
    assert line["tools"][0]["rows"][0] == [0, "2026-01-01T00:00:00+00:00"]
    assert line["tools"][0]["row_count"] == 50


def test_held_secrets_are_redacted_and_counted(tmp_path: Path) -> None:
    log, path = make_log(tmp_path)
    leaky = record(
        question=f"my password is {PASSWORD}",
        tools=[tool(error=f"connect failed for user X with password {PASSWORD} and key {KEY}")],
    )
    log.write(leaky)
    text = path.read_text(encoding="utf-8")
    assert PASSWORD not in text
    assert KEY not in text
    (line,) = lines(path)
    assert line["question"] == "my password is [REDACTED]"
    assert line["redactions"] == 3


def test_secrets_in_nested_values_and_keys_are_redacted(tmp_path: Path) -> None:
    log, path = make_log(tmp_path)
    log.write(record(app_state={PASSWORD: [PASSWORD, {"deep": PASSWORD}]}))
    assert PASSWORD not in path.read_text(encoding="utf-8")


def test_a_clean_line_has_no_redactions(tmp_path: Path) -> None:
    log, path = make_log(tmp_path)
    log.write(record())
    assert lines(path)[0]["redactions"] == 0


def test_very_short_secrets_are_ignored(tmp_path: Path) -> None:
    log, path = make_log(tmp_path, secrets=("", "ab", PASSWORD))
    log.write(record(question="a b ab"))
    assert lines(path)[0]["question"] == "a b ab"


def test_a_failing_write_is_one_warning_without_the_content(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory")
    log = TurnLog(blocker / "chat-log.jsonl", secrets=(KEY,), clock=lambda: NOW)
    with caplog.at_level(logging.WARNING, logger=turnlog.LOGGER.name):
        assert log.write(record(question="SECRET-QUESTION-TEXT")) is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "SECRET-QUESTION-TEXT" not in warnings[0].getMessage()
    assert "chat log" in warnings[0].getMessage()


def test_a_log_that_works_again_is_used_again(tmp_path: Path) -> None:
    log, path = make_log(tmp_path)
    assert log.write(record()) is True
    path.unlink()
    assert log.write(record(turn_id="t2")) is True
    assert lines(path)[0]["turn_id"] == "t2"


def input_transformation(reason: str, path_name: str = "messages.1.content.0") -> dict[str, str]:
    return {"type": "thinking_dropped", "path": path_name, "reason": reason}


def test_a_prefix_mismatch_is_logged_and_warned_about_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    log, path = make_log(tmp_path)
    calls = [
        call(input_transformations=[input_transformation("prefix_binding_mismatch")]),
        call(
            model="claude-opus-5",
            input_transformations=[input_transformation("model_binding_mismatch", "messages.1")],
        ),
    ]
    with caplog.at_level(logging.WARNING, logger=turnlog.LOGGER.name):
        log.write(record(calls=calls))
    (line,) = lines(path)
    assert line["calls"][0]["input_transformations"][0]["reason"] == "prefix_binding_mismatch"
    assert line["calls"][1]["input_transformations"][0]["reason"] == "model_binding_mismatch"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "prefix_binding_mismatch" in warnings[0]
    assert "call 1" in warnings[0]
    assert "model_binding_mismatch" not in warnings[0]


def test_unknown_transformations_are_logged_as_they_came_without_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    log, path = make_log(tmp_path)
    odd = {"type": "image_resized", "path": "x", "reason": "something_new"}
    with caplog.at_level(logging.WARNING, logger=turnlog.LOGGER.name):
        log.write(record(calls=[call(input_transformations=[odd])]))
    assert lines(path)[0]["calls"][0]["input_transformations"] == [odd]
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_fallback_blocks_and_kept_flags_are_logged_per_call(tmp_path: Path) -> None:
    log, path = make_log(tmp_path)
    block = {
        "type": "fallback",
        "from": {"model": "claude-opus-5-5"},
        "to": {"model": "claude-opus-5"},
    }
    log.write(record(calls=[call(fallback_blocks=[block], kept=False, estimated=True)]))
    (entry,) = lines(path)[0]["calls"]
    assert entry["fallback_blocks"] == [block]
    assert entry["kept"] is False
    assert entry["estimated"] is True


def test_concurrent_writers_never_interleave_lines(tmp_path: Path) -> None:
    log, path = make_log(tmp_path)
    errors: list[BaseException] = []

    def write(index: int) -> None:
        try:
            for turn in range(10):
                log.write(record(turn_id=f"t{index}-{turn}", question="q" * 5000))
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=write, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(lines(path)) == 80


def test_two_log_objects_on_one_file_do_not_interleave(tmp_path: Path) -> None:
    first, path = make_log(tmp_path)
    second = TurnLog(path, secrets=(KEY,), clock=lambda: NOW)

    def write(log: TurnLog, name: str) -> None:
        for turn in range(20):
            log.write(record(turn_id=f"{name}{turn}", question="q" * 5000))

    threads = [
        threading.Thread(target=write, args=(first, "a")),
        threading.Thread(target=write, args=(second, "b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(lines(path)) == 40


def test_stub_runs_say_so(tmp_path: Path) -> None:
    log, path = make_log(tmp_path)
    log.write(record(stub=True, model="stub"))
    assert lines(path)[0]["stub"] is True
