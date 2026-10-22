import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vs.host import ledger
from vs.host.ledger import Entry, Ledger, LedgerConfig, LedgerError, LedgerRefused

PLAN = "add-cfbd-vs-foundation"


def entry(**overrides: object) -> Entry:
    values: dict[str, object] = {
        "ts": "2026-10-22T15:00:00.000Z",
        "plan": PLAN,
        "target": "aws",
        "run": "20261022T150000Z",
        "slice_key": "/games?seasonType=both&year=2024",
        "cost": 1,
        "status": 200,
        "attempts": 1,
        "ms": 812,
        "bytes": 12345,
        "remaining_before": 3000,
        "remaining_after": 2999,
        "error": None,
    }
    values.update(overrides)
    return Entry(**values)  # type: ignore[arg-type]


def test_append_writes_one_json_line_with_exactly_the_documented_fields(tmp_path: Path) -> None:
    path = tmp_path / "cache" / "live-calls.jsonl"
    Ledger(path).append(entry())
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert tuple(json.loads(lines[0])) == ledger.FIELDS
    assert ledger.FIELDS == (
        "ts",
        "plan",
        "target",
        "run",
        "slice_key",
        "status",
        "attempts",
        "cost",
        "ms",
        "bytes",
        "remaining_before",
        "remaining_after",
        "error",
    )


def test_entries_round_trip_in_append_order(tmp_path: Path) -> None:
    book = Ledger(tmp_path / "live-calls.jsonl")
    first = entry()
    second = entry(slice_key="/info", cost=0, status=200, bytes=None, error=None)
    book.append(first)
    book.append(second)
    assert book.entries() == [first, second]


def test_a_missing_ledger_has_no_entries(tmp_path: Path) -> None:
    assert Ledger(tmp_path / "absent.jsonl").entries() == []


def test_blank_lines_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "live-calls.jsonl"
    path.write_text(entry().to_line() + "\n\n", encoding="utf-8")
    assert Ledger(path).entries() == [entry()]


@pytest.mark.parametrize(
    "line",
    [
        "{not json",
        "[1, 2]",
        json.dumps({"ts": "2026-10-22T15:00:00.000Z"}),
        entry().to_line().replace('"cost": 1', '"cost": -1'),
        entry().to_line().replace('"cost": 1', '"cost": "1"'),
        entry().to_line().replace('"cost": 1', '"cost": true'),
        entry().to_line().replace('"attempts": 1', '"attempts": 1.5'),
        entry().to_line().replace('"plan": "add-cfbd-vs-foundation"', '"plan": ""'),
        entry().to_line().replace('"ts": "2026-10-22T15:00:00.000Z"', '"ts": "yesterday"'),
    ],
)
def test_an_unreadable_line_fails_closed_and_names_the_line(tmp_path: Path, line: str) -> None:
    path = tmp_path / "live-calls.jsonl"
    path.write_text(entry().to_line() + "\n" + line + "\n", encoding="utf-8")
    with pytest.raises(LedgerError, match=r"live-calls\.jsonl:2"):
        Ledger(path).entries()


def test_plan_total_sums_the_cost_of_that_plan_only() -> None:
    entries = [
        entry(cost=1),
        entry(cost=4),
        entry(slice_key="/info", cost=0),
        entry(plan="add-cfb-persistence-refresh", cost=7),
    ]
    assert ledger.plan_total(entries, PLAN) == 5
    assert ledger.plan_total(entries, "add-cfb-persistence-refresh") == 7
    assert ledger.plan_total(entries, "unknown") == 0


def test_month_total_sums_every_plan_within_the_utc_month_only() -> None:
    entries = [
        entry(cost=3),
        entry(plan="other", cost=10),
        entry(ts="2026-09-30T23:59:59.999Z", cost=100),
        entry(ts="2026-11-01T00:00:00.000Z", cost=1000),
    ]
    assert ledger.month_total(entries, "2026-10") == 13


def test_month_of_and_timestamp_use_utc() -> None:
    moment = datetime(2026, 10, 31, 23, 30, 0, 123456, tzinfo=UTC)
    assert ledger.timestamp(moment) == "2026-10-31T23:30:00.123Z"
    assert ledger.month_of(moment) == "2026-10"


def test_the_exclusive_lock_refuses_a_second_holder(tmp_path: Path) -> None:
    book = Ledger(tmp_path / "live-calls.jsonl")
    second = Ledger(tmp_path / "live-calls.jsonl")
    with book.exclusive(), pytest.raises(LedgerError, match="another"), second.exclusive():
        pass
    with book.exclusive():
        pass


def test_the_default_path_is_the_shared_cache_ledger() -> None:
    assert ledger.LEDGER_PATH.parts[-2:] == ("cache", "live-calls.jsonl")
    assert ledger.MONTH_CAP == 400


