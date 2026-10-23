"""Integration tests of model/refresh-step, the `MODEL_CHECKS` view part (task 3.11).

Every test seeds its own league (`harness.seed_league(variant)` empties and re-seeds), so no test
order leaks state. Expectations are the spec's facts about the league. The step script
`CFB_INFRA.REFRESH_MODEL` (task 4.1): the rebuild of the derived tables (decision [40]), the
checks outside a refresh, an unknown point, and the refresh chain in replay mode (step 100 `MODEL`,
then the recording step 200).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pyexasol
import pytest

from model import step as model_step
from tests.it.harness import Harness, Mock
from tests.synthetic.league import (
    League,
    with_duplicate_rows,
    with_incomplete_week9_game,
    with_nonfinal_prior,
    with_renamed_conference,
    with_unaffiliated_fbs_team,
)

PLAN_VARIANT = "model"
pytestmark = pytest.mark.it

FIRST_SEASON, LAST_SEASON = 1869, 2026
SEASON_CHECKS = 15


@pytest.fixture(autouse=True)
def model(harness: Harness, model_deployed: None) -> None:
    """Every test runs against the deployed `sql/model/`."""


def checks(harness: Harness, season: int) -> dict[tuple[str, int | None], dict[str, Any]]:
    rows = harness.query_dicts(
        f"SELECT * FROM {harness.model}.MODEL_CHECKS WHERE SEASON = {season}"
    )
    result = {(row["CHECK_NAME"], row["WEEK_SEQ"]): row for row in rows}
    assert len(result) == len(rows), season
    return result


def failed(harness: Harness, season: int, name: str, week: int | None = None) -> dict[str, Any]:
    row = checks(harness, season)[(name, week)]
    assert row["CHECK_STATUS"] == "FAIL", (season, name, row)
    assert row["OBSERVED"] > 0 and row["DETAIL"]
    return row


def compose(*variants: Callable[[League], League]) -> Callable[[League], League]:
    def apply(league: League) -> League:
        for variant in variants:
            league = variant(league)
        return league

    return apply


def test_base_league_passes_error_checks(harness: Harness) -> None:
    """No ERROR check fails for any season of the base league; every check has its row for every
    season, and the week check one per week; the league's planted warnings are reported."""
    harness.seed_league()
    errors = harness.query(
        f"SELECT CHECK_NAME, SEASON, DETAIL FROM {harness.model}.MODEL_CHECKS "
        "WHERE SEVERITY = 'ERROR' AND CHECK_STATUS = 'FAIL'"
    )
    assert errors == []
    counts = harness.query(
        f"SELECT COUNT(*), COUNT(DISTINCT CHECK_NAME), MIN(SEASON), MAX(SEASON) "
        f"FROM {harness.model}.MODEL_CHECKS WHERE WEEK_SEQ IS NULL"
    )
    seasons = LAST_SEASON - FIRST_SEASON + 1
    assert counts == [(SEASON_CHECKS * seasons, SEASON_CHECKS, FIRST_SEASON, LAST_SEASON)]
    weeks = harness.query(
        f"SELECT (SELECT COUNT(*) FROM {harness.model}.MODEL_CHECKS WHERE WEEK_SEQ IS NOT NULL), "
        f"(SELECT COUNT(*) FROM {harness.model}.SEASON_WEEK)"
    )
    assert weeks[0][0] == weeks[0][1]
    assert failed(harness, 2014, "MEMBERSHIP_CONFLICT")["OBSERVED"] == 1
    assert failed(harness, 2024, "SP_TEAM_UNMATCHED")["DETAIL"] == "Ashford St."
    assert "Mystery Poll" in failed(harness, 2024, "POLL_UNKNOWN")["DETAIL"]
    notes = failed(harness, 2014, "CONF_CHAMPIONSHIP_NOTES_MISMATCH")
    assert "Eastport vs Fairhaven" in notes["DETAIL"] and notes["SEVERITY"] == "WARNING"
    missing = failed(harness, 2014, "CONF_CHAMPIONSHIP_MISSING")
    assert "Mountain West" in missing["DETAIL"] and missing["SEVERITY"] == "WARNING"
    assert failed(harness, 1885, "VENUE_WITHOUT_LOCATION")["SEVERITY"] == "INFO"
    assert "Marston" in failed(harness, 1885, "GAME_TEAM_NOT_IN_TEAM_LIST")["DETAIL"]
    assert failed(harness, 1950, "WEEK_NOT_COMPLETE", 5)["OBSERVED"] == 1
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.MODEL_CHECKS "
        "WHERE CHECK_NAME = 'DUPLICATE_SOURCE_ROW' AND CHECK_STATUS = 'FAIL'"
    ) == [(0,)]


