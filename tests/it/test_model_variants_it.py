"""Integration tests of the model over league variants: data that changes per test.

Every test seeds its own league (`harness.seed_league(variant)` empties and re-seeds), and the
per-test reset empties the data again, so no test order leaks state. A test that changes a model
reference table restores it before it ends. The `MODEL_CHECKS` parts of the shared scenarios are
added with their view.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from tests.it.harness import Harness
from tests.synthetic import league_teams as lt
from tests.synthetic.league import (
    FETCHED_LATER,
    FETCHED_LATER_RANKINGS,
    SP_OVERRIDES,
    League,
    mutate_after,
    season_key,
    with_duplicate_rows,
    with_nonfinal_prior,
    with_renamed_conference,
    with_thursday_result,
    with_unaffiliated_fbs_team,
)
from tests.synthetic.league_games import game_week_seq

PLAN_VARIANT = "model"
pytestmark = pytest.mark.it

NORTHAM = lt.team("Northam").id


@pytest.fixture(autouse=True)
def model(harness: Harness, model_deployed: None) -> None:
    """Every test runs against the deployed `sql/model/`."""


def moment(value: Any) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))


def team_seasons(harness: Harness, where: str) -> list[dict[str, Any]]:
    return harness.query_dicts(
        f"SELECT * FROM {harness.model}.TEAM_SEASON WHERE {where} ORDER BY TEAM_ID, SEASON"
    )


def team_season(harness: Harness, name: str, season: int) -> dict[str, Any]:
    rows = team_seasons(harness, f"TEAM_ID = {lt.team(name).id} AND SEASON = {season}")
    assert len(rows) == 1, (name, season)
    return rows[0]


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
    harness.rebuild_model()
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
    kickoff = harness.query(
        f"SELECT MIN(START_DATE) FROM {harness.core}.GAMES "
        "WHERE SEASON = 2024 AND SEASON_TYPE = 'regular' AND WEEK = 14"
    )
    # FIRST_GAME_AT is the week's first kickoff, not the calendar's (decision [39]).
    assert moment(week[0]["FIRST_GAME_AT"]) == moment(kickoff[0][0]) != newest[2]


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
        harness.rebuild_model()


def test_classification_from_transition_seed(harness: Harness, northam_transition: None) -> None:
    """Scenario teams-conferences: A seeded transition overrides the recorded classification:
    every 2014 game of Northam classifies Northam as `fcs`, and so does `TEAM_SEASON` inside the
    seeded range (source `TRANSITION`), while a later season falls back to the membership."""
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
    seasons = {
        row["SEASON"]: (row["CLASSIFICATION"], row["CLASSIFICATION_SOURCE"])
        for row in team_seasons(harness, f"TEAM_ID = {NORTHAM} AND SEASON BETWEEN 2013 AND 2015")
    }
    assert seasons == {
        2013: ("fcs", "TRANSITION"),
        2014: ("fcs", "TRANSITION"),
        2015: ("fbs", "AFFILIATION"),
    }


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


def test_duplicate_rating_appears_once(harness: Harness) -> None:
    """Scenario ratings-rankings: A rating or a ranking stored twice appears once (the
    `RATING_SP` part: Ashford's 2026 SP+ is in the season slice and in a later team slice)."""
    harness.seed_league(with_duplicate_rows)
    ashford = lt.team("Ashford").id
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.core}.RATINGS_SP WHERE YEAR_ = 2026 AND TEAM = 'Ashford'"
    ) == [(2,)]
    rows = harness.query_dicts(
        f"SELECT * FROM {harness.model}.RATING_SP WHERE SEASON = 2026 AND SCHOOL = 'Ashford'"
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["TEAM_ID"] == ashford and row["IS_TEAM_MATCHED"] is True
    assert Decimal(str(row["SP_RATING"])) == Decimal("24.00")
    assert moment(row["FETCHED_AT"]) == FETCHED_LATER
    assert row["IS_FINAL"] is False
    for season in (2013, 2014, 2023, 2024, 2025, 2026):
        stored = harness.query(
            f"SELECT COUNT(DISTINCT TEAM) FROM {harness.core}.RATINGS_SP "
            f"WHERE YEAR_ = {season} AND TEAM <> 'nationalAverages'"
        )
        assert (
            harness.query(f"SELECT COUNT(*) FROM {harness.model}.RATING_SP WHERE SEASON = {season}")
            == stored
        ), season


def test_nonfinal_previous_season_slice_is_not_final(harness: Harness) -> None:
    """Scenario ratings-rankings: A prior that is not final is used and reported (the
    `RATING_SP` part: the 2025 rows say so; the other seasons keep their own flag)."""
    harness.seed_league(with_nonfinal_prior)
    flags = harness.query(
        f"SELECT SEASON, MIN(CASE WHEN IS_FINAL THEN 1 ELSE 0 END), "
        f"MAX(CASE WHEN IS_FINAL THEN 1 ELSE 0 END) FROM {harness.model}.RATING_SP "
        "GROUP BY SEASON ORDER BY SEASON"
    )
    assert {row[0]: (row[1], row[2]) for row in flags} == {
        2013: (1, 1),
        2014: (1, 1),
        2023: (1, 1),
        2024: (1, 1),
        2025: (0, 0),
        2026: (0, 0),
    }


def test_nonfinal_prior_reported(harness: Harness) -> None:
    """Scenario ratings-rankings: A prior that is not final is used and reported (the
    `TEAM_SEASON` part; `SP_PRIOR_NOT_FINAL` belongs to `MODEL_CHECKS`)."""
    harness.seed_league(with_nonfinal_prior)
    ashford = team_season(harness, "Ashford", 2026)
    assert Decimal(str(ashford["SP_PRIOR_RATING"])) == Decimal(str(SP_OVERRIDES[(2025, "Ashford")]))
    assert (ashford["HAS_SP_PRIOR"], ashford["SP_PRIOR_IS_FINAL"]) == (True, False)
    final = team_season(harness, "Ashford", 2025)
    assert (final["HAS_SP_PRIOR"], final["SP_PRIOR_IS_FINAL"]) == (True, True)
    priors = harness.query(
        f"SELECT DISTINCT SP_PRIOR_IS_FINAL FROM {harness.model}.TEAM_SEASON "
        "WHERE SEASON = 2026 AND HAS_SP_PRIOR"
    )
    assert priors == [(False,)]


def test_a_team_without_any_membership(harness: Harness) -> None:
    """Quarry, an FBS team without a conference, is a 2026 team-season from its one game: no
    conference, no tier, classified fbs by its game."""
    harness.seed_league(with_unaffiliated_fbs_team)
    quarry = harness.query_dicts(
        f"SELECT * FROM {harness.model}.TEAM_SEASON WHERE TEAM_ID = {lt.QUARRY.id}"
    )
    assert len(quarry) == 1
    row = quarry[0]
    assert (row["SEASON"], row["SCHOOL"], row["GAMES"], row["WINS"]) == (2026, "Quarry", 1, 1)
    assert (row["CONFERENCE_NAME"], row["CONFERENCE_SOURCE"]) == (None, "NONE")
    assert (row["CLASSIFICATION"], row["CLASSIFICATION_SOURCE"]) == ("fbs", "GAMES")
    assert (row["CONFERENCE_TIER"], row["IS_POWER_CONFERENCE"]) == (None, False)


def test_a_renamed_power_conference_loses_its_tier(harness: Harness) -> None:
    """A CFBD rename of a power conference falls to the default tier of the era, which is what
    `POWER_CONFERENCE_MISSING` (model/refresh-step) stops the refresh for."""
    harness.seed_league(with_renamed_conference)
    renamed = team_season(harness, "Ashford", 2024)
    assert (renamed["CONFERENCE_NAME"], renamed["CONFERENCE_TIER"]) == ("Southeastern", "G6")
    assert renamed["IS_POWER_CONFERENCE"] is False
    before = team_season(harness, "Ashford", 2023)
    assert (before["CONFERENCE_NAME"], before["CONFERENCE_TIER"]) == ("SEC", "P5")
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.TEAM_SEASON "
        "WHERE SEASON = 2024 AND CONFERENCE_NAME = 'SEC'"
    ) == [(0,)]


