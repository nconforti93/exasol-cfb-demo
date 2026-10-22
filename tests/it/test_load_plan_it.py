"""Integration tests of the load plan (spec `loader/load-plan`): the deploy and the views.

No Lua and no CFBD call: the views read the deployed plan, the load log and the virtual schema's
properties. Each test sets its own clock and, when it needs another plan, deploys a variant of
`tests/it/fixtures` (the reset puts the module's base plan back). The fetch-blocking part of the
orphan scenario is tested with the scripts (`test_backfill_it.py`, `test_refresh_week_it.py`).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from tests.it.harness import Harness, Mock
from vs.host.deploy import DeployError

pytestmark = pytest.mark.it

NOW = "2020-10-20"
PROJECT_NOW = "2026-10-22"
GAMES_2019 = "/games?seasonType=both&year=2019"
LOADED_BY_BACKFILL = "BACKFILL#1"
PROJECT_PLAN = Path(__file__).resolve().parents[2] / "loader" / "load-plan.yaml"
NO_FEATURES = ""

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

COST_PLAN = """\
version: 1
entries:
  - {table: GAMES, profile: demo, priority: 1, slicing: season,
     seasons: {from: 2018, to: 2020}, gaps: [2019], refresh_class: weekly}
  - {table: RATINGS_SP, profile: demo, priority: 2, slicing: season,
     seasons: {from: 2019}, refresh_class: weekly}
"""


def seasons_of(harness: Harness, table: str) -> list[int | None]:
    return [row["SEASON"] for row in harness.load_slices(table)]


def status_of(harness: Harness, table: str) -> dict[str, Any]:
    return next(row for row in harness.load_status() if row["TABLE_NAME"] == table)


def plan_tables(harness: Harness) -> list[tuple[Any, ...]]:
    return harness.query(
        "SELECT ENTRY_ID, TABLE_NAME, PROFILE_NAME, PRIORITY, SLICING, SLICE_TEMPLATE, PLAN_HASH "
        f"FROM {harness.meta}.LOAD_PLAN ORDER BY ENTRY_ID"
    )


def test_deployed_plan_lists_every_slice(harness: Harness) -> None:
    """Scenario: The deployed plan lists every slice the loader may fetch."""
    harness.set_now(NOW)
    slices = harness.load_slices("GAMES")
    assert [row["SEASON"] for row in slices] == [2017, 2018, 2019, 2020]
    assert len({row["SLICE_KEY"] for row in slices}) == 4
    key_of_2019 = next(row["SLICE_KEY"] for row in slices if row["SEASON"] == 2019)
    assert key_of_2019 == GAMES_2019


def test_project_plan_keys_match_explain(harness: Harness, mock: Mock) -> None:
    """Scenario: Planned slice keys match the virtual schema's requests."""
    harness.deploy_plan_text(
        PROJECT_PLAN.read_text(encoding="utf-8"), {"now_override": PROJECT_NOW}
    )
    entries = harness.query_dicts(
        f"SELECT ENTRY_ID, TABLE_NAME, SLICING, FIXED_FILTER, SEASON_FROM, SEASON_TO "
        f"FROM {harness.meta}.LOAD_PLAN ORDER BY ENTRY_ID"
    )
    slices = harness.load_slices()
    compared = 0
    for entry in entries:
        planned = [s for s in slices if s["ENTRY_ID"] == entry["ENTRY_ID"] and not s["IS_GAP"]]
        assert planned, entry
        for chosen in (planned[0], planned[-1]):
            query = f"SELECT * FROM {harness.vs}.{entry['TABLE_NAME']}{where_of(entry, chosen)}"
            assert list(harness.explain(query).slice_keys) == [chosen["SLICE_KEY"]], (entry, chosen)
            compared += 1
    assert compared == 2 * len(entries)
    assert harness.mock_hits() == 0


def where_of(entry: dict[str, Any], planned: dict[str, Any]) -> str:
    predicates = [entry["FIXED_FILTER"]] if entry["FIXED_FILTER"] else []
    if entry["SLICING"] == "season":
        predicates.append(f'"P_YEAR" = {int(planned["SEASON"])}')
    elif entry["SLICING"] == "season_range":
        predicates.append(f'"P_MIN_YEAR" = {int(entry["SEASON_FROM"])}')
        predicates.append(f'"P_MAX_YEAR" = {int(entry["SEASON_TO"])}')
    return " WHERE " + " AND ".join(predicates) if predicates else ""


