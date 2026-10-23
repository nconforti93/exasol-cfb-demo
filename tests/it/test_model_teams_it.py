"""Integration tests of model/teams-conferences: `TEAM` and `CONFERENCE`.

Read-only module over the synthetic league (marker `model_readonly`). A test that stores another
fetch of a row removes it again before it ends (`tests/it/mirror_edit.py`). The expectations are
the league's own bodies (team list, game bodies), never the SQL under test. The `TEAM_SEASON`
parts of the scenarios belong to `210_team_season.sql`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.it import mirror_edit
from tests.it.harness import Harness
from tests.synthetic import league_teams as lt
from tests.synthetic.league import STORED_SEASONS, League

PLAN_VARIANT = "model"
pytestmark = [pytest.mark.it, pytest.mark.model_readonly]

LATER_FETCH = "2030-01-01 00:00:00"
LISTED = [team for team in lt.TEAMS if team.in_team_list]


def team_rows(harness: Harness, where: str = "1 = 1") -> list[dict[str, Any]]:
    return harness.query_dicts(f"SELECT * FROM {harness.model}.TEAM WHERE {where} ORDER BY TEAM_ID")


def team_row(harness: Harness, name: str) -> dict[str, Any]:
    rows = harness.query_dicts(
        f"SELECT * FROM {harness.model}.TEAM WHERE SCHOOL = {{name}}", {"name": name}
    )
    assert len(rows) == 1, name
    return rows[0]


def seasons_of_games(league: League) -> dict[int, tuple[int, int]]:
    """First and last season with a stored game, per team id, from the game bodies."""
    seen: dict[int, list[int]] = {}
    for season in STORED_SEASONS:
        for body in league.game_bodies(season):
            for side in ("homeId", "awayId"):
                if body.get(side) is not None:
                    seen.setdefault(body[side], []).append(season)
    return {team_id: (min(years), max(years)) for team_id, years in seen.items()}


# --- TEAM: one row per team, listed or seen in games --------------------------------------------


def test_teams_that_appear_only_in_games_are_listed(harness: Harness, league: League) -> None:
    """Scenario teams-conferences: Teams that appear only in games are listed."""
    marston = team_row(harness, "Marston")
    assert marston["TEAM_ID"] == lt.team("Marston").id
    assert marston["IS_IN_TEAM_LIST"] is False
    assert (marston["FIRST_SEASON"], marston["LAST_SEASON"]) == (1885, 1885)
    for column in (
        "CURRENT_CONFERENCE",
        "CURRENT_DIVISION",
        "CURRENT_CLASSIFICATION",
        "HOME_VENUE_ID",
        "MASCOT",
        "ABBREVIATION",
    ):
        assert marston[column] is None, column


def test_every_listed_team_and_every_game_team_is_one_row(harness: Harness, league: League) -> None:
    seasons = seasons_of_games(league)
    expected_ids = {team.id for team in LISTED} | set(seasons)
    rows = team_rows(harness)
    assert [row["TEAM_ID"] for row in rows] == sorted(expected_ids)
    listed_ids = {team.id for team in LISTED}
    assert {row["TEAM_ID"] for row in rows if row["IS_IN_TEAM_LIST"]} == listed_ids
    assert {row["TEAM_ID"] for row in rows if not row["IS_IN_TEAM_LIST"]} == (
        expected_ids - listed_ids
    )
    assert expected_ids - listed_ids == {lt.team("Marston").id}


def test_first_and_last_season_come_from_stored_games(harness: Harness, league: League) -> None:
    seasons = seasons_of_games(league)
    for row in team_rows(harness):
        expected = seasons.get(row["TEAM_ID"], (None, None))
        assert (row["FIRST_SEASON"], row["LAST_SEASON"]) == expected, row["SCHOOL"]
    ashford = team_row(harness, "Ashford")
    assert (ashford["FIRST_SEASON"], ashford["LAST_SEASON"]) == (1885, 2026)
    assert team_row(harness, "Kingsley")["FIRST_SEASON"] == 2013


def test_current_conference_follows_the_team_list(harness: Harness, league: League) -> None:
    """Scenario teams-conferences: Membership follows realignment (the `TEAM` part: the SEC is
    Carrow's `CURRENT_CONFERENCE`)."""
    carrow = team_row(harness, "Carrow")
    assert carrow["CURRENT_CONFERENCE"] == "SEC"
    assert carrow["CURRENT_CLASSIFICATION"] == "fbs"
    assert team_row(harness, "Dunmore")["CURRENT_CONFERENCE"] == "Mountain West"


def test_current_values_equal_the_stored_team_list(harness: Harness, league: League) -> None:
    for definition in LISTED:
        body = lt.team_body(definition)
        row = team_row(harness, definition.name)
        assert row["TEAM_ID"] == body["id"], definition.name
        assert row["CURRENT_CONFERENCE"] == body["conference"], definition.name
        assert row["CURRENT_DIVISION"] == body["division"], definition.name
        assert row["CURRENT_CLASSIFICATION"] == body["classification"], definition.name
        assert row["HOME_VENUE_ID"] == body["location"]["id"], definition.name
        assert (row["MASCOT"], row["ABBREVIATION"]) == (body["mascot"], body["abbreviation"])
        assert row["IS_IN_TEAM_LIST"] is True
    assert team_row(harness, "Fairhaven")["CURRENT_DIVISION"] == "West"
    assert team_row(harness, "Eastport")["CURRENT_DIVISION"] is None
    assert team_row(harness, "Lowell")["CURRENT_CLASSIFICATION"] == "fcs"


# --- deduplication and the game-only name -----------------------------------------------------


@pytest.fixture
def stored_twice(harness: Harness, league: League) -> Iterator[None]:
    yield
    for table in ("TEAMS", "CONFERENCES", "GAMES"):
        mirror_edit.remove_copies(harness, table, "marker-teams-it")


def test_the_newest_fetch_of_a_team_wins(
    harness: Harness, league: League, stored_twice: None
) -> None:
    """Scenario teams-conferences (deduplication): a team listed twice appears once."""
    stored = mirror_edit.store_changed_copy(
        harness,
        "TEAMS",
        "SCHOOL = 'Carrow'",
        marker="marker-teams-it",
        fetched_at=LATER_FETCH,
        row_offset=1000,
        changes={"CONFERENCE": "'ACC'", "ABBREVIATION": "'NEW'", "LOCATION_ID": "NULL"},
    )
    assert stored == 1
    rows = harness.query_dicts(f"SELECT * FROM {harness.model}.TEAM WHERE SCHOOL = 'Carrow'")
    assert len(rows) == 1
    assert (rows[0]["CURRENT_CONFERENCE"], rows[0]["ABBREVIATION"]) == ("ACC", "NEW")
    assert rows[0]["HOME_VENUE_ID"] is None
    assert len(team_rows(harness)) == len(LISTED) + 1


def test_a_game_only_team_takes_its_name_from_its_most_recent_game(
    harness: Harness, league: League, stored_twice: None
) -> None:
    mirror_edit.store_changed_copy(
        harness,
        "GAMES",
        f"HOME_ID = {lt.team('Marston').id}",
        marker="marker-teams-it",
        fetched_at=LATER_FETCH,
        row_offset=1000,
        changes={
            "ID": "ID + 900000",
            "SEASON": "1886",
            "START_DATE": "ADD_YEARS(START_DATE, 1)",
            "HOME_TEAM": "'Marston Tech'",
        },
    )
    row = team_row(harness, "Marston Tech")
    assert row["TEAM_ID"] == lt.team("Marston").id and row["IS_IN_TEAM_LIST"] is False
    assert (row["FIRST_SEASON"], row["LAST_SEASON"]) == (1885, 1886)
    assert harness.query(f"SELECT COUNT(*) FROM {harness.model}.TEAM WHERE SCHOOL = 'Marston'") == [
        (0,)
    ]


def test_a_listed_team_keeps_its_listed_name(
    harness: Harness, league: League, stored_twice: None
) -> None:
    mirror_edit.store_changed_copy(
        harness,
        "GAMES",
        f"AWAY_ID = {lt.team('Ashford').id} AND SEASON = 1885",
        marker="marker-teams-it",
        fetched_at=LATER_FETCH,
        row_offset=1000,
        changes={"ID": "ID + 900000", "AWAY_TEAM": "'Ashford Old Name'"},
    )
    assert team_row(harness, "Ashford")["TEAM_ID"] == lt.team("Ashford").id
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.TEAM WHERE TEAM_ID = {lt.team('Ashford').id}"
    ) == [(1,)]