# --- Phase 2: caps from vs/live/ledger.yaml, `check`, `room`, LEDGER=off, run entries -----------

PERSISTENCE = "add-cfb-persistence-refresh"
CONFIG = LedgerConfig(plans={PLAN: 50, PERSISTENCE: 300}, months={"2026-10": 400})
BIG_PLANS = LedgerConfig(plans={"a": 1000, "b": 1000}, months={"2026-10": 400})
NOW = datetime(2026, 10, 22, 9, 30, tzinfo=UTC)


def make(path: Path, *, enabled: bool = True, config: LedgerConfig = CONFIG) -> Ledger:
    return Ledger(path, enabled=enabled, config=config)


def write(path: Path, *entries: Entry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(e.to_line() + "\n" for e in entries), encoding="utf-8")


def test_the_committed_config_has_the_plan_caps_and_the_owner_authorizations() -> None:
    config = ledger.load_config()
    assert (
        config.plans["add-cfbd-vs-foundation"] == 50
        and config.plans["add-cfb-persistence-refresh"] == 300
    )
    assert config.months == {"2026-10": 400, "2026-11": 1000}


def test_check_passes_inside_both_caps(tmp_path: Path) -> None:
    path = tmp_path / "live-calls.jsonl"
    write(path, *[entry(cost=1) for _ in range(40)])
    make(path).check(PERSISTENCE, "2026-10", estimate=217, used_calls=40)


def test_check_refuses_when_the_plan_cap_would_be_exceeded(tmp_path: Path) -> None:
    path = tmp_path / "live-calls.jsonl"
    write(path, entry(plan=PERSISTENCE, cost=100))
    with pytest.raises(LedgerRefused, match=r"plan add-cfb-persistence-refresh.*300.*100.*201"):
        make(path).check(PERSISTENCE, "2026-10", estimate=201, used_calls=0)
    make(path).check(PERSISTENCE, "2026-10", estimate=200, used_calls=0)


def test_check_refuses_when_info_used_calls_plus_estimate_exceed_the_month_cap(
    tmp_path: Path,
) -> None:
    book = make(tmp_path / "live-calls.jsonl")
    with pytest.raises(LedgerRefused, match=r"2026-10.*400.*/info used 250.*estimate 151"):
        book.check(PERSISTENCE, "2026-10", estimate=151, used_calls=250)
    book.check(PERSISTENCE, "2026-10", estimate=150, used_calls=250)


def test_the_month_cap_counts_all_plans_in_the_file_even_when_info_is_lower(
    tmp_path: Path,
) -> None:
    path = tmp_path / "live-calls.jsonl"
    write(
        path,
        entry(plan="a", cost=50),
        entry(plan="b", cost=250),
        entry(plan="b", cost=100, ts="2026-09-30T23:59:59.999Z"),
        entry(plan="b", slice_key="/info", cost=0),
    )
    book = make(path, config=BIG_PLANS)
    book.check("a", "2026-10", estimate=100, used_calls=0)
    with pytest.raises(LedgerRefused, match=r"2026-10.*400"):
        book.check("a", "2026-10", estimate=101, used_calls=0)


def test_check_without_info_falls_back_to_the_file_total(tmp_path: Path) -> None:
    path = tmp_path / "live-calls.jsonl"
    write(path, entry(plan="b", cost=250))
    book = make(path, config=BIG_PLANS)
    book.check("a", "2026-10", estimate=150, used_calls=None)
    with pytest.raises(LedgerRefused, match=r"2026-10.*ledger file"):
        book.check("a", "2026-10", estimate=151, used_calls=None)


def test_headroom_is_kept_free_on_both_caps(tmp_path: Path) -> None:
    write(tmp_path / "live-calls.jsonl", entry(plan=PERSISTENCE, cost=100))
    book = make(tmp_path / "live-calls.jsonl")
    book.check(PERSISTENCE, "2026-10", estimate=197, used_calls=0, headroom=3)
    with pytest.raises(LedgerRefused, match="plan"):
        book.check(PERSISTENCE, "2026-10", estimate=198, used_calls=0, headroom=3)


def test_a_month_without_an_entry_refuses_until_the_owner_adds_one(tmp_path: Path) -> None:
    book = make(tmp_path / "live-calls.jsonl")
    with pytest.raises(LedgerRefused, match=r"no entry for month 2026-11.*vs/live/ledger.yaml"):
        book.check(PERSISTENCE, "2026-11", estimate=1, used_calls=0)
    extended = LedgerConfig(CONFIG.plans, {**CONFIG.months, "2026-11": 200})
    make(tmp_path / "live-calls.jsonl", config=extended).check(
        PERSISTENCE, "2026-11", estimate=1, used_calls=0
    )