def test_duplicate_rows_keep_one_row_per_team_season(harness: Harness) -> None:
    """Scenario games and ratings-rankings: a game, a rating and a poll stored twice count once
    in `TEAM_SEASON`."""
    league = harness.seed_league(with_duplicate_rows)
    target = league.game_id("carrow_dunmore_2026_w3")
    keys = harness.query(f"SELECT TEAM_ID, SEASON FROM {harness.model}.TEAM_SEASON")
    assert len(keys) == len(set(keys))
    carrow = team_season(harness, "Carrow", 2026)
    games = harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.TEAM_GAME "
        f"WHERE SEASON = 2026 AND TEAM_ID = {lt.team('Carrow').id}"
    )
    assert carrow["GAMES"] == games[0][0]
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.TEAM_GAME WHERE GAME_ID = {target}"
    ) == [(2,)]
    ashford = team_season(harness, "Ashford", 2026)
    assert Decimal(str(ashford["SP_RATING"])) == Decimal("24.00")


# --- as-of (model/as-of) ---------------------------------------------------------------------

AS_OF_VIEWS = ("GAME_AS_OF", "TEAM_SEASON_AS_OF", "RANKING_AS_OF")


def test_current_as_of_ignores_partly_played_week(harness: Harness) -> None:
    """Scenario as-of: A partly played week does not move the current point."""
    league = harness.seed_league(with_thursday_result)
    thursday = league.game_id("thursday_2026_w10")
    assert harness.query(
        f"SELECT IS_COMPLETED FROM {harness.model}.GAME WHERE GAME_ID = {thursday}"
    ) == [(True,)]
    current = harness.query(
        f"SELECT AS_OF_SEASON, AS_OF_WEEK, AS_OF_SEASON_TYPE FROM {harness.model}.CURRENT_AS_OF"
    )
    assert current == [(2026, 9, "regular")]


