"""CLI side of the load plan (spec `loader/load-plan`): `make deploy` and `python -m loader.plan`.

An invalid plan makes the deploy fail with one message per problem, before it connects to a
database or builds the bundle (the previously deployed plan staying in place is the integration
test `tests/it/test_load_plan_it.py::test_invalid_plan_keeps_deployed_plan`).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from loader import deploy as loader_deploy
from loader import plan as load_plan_module
from scripts import deploy as deploy_cli
from vs.cfbd_vs.catalog import Catalog
from vs.host import target

FIVE_PROBLEMS = """\
version: 1
entries:
  - {table: NO_SUCH_TABLE, profile: demo, priority: 1, slicing: none, refresh_class: static}
  - {table: GAMES_SCHEDULE, profile: demo, priority: 2, slicing: none, refresh_class: static}
  - {table: PLAYS, profile: demo, priority: 3, slicing: season, seasons: {from: 2019},
     refresh_class: weekly}
  - {table: GAMES, profile: demo, priority: 4, slicing: season, seasons: {from: 2019},
     refresh_class: weekly}
  - {table: GAMES, profile: demo, priority: 5, slicing: season, seasons: {from: 2019},
     refresh_class: weekly}
  - {table: RATINGS_SP, profile: demo, priority: 6, slicing: season,
     seasons: {from: 2019, to: 2020}, gaps: [2015], refresh_class: weekly}
"""
EXPECTED_PROBLEMS = (
    "entry 1 (NO_SUCH_TABLE): unknown-table",
    "entry 2 (GAMES_SCHEDULE): volatile-table",
    "entry 3 (PLAYS): plays.spec_required",
    "entry 5 (GAMES): overlap",
    "entry 6 (RATINGS_SP): gap-outside-seasons",
)


class Untouched:
    """A connector that fails the test when the deploy reaches the database."""

    def __call__(self, deployment: target.Target) -> Any:
        raise AssertionError("an invalid plan must stop the deploy before it connects")


def never_written() -> Any:
    raise AssertionError("an invalid plan must stop the deploy before it builds the bundle")


@pytest.fixture
def plan_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[str], Path]:
    """Make the deploy read the plan from a file the test writes (the project plan stays)."""

    def write(text: str) -> Path:
        path = tmp_path / "load-plan.yaml"
        path.write_text(text, encoding="utf-8")

        def read(catalog: Catalog | None = None) -> load_plan_module.LoadPlan:
            return load_plan_module.load_plan(path, catalog)

        monkeypatch.setattr(loader_deploy, "load_plan", read)
        return path

    return write


def test_invalid_plan_lists_every_error(
    plan_file: Callable[[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Scenario: An invalid plan stops the deploy (one message per problem, nothing deployed)."""
    plan_file(FIVE_PROBLEMS)
    code = deploy_cli.main(
        ["--prefix", "PROBE_", "--dsn", "db:1"], connector=Untouched(), write_bundle=never_written
    )
    captured = capsys.readouterr()
    assert code == deploy_cli.EXIT_ERROR
    assert captured.out == ""
    assert captured.err.startswith("deploy failed: ")
    lines = [line for line in captured.err.splitlines() if line.lstrip().startswith("- entry")]
    assert len(lines) == len(EXPECTED_PROBLEMS)
    for needle in EXPECTED_PROBLEMS:
        assert sum(needle in line for line in lines) == 1, needle


def test_the_plan_checker_prints_every_problem_it_can_decide(
    plan_file: Callable[[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Scenario: An invalid plan stops the deploy (`python -m loader.plan --check`)."""
    path = plan_file(FIVE_PROBLEMS)
    code = load_plan_module.main(["--check", "--plan", str(path)])
    err = capsys.readouterr().err
    assert code == 1
    assert "entry 1 (NO_SUCH_TABLE): unknown-table" in err
    assert "entry 6 (RATINGS_SP): gap-outside-seasons" in err
    assert "plan ok" not in err


def test_the_project_plan_passes_the_check_with_the_demo_estimate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Scenario: The deployed plan lists every slice the loader may fetch (the call estimate)."""
    code = load_plan_module.main(["--check", "--estimate", "--today", "2026-10-22"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("plan ok: ")
    assert "217" in out
