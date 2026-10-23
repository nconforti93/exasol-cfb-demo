"""Integration tests of model/teams-conferences: `TEAM`, `CONFERENCE` and `TEAM_SEASON`.

Read-only module over the synthetic league (marker `model_readonly`). A test that stores another
fetch of a row removes it again before it ends (`tests/it/mirror_edit.py`). The expectations are
the league's own bodies (team list, affiliation spells, game bodies) and the spec's rules restated
in Python (`tests/it/team_season_expect.py`), never the SQL under test. The transition seed and the
league variants are in `tests/it/test_model_variants_it.py`, the division labels in
`test_model_seasons_it.py`, the SP+ prior and the final ranks in `test_model_ratings_it.py`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.it import mirror_edit
from tests.it.harness import Harness
from tests.it.team_season_expect import Expected, TeamSeason, expected_team_seasons
from tests.synthetic import league_teams as lt
from tests.synthetic.league import ASHFORD_2024, STORED_SEASONS, League

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
        "COLOR",
        "ALTERNATE_COLOR",
        "LOGO_URL",
        "LOGO_DARK_URL",
        "LATITUDE",
        "LONGITUDE",
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
        assert (row["COLOR"], row["ALTERNATE_COLOR"]) == (body["color"], body["alternateColor"])
        assert row["LOGO_URL"] == body["logos"][0], definition.name
        assert row["LOGO_DARK_URL"] is None, definition.name
        assert row["LATITUDE"] == pytest.approx(body["location"]["latitude"]), definition.name
        assert row["LONGITUDE"] == pytest.approx(body["location"]["longitude"]), definition.name
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


def test_logo_urls_come_from_the_logo_array(
    harness: Harness, league: League, stored_twice: None
) -> None:
    """Scenario teams-conferences: A team carries its colors, logos and coordinates from the team
    list (the logo part; the colors and coordinates are in the test of the current values)."""
    logos = '\'["https://example.invalid/500/1.png","https://example.invalid/500-dark/1.png"]\''
    mirror_edit.store_changed_copy(
        harness,
        "TEAMS",
        "SCHOOL = 'Carrow'",
        marker="marker-teams-it",
        fetched_at=LATER_FETCH,
        row_offset=1000,
        changes={"LOGOS": logos},
    )
    mirror_edit.store_changed_copy(
        harness,
        "TEAMS",
        "SCHOOL = 'Dunmore'",
        marker="marker-teams-it",
        fetched_at=LATER_FETCH,
        row_offset=1000,
        changes={"LOGOS": "'[]'", "COLOR": "NULL"},
    )
    carrow = team_row(harness, "Carrow")
    assert carrow["LOGO_URL"] == "https://example.invalid/500/1.png"
    assert carrow["LOGO_DARK_URL"] == "https://example.invalid/500-dark/1.png"
    dunmore = team_row(harness, "Dunmore")
    assert (dunmore["LOGO_URL"], dunmore["LOGO_DARK_URL"], dunmore["COLOR"]) == (None, None, None)
    assert dunmore["ALTERNATE_COLOR"] == lt.team_body(lt.team("Dunmore"))["alternateColor"]


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


# --- TEAM_SEASON: one row per team and season with a game or a membership -------------------

MEMBERSHIP_MARKER = "marker-team-season-it"
OLDER_FETCH = "2000-01-01 00:00:00"
MEMBERSHIP_COLUMNS = (
    "SCHOOL",
    "CONFERENCE_NAME",
    "CONFERENCE_SOURCE",
    "CONFERENCE_DIVISION",
    "HAS_MEMBERSHIP_CONFLICT",
    "CLASSIFICATION",
    "CLASSIFICATION_SOURCE",
    "CONFERENCE_TIER",
    "IS_POWER_CONFERENCE",
)


@pytest.fixture(scope="module")
def expected(harness: Harness, league: League) -> dict[TeamSeason, Expected]:
    return expected_team_seasons(league, harness)


def team_season(harness: Harness, name: str, season: int) -> dict[str, Any]:
    rows = harness.query_dicts(
        f"SELECT * FROM {harness.model}.TEAM_SEASON "
        f"WHERE TEAM_ID = {lt.team(name).id} AND SEASON = {season}"
    )
    assert len(rows) == 1, (name, season)
    return rows[0]


def team_seasons(harness: Harness, where: str = "1 = 1") -> list[dict[str, Any]]:
    return harness.query_dicts(
        f"SELECT * FROM {harness.model}.TEAM_SEASON WHERE {where} ORDER BY TEAM_ID, SEASON"
    )


def membership_values(expected: Expected) -> tuple[Any, ...]:
    return (
        expected.school,
        expected.conference,
        expected.conference_source,
        expected.division,
        expected.conflict,
        expected.classification,
        expected.classification_source,
        expected.tier,
        expected.is_power,
    )


def test_one_row_per_team_season_with_a_game_or_a_membership(
    harness: Harness, league: League, expected: dict[TeamSeason, Expected]
) -> None:
    keys = harness.query(f"SELECT TEAM_ID, SEASON FROM {harness.model}.TEAM_SEASON")
    found = [(int(team_id), int(season)) for team_id, season in keys]
    assert len(found) == len(set(found))
    assert sorted(found) == sorted(expected)
    assert (lt.team("Ashford").id, 1933) in expected
    assert (lt.team("Ashford").id, 1932) not in expected
    assert (lt.team("Dunmore").id, 2027) not in expected


def test_every_team_season_has_its_membership_classification_and_tier(
    harness: Harness, league: League, expected: dict[TeamSeason, Expected]
) -> None:
    for row in team_seasons(harness):
        key = (row["TEAM_ID"], row["SEASON"])
        found = tuple(row[column] for column in MEMBERSHIP_COLUMNS)
        assert found == membership_values(expected[key]), key


def test_every_team_season_has_its_records_and_elo(
    harness: Harness, league: League, expected: dict[TeamSeason, Expected]
) -> None:
    for row in team_seasons(harness):
        key = (row["TEAM_ID"], row["SEASON"])
        wanted = expected[key]
        assert {column: row[column] for column in wanted.record()} == wanted.record(), key
        assert row["FIRST_PREGAME_ELO"] == wanted.first_pregame_elo(), key
        assert row["LAST_POSTGAME_ELO"] == wanted.last_postgame_elo(), key


def test_membership_follows_realignment(harness: Harness, league: League) -> None:
    """Scenario teams-conferences: Membership follows realignment (the `TEAM_SEASON` part)."""
    pac12 = team_season(harness, "Carrow", 2023)
    sec = team_season(harness, "Carrow", 2024)
    assert (pac12["CONFERENCE_NAME"], pac12["CONFERENCE_SOURCE"]) == ("Pac-12", "AFFILIATION")
    assert (sec["CONFERENCE_NAME"], sec["CONFERENCE_SOURCE"]) == ("SEC", "AFFILIATION")
    assert team_row(harness, "Carrow")["CURRENT_CONFERENCE"] == "SEC"


def test_slice_speaks_only_for_its_seasons(harness: Harness, league: League) -> None:
    """Scenario teams-conferences: A stored slice speaks only for the seasons it was requested
    for (the range slice's open Pac-12 spell does not claim 2026)."""
    before = team_season(harness, "Dunmore", 2025)
    after = team_season(harness, "Dunmore", 2026)
    assert before["CONFERENCE_NAME"] == "Pac-12"
    assert after["CONFERENCE_NAME"] == "Mountain West"
    assert (before["HAS_MEMBERSHIP_CONFLICT"], after["HAS_MEMBERSHIP_CONFLICT"]) == (False, False)
    assert {row["SEASON"] for row in team_seasons(harness, "SEASON > 2026")} == set()


def test_membership_conflict_flagged(harness: Harness, league: League) -> None:
    """Scenario teams-conferences: Conflicting memberships are resolved and flagged (the
    `TEAM_SEASON` part; `MEMBERSHIP_CONFLICT` belongs to `MODEL_CHECKS`)."""
    northam = team_season(harness, "Northam", 2014)
    assert northam["CONFERENCE_NAME"] == "Sun Belt"
    assert northam["HAS_MEMBERSHIP_CONFLICT"] is True
    assert team_season(harness, "Northam", 2013)["CONFERENCE_NAME"] == "Mountain West"
    assert team_season(harness, "Northam", 2026)["HAS_MEMBERSHIP_CONFLICT"] is False
    conflicts = harness.query(
        f"SELECT TEAM_ID, SEASON FROM {harness.model}.TEAM_SEASON WHERE HAS_MEMBERSHIP_CONFLICT"
    )
    assert conflicts == [(lt.team("Northam").id, 2014)]


def test_power_tiers_follow_era(harness: Harness, league: League) -> None:
    """Scenario teams-conferences: Power tiers follow the era."""
    for season, tier in ((2013, "AQ"), (2014, "P5"), (2024, "P4")):
        row = team_season(harness, "Ashford", season)
        assert (row["CONFERENCE_NAME"], row["CONFERENCE_TIER"]) == ("SEC", tier), season
        assert row["IS_POWER_CONFERENCE"] is True, season
    pac12 = harness.query(
        f"SELECT SEASON, CONFERENCE_TIER, IS_POWER_CONFERENCE FROM {harness.model}.TEAM_SEASON "
        "WHERE CONFERENCE_NAME = 'Pac-12' AND SEASON IN (2023, 2024) GROUP BY 1, 2, 3 ORDER BY 1"
    )
    assert pac12 == [(2023, "P5", True), (2024, "G6", False)]


def test_default_tiers_follow_era(harness: Harness, league: League) -> None:
    """Scenario teams-conferences: Other FBS conferences take the default tier of the era."""
    tiers = harness.query(
        f"SELECT CONFERENCE_NAME, SEASON, CONFERENCE_TIER, IS_POWER_CONFERENCE "
        f"FROM {harness.model}.TEAM_SEASON "
        "WHERE CONFERENCE_NAME IN ('Mountain West', 'FBS Independents') "
        "AND SEASON IN (2013, 2014, 2024) GROUP BY 1, 2, 3, 4 ORDER BY 1, 2"
    )
    assert tiers == [
        ("FBS Independents", 2013, "INDEPENDENT", False),
        ("FBS Independents", 2014, "INDEPENDENT", False),
        ("FBS Independents", 2024, "INDEPENDENT", False),
        ("Mountain West", 2013, "NON_AQ", False),
        ("Mountain West", 2014, "G5", False),
        ("Mountain West", 2024, "G6", False),
    ]
    fairhaven = team_season(harness, "Fairhaven", 2013)
    assert (fairhaven["CLASSIFICATION"], fairhaven["CONFERENCE_TIER"]) == ("fcs", "NON_AQ")
    early = team_seasons(harness, "SEASON = 1985")
    assert early and {(row["CONFERENCE_TIER"], row["IS_POWER_CONFERENCE"]) for row in early} == {
        (None, False)
    }


def test_an_fcs_conference_has_no_tier(harness: Harness, league: League) -> None:
    lowell = team_season(harness, "Lowell", 2024)
    assert (lowell["CONFERENCE_NAME"], lowell["CLASSIFICATION"]) == (lt.BIG_SKY, "fcs")
    assert (lowell["CONFERENCE_TIER"], lowell["IS_POWER_CONFERENCE"]) == (None, False)


def test_conference_from_games(harness: Harness, league: League) -> None:
    """Scenario teams-conferences: A team without a membership takes its conference from its
    games."""
    lowell = team_season(harness, "Lowell", 1985)
    assert (lowell["CONFERENCE_NAME"], lowell["CONFERENCE_SOURCE"]) == (lt.BIG_SKY, "GAMES")
    assert (lowell["CLASSIFICATION"], lowell["CLASSIFICATION_SOURCE"]) == ("fcs", "GAMES")
    assert lowell["CONFERENCE_DIVISION"] is None


def test_classification_from_games(harness: Harness, league: League) -> None:
    """Scenario teams-conferences: The games decide the classification when no transition is
    seeded."""
    fairhaven = team_season(harness, "Fairhaven", 2013)
    assert (fairhaven["CLASSIFICATION"], fairhaven["CLASSIFICATION_SOURCE"]) == ("fcs", "GAMES")
    assert (fairhaven["CONFERENCE_NAME"], fairhaven["CONFERENCE_SOURCE"]) == (
        "Mountain West",
        "AFFILIATION",
    )
    member_only = team_season(harness, "Fairhaven", 2020)
    assert (member_only["CLASSIFICATION"], member_only["CLASSIFICATION_SOURCE"]) == (
        "fbs",
        "AFFILIATION",
    )
    assert member_only["GAMES"] == 0


def test_result_dependent_games_decide_neither_conference_nor_classification(
    harness: Harness, league: League
) -> None:
    """model/teams-conferences: conference championship and postseason games do not count for the
    conference or the classification taken from games; they still count in the records."""
    pemberton = team_season(harness, "Pemberton", 2024)
    assert (pemberton["GAMES"], pemberton["FIXED_GAMES"]) == (1, 0)
    assert (pemberton["CONFERENCE_NAME"], pemberton["CONFERENCE_SOURCE"]) == (None, "NONE")
    assert (pemberton["CLASSIFICATION"], pemberton["CLASSIFICATION_SOURCE"]) == (None, "NONE")
    assert (pemberton["WINS"], pemberton["LOSSES"]) == (0, 1)
    assert pemberton["CLASSIFICATION_LABEL"] is None


def test_game_only_team_seasons(harness: Harness, league: League) -> None:
    """Scenario teams-conferences: Teams that appear only in games are listed (a team-season
    per season with a stored game)."""
    marston = team_seasons(harness, f"TEAM_ID = {lt.team('Marston').id}")
    assert [(row["SEASON"], row["SCHOOL"]) for row in marston] == [(1885, "Marston")]
    assert (marston[0]["GAMES"], marston[0]["LOSSES"]) == (2, 2)
    assert (marston[0]["CONFERENCE_SOURCE"], marston[0]["CLASSIFICATION"]) == ("NONE", "fbs")


def test_divisions_per_season(harness: Harness, league: League) -> None:
    """Scenario teams-conferences: Divisions are kept per season."""
    assert team_season(harness, "Eastport", 2014)["CONFERENCE_DIVISION"] == "Mountain"
    assert team_season(harness, "Eastport", 2024)["CONFERENCE_DIVISION"] is None
    assert team_season(harness, "Fairhaven", 2026)["CONFERENCE_DIVISION"] == "West"


def test_season_records(harness: Harness, league: League) -> None:
    """Scenario teams-conferences: Season records count completed games, and conference records
    leave out the championship game."""
    ashford = team_season(harness, "Ashford", 2024)
    record = tuple(ashford[c] for c in ("WINS", "LOSSES", "TIES", "CONF_WINS", "CONF_LOSSES"))
    assert record == (11, 3, 0, 7, 1)
    assert ashford["CONF_TIES"] == 0
    assert (ashford["GAMES"], ashford["FIXED_GAMES"]) == (14, 12)
    tie = team_season(harness, "Ashford", 1950)
    assert (tie["WINS"], tie["LOSSES"], tie["TIES"]) == (1, 0, 1)
    assert team_season(harness, "Brookvale", 1950)["TIES"] == 1
    unplayed = team_season(harness, "Brookvale", 1950)
    assert (unplayed["GAMES"], unplayed["WINS"], unplayed["LOSSES"]) == (3, 1, 0)


def test_points_and_conference_point_differential(harness: Harness, league: League) -> None:
    """Points count every completed game; the conference point differential leaves out the SEC
    championship game (28-21) like the conference record does."""
    ashford = team_season(harness, "Ashford", 2024)
    conference = [(mine, theirs) for *_, mine, theirs, flagged in ASHFORD_2024 if flagged]
    assert ashford["CONF_POINT_DIFF"] == sum(mine - theirs for mine, theirs in conference)
    postseason = ((28, 21), (24, 31))
    assert ashford["POINTS_FOR"] == sum(game[3] for game in ASHFORD_2024) + sum(
        mine for mine, _ in postseason
    )
    assert ashford["POINTS_AGAINST"] == sum(game[4] for game in ASHFORD_2024) + sum(
        theirs for _, theirs in postseason
    )


def test_elo_first_pregame_and_last_postgame(harness: Harness, league: League) -> None:
    ashford = team_season(harness, "Ashford", 2024)
    assert ashford["FIRST_PREGAME_ELO"] == 1650
    early = team_season(harness, "Ashford", 1885)
    assert (early["FIRST_PREGAME_ELO"], early["LAST_POSTGAME_ELO"]) == (None, None)
    current = team_season(harness, "Ashford", 2026)
    week9 = harness.query(
        f"SELECT TEAM_POSTGAME_ELO FROM {harness.model}.TEAM_GAME "
        f"WHERE SEASON = 2026 AND WEEK = 9 AND TEAM_ID = {lt.team('Ashford').id}"
    )
    assert week9 and current["LAST_POSTGAME_ELO"] == week9[0][0]


@pytest.fixture
def membership_copies(harness: Harness, league: League) -> Iterator[None]:
    yield
    for marker in (MEMBERSHIP_MARKER, f"{MEMBERSHIP_MARKER}-range", f"{MEMBERSHIP_MARKER}-old"):
        mirror_edit.remove_copies(harness, "CONFERENCES_AFFILIATIONS", marker)


def test_the_newest_slice_wins_only_for_the_seasons_it_requested(
    harness: Harness, league: League, membership_copies: None
) -> None:
    """Scenario teams-conferences: A stored slice speaks only for the seasons it was requested
    for, and the newest slice wins where slices overlap."""
    carrow = lt.team("Carrow").id
    year_slice = {"P_YEAR": "2024", "P_MIN_YEAR": "NULL", "P_MAX_YEAR": "NULL"}
    stored = mirror_edit.store_changed_copy(
        harness,
        "CONFERENCES_AFFILIATIONS",
        f"TEAM_ID = {carrow} AND CONFERENCE = 'SEC' AND P_MAX_YEAR = 2025",
        marker=MEMBERSHIP_MARKER,
        fetched_at=LATER_FETCH,
        row_offset=1000,
        changes={**year_slice, "CONFERENCE": "'ACC'", "START_YEAR": "2020"},
    )
    assert stored == 1
    mirror_edit.store_changed_copy(
        harness,
        "CONFERENCES_AFFILIATIONS",
        f"TEAM_ID = {carrow} AND CONFERENCE = 'Pac-12' AND P_MAX_YEAR = 2025",
        marker=f"{MEMBERSHIP_MARKER}-range",
        fetched_at=LATER_FETCH,
        row_offset=2000,
        changes={"P_MIN_YEAR": "2020", "P_MAX_YEAR": "2021", "CONFERENCE": "'Big 12'"},
    )
    mirror_edit.store_changed_copy(
        harness,
        "CONFERENCES_AFFILIATIONS",
        f"TEAM_ID = {carrow} AND CONFERENCE = 'Pac-12' AND P_MAX_YEAR = 2025",
        marker=f"{MEMBERSHIP_MARKER}-old",
        fetched_at=OLDER_FETCH,
        row_offset=3000,
        changes={"CONFERENCE": "'Sun Belt'"},
    )
    by_season = {
        row["SEASON"]: (row["CONFERENCE_NAME"], row["HAS_MEMBERSHIP_CONFLICT"])
        for row in team_seasons(harness, f"TEAM_ID = {carrow} AND SEASON BETWEEN 2019 AND 2026")
    }
    assert by_season == {
        2019: ("Pac-12", False),
        2020: ("Big 12", False),
        2021: ("Big 12", False),
        2022: ("Pac-12", False),
        2023: ("Pac-12", False),
        2024: ("ACC", False),
        2025: ("SEC", False),
        2026: ("SEC", False),
    }