def test_renamed_power_conference(harness: Harness) -> None:
    """Scenario refresh-step: A renamed power conference is an error for its season (view part)."""
    harness.seed_league(with_renamed_conference)
    row = failed(harness, 2024, "POWER_CONFERENCE_MISSING")
    assert (row["SEVERITY"], row["DETAIL"]) == ("ERROR", "SEC")
    assert checks(harness, 2023)[("POWER_CONFERENCE_MISSING", None)]["CHECK_STATUS"] == "PASS"


def test_unaffiliated_fbs_team_and_open_week(harness: Harness) -> None:
    """An FBS team without any conference fails the ERROR check of its season; a week with a game
    that is not completed fails the week check with severity WARNING and the number of games."""
    harness.seed_league(compose(with_unaffiliated_fbs_team, with_incomplete_week9_game))
    row = failed(harness, 2026, "FBS_TEAM_WITHOUT_CONFERENCE")
    assert (row["SEVERITY"], row["DETAIL"], row["OBSERVED"]) == ("ERROR", "Quarry", 1)
    week = failed(harness, 2026, "WEEK_NOT_COMPLETE", 9)
    assert (week["SEVERITY"], week["OBSERVED"]) == ("WARNING", 1)
    assert checks(harness, 2026)[("WEEK_NOT_COMPLETE", 8)]["CHECK_STATUS"] == "PASS"


def test_duplicates_and_nonfinal_prior_reported(harness: Harness) -> None:
    """Scenarios games, ratings-rankings: stored-twice rows and a prior from a slice that is not
    final are WARNINGs for 2026, naming the sources."""
    harness.seed_league(compose(with_duplicate_rows, with_nonfinal_prior))
    duplicates = failed(harness, 2026, "DUPLICATE_SOURCE_ROW")
    assert duplicates["SEVERITY"] == "WARNING"
    for source in ("GAMES", "METRICS_WP_PREGAME", "RATINGS_SP", "RANKINGS_POLLS_RANKS"):
        assert source in duplicates["DETAIL"], source
    prior = failed(harness, 2026, "SP_PRIOR_NOT_FINAL")
    assert prior["SEVERITY"] == "WARNING"


# --- the step script (task 4.1) ---------------------------------------------------------------


def step(harness: Harness, *arguments: object) -> list[dict[str, Any]]:
    run_id, season, week, season_type = arguments
    sql = model_step.step_call(
        harness.namespace,
        run_id=run_id,  # type: ignore[arg-type]
        season=season,  # type: ignore[arg-type]
        week=week,  # type: ignore[arg-type]
        season_type=season_type,  # type: ignore[arg-type]
    )
    return harness.query_dicts(sql)


def log_count(harness: Harness) -> int:
    return int(harness.query(f"SELECT COUNT(*) FROM {harness.meta}.MODEL_CHECK_LOG")[0][0])