def test_invalid_plan_keeps_deployed_plan(harness: Harness) -> None:
    """Scenario: An invalid plan stops the deploy (the deployed plan stays unchanged)."""
    harness.deploy_plan("gaps", {"now_override": NOW})
    before = plan_tables(harness)
    gaps_before = harness.query(f"SELECT * FROM {harness.meta}.LOAD_PLAN_GAPS")
    assert gaps_before
    with pytest.raises(DeployError) as raised:
        harness.deploy_plan_text(FIVE_PROBLEMS)
    message = str(raised.value)
    for needle in (
        "entry 1 (NO_SUCH_TABLE): unknown-table",
        "entry 2 (GAMES_SCHEDULE): volatile-table",
        "entry 3 (PLAYS): plays.spec_required",
        "entry 5 (GAMES): overlap",
        "entry 6 (RATINGS_SP): gap-outside-seasons",
    ):
        assert needle in message
    assert plan_tables(harness) == before
    assert harness.query(f"SELECT * FROM {harness.meta}.LOAD_PLAN_GAPS") == gaps_before
    assert [row["IS_GAP"] for row in harness.load_slices("GAMES")] == [False, False, True, False]


def test_new_season_on_rollover_date(harness: Harness) -> None:
    """Scenario: A new season enters the plan on the rollover date."""
    harness.set_now("2021-07-31")
    before = seasons_of(harness, "GAMES")
    harness.set_now("2021-08-01")
    after = seasons_of(harness, "GAMES")
    assert max(s for s in before if s is not None) == 2020
    assert after == [*before, 2021]


def test_finality_follows_refresh_class(harness: Harness) -> None:
    """Scenario: Finality follows the refresh class."""
    harness.deploy_plan("refresh", {"now_override": NOW})
    final = {
        (row["TABLE_NAME"], row["SEASON"]): row["FINAL_IF_LOADED_NOW"]
        for row in harness.load_slices()
        if row["SEASON"] is not None
    }
    assert final[("GAMES", 2020)] is False
    assert final[("GAMES", 2019)] is True
    assert final[("CALENDAR", 2020)] is True
    harness.set_now("2021-02-14")
    assert games_final(harness, 2020) is False
    harness.set_now("2021-02-15")
    assert games_final(harness, 2020) is True


def games_final(harness: Harness, season: int) -> bool:
    row = next(r for r in harness.load_slices("GAMES") if r["SEASON"] == season)
    return bool(row["FINAL_IF_LOADED_NOW"])


def test_stored_non_final_slice_is_pending_once_it_would_be_final(harness: Harness) -> None:
    """Scenario: A slice that became final is fetched once more (the view side: IS_PENDING)."""
    harness.deploy_plan("refresh", {"now_override": NOW})
    key = next(r["SLICE_KEY"] for r in harness.load_slices("GAMES") if r["SEASON"] == 2019)
    harness.register_slice("GAMES", key, 10, final=False, loaded_by=LOADED_BY_BACKFILL)
    pending = {r["SEASON"] for r in harness.load_slices("GAMES") if r["IS_PENDING"]}
    assert pending == {2019, 2020}
    assert status_of(harness, "GAMES")["CALLS_TO_COMPLETE"] == 2
    harness.set_now("2019-10-20")  # stored non-final and not final yet: left to the refresh
    pending = {r["SEASON"] for r in harness.load_slices("GAMES") if r["IS_PENDING"]}
    assert pending == set()


def test_range_and_seasonless_slices_have_no_season(harness: Harness) -> None:
    """Scenario: Range and seasonless slices have no season."""
    harness.deploy_plan("seasonless", {"now_override": NOW})
    for table in ("TEAMS", "CONFERENCES_AFFILIATIONS"):
        slices = harness.load_slices(table)
        assert [row["SEASON"] for row in slices] == [None], table
    range_slice = harness.load_slices("CONFERENCES_AFFILIATIONS")[0]
    assert range_slice["SLICE_KEY"] == "/conferences/affiliations?maxYear=2019&minYear=1869"
    harness.set_now("2002-10-20")
    assert seasons_of(harness, "CONFERENCES_AFFILIATIONS") == [None]
    assert seasons_of(harness, "GAMES") == []


