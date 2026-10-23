"""Integration tests of model/as-of: `AS_OF_POINT`, `CURRENT_AS_OF`, `GAME_AS_OF`, `RANKING_AS_OF`
and `TEAM_SEASON_AS_OF` over the synthetic league.

Read-only module (marker `model_readonly`). Expectations are the spec's facts and the league's
own bodies; values of other views (`GAME`, `RATING_SP`, `TEAM_SEASON`) are used only where their own
tests cover them. The partly played week and the leak-free invariant change data: they are in
`tests/it/test_model_variants_it.py`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from tests.it.harness import Harness
from tests.synthetic import league_teams as lt
from tests.synthetic.league import League
from tests.synthetic.league_games import game_week_seq

PLAN_VARIANT = "model"
pytestmark = [pytest.mark.it, pytest.mark.model_readonly]

ASHFORD = lt.team("Ashford").id
KICKOFF_FORMAT = "%Y-%m-%dT%H:%M:%S.000Z"


def moment(value: Any) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))


def points(harness: Harness, season: int) -> dict[int, dict[str, Any]]:
    rows = harness.query_dicts(
        f"SELECT * FROM {harness.model}.AS_OF_POINT WHERE AS_OF_SEASON = {season}"
    )
    return {int(row["AS_OF_WEEK_SEQ"]): row for row in rows}


def games_as_of(harness: Harness, season: int, sequence: int) -> dict[int, dict[str, Any]]:
    rows = harness.query_dicts(
        f"SELECT * FROM {harness.model}.GAME_AS_OF "
        f"WHERE AS_OF_SEASON = {season} AND AS_OF_WEEK_SEQ = {sequence}"
    )
    return {int(row["GAME_ID"]): row for row in rows}


def team_as_of(harness: Harness, season: int, sequence: int, team: int = ASHFORD) -> dict[str, Any]:
    rows = harness.query_dicts(
        f"SELECT * FROM {harness.model}.TEAM_SEASON_AS_OF WHERE AS_OF_SEASON = {season} "
        f"AND AS_OF_WEEK_SEQ = {sequence} AND TEAM_ID = {team}"
    )
    assert len(rows) == 1, (season, sequence, team)
    return rows[0]


def rankings_as_of(harness: Harness, season: int, sequence: int) -> dict[str, set[int]]:
    """The visible label sequences per poll code at the point."""
    rows = harness.query(
        f"SELECT POLL_CODE, WEEK_SEQ FROM {harness.model}.RANKING_AS_OF "
        f"WHERE AS_OF_SEASON = {season} AND AS_OF_WEEK_SEQ = {sequence}"
    )
    found: dict[str, set[int]] = {}
    for code, label in rows:
        found.setdefault(code, set()).add(int(label))
    return found


def first_kickoff(league: League, season: int, sequence: int) -> datetime:
    return min(
        datetime.strptime(g["startDate"], KICKOFF_FORMAT)
        for g in league.game_bodies(season)
        if game_week_seq(g) == sequence
    )


# --- points ------------------------------------------------------------------------------------


def test_points_match_refresh_arguments(harness: Harness, league: League) -> None:
    """Scenario as-of: As-of points match the refresh arguments."""
    season = points(harness, 2024)
    assert sorted(season) == [0, *range(1, 16), 101]
    week9 = season[9]
    assert (week9["AS_OF_WEEK"], week9["AS_OF_SEASON_TYPE"]) == (9, "regular")
    assert (week9["PREVIOUS_WEEK_SEQ"], week9["NEXT_WEEK_SEQ"]) == (8, 10)
    assert week9["LAST_REGULAR_WEEK_SEQ"] == 15
    assert moment(week9["CUTOFF_AT"]) == first_kickoff(league, 2024, 10)
    assert week9["AS_OF_LABEL"] == "2024, after week 9"
    final = season[101]
    assert (final["AS_OF_WEEK"], final["AS_OF_SEASON_TYPE"]) == (1, "postseason")
    assert (final["PREVIOUS_WEEK_SEQ"], final["NEXT_WEEK_SEQ"]) == (15, None)
    assert final["CUTOFF_AT"] is None
    assert final["IS_SEASON_COMPLETE"] is True
    assert final["AS_OF_LABEL"] == "2024, after postseason week 1"
    preseason = season[0]
    assert (preseason["AS_OF_WEEK"], preseason["AS_OF_SEASON_TYPE"]) == (0, "regular")
    assert (preseason["PREVIOUS_WEEK_SEQ"], preseason["NEXT_WEEK_SEQ"]) == (None, 1)
    assert moment(preseason["CUTOFF_AT"]) == first_kickoff(league, 2024, 1)
    assert [s for s, row in season.items() if row["IS_SEASON_COMPLETE"]] == [101]


def test_current_as_of(harness: Harness, league: League) -> None:
    """Scenario as-of: The current point is the newest finished week."""
    current = harness.query_dicts(f"SELECT * FROM {harness.model}.CURRENT_AS_OF")
    assert len(current) == 1
    row = current[0]
    assert (row["AS_OF_SEASON"], row["AS_OF_WEEK"]) == (2026, 9)
    assert row["AS_OF_SEASON_TYPE"] == "regular"
    assert row["AS_OF_WEEK_SEQ"] == 9
    assert moment(row["CUTOFF_AT"]) == datetime(2026, 10, 29, 23, 30)
    flagged = harness.query(
        f"SELECT AS_OF_SEASON, AS_OF_WEEK_SEQ FROM {harness.model}.AS_OF_POINT WHERE IS_CURRENT"
    )
    assert flagged == [(2026, 9)]


# --- games -------------------------------------------------------------------------------------


def test_results_after_point_hidden(harness: Harness, league: League) -> None:
    """Scenario as-of: Results after the point are hidden."""
    rows = games_as_of(harness, 2026, 5)
    played = rows[league.game_id("ashford_2026_w5")]
    assert played["GAME_STATUS_AS_OF"] == "PLAYED"
    assert None not in (played["HOME_POINTS"], played["AWAY_POINTS"], played["MARGIN"])
    assert played["WINNER_TEAM_ID"] == ASHFORD
    assert played["HOME_POSTGAME_ELO"] is not None and played["AWAY_POSTGAME_ELO"] is not None
    following = rows[league.game_id("ashford_2026_w6")]
    assert following["GAME_STATUS_AS_OF"] == "NEXT"
    assert following["HOME_PREGAME_ELO"] is not None and following["AWAY_PREGAME_ELO"] is not None
    hidden = ("HOME_POINTS", "AWAY_POINTS", "MARGIN", "WINNER_TEAM_ID", "IS_TIE",
              "HOME_POSTGAME_ELO", "AWAY_POSTGAME_ELO")  # fmt: skip
    assert [following[column] for column in hidden] == [None] * len(hidden)
    later = rows[league.game_id("ashford_2026_w7")]
    assert later["GAME_STATUS_AS_OF"] == "REMAINING"
    assert [later[c] for c in (*hidden, "HOME_PREGAME_ELO", "AWAY_PREGAME_ELO")] == [None] * 9


def test_result_dependent_games_hidden(harness: Harness, league: League) -> None:
    """Scenario as-of: Games whose participants depend on results are hidden until they are
    known."""
    names = {
        league.game_id(name): name
        for name in (
            "sec_championship_2024",
            "cfp_first_round_2024",
            "cfp_quarterfinal_2024",
            "harbor_bowl_2024",
        )
    }

    def visible(sequence: int) -> set[str]:
        return {names[i] for i in games_as_of(harness, 2024, sequence) if i in names}

    assert visible(9) == set()
    assert visible(14) == {"sec_championship_2024"}
    championship = games_as_of(harness, 2024, 14)[league.game_id("sec_championship_2024")]
    assert championship["GAME_STATUS_AS_OF"] == "NEXT" and championship["HAS_TEAMS"] is True
    assert visible(15) == {"sec_championship_2024", "cfp_first_round_2024", "harbor_bowl_2024"}
    assert visible(101) == set(names.values())


def test_records_up_to_point(harness: Harness, league: League) -> None:
    """Scenario as-of: Records count only games up to the point."""
    preseason = team_as_of(harness, 2026, 0)
    assert (preseason["WINS"], preseason["LOSSES"], preseason["GAMES_PLAYED"]) == (0, 0, 0)
    scheduled = [
        g for g in league.game_bodies(2026) if ASHFORD in (g.get("homeId"), g.get("awayId"))
    ]
    assert preseason["GAMES_REMAINING"] == len(scheduled) == 12
    week5 = team_as_of(harness, 2026, 5)
    assert (week5["WINS"], week5["LOSSES"]) == (4, 1)
    week9 = team_as_of(harness, 2026, 9)
    assert (week9["WINS"], week9["LOSSES"], week9["GAMES_REMAINING"]) == (7, 2, 3)
    assert week9["NEXT_GAME_ID"] == league.game_id("ashford_2026_w10")


def test_remaining_games_known_participants(harness: Harness, league: League) -> None:
    """Scenario as-of: Remaining games count only games with known participants."""
    week9 = team_as_of(harness, 2024, 9)
    assert week9["GAMES_REMAINING"] == 4
    assert week9["NEXT_GAME_ID"] == league.game_id("ashford_2024_w10")
    week14 = team_as_of(harness, 2024, 14)
    assert week14["GAMES_REMAINING"] == 1
    assert week14["NEXT_GAME_ID"] == league.game_id("sec_championship_2024")


def test_line_visibility(harness: Harness, league: League) -> None:
    """Scenario as-of: A line is visible only once it was known."""

    def lines(season: int, sequence: int, week: int) -> tuple[int, int, int]:
        rows = harness.query(
            f"SELECT COUNT(g.GAME_ID), COUNT(a.PREGAME_SPREAD), COUNT(a.HOME_PREGAME_WIN_PROB) "
            f"FROM {harness.model}.GAME g JOIN {harness.model}.GAME_AS_OF a "
            f"ON a.GAME_ID = g.GAME_ID AND a.AS_OF_SEASON = {season} "
            f"AND a.AS_OF_WEEK_SEQ = {sequence} "
            f"WHERE g.SEASON = {season} AND g.WEEK_SEQ = {week} AND g.HAS_PREGAME_WP"
        )
        return tuple(int(value) for value in rows[0])  # type: ignore[return-value]

    stored, spreads, probabilities = lines(2026, 9, 10)
    assert stored > 0 and spreads == probabilities == stored
    stored, spreads, probabilities = lines(2026, 9, 11)
    assert stored > 0 and spreads == probabilities == 0
    stored, spreads, probabilities = lines(2024, 9, 9)
    assert stored > 0 and spreads == probabilities == stored
    stored, spreads, probabilities = lines(2024, 9, 10)
    assert stored > 0 and spreads == probabilities == 0


def test_engine_inputs_are_decimals(harness: Harness, league: League) -> None:
    """Scenario as-of: Engine inputs are fixed-scale decimals."""
    types = dict(
        harness.query(
            "SELECT COLUMN_TABLE || '.' || COLUMN_NAME, COLUMN_TYPE FROM EXA_ALL_COLUMNS "
            f"WHERE COLUMN_SCHEMA = '{harness.model}' "
            "AND COLUMN_TABLE IN ('GAME_AS_OF', 'TEAM_SEASON_AS_OF')"
        )
    )
    assert types["GAME_AS_OF.PREGAME_SPREAD"] == "DECIMAL(6,2)"
    assert types["GAME_AS_OF.HOME_PREGAME_WIN_PROB"] == "DECIMAL(5,4)"
    assert types["TEAM_SEASON_AS_OF.SP_PRIOR_RATING"] == "DECIMAL(6,2)"
    assert types["TEAM_SEASON_AS_OF.SP_CURRENT_RATING"] == "DECIMAL(6,2)"
    for column in ("WINS", "LOSSES", "CONF_WINS", "CONF_LOSSES", "CONF_POINT_DIFF", "ELO_AS_OF"):
        assert types[f"TEAM_SEASON_AS_OF.{column}"].startswith("DECIMAL("), column
        assert types[f"TEAM_SEASON_AS_OF.{column}"].endswith(",0)"), column
    line = games_as_of(harness, 2024, 14)[league.game_id("ashford_brookvale_2024")]
    assert Decimal(str(line["PREGAME_SPREAD"])) == Decimal("-7.50")
    assert Decimal(str(line["HOME_PREGAME_WIN_PROB"])) == Decimal("0.7000")


# --- rankings, SP+, Elo ------------------------------------------------------------------------


def test_poll_visibility(harness: Harness, league: League) -> None:
    """Scenario as-of: Polls become visible when they are released."""
    assert rankings_as_of(harness, 2024, 0).get("AP") == {1}
    assert "CFP" not in rankings_as_of(harness, 2024, 0)
    week9 = rankings_as_of(harness, 2024, 9)
    assert week9.get("AP") == {10} and "CFP" not in week9
    week10 = rankings_as_of(harness, 2024, 10)
    assert week10.get("AP") == {11} and week10.get("CFP") == {10}
    sequences = (0, *range(1, 16), 101)
    finals = [s for s in sequences if rankings_as_of(harness, 2024, s).get("AP") == {101}]
    assert finals == [101]


def test_ranking_fetched_before_cutoff(harness: Harness, league: League) -> None:
    """Scenario as-of: A ranking fetched before the next kickoff is visible at that point."""
    assert rankings_as_of(harness, 2026, 9).get("CFP") == {10}
    assert "CFP" not in rankings_as_of(harness, 2026, 8)
    ranks = harness.query(
        f"SELECT CFP_RANK FROM {harness.model}.TEAM_SEASON_AS_OF "
        "WHERE AS_OF_SEASON = 2026 AND AS_OF_WEEK_SEQ = 9 AND CFP_RANK IS NOT NULL"
    )
    assert ranks


def test_sp_visibility(harness: Harness, league: League) -> None:
    """Scenario as-of: The current season's SP+ is visible only from the point when it was
    fetched."""
    ratings = dict(
        harness.query(
            f"SELECT SEASON, SP_RATING FROM {harness.model}.RATING_SP WHERE TEAM_ID = {ASHFORD}"
        )
    )
    week8, week9 = team_as_of(harness, 2026, 8), team_as_of(harness, 2026, 9)
    assert week8["SP_PRIOR_RATING"] == week9["SP_PRIOR_RATING"] == ratings[2025]
    assert week8["SP_CURRENT_RATING"] is None and week8["SP_CURRENT_FETCHED_AT"] is None
    assert week9["SP_CURRENT_RATING"] == ratings[2026]
    assert team_as_of(harness, 2024, 9)["SP_CURRENT_RATING"] is None
    assert team_as_of(harness, 2024, 101)["SP_CURRENT_RATING"] == ratings[2024]


def test_team_elo_as_of(harness: Harness, league: League) -> None:
    """Scenario as-of: Team Elo uses only completed games."""
    week9 = team_as_of(harness, 2024, 9)
    assert (week9["ELO_AS_OF"], week9["ELO_SOURCE"]) == (1702, "POSTGAME")
    preseason = team_as_of(harness, 2024, 0)
    assert (preseason["ELO_AS_OF"], preseason["ELO_SOURCE"]) == (1650, "PRESEASON")


# --- consistency with TEAM_SEASON ----------------------------------------------------------------

ATTRIBUTES = (
    "TEAM_ID, SCHOOL, CONFERENCE_NAME, CONFERENCE_DIVISION, CONFERENCE_TIER, IS_POWER_CONFERENCE, "
    "CLASSIFICATION"
)
RECORDS = (
    "TEAM_ID, WINS, LOSSES, TIES, CONF_WINS, CONF_LOSSES, CONF_TIES, POINTS_FOR, POINTS_AGAINST, "
    "CONF_POINT_DIFF"
)


def differences(harness: Harness, columns: str, season: int, sequence: int) -> list[Any]:
    """Rows of the point that differ from TEAM_SEASON, both ways (`MINUS`)."""
    as_of = (
        f"SELECT {columns} FROM {harness.model}.TEAM_SEASON_AS_OF "
        f"WHERE AS_OF_SEASON = {season} AND AS_OF_WEEK_SEQ = {sequence}"
    )
    whole = (
        f"SELECT {columns} FROM {harness.model}.TEAM_SEASON WHERE SEASON = {season} "
        "AND (CONFERENCE_SOURCE = 'AFFILIATION' OR FIXED_GAMES > 0)"
    )
    return harness.query(f"{as_of} MINUS {whole}") + harness.query(f"{whole} MINUS {as_of}")


@pytest.mark.parametrize(
    ("season", "sequence"), [(2014, 0), (2024, 0), (2024, 9), (2024, 15), (2026, 5), (2026, 9)]
)
def test_team_attributes_equal_team_season(
    harness: Harness, league: League, season: int, sequence: int
) -> None:
    """Conference, division, tier and classification are the season's at every point (they come
    from memberships and FIXED games), so they equal TEAM_SEASON for the kept team-seasons."""
    assert differences(harness, ATTRIBUTES, season, sequence) == []


@pytest.mark.parametrize("season", [2014, 2024, 2025])
def test_records_at_last_point_equal_team_season(
    harness: Harness, league: League, season: int
) -> None:
    """At a complete season's last point every game is visible, so the records are the season's."""
    last = max(points(harness, season))
    assert differences(harness, RECORDS, season, last) == []
