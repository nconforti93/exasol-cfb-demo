"""Integration tests of the model over league variants: data that changes per test.

Every test seeds its own league (`harness.seed_league(variant)` empties and re-seeds), and the
per-test reset empties the data again, so no test order leaks state. A test that changes a model
reference table restores it before it ends. Other parts of the shared scenarios (`MODEL_CHECKS`,
`TEAM_SEASON`) are added with their views.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from tests.it.harness import Harness
from tests.synthetic import league_teams as lt
from tests.synthetic.league import FETCHED_LATER, FETCHED_LATER_RANKINGS, with_duplicate_rows

PLAN_VARIANT = "model"
pytestmark = pytest.mark.it

NORTHAM = lt.team("Northam").id


@pytest.fixture(autouse=True)
def model(harness: Harness, model_deployed: None) -> None:
    """Every test runs against the deployed `sql/model/`."""


def moment(value: Any) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))


def test_duplicate_game_appears_once(harness: Harness) -> None:
    """Scenario games: A game stored twice appears once (the `GAME` part)."""
    league = harness.seed_league(with_duplicate_rows)
    target = league.game_id("carrow_dunmore_2026_w3")
    assert harness.query(f"SELECT COUNT(*) FROM {harness.core}.GAMES WHERE ID = {target}") == [(2,)]
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.core}.METRICS_WP_PREGAME WHERE GAME_ID = {target}"
    ) == [(2,)]
    rows = harness.query_dicts(f"SELECT * FROM {harness.model}.GAME WHERE GAME_ID = {target}")
    assert len(rows) == 1
    row = rows[0]
    assert (row["HOME_POINTS"], row["AWAY_POINTS"]) == (21, 17)
    assert Decimal(str(row["PREGAME_SPREAD"])) == Decimal("-3.50")
    assert Decimal(str(row["HOME_PREGAME_WIN_PROB"])) == Decimal("0.6000")
    assert moment(row["FETCHED_AT"]) == FETCHED_LATER
    assert moment(row["PREGAME_WP_FETCHED_AT"]) == FETCHED_LATER
    stored = harness.query(f"SELECT COUNT(DISTINCT ID) FROM {harness.core}.GAMES")
    assert harness.query(f"SELECT COUNT(*) FROM {harness.model}.GAME") == stored
    week3 = harness.query_dicts(
        f"SELECT GAME_COUNT FROM {harness.model}.SEASON_WEEK "
        "WHERE SEASON = 2026 AND SEASON_TYPE = 'regular' AND WEEK = 3"
    )
    expected = harness.query(
        f"SELECT COUNT(DISTINCT ID) FROM {harness.core}.GAMES "
        "WHERE SEASON = 2026 AND SEASON_TYPE = 'regular' AND WEEK = 3"
    )
    assert [(w["GAME_COUNT"],) for w in week3] == expected


def test_duplicate_calendar_week_appears_once(harness: Harness) -> None:
    harness.seed_league()
    harness.query(
        f"INSERT INTO {harness.core}.CALENDAR "
        "SELECT ROW_NO, SEASON, WEEK, SEASON_TYPE, ADD_HOURS(START_DATE, 1), "
        "ADD_HOURS(END_DATE, 1), ADD_HOURS(FIRST_GAME_START, 1), LAST_GAME_START, P_YEAR, "
        "'/calendar?year=2024&later=1', REQ_SOURCE, REQ_STATUS, REQ_ATTEMPTS, REQ_MS, REQ_ROWS, "
        f"ADD_DAYS(REQ_AT, 1) FROM {harness.core}.CALENDAR "
        "WHERE SEASON = 2024 AND SEASON_TYPE = 'regular' AND WEEK = 14"
    )
    stored = harness.query(
        f"SELECT START_DATE, END_DATE, FIRST_GAME_START FROM {harness.core}.CALENDAR "
        "WHERE SEASON = 2024 AND SEASON_TYPE = 'regular' AND WEEK = 14 ORDER BY REQ_AT DESC"
    )
    assert len(stored) == 2
    week = harness.query_dicts(
        f"SELECT * FROM {harness.model}.SEASON_WEEK "
        "WHERE SEASON = 2024 AND SEASON_TYPE = 'regular' AND WEEK = 14"
    )
    assert len(week) == 1
    newest = tuple(moment(cell) for cell in stored[0])
    assert (moment(week[0]["START_AT"]), moment(week[0]["END_AT"])) == newest[:2]
    assert moment(week[0]["FIRST_GAME_AT"]) == newest[2]


@pytest.fixture
def northam_transition(harness: Harness) -> Iterator[None]:
    """A `CLASSIFICATION_TRANSITION` row that classifies Northam as `fcs` for 2010-2014."""
    table = f"{harness.model}.CLASSIFICATION_TRANSITION"
    harness.query(
        f"INSERT INTO {table} (TEAM_NAME, FIRST_SEASON, LAST_SEASON, CLASSIFICATION, NOTE) "
        "VALUES ('Northam', 2010, 2014, 'fcs', 'test row')"
    )
    try:
        yield
    finally:
        harness.query(f"DELETE FROM {table} WHERE TEAM_NAME = 'Northam'")


def test_classification_from_transition_seed(harness: Harness, northam_transition: None) -> None:
    """Scenario teams-conferences: A seeded transition overrides the recorded classification
    (the `GAME` part: every 2014 game of Northam classifies Northam as `fcs`)."""
    harness.seed_league()
    recorded = harness.query(
        "SELECT HOME_ID, HOME_CLASSIFICATION, AWAY_ID, AWAY_CLASSIFICATION "
        f"FROM {harness.core}.GAMES "
        f"WHERE SEASON = 2014 AND {NORTHAM} IN (HOME_ID, AWAY_ID)"
    )
    assert recorded
    assert {hc if h == NORTHAM else ac for h, hc, _, ac in recorded} == {"fbs"}
    rows = harness.query_dicts(
        f"SELECT * FROM {harness.model}.GAME "
        f"WHERE SEASON = 2014 AND {NORTHAM} IN (HOME_TEAM_ID, AWAY_TEAM_ID)"
    )
    assert len(rows) == len(recorded)
    for row in rows:
        side = "HOME" if row["HOME_TEAM_ID"] == NORTHAM else "AWAY"
        other = "AWAY" if side == "HOME" else "HOME"
        assert row[f"{side}_CLASSIFICATION"] == "fcs", row["GAME_ID"]
        assert row[f"{other}_CLASSIFICATION"] == "fbs", row["GAME_ID"]
        assert (row["INVOLVES_FBS"], row["IS_FBS_GAME"]) == (True, False), row["GAME_ID"]
    outside = harness.query(
        f"SELECT DISTINCT HOME_CLASSIFICATION FROM {harness.model}.GAME "
        f"WHERE SEASON = 2024 AND HOME_TEAM_ID = {NORTHAM}"
    )
    assert outside in ([], [("fbs",)])


def test_duplicate_ranking_appears_once(harness: Harness) -> None:
    """Scenario ratings-rankings: A rating or a ranking stored twice appears once (the `RANKING`
    part: the 2026 AP poll labeled week 9 is in the season slice and in a later week slice)."""
    harness.seed_league(with_duplicate_rows)
    where = "SEASON = 2026 AND SEASON_TYPE = 'regular' AND WEEK = 9 AND POLL = 'AP Top 25'"
    stored = harness.query(
        "SELECT COUNT(*), COUNT(DISTINCT TEAM_ID) "
        f"FROM {harness.core}.RANKINGS_POLLS_RANKS WHERE {where}"
    )
    assert stored[0][0] == 2 * stored[0][1] > 0
    rows = harness.query_dicts(
        f"SELECT * FROM {harness.model}.RANKING WHERE SEASON = 2026 AND WEEK = 9 "
        "AND POLL_NAME = 'AP Top 25' ORDER BY POLL_RANK"
    )
    assert len(rows) == stored[0][1]
    assert len({row["TEAM_ID"] for row in rows}) == len(rows)
    assert {moment(row["FETCHED_AT"]) for row in rows} == {FETCHED_LATER_RANKINGS}
    others = harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.RANKING WHERE SEASON = 2026 AND NOT "
        "(WEEK = 9 AND POLL_NAME = 'AP Top 25')"
    )
    expected = harness.query(
        f"SELECT COUNT(*) FROM {harness.core}.RANKINGS_POLLS_RANKS WHERE SEASON = 2026 AND NOT "
        "(WEEK = 9 AND POLL = 'AP Top 25')"
    )
    assert others == expected