def test_an_unknown_plan_refuses(tmp_path: Path) -> None:
    with pytest.raises(LedgerRefused, match=r"no cap for plan.*other-plan"):
        make(tmp_path / "live-calls.jsonl").check("other-plan", "2026-10", 1, 0)


def test_a_negative_estimate_or_headroom_is_a_caller_error(tmp_path: Path) -> None:
    book = make(tmp_path / "live-calls.jsonl")
    with pytest.raises(LedgerError, match="estimate"):
        book.check(PERSISTENCE, "2026-10", -1, 0)
    with pytest.raises(LedgerError, match="headroom"):
        book.check(PERSISTENCE, "2026-10", 1, 0, headroom=-1)


def test_check_fails_closed_on_an_unreadable_line(tmp_path: Path) -> None:
    path = tmp_path / "live-calls.jsonl"
    path.write_text("{oops\n", encoding="utf-8")
    with pytest.raises(LedgerError, match=r"live-calls\.jsonl:1"):
        make(path).check(PERSISTENCE, "2026-10", 1, 0)


def test_room_is_the_smaller_of_the_two_caps(tmp_path: Path) -> None:
    path = tmp_path / "live-calls.jsonl"
    write(path, entry(plan=PERSISTENCE, cost=100))
    book = make(path)
    assert book.room(PERSISTENCE, "2026-10", used_calls=0) == 200
    assert book.room(PERSISTENCE, "2026-10", used_calls=300) == 100
    assert book.room(PERSISTENCE, "2026-10", used_calls=450) == 0


def test_room_of_a_month_or_plan_without_an_entry_is_zero(tmp_path: Path) -> None:
    book = make(tmp_path / "live-calls.jsonl")
    assert book.room(PERSISTENCE, "2026-11", used_calls=0) == 0
    assert book.room("other-plan", "2026-10", used_calls=0) == 0


def test_ledger_off_disables_checks_reads_and_writes(tmp_path: Path) -> None:
    path = tmp_path / "live-calls.jsonl"
    path.write_text("{this would fail closed if it were read\n", encoding="utf-8")
    book = make(path, enabled=False)
    book.check("anything", "2099-01", estimate=10**6, used_calls=10**6)
    assert book.room("anything", "2099-01", used_calls=0) is None
    assert book.entries() == []
    before = path.read_text(encoding="utf-8")
    book.append(entry())
    book.record_run(PERSISTENCE, "aws", kind="BACKFILL", run_id=1, calls=5)
    with book.exclusive():
        pass
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("value", ["off", "OFF", " Off "])
def test_ledger_off_is_read_from_the_environment(value: str) -> None:
    assert ledger.is_off({"LEDGER": value}) is True


@pytest.mark.parametrize("value", ["", "on", "0", "no", "false"])
def test_only_the_word_off_turns_the_ledger_off(value: str) -> None:
    assert ledger.is_off({"LEDGER": value}) is False
    assert ledger.is_off({}) is False


def test_the_default_path_is_the_main_trees_cache(tmp_path: Path) -> None:
    main = tmp_path / "main"
    gitdir = main / ".git" / "worktrees" / "p2"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = tmp_path / "p2"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    assert ledger.shared_root(worktree) == main.resolve()
    assert ledger.default_path(worktree) == main.resolve() / "cache" / "live-calls.jsonl"


def test_in_the_main_tree_or_without_git_metadata_the_root_is_the_tree_itself(
    tmp_path: Path,
) -> None:
    assert ledger.shared_root(tmp_path) == tmp_path
    (tmp_path / ".git").mkdir()
    assert ledger.shared_root(tmp_path) == tmp_path
    (tmp_path / ".git").rmdir()
    (tmp_path / ".git").write_text("not a gitdir pointer\n", encoding="utf-8")
    assert ledger.shared_root(tmp_path) == tmp_path


def test_the_module_default_path_is_the_shared_default() -> None:
    assert ledger.default_path() == ledger.LEDGER_PATH


def test_the_path_can_be_set_by_argument_or_environment(tmp_path: Path) -> None:
    explicit, from_env = tmp_path / "explicit.jsonl", tmp_path / "env.jsonl"
    environ = {"LEDGER_PATH": str(from_env)}
    assert Ledger.from_environment(path=explicit, environ=environ).path == explicit
    assert Ledger.from_environment(environ=environ).path == from_env
    assert Ledger.from_environment(environ={}).path == ledger.LEDGER_PATH


def test_from_environment_honours_ledger_off(tmp_path: Path) -> None:
    assert Ledger.from_environment(environ={"LEDGER": "off"}).enabled is False
    assert Ledger.from_environment(environ={}).enabled is True


