"""Integration tests of model/refresh-step, the `MODEL_CHECKS` view part (task 3.11).

Every test seeds its own league (`harness.seed_league(variant)` empties and re-seeds), so no test
order leaks state. The step script `CFB_INFRA.REFRESH_MODEL`, its log and the refresh chain are task
4.1 and will join this module. Expectations are the spec's facts about the league.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tests.it.harness import Harness
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