def test_demo_part_of_full_and_first(harness: Harness) -> None:
    """Scenario: The demo profile is part of the full profile and comes first."""
    harness.deploy_plan("profiles", {"now_override": NOW})
    queue = harness.backfill_queue()
    demo = [row for row in queue if row["PROFILE_NAME"] == "demo"]
    full_only = [row for row in queue if row["PROFILE_NAME"] == "full"]
    planned_demo = [
        row for row in harness.load_slices() if row["PROFILE_NAME"] == "demo" and row["IS_PENDING"]
    ]
    assert len(demo) == len(planned_demo) == 4
    assert len(full_only) == 2
    assert {r["PROFILE_RANK"] for r in demo} == {1}
    assert max(r["QUEUE_ORDER"] for r in demo) < min(r["QUEUE_ORDER"] for r in full_only)
    assert [r["TABLE_NAME"] for r in full_only] == ["RATINGS_SP", "RATINGS_SP"]
    assert [r["QUEUE_ORDER"] for r in queue] == list(range(1, len(queue) + 1))


def test_gated_entries_wait_for_feature(harness: Harness) -> None:
    """Scenario: Gated entries wait for their feature."""
    harness.deploy_plan("profiles", {"now_override": NOW})
    assert "weather" not in harness.properties().get("API_FEATURES", NO_FEATURES)
    assert harness.load_slices("GAMES_WEATHER") == []
    gated = status_of(harness, "GAMES_WEATHER")
    assert gated["BLOCKED_BY"] == "weather"
    assert gated["SLICES_PLANNED"] == 0
    assert gated["CALLS_TO_COMPLETE"] == 0
    assert [
        row["BLOCKED_BY"] for row in harness.load_status() if row["TABLE_NAME"] != "GAMES_WEATHER"
    ] == [
        None,
        None,
    ]
    assert "GAMES_WEATHER" not in {row["TABLE_NAME"] for row in harness.backfill_queue()}


def test_gated_entries_are_planned_when_the_feature_arrives(harness: Harness) -> None:
    """Scenario: Gated entries wait for their feature (once the key has it, the slices appear)."""
    harness.deploy_plan("profiles", {"now_override": NOW})
    harness.set_property("API_FEATURES", "weather")
    assert seasons_of(harness, "GAMES_WEATHER") == [2019, 2020]
    assert status_of(harness, "GAMES_WEATHER")["BLOCKED_BY"] is None
    assert "GAMES_WEATHER" in {row["TABLE_NAME"] for row in harness.backfill_queue()}


def test_load_status_shows_cost(harness: Harness) -> None:
    """Scenario: The plan shows what completing it costs."""
    harness.deploy_plan_text(COST_PLAN, {"now_override": NOW})
    games = {row["SEASON"]: row["SLICE_KEY"] for row in harness.load_slices("GAMES")}
    ratings = {row["SEASON"]: row["SLICE_KEY"] for row in harness.load_slices("RATINGS_SP")}
    harness.register_slice("GAMES", games[2018], 7)
    harness.register_failure("RATINGS_SP", ratings[2020], 2, "HTTP 500")
    games_row = status_of(harness, "GAMES")
    assert counts(games_row) == {
        "SLICES_PLANNED": 3,
        "GAP_SLICES": 1,
        "SLICES_STORED": 1,
        "SLICES_FINAL": 1,
        "SLICES_MISSING": 1,
        "QUARANTINED_SLICES": 0,
        "ORPHAN_SLICES": 0,
        "CALLS_TO_COMPLETE": 1,
    }
    ratings_row = status_of(harness, "RATINGS_SP")
    assert counts(ratings_row) == {
        "SLICES_PLANNED": 2,
        "GAP_SLICES": 0,
        "SLICES_STORED": 0,
        "SLICES_FINAL": 0,
        "SLICES_MISSING": 2,
        "QUARANTINED_SLICES": 1,
        "ORPHAN_SLICES": 0,
        "CALLS_TO_COMPLETE": 1,
    }
    assert (ratings_row["LAST_ERROR_SLICE"], ratings_row["LAST_ERROR"]) == (
        ratings[2020],
        "HTTP 500",
    )
    assert (games_row["STORED_ROWS"], ratings_row["STORED_ROWS"]) == (7, 0)
    assert datetime.fromisoformat(str(games_row["LAST_LOADED_AT"])) is not None
    assert sum(row["CALLS_TO_COMPLETE"] for row in harness.load_status()) == 2


