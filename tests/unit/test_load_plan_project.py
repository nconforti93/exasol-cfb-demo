"""The project's own `loader/load-plan.yaml`: it validates, compiles and costs 217 demo calls."""

from datetime import date
from typing import Any

import pytest

from loader import plan as load_plan
from loader.plan import EntryEstimate, PlanConfig, estimate_plan, weekly_refresh_calls
from loader.plan import load_plan as read_plan
from loader.templates import compile_plan

TODAY = date(2026, 10, 22)

DEMO_CALLS_BY_TABLE = {
    ("TEAMS", "none"): 1,
    ("VENUES", "none"): 1,
    ("CONFERENCES", "none"): 1,
    ("CONFERENCES_AFFILIATIONS", "season_range"): 1,
    ("CONFERENCES_AFFILIATIONS", "season"): 1,
    ("CALENDAR", "season"): 14,
    ("GAMES", "season"): 157,
    ("METRICS_WP_PREGAME", "season"): 14,
    ("RATINGS_SP", "season"): 14,
    ("RANKINGS_POLLS_RANKS", "season"): 13,
}
DEMO_CALL_ESTIMATE = 217
WEEKLY_REFRESH_CALLS = 4


def games_calls(estimates: tuple[EntryEstimate, ...]) -> int:
    return next(e.calls for e in estimates if e.table == "GAMES")


@pytest.fixture(scope="module")
def project_plan() -> Any:
    return read_plan()


def test_the_project_plan_validates_and_compiles(project_plan: Any) -> None:
    templates = compile_plan(project_plan, load_plan.default_catalog())
    assert len(templates) == len(project_plan.entries)


def test_the_demo_estimate_is_217_calls_split_by_entry(project_plan: Any) -> None:
    estimates = estimate_plan(project_plan, TODAY)
    by_entry = {(e.table, e.slicing): e.calls for e in estimates if e.profile == "demo"}
    assert by_entry == DEMO_CALLS_BY_TABLE
    assert sum(e.calls for e in estimates if e.profile == "demo") == DEMO_CALL_ESTIMATE


def test_the_games_gap_is_planned_but_costs_no_call(project_plan: Any) -> None:
    games = next(e for e in estimate_plan(project_plan, TODAY) if e.table == "GAMES")
    assert (games.slices, games.gaps, games.calls) == (158, 1, 157)
    gapped = [(e.table, e.gaps) for e in estimate_plan(project_plan, TODAY) if e.gaps]
    assert gapped == [("GAMES", 1)]


def test_the_weekly_refresh_fetches_four_current_season_slices(project_plan: Any) -> None:
    assert weekly_refresh_calls(project_plan, TODAY) == WEEKLY_REFRESH_CALLS


def test_the_demo_stores_only_the_rankings_child_table_and_no_volatile_table(
    project_plan: Any,
) -> None:
    tables = {entry.table for entry in project_plan.entries}
    assert "RANKINGS_POLLS_RANKS" in tables
    assert not tables & {"RANKINGS_POLLS", "GAMES_SCHEDULE"}


def test_the_season_sliced_entries_have_distinct_priorities(project_plan: Any) -> None:
    priorities = {(e.table, e.slicing): e.priority for e in project_plan.entries}
    assert [priorities[(t, "season")] for t in ("CALENDAR", "GAMES")] == [2, 3]
    assert [
        priorities[(table, "season")]
        for table in ("METRICS_WP_PREGAME", "RATINGS_SP", "RANKINGS_POLLS_RANKS")
    ] == [4, 5, 6]
    seasonal = [e.priority for e in project_plan.entries if e.slicing == "season"]
    assert len(set(seasonal)) == len(seasonal)


def test_the_project_plan_ships_the_default_settings_and_the_full_refresh_chain(
    project_plan: Any,
) -> None:
    assert project_plan.config == PlanConfig()
    assert [(s.step_no, s.name, s.script) for s in project_plan.refresh_steps] == [
        (100, "MODEL", "CFB_INFRA.REFRESH_MODEL"),
        (200, "PREDICT", "CFB_INFRA.REFRESH_PREDICT"),
        (300, "SIMULATE", "CFB_INFRA.REFRESH_SIMULATE"),
    ]


def test_the_estimate_drops_the_current_season_before_the_rollover(project_plan: Any) -> None:
    before = estimate_plan(project_plan, date(2026, 7, 31))
    after = estimate_plan(project_plan, date(2026, 8, 1))
    assert games_calls(after) - games_calls(before) == 1
    assert sum(e.calls for e in before) < sum(e.calls for e in after)


def test_check_estimate_prints_the_per_profile_counts(capsys: pytest.CaptureFixture[str]) -> None:
    assert load_plan.main(["--check", "--estimate", "--today", "2026-10-22"]) == 0
    out = capsys.readouterr().out
    assert "plan ok" in out
    assert "demo: 10 entries" in out
    assert f"{DEMO_CALL_ESTIMATE} calls" in out
    assert f"weekly refresh: {WEEKLY_REFRESH_CALLS} calls" in out


def test_check_prints_every_problem_and_fails(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "plan.yaml"
    broken.write_text("version: 1\nentries:\n  - table: NOPE\n", encoding="utf-8")
    assert load_plan.main(["--check", "--plan", str(broken)]) == 1
    assert "unknown-table" in capsys.readouterr().err