def test_the_config_is_loaded_lazily_so_an_off_ledger_needs_no_valid_config(
    tmp_path: Path,
) -> None:
    broken = tmp_path / "ledger.yaml"
    broken.write_text("plans: [oops\n", encoding="utf-8")
    book = Ledger.from_environment(config_path=broken, environ={"LEDGER": "off"})
    book.check(PERSISTENCE, "2026-10", 1, 0)
    on = Ledger.from_environment(config_path=broken, environ={})
    with pytest.raises(LedgerError, match="not valid YAML"):
        on.check(PERSISTENCE, "2026-10", 1, 0)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("plans: {a: 1}\n", "months"),
        ("months: {'2026-10': 5}\n", "plans"),
        ("plans: {a: -1}\nmonths: {'2026-10': 5}\n", "plans.a"),
        ("plans: {a: x}\nmonths: {'2026-10': 5}\n", "plans.a"),
        ("plans: {a: 1}\nmonths: {'2026-13': 5}\n", "2026-13"),
        ("plans: {a: 1}\nmonths: {Oct: 5}\n", "Oct"),
        ("plans: {a: 1}\nmonths: {'2026-10': 5}\nextra: 1\n", "extra"),
        ("- just\n- a list\n", "mapping"),
        ("plans: [unclosed\n", "not valid YAML"),
    ],
)
def test_an_invalid_config_is_rejected_with_the_reason(
    tmp_path: Path, text: str, message: str
) -> None:
    path = tmp_path / "ledger.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(LedgerError, match=message):
        ledger.load_config(path)


def test_a_missing_config_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(LedgerError, match="not found"):
        ledger.load_config(tmp_path / "missing.yaml")


def test_a_run_entry_carries_the_measured_calls_as_its_cost(tmp_path: Path) -> None:
    path = tmp_path / "live-calls.jsonl"
    book = make(path)
    book.record_run(
        PERSISTENCE,
        "aws",
        kind="BACKFILL",
        run_id=42,
        calls=20,
        remaining_before=900,
        remaining_after=880,
        clock=lambda: NOW,
    )
    (written,) = book.entries()
    assert written.ts == "2026-10-22T09:30:00.000Z"
    assert (written.plan, written.target, written.cost) == (PERSISTENCE, "aws", 20)
    assert written.slice_key == "BACKFILL#42"
    assert written.run == "backfill-42"
    assert (written.remaining_before, written.remaining_after) == (900, 880)
    assert written.error is None
    assert (written.status, written.attempts, written.ms, written.bytes) == (None,) * 4
    assert tuple(json.loads(path.read_text(encoding="utf-8"))) == ledger.FIELDS
    assert ledger.plan_total(book.entries(), PERSISTENCE) == 20


def test_a_failed_run_entry_keeps_the_first_line_of_the_error(tmp_path: Path) -> None:
    book = make(tmp_path / "live-calls.jsonl")
    book.record_run(
        PERSISTENCE,
        "aws",
        kind="REFRESH_WEEK",
        run_id=7,
        calls=1,
        error="FAILED: step FETCH GAMES\nsecond line",
        clock=lambda: NOW,
    )
    assert book.entries()[0].error == "FAILED: step FETCH GAMES"


def test_summary_lists_plan_totals_and_month_totals(tmp_path: Path) -> None:
    path = tmp_path / "live-calls.jsonl"
    write(
        path,
        entry(cost=1),
        entry(plan=PERSISTENCE, cost=20),
        entry(plan="retired-plan", cost=3, ts="2026-09-01T08:00:00.000Z"),
        entry(slice_key="/info", cost=0),
    )
    text = "\n".join(make(path).summary_lines())
    assert f"{PLAN}: 1 of 50" in text
    assert f"{PERSISTENCE}: 20 of 300" in text
    assert "retired-plan: 3 (no cap)" in text
    assert "2026-10: 21 of 400" in text
    assert "2026-09: 3 (no entry)" in text
    assert str(path) in text


def test_summary_of_a_disabled_ledger_says_so(tmp_path: Path) -> None:
    text = "\n".join(make(tmp_path / "live-calls.jsonl", enabled=False).summary_lines())
    assert "LEDGER=off" in text


def test_main_summary_prints_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "live-calls.jsonl"
    write(path, entry(plan=PERSISTENCE, cost=1))
    assert ledger.main(["--summary", "--path", str(path)]) == 0
    assert f"{PERSISTENCE}: 1 of 300" in capsys.readouterr().out


def test_main_reports_a_broken_ledger_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "live-calls.jsonl"
    path.write_text("{oops\n", encoding="utf-8")
    assert ledger.main(["--summary", "--path", str(path)]) == 1
    assert "not valid JSON" in capsys.readouterr().err