def test_the_step_rebuilds_the_derived_tables_from_the_stored_rows(harness: Harness) -> None:
    """Decision [40]: a derived table keeps its rows until the step rebuilds it; then GAME and
    TEAM_SEASON show the changed result. A rebuild logs no check, and the run lock refuses it while
    another run holds the lock."""
    harness.seed_league()
    game_id, team_id, points = harness.query(
        f"SELECT GAME_ID, HOME_TEAM_ID, HOME_POINTS FROM {harness.model}.GAME "
        "WHERE SEASON = 2024 AND HAS_RESULT ORDER BY GAME_ID LIMIT 1"
    )[0]
    before = harness.query(
        f"SELECT POINTS_FOR FROM {harness.model}.TEAM_SEASON WHERE SEASON = 2024 AND TEAM_ID = "
        f"{team_id}"
    )[0][0]
    logged = log_count(harness)
    harness.query(
        f"UPDATE {harness.core}.GAMES SET HOME_POINTS = HOME_POINTS + 50 WHERE ID = {game_id}"
    )
    stale = harness.query(f"SELECT HOME_POINTS FROM {harness.model}.GAME WHERE GAME_ID = {game_id}")
    assert stale == [(points,)]
    rows = step(harness, None, None, None, None)
    assert [row["ITEM_NAME"] for row in rows] == [
        "GAME", "SEASON_WEEK", "TEAM", "RATING_SP", "RANKING", "TEAM_MEMBERSHIP", "TEAM_SEASON",
        "AS_OF_POINT",
    ]  # fmt: skip
    assert {row["ITEM_STATUS"] for row in rows} == {"REBUILT"}
    fresh = harness.query(f"SELECT HOME_POINTS FROM {harness.model}.GAME WHERE GAME_ID = {game_id}")
    assert fresh == [(points + 50,)]
    after = harness.query(
        f"SELECT POINTS_FOR FROM {harness.model}.TEAM_SEASON WHERE SEASON = 2024 AND TEAM_ID = "
        f"{team_id}"
    )[0][0]
    assert after == before + 50
    assert log_count(harness) == logged
    run = harness.runs()[-1]
    assert (run["RUN_KIND"], run["RUN_STATUS"]) == ("MODEL", "DONE")
    with harness.hold_run_lock("RESTORE"), pytest.raises(pyexasol.ExaError) as refused:
        step(harness, None, None, None, None)
    assert "MODEL refused: RESTORE run" in str(refused.value)


def test_the_checks_run_for_any_point_outside_a_refresh(harness: Harness) -> None:
    """Scenarios: the checks run for any point outside a refresh; an unknown point is rejected."""
    harness.seed_league()
    logged = log_count(harness)
    rows = step(harness, None, 2024, 9, "regular")
    checks = [row for row in rows if row["ITEM_KIND"] == "CHECK"]
    assert len(checks) == SEASON_CHECKS + 1
    assert not [
        row for row in checks if row["SEVERITY"] == "ERROR" and row["ITEM_STATUS"] == "FAIL"
    ]
    log = harness.query(
        "SELECT RUN_ID, AS_OF_SEASON, AS_OF_WEEK_SEQ, COUNT(*), COUNT(CHECK_WEEK_SEQ) "
        f"FROM {harness.meta}.MODEL_CHECK_LOG GROUP BY 1, 2, 3"
    )
    assert log == [(None, 2024, 9, SEASON_CHECKS + 1, 1)]
    assert log_count(harness) == logged + SEASON_CHECKS + 1
    with pytest.raises(pyexasol.ExaError) as unknown:
        step(harness, None, 2024, 23, "regular")
    assert "season 2024, regular week 23 is unknown" in str(unknown.value)
    assert log_count(harness) == logged + SEASON_CHECKS + 1


def test_the_refresh_runs_the_model_step_after_the_fetch(harness: Harness, mock: Mock) -> None:
    """Scenario: the refresh runs the model step after the fetch (replay mode, zero requests)."""
    harness.seed_league()
    harness.write_league_recordings()
    harness.use_replay()
    try:
        rows = harness.refresh(2026, 9, "regular")
    finally:
        harness.reset_properties()
    steps = [(int(row["STEP_NO"]), str(row["STEP_NAME"]), str(row["STEP_STATUS"])) for row in rows]
    assert steps[0] == (1, "CHECK", "DONE")
    assert all(name.startswith("FETCH ") and status == "DONE" for _, name, status in steps[1:-2])
    assert len(steps) > 3
    assert steps[-2:] == [(100, "MODEL", "DONE"), (200, "PREDICT", "DONE")]
    model_row = next(row for row in rows if int(row["STEP_NO"]) == 100)
    assert int(model_row["CALLS"]) == 0
    run_id = int(rows[0]["RUN_ID"])
    log = harness.query(
        "SELECT AS_OF_SEASON, AS_OF_WEEK_SEQ, COUNT(*), MAX(CHECK_WEEK_SEQ) "
        f"FROM {harness.meta}.MODEL_CHECK_LOG WHERE RUN_ID = {run_id} GROUP BY 1, 2"
    )
    assert log == [(2026, 9, SEASON_CHECKS + 1, 9)]
    assert mock.hits() == 0
