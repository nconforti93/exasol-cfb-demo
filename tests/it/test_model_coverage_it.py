"""Integration tests of model/coverage: `SEASON_COVERAGE` and `DATASET` over the synthetic league.

Read-only module (marker `model_readonly`). The league's load plan (`load-plan.model.yaml`) covers
GAMES from 1869 with the gap 1871, the calendar, pregame lines and SP+ from 2013, rankings from 2014
and memberships from 1869; the league stores only some of those seasons. Expectations are the spec's
facts and counts restated from the league, never the SQL under test.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from tests.it.harness import Harness
from tests.synthetic.league import League

PLAN_VARIANT = "model"
pytestmark = [pytest.mark.it, pytest.mark.model_readonly]


def coverage(harness: Harness, dataset: str, season: int) -> dict[str, Any]:
    rows = harness.query_dicts(
        f"SELECT * FROM {harness.model}.SEASON_COVERAGE "
        f"WHERE DATASET_CODE = '{dataset}' AND SEASON = {season}"
    )
    assert len(rows) == 1, (dataset, season)
    return rows[0]


def test_known_gap_not_available(harness: Harness, league: League) -> None:
    """Scenario coverage: A known gap is not available."""
    row = coverage(harness, "GAMES", 1871)
    assert (row["TRUST_LEVEL"], row["IS_GAP"]) == ("NOT_AVAILABLE", True)
    assert "1871" in row["TRUST_NOTE"] and "known gap" in row["TRUST_NOTE"]


def test_complete_season(harness: Harness, league: League) -> None:
    """Scenario coverage: A finished season with full coverage is complete."""
    row = coverage(harness, "PREGAME_WP", 2013)
    assert (row["POPULATION_COUNT"], row["COVERED_COUNT"]) == (25, 24)
    assert Decimal(str(row["MEASURED_SHARE"])) == Decimal("0.96")
    assert row["TRUST_LEVEL"] == "COMPLETE"


def test_partial_season(harness: Harness, league: League) -> None:
    """Scenario coverage: Low measured coverage is partial."""
    row = coverage(harness, "PREGAME_WP", 2014)
    assert Decimal(str(row["MEASURED_SHARE"])) == Decimal("0.80")
    assert row["TRUST_LEVEL"] == "PARTIAL"
    assert "0.8" in row["TRUST_NOTE"] and "20 of 25" in row["TRUST_NOTE"]


def test_current_season_in_progress(harness: Harness, league: League) -> None:
    """Scenario coverage: The current season is in progress."""
    rows = harness.query(
        f"SELECT DATASET_CODE, TRUST_LEVEL FROM {harness.model}.SEASON_COVERAGE WHERE SEASON = 2026"
    )
    levels = dict(rows)
    for dataset in ("GAMES", "SCORES", "ELO", "PREGAME_WP", "SP_PLUS", "POLLS"):
        assert levels[dataset] == "IN_PROGRESS", dataset


def test_unplanned_seasons_on_demand(harness: Harness, league: League) -> None:
    """Scenario coverage: Stored data for seasons outside the load plan is on demand."""
    row = coverage(harness, "SP_PLUS", 1985)
    assert (row["TRUST_LEVEL"], row["IS_PLANNED"]) == ("ON_DEMAND", False)
    assert "CFBD_VS.RATINGS_SP" in row["TRUST_NOTE"]
    assert coverage(harness, "SP_PLUS", 1960)["TRUST_LEVEL"] == "NOT_AVAILABLE"


def test_unstored_datasets_have_no_season_rows(harness: Harness, league: League) -> None:
    """Scenario coverage: Datasets that are never stored have no season rows."""
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.SEASON_COVERAGE "
        "WHERE DATASET_CODE IN ('PLAYS', 'WEATHER')"
    ) == [(0,)]
    datasets = {
        row["DATASET_CODE"]: row
        for row in harness.query_dicts(f"SELECT * FROM {harness.model}.DATASET")
    }
    plays, weather = datasets["PLAYS"], datasets["WEATHER"]
    assert (plays["ACCESS_CLASS"], plays["FIRST_SEASON"], plays["VS_TABLE"]) == (
        "ON_DEMAND",
        2001,
        "PLAYS",
    )
    assert weather["ACCESS_CLASS"] == "UNAVAILABLE"
    stored = harness.query(
        f"SELECT DISTINCT DATASET_CODE FROM {harness.model}.SEASON_COVERAGE ORDER BY 1"
    )
    assert [code for (code,) in stored] == sorted(
        code
        for code, row in datasets.items()
        if row["ACCESS_CLASS"] == "STORED" and row["FIRST_SEASON"] is not None
    )


def test_planned_not_loaded_missing(harness: Harness, league: League) -> None:
    """Scenario coverage: Planned data that is not loaded yet is missing."""
    row = coverage(harness, "GAMES", 1900)
    assert (row["TRUST_LEVEL"], row["IS_PLANNED"], row["IS_STORED"]) == ("MISSING", True, False)


def test_elo_coverage_from_1890(harness: Harness, league: League) -> None:
    """Scenario coverage: Elo coverage starts in 1890."""
    assert coverage(harness, "ELO", 1885)["TRUST_LEVEL"] == "NOT_AVAILABLE"
    row = coverage(harness, "ELO", 1950)
    assert row["TRUST_LEVEL"] == "COMPLETE"
    assert Decimal(str(row["MEASURED_SHARE"])) == Decimal(1)


def test_one_row_per_season_and_stored_dataset(harness: Harness, league: League) -> None:
    rows = harness.query(
        f"SELECT COUNT(*), COUNT(DISTINCT SEASON), MIN(SEASON), MAX(SEASON), "
        f"COUNT(DISTINCT DATASET_CODE) FROM {harness.model}.SEASON_COVERAGE"
    )
    count, seasons, first, last, datasets = rows[0]
    assert (first, last) == (1869, 2026)
    assert count == seasons * datasets