# --- CONFERENCE --------------------------------------------------------------------------------


def test_one_row_per_conference_with_its_columns(harness: Harness, league: League) -> None:
    rows = harness.query_dicts(f"SELECT * FROM {harness.model}.CONFERENCE ORDER BY CONFERENCE_ID")
    assert [row["CONFERENCE_NAME"] for row in rows] == sorted(
        lt.CONFERENCE_NAMES, key=lt.conference_id
    )
    for row, body in zip(rows, lt.conference_bodies(), strict=True):
        assert row["CONFERENCE_ID"] == body["id"]
        assert row["SHORT_NAME"] == body["shortName"]
        assert row["ABBREVIATION"] == body["abbreviation"]
        assert row["CLASSIFICATION"] == body["classification"]
    by_name = {row["CONFERENCE_NAME"]: row for row in rows}
    assert by_name[lt.BIG_SKY]["CLASSIFICATION"] == "fcs"
    assert by_name["SEC"]["CLASSIFICATION"] == "fbs"


def test_the_newest_fetch_of_a_conference_wins(
    harness: Harness, league: League, stored_twice: None
) -> None:
    mirror_edit.store_changed_copy(
        harness,
        "CONFERENCES",
        "NAME = 'SEC'",
        marker="marker-teams-it",
        fetched_at=LATER_FETCH,
        row_offset=1000,
        changes={"ABBREVIATION": "'SECX'"},
    )
    rows = harness.query_dicts(
        f"SELECT * FROM {harness.model}.CONFERENCE WHERE CONFERENCE_NAME = 'SEC'"
    )
    assert [row["ABBREVIATION"] for row in rows] == ["SECX"]
    assert harness.query(f"SELECT COUNT(*) FROM {harness.model}.CONFERENCE") == [
        (len(lt.CONFERENCE_NAMES),)
    ]