def counts(row: dict[str, Any]) -> dict[str, int]:
    columns = (
        "SLICES_PLANNED",
        "GAP_SLICES",
        "SLICES_STORED",
        "SLICES_FINAL",
        "SLICES_MISSING",
        "QUARANTINED_SLICES",
        "ORPHAN_SLICES",
        "CALLS_TO_COMPLETE",
    )
    return {column: int(row[column]) for column in columns}


def stored_by_a_backfill(harness: Harness, table: str, seasons: list[int]) -> None:
    keys = {row["SEASON"]: row["SLICE_KEY"] for row in harness.load_slices(table)}
    for season in seasons:
        harness.register_slice(table, keys[season], 5, loaded_by=LOADED_BY_BACKFILL)


def test_orphan_slices_reported(harness: Harness) -> None:
    """Scenario: Orphan slices are reported and block fetching their table (views and deploy)."""
    harness.set_now(NOW)
    stored_by_a_backfill(harness, "GAMES", [2017, 2018, 2019, 2020])
    assert status_of(harness, "GAMES")["ORPHAN_SLICES"] == 0
    report = harness.deploy_plan("games-2019", {"now_override": NOW})
    assert report.orphans == {"GAMES": 2}
    assert any("orphan slices of GAMES: 2" in warning for warning in report.warnings)
    assert any("BACKFILL and REFRESH_WEEK refuse" in warning for warning in report.warnings)
    games = status_of(harness, "GAMES")
    assert (games["ORPHAN_SLICES"], games["BLOCKED_BY"]) == (2, "ORPHANS")
    assert (games["SLICES_PLANNED"], games["SLICES_STORED"]) == (2, 2)
    ratings = status_of(harness, "RATINGS_SP")
    assert (ratings["ORPHAN_SLICES"], ratings["BLOCKED_BY"]) == (0, None)
    assert {row["TABLE_NAME"] for row in harness.backfill_queue()} == {"RATINGS_SP"}
    orphans = harness.query(f"SELECT SLICE_KEY FROM {harness.meta}.LOAD_ORPHANS ORDER BY SLICE_KEY")
    assert orphans == [
        ("/games?seasonType=both&year=2017",),
        ("/games?seasonType=both&year=2018",),
    ]


def test_orphans_do_not_hide_the_calls_still_needed(harness: Harness) -> None:
    """Scenario: Orphan slices block fetching their table (decision [35]: calls still counted)."""
    harness.set_now(NOW)
    stored_by_a_backfill(harness, "GAMES", [2017, 2018])
    harness.deploy_plan("games-2019", {"now_override": NOW})
    games = status_of(harness, "GAMES")
    assert (games["ORPHAN_SLICES"], games["CALLS_TO_COMPLETE"]) == (2, 2)
    assert "GAMES" not in {row["TABLE_NAME"] for row in harness.backfill_queue()}


def test_only_rows_written_by_a_loader_run_are_orphans(harness: Harness) -> None:
    """Scenario: Orphan slices are reported (rows written by others are never orphans)."""
    harness.set_now(NOW)
    keys = {row["SEASON"]: row["SLICE_KEY"] for row in harness.load_slices("GAMES")}
    harness.register_slice("GAMES", keys[2017], 5)
    harness.register_slice("GAMES", keys[2018], 5, loaded_by="REFRESH_WEEK#4")
    harness.register_slice("GAMES", "/games?seasonType=both&year=1999", 5)
    harness.deploy_plan("games-2019", {"now_override": NOW})
    assert status_of(harness, "GAMES")["ORPHAN_SLICES"] == 1