def as_of_rows(harness: Harness, season: int, sequence: int) -> dict[str, list[tuple[str, ...]]]:
    """Every row of the three as-of views for the point, as sorted text tuples."""
    result = {}
    for view in AS_OF_VIEWS:
        rows = harness.query(
            f"SELECT * FROM {harness.model}.{view} "
            f"WHERE AS_OF_SEASON = {season} AND AS_OF_WEEK_SEQ = {sequence}"
        )
        result[view] = sorted(tuple(str(value) for value in row) for row in rows)
    return result


def with_changed_score(season: int, sequence: int) -> Any:
    """A variant that adds a point to the home score of one completed game of the point's week."""

    def change(league: League) -> League:
        games = league.get("GAMES", season_key("GAMES", season))
        body = [dict(game) for game in games.body]
        target = next(
            g for g in body if game_week_seq(g) == sequence and g.get("homePoints") is not None
        )
        target["homePoints"] += 1
        return league.with_slices(
            dataclasses.replace(games, body=body) if item is games else item
            for item in league.slices
        )

    return change


@pytest.mark.parametrize(("season", "sequence"), [(2024, 9), (2026, 5)])
def test_leak_free_invariant(harness: Harness, season: int, sequence: int) -> None:
    """Scenario as-of: No fact after the point changes an as-of answer, and the comparison detects a
    change of a visible fact (positive control)."""
    harness.seed_league()
    recorded = as_of_rows(harness, season, sequence)
    assert all(recorded[view] for view in AS_OF_VIEWS), recorded.keys()
    harness.seed_league(lambda league: mutate_after(league, season, sequence))
    after = as_of_rows(harness, season, sequence)
    for view in AS_OF_VIEWS:
        assert after[view] == recorded[view], view
    harness.seed_league(with_changed_score(season, sequence))
    control = as_of_rows(harness, season, sequence)
    assert control["GAME_AS_OF"] != recorded["GAME_AS_OF"]
    assert control["TEAM_SEASON_AS_OF"] != recorded["TEAM_SEASON_AS_OF"]
