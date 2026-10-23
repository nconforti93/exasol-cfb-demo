"""Integration tests of model/games: `GAME` and `CONFERENCE_CHAMPIONSHIP_GAME`.

Read-only module over the synthetic league (marker `model_readonly`). The expectations are the
spec's numbers, the league's own bodies and the Python restatement of the championship rule
(`tests/synthetic/rules.py`), never the SQL under test. The `TEAM_GAME` side of the shared
scenarios (`200_team_game.sql`) sits in the same tests; the `MODEL_CHECKS` ones belong to
`500_model_checks.sql`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from model.deploy import MODEL_DIR
from tests.it.harness import Harness, Mock
from tests.synthetic import league_teams as lt
from tests.synthetic.league import FETCHED_EARLY, League
from tests.synthetic.rules import detect_championships
from tests.synthetic.seed_sql import championship_ranges, power_conferences
from tests.synthetic.slices import mirror_columns
from vs.host.metaddl import fill_placeholders

PLAN_VARIANT = "model"
pytestmark = [pytest.mark.it, pytest.mark.model_readonly]

ASHFORD = lt.team("Ashford").id
BROOKVALE = lt.team("Brookvale").id
NORTHAM = lt.team("Northam").id
KINGSLEY = lt.team("Kingsley").id
HEAD_TO_HEAD = MODEL_DIR / "examples" / "head_to_head.sql"


def game(harness: Harness, league: League, name: str) -> dict[str, Any]:
    rows = harness.query_dicts(
        f"SELECT * FROM {harness.model}.GAME WHERE GAME_ID = {league.game_id(name)}"
    )
    assert len(rows) == 1, name
    return rows[0]


def team_games(harness: Harness, league: League, name: str) -> dict[int, dict[str, Any]]:
    """The `TEAM_GAME` rows of one game by team id."""
    rows = harness.query_dicts(
        f"SELECT * FROM {harness.model}.TEAM_GAME WHERE GAME_ID = {league.game_id(name)}"
    )
    by_team = {int(row["TEAM_ID"]): row for row in rows}
    assert len(by_team) == len(rows), name
    return by_team


def number(value: Any) -> Decimal | None:
    """A numeric cell as `Decimal` (pyexasol returns scaled `DECIMAL` values as text)."""
    return None if value is None else Decimal(str(value))


def moment(value: Any) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))


def column_types(harness: Harness, view: str) -> dict[str, str]:
    rows = harness.query(
        "SELECT COLUMN_NAME, COLUMN_TYPE FROM EXA_ALL_COLUMNS "
        f"WHERE COLUMN_SCHEMA = '{harness.model}' AND COLUMN_TABLE = '{view}'"
    )
    return {str(name): str(kind) for name, kind in rows}


def championships(harness: Harness, season: int | None = None) -> list[dict[str, Any]]:
    where = "" if season is None else f" WHERE SEASON = {season}"
    return harness.query_dicts(
        f"SELECT * FROM {harness.model}.CONFERENCE_CHAMPIONSHIP_GAME{where} ORDER BY GAME_ID"
    )


def league_games(league: League) -> list[dict[str, Any]]:
    return [dict(zip(mirror_columns("GAMES"), row, strict=True)) for row in league.rows("GAMES")]


# --- one row per game -------------------------------------------------------------------------


def test_every_stored_game_is_one_row_with_its_keys(harness: Harness, league: League) -> None:
    stored = league_games(league)
    assert harness.query(f"SELECT COUNT(*), COUNT(DISTINCT GAME_ID) FROM {harness.model}.GAME") == [
        (len(stored), len(stored))
    ]
    row = game(harness, league, "ashford_brookvale_2024")
    assert (row["SEASON"], row["SEASON_TYPE"], row["WEEK"], row["WEEK_SEQ"]) == (
        2024,
        "regular",
        14,
        14,
    )
    assert moment(row["START_AT"]) == datetime(2024, 11, 30, 17, 0)
    assert moment(row["FETCHED_AT"]) == FETCHED_EARLY
    assert (row["HOME_TEAM_ID"], row["HOME_TEAM"], row["AWAY_TEAM_ID"], row["AWAY_TEAM"]) == (
        ASHFORD,
        "Ashford",
        BROOKVALE,
        "Brookvale",
    )
    assert (row["HOME_CONFERENCE"], row["AWAY_CONFERENCE"]) == ("SEC", "SEC")
    assert (row["HOME_CLASSIFICATION"], row["AWAY_CLASSIFICATION"]) == ("fbs", "fbs")
    assert (row["VENUE_NAME"], row["IS_NEUTRAL_SITE"]) == ("Ashford Field", False)
    assert row["HAS_TEAMS"] is True and row["IS_START_TIME_TBD"] is False
    bowl = game(harness, league, "desert_bowl_2023")
    assert (bowl["WEEK"], bowl["WEEK_SEQ"], bowl["NOTES"]) == (1, 101, "Desert Bowl")


# --- results ---------------------------------------------------------------------------------


def test_results_winner_and_loser(harness: Harness, league: League) -> None:
    """Scenario games: Results name the winner and the loser (the `GAME` part)."""
    played = game(harness, league, "ashford_brookvale_2024")
    assert (played["HOME_POINTS"], played["AWAY_POINTS"]) == (31, 24)
    assert (played["WINNER_TEAM_ID"], played["LOSER_TEAM_ID"]) == (ASHFORD, BROOKVALE)
    assert (played["MARGIN"], played["TOTAL_POINTS"]) == (7, 55)
    assert (played["IS_COMPLETED"], played["HAS_RESULT"], played["IS_TIE"]) == (True, True, False)
    future = game(harness, league, "brookvale_ashford_2026_w12")
    for column in (
        "HOME_POINTS",
        "AWAY_POINTS",
        "MARGIN",
        "TOTAL_POINTS",
        "WINNER_TEAM_ID",
        "LOSER_TEAM_ID",
        "IS_TIE",
    ):
        assert future[column] is None, column
    assert (future["IS_COMPLETED"], future["HAS_RESULT"]) == (False, False)
    sides = team_games(harness, league, "ashford_brookvale_2024")
    assert (sides[ASHFORD]["GAME_RESULT"], sides[BROOKVALE]["GAME_RESULT"]) == ("W", "L")
    assert sides[ASHFORD]["HAS_RESULT"] is True and sides[BROOKVALE]["HAS_RESULT"] is True
    unplayed = team_games(harness, league, "brookvale_ashford_2026_w12")
    assert set(unplayed) == {ASHFORD, BROOKVALE}
    for side in unplayed.values():
        for column in ("GAME_RESULT", "POINTS_FOR", "POINTS_AGAINST", "MARGIN"):
            assert side[column] is None, column
        assert side["HAS_RESULT"] is False


def test_tie_has_no_winner(harness: Harness, league: League) -> None:
    """Scenario games: A tie has no winner (the `GAME` part)."""
    tie = game(harness, league, "tie_1950")
    assert (tie["HOME_POINTS"], tie["AWAY_POINTS"], tie["MARGIN"]) == (7, 7, 0)
    assert tie["IS_TIE"] is True and tie["HAS_RESULT"] is True
    assert tie["WINNER_TEAM_ID"] is None and tie["LOSER_TEAM_ID"] is None
    sides = team_games(harness, league, "tie_1950")
    assert [sides[ASHFORD]["GAME_RESULT"], sides[BROOKVALE]["GAME_RESULT"]] == ["T", "T"]
    assert [
        (s["POINTS_FOR"], s["POINTS_AGAINST"], number(s["MARGIN"])) for s in sides.values()
    ] == [(7, 7, Decimal(0))] * 2


# --- lines ------------------------------------------------------------------------------------


def test_pregame_lines_join_by_game(harness: Harness, league: League) -> None:
    """Scenario games: Pregame lines join by game id (the `GAME` part)."""
    lined = game(harness, league, "ashford_brookvale_2024")
    assert number(lined["PREGAME_SPREAD"]) == Decimal("-7.50")
    assert number(lined["HOME_PREGAME_WIN_PROB"]) == Decimal("0.7000")
    assert lined["HAS_PREGAME_WP"] is True
    assert moment(lined["PREGAME_WP_FETCHED_AT"]) == FETCHED_EARLY
    unlined = game(harness, league, "tie_1950")
    assert unlined["HAS_PREGAME_WP"] is False
    assert unlined["PREGAME_SPREAD"] is None and unlined["HOME_PREGAME_WIN_PROB"] is None
    assert unlined["PREGAME_WP_FETCHED_AT"] is None
    types = column_types(harness, "GAME")
    assert (types["PREGAME_SPREAD"], types["HOME_PREGAME_WIN_PROB"]) == (
        "DECIMAL(6,2)",
        "DECIMAL(5,4)",
    )
    sides = team_games(harness, league, "ashford_brookvale_2024")
    assert number(sides[ASHFORD]["TEAM_SPREAD"]) == Decimal("-7.50")
    assert number(sides[ASHFORD]["TEAM_PREGAME_WIN_PROB"]) == Decimal("0.7000")
    assert number(sides[BROOKVALE]["TEAM_SPREAD"]) == Decimal("7.50")
    assert number(sides[BROOKVALE]["TEAM_PREGAME_WIN_PROB"]) == Decimal("0.3000")
    assert sides[ASHFORD]["HAS_PREGAME_WP"] is True and sides[BROOKVALE]["HAS_PREGAME_WP"] is True
    for side in team_games(harness, league, "tie_1950").values():
        assert side["HAS_PREGAME_WP"] is False
        assert side["TEAM_SPREAD"] is None and side["TEAM_PREGAME_WIN_PROB"] is None
    team_types = column_types(harness, "TEAM_GAME")
    assert (team_types["TEAM_SPREAD"], team_types["TEAM_PREGAME_WIN_PROB"]) == (
        "DECIMAL(6,2)",
        "DECIMAL(5,4)",
    )
    assert team_types["MARGIN"] == "DECIMAL(9,0)"


def test_every_stored_line_lands_on_its_game(harness: Harness, league: League) -> None:
    lines = [
        dict(zip(mirror_columns("METRICS_WP_PREGAME"), row, strict=True))
        for row in league.rows("METRICS_WP_PREGAME")
    ]
    assert harness.query(f"SELECT COUNT(*) FROM {harness.model}.GAME WHERE HAS_PREGAME_WP") == [
        (len({line["GAME_ID"] for line in lines}),)
    ]
    wanted = {line["GAME_ID"]: line["SPREAD"] for line in lines if line["SEASON"] == 2014}
    stored = harness.query(
        f"SELECT GAME_ID, PREGAME_SPREAD FROM {harness.model}.GAME "
        "WHERE SEASON = 2014 AND HAS_PREGAME_WP"
    )
    assert {int(g): number(s) for g, s in stored} == {
        g: Decimal(str(s)).quantize(Decimal("0.01")) for g, s in wanted.items()
    }


def test_team_sides_of_a_line_are_complements(harness: Harness, league: League) -> None:
    pairs = harness.query(
        "SELECT h.GAME_ID, h.TEAM_SPREAD + a.TEAM_SPREAD, "
        "h.TEAM_PREGAME_WIN_PROB + a.TEAM_PREGAME_WIN_PROB "
        f"FROM {harness.model}.TEAM_GAME h JOIN {harness.model}.TEAM_GAME a "
        "ON a.GAME_ID = h.GAME_ID AND a.TEAM_ID <> h.TEAM_ID "
        "WHERE h.IS_HOME_TEAM AND h.HAS_PREGAME_WP"
    )
    assert pairs
    assert {(number(spread), number(prob)) for _, spread, prob in pairs} == {
        (Decimal("0.00"), Decimal("1.0000"))
    }
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.TEAM_GAME WHERE HAS_PREGAME_WP "
        "AND (TEAM_SPREAD IS NULL OR TEAM_PREGAME_WIN_PROB IS NULL)"
    ) == [(0,)]


# --- postseason -------------------------------------------------------------------------------


def test_postseason_kinds(harness: Harness, league: League) -> None:
    """Scenario games: Postseason games are typed."""
    quarterfinal = game(harness, league, "cfp_quarterfinal_2024")
    assert quarterfinal["POSTSEASON_KIND"] == "CFP"
    assert (quarterfinal["CFP_ROUND"], quarterfinal["CFP_ROUND_NAME"]) == (
        "quarterfinal",
        "Quarterfinal",
    )
    assert quarterfinal["CFP_ROUND_SEQ"] == 2
    assert (quarterfinal["CFP_HOME_SEED"], quarterfinal["CFP_AWAY_SEED"]) == (1, 8)
    assert quarterfinal["BOWL_NAME"] == "Sugar Bowl"
    assert quarterfinal["WEEK_SEQ"] == 101 and quarterfinal["IS_NEUTRAL_SITE"] is True
    assert quarterfinal["PARTICIPANTS_RULE"] == "PREVIOUS_ROUND"
    first_round = game(harness, league, "cfp_first_round_2024")
    assert (first_round["POSTSEASON_KIND"], first_round["CFP_ROUND"]) == ("CFP", "firstRound")
    assert first_round["CFP_ROUND_SEQ"] == 1 and first_round["BOWL_NAME"] is None
    assert (first_round["CFP_HOME_SEED"], first_round["CFP_AWAY_SEED"]) == (8, 9)
    assert first_round["PARTICIPANTS_RULE"] == "SELECTION"
    bowls = (("desert_bowl_2023", "Desert Bowl"), ("harbor_bowl_2024", "Harbor Bowl"))
    for name, bowl_name in bowls:
        bowl = game(harness, league, name)
        assert (bowl["POSTSEASON_KIND"], bowl["BOWL_NAME"]) == ("BOWL", bowl_name), name
        assert bowl["CFP_ROUND"] is None and bowl["CFP_ROUND_SEQ"] is None, name
        assert bowl["PARTICIPANTS_RULE"] == "SELECTION", name
    regular = game(harness, league, "ashford_brookvale_2024")
    assert (regular["POSTSEASON_KIND"], regular["BOWL_NAME"], regular["CFP_ROUND_SEQ"]) == (
        None,
        None,
        None,
    )
    assert regular["PARTICIPANTS_RULE"] == "FIXED"


def test_non_fbs_postseason_waits(harness: Harness, league: League) -> None:
    """Scenario games: Postseason games without an FBS team wait for their own week."""
    playoff = game(harness, league, "fcs_playoff_2024")
    assert (playoff["HOME_CLASSIFICATION"], playoff["AWAY_CLASSIFICATION"]) == ("fcs", "fcs")
    assert playoff["INVOLVES_FBS"] is False
    assert playoff["POSTSEASON_KIND"] == "NON_FBS"
    assert playoff["PARTICIPANTS_RULE"] == "PREVIOUS_ROUND"
    assert playoff["BOWL_NAME"] is None and playoff["CFP_ROUND_SEQ"] is None


# --- conference championship games -----------------------------------------------------------


def test_conference_championship_rule(harness: Harness, league: League) -> None:
    """Scenario games: A conference championship game is recognized within its conference's
    championship seasons."""
    sec = game(harness, league, "sec_championship_2024")
    assert sec["IS_CONF_CHAMPIONSHIP"] is True
    assert sec["PARTICIPANTS_RULE"] == "CONF_CHAMPIONSHIP"
    found = championships(harness, 2024)
    assert [(row["GAME_ID"], row["CONFERENCE_NAME"]) for row in found] == [
        (league.game_id("sec_championship_2024"), "SEC")
    ]
    detected = found[0]
    assert (detected["WEEK"], detected["WEEK_SEQ"]) == (15, 15)
    assert detected["NOTES"] == "SEC Championship"
    assert (detected["HOME_TEAM"], detected["AWAY_TEAM"]) == ("Ashford", "Carrow")
    assert detected["IS_COMPLETED"] is True and detected["WINNER_TEAM_ID"] == ASHFORD
    pac12 = game(harness, league, "pac12_final_2024")
    assert pac12["IS_CONF_CHAMPIONSHIP"] is False and pac12["PARTICIPANTS_RULE"] == "FIXED"
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.GAME "
        "WHERE (SEASON = 1985 OR HOME_CONFERENCE = 'Pac-12') AND IS_CONF_CHAMPIONSHIP"
    ) == [(0,)]
    assert championships(harness, 1985) == []


def test_game_after_championship_not_marked(harness: Harness, league: League) -> None:
    """Scenario games: A same-conference game after the championship game is not mistaken for it."""
    title = game(harness, league, "aac_championship_2023")
    assert title["IS_CONF_CHAMPIONSHIP"] is True
    assert title["PARTICIPANTS_RULE"] == "CONF_CHAMPIONSHIP"
    later = game(harness, league, "aac_week15_2023")
    assert later["IS_CONFERENCE_GAME"] is False
    assert later["IS_CONF_CHAMPIONSHIP"] is False and later["PARTICIPANTS_RULE"] == "FIXED"
    assert [row["CONFERENCE_NAME"] for row in championships(harness, 2023)] == ["American Athletic"]


def test_conference_game_after_power_championship_week_not_marked(
    harness: Harness, league: League
) -> None:
    """Scenario games: A conference game after the power conferences' championship week is not
    taken for a championship game (cross-plan X1)."""
    late = game(harness, league, "aac_week15_2025")
    assert late["IS_CONFERENCE_GAME"] is True and late["WEEK"] == 15
    assert late["HOME_CONFERENCE"] == late["AWAY_CONFERENCE"] == "American Athletic"
    assert late["IS_CONF_CHAMPIONSHIP"] is False and late["PARTICIPANTS_RULE"] == "FIXED"
    title = game(harness, league, "aac_championship_2025")
    assert title["IS_CONF_CHAMPIONSHIP"] is True
    assert title["PARTICIPANTS_RULE"] == "CONF_CHAMPIONSHIP"
    found = championships(harness, 2025)
    assert [(row["CONFERENCE_NAME"], row["GAME_ID"]) for row in found] == sorted(
        [
            ("SEC", league.game_id("sec_championship_2025")),
            ("American Athletic", league.game_id("aac_championship_2025")),
        ],
        key=lambda item: item[1],
    )
    assert {row["POWER_CHAMPIONSHIP_WEEK"] for row in found} == {14}
    assert {row["POWER_CHAMPIONSHIP_WEEK"] for row in championships(harness, 2023)} == {None}


def test_missed_championship_reported(harness: Harness, league: League) -> None:
    """Scenario games: A championship game the rule misses is reported (the `GAME` part: no
    Mountain West game of 2014 is marked; the warnings are `MODEL_CHECKS`)."""
    for name in ("mw_championship_2014", "mw_makeup_2014"):
        row = game(harness, league, name)
        assert row["IS_CONFERENCE_GAME"] is True, name
        assert row["IS_CONF_CHAMPIONSHIP"] is False, name
    assert game(harness, league, "mw_championship_2014")["NOTES"] == "Mountain West Championship"
    assert [row["CONFERENCE_NAME"] for row in championships(harness, 2014)] == ["SEC"]


def test_detected_games_equal_the_rule(harness: Harness, league: League) -> None:
    expected = detect_championships(league_games(league), championship_ranges(), power_conferences)
    assert {row["GAME_ID"] for row in championships(harness)} == expected
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.GAME WHERE IS_CONF_CHAMPIONSHIP"
    ) == [(len(expected),)]


# --- placeholders and coverage ----------------------------------------------------------------


def test_placeholder_game(harness: Harness, league: League) -> None:
    """Scenario games: A game without named teams is a placeholder (the `GAME` part)."""
    placeholder = game(harness, league, "sec_placeholder_2026")
    assert placeholder["HAS_TEAMS"] is False and placeholder["HAS_RESULT"] is False
    assert placeholder["PARTICIPANTS_RULE"] == "FIXED"
    assert placeholder["IS_CONF_CHAMPIONSHIP"] is False
    assert placeholder["HOME_TEAM_ID"] is None and placeholder["AWAY_TEAM_ID"] is None
    assert placeholder["IS_START_TIME_TBD"] is True
    assert (placeholder["WEEK_SEQ"], placeholder["NOTES"]) == (15, "SEC Championship")
    assert team_games(harness, league, "sec_placeholder_2026") == {}
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.TEAM_GAME WHERE TEAM_ID IS NULL"
    ) == [(0,)]


def test_team_game_has_two_rows_per_game_with_teams(harness: Harness, league: League) -> None:
    rows = harness.query(f"SELECT COUNT(*) FROM {harness.model}.TEAM_GAME")
    games = harness.query(f"SELECT COUNT(*) FROM {harness.model}.GAME WHERE HAS_TEAMS")
    assert int(rows[0][0]) == 2 * int(games[0][0])
    assert harness.query(
        f"SELECT COUNT(*) FROM (SELECT GAME_ID FROM {harness.model}.TEAM_GAME "
        "GROUP BY GAME_ID HAVING COUNT(*) <> 2 OR SUM(MARGIN) <> 0 "
        "OR COUNT(CASE WHEN GAME_RESULT = 'W' THEN 1 END) <> COUNT(CASE WHEN GAME_RESULT = 'L' "
        "THEN 1 END))"
    ) == [(0,)]


def test_game_coverage_flags(harness: Harness, league: League) -> None:
    """Scenario games: Every game carries its coverage flags."""
    early = game(harness, league, "old_grounds_1885")
    assert (early["HAS_ELO"], early["HAS_VENUE_LOCATION"], early["PLAYS_ACCESS"]) == (
        False,
        False,
        "NOT_AVAILABLE",
    )
    assert early["HAS_LINE_SCORES"] is False and early["HAS_PREGAME_WP"] is False
    modern = game(harness, league, "ashford_brookvale_2024")
    for flag in (
        "HAS_ELO",
        "HAS_LINE_SCORES",
        "HAS_VENUE_LOCATION",
        "IS_CONFERENCE_GAME",
        "IS_FBS_GAME",
        "INVOLVES_FBS",
        "HAS_RESULT",
        "HAS_TEAMS",
    ):
        assert modern[flag] is True, flag
    assert modern["PLAYS_ACCESS"] == "ON_DEMAND"
    assert (modern["HOME_PREGAME_ELO"], modern["AWAY_PREGAME_ELO"]) == (1727, 1594)
    mixed = game(harness, league, "fbs_fcs_1985")
    assert (mixed["INVOLVES_FBS"], mixed["IS_FBS_GAME"]) == (True, False)
    flags = harness.query(
        "SELECT COUNT(*) FROM "
        f"{harness.model}.GAME WHERE HAS_RESULT IS NULL OR HAS_TEAMS IS NULL OR HAS_ELO IS NULL "
        "OR HAS_PREGAME_WP IS NULL OR HAS_LINE_SCORES IS NULL OR HAS_VENUE_LOCATION IS NULL "
        "OR INVOLVES_FBS IS NULL OR IS_FBS_GAME IS NULL OR PLAYS_ACCESS IS NULL "
        "OR IS_CONF_CHAMPIONSHIP IS NULL OR PARTICIPANTS_RULE IS NULL"
    )
    assert flags == [(0,)]


def test_northam_is_recorded_as_on_its_games_without_a_transition(
    harness: Harness, league: League
) -> None:
    rows = harness.query(
        f"SELECT HOME_TEAM_ID, HOME_CLASSIFICATION, AWAY_TEAM_ID, AWAY_CLASSIFICATION "
        f"FROM {harness.model}.GAME WHERE SEASON = 2014 "
        f"AND {NORTHAM} IN (HOME_TEAM_ID, AWAY_TEAM_ID)"
    )
    assert rows
    sides = {(h if h == NORTHAM else a, hc if h == NORTHAM else ac) for h, hc, a, ac in rows}
    assert sides == {(NORTHAM, "fbs")}


# --- each team's side --------------------------------------------------------------------------


def test_team_game_sides(harness: Harness, league: League) -> None:
    """Scenario games: Each team sees the game from its side."""
    played = game(harness, league, "ashford_brookvale_2024")
    sides = team_games(harness, league, "ashford_brookvale_2024")
    assert set(sides) == {ASHFORD, BROOKVALE}
    home, away = sides[ASHFORD], sides[BROOKVALE]
    assert (home["TEAM_NAME"], home["OPPONENT_ID"], home["OPPONENT_NAME"]) == (
        "Ashford",
        BROOKVALE,
        "Brookvale",
    )
    assert (away["TEAM_NAME"], away["OPPONENT_ID"], away["OPPONENT_NAME"]) == (
        "Brookvale",
        ASHFORD,
        "Ashford",
    )
    assert (home["SITE"], away["SITE"]) == ("HOME", "AWAY")
    assert (home["IS_HOME_TEAM"], away["IS_HOME_TEAM"]) == (True, False)
    assert (home["POINTS_FOR"], home["POINTS_AGAINST"]) == (31, 24)
    assert (away["POINTS_FOR"], away["POINTS_AGAINST"]) == (24, 31)
    assert (number(home["MARGIN"]), number(away["MARGIN"])) == (Decimal(7), Decimal(-7))
    for side in sides.values():
        assert (side["GAME_ID"], side["SEASON"], side["WEEK"], side["WEEK_SEQ"]) == (
            played["GAME_ID"],
            2024,
            14,
            14,
        )
        assert moment(side["START_AT"]) == moment(played["START_AT"])
        assert side["SEASON_TYPE"] == "regular"
        assert (side["TEAM_CONFERENCE"], side["OPPONENT_CONFERENCE"]) == ("SEC", "SEC")
        assert (side["TEAM_CLASSIFICATION"], side["OPPONENT_CLASSIFICATION"]) == ("fbs", "fbs")
    assert (home["TEAM_PREGAME_ELO"], home["OPPONENT_PREGAME_ELO"]) == (1727, 1594)
    assert (away["TEAM_PREGAME_ELO"], away["OPPONENT_PREGAME_ELO"]) == (1594, 1727)
    assert home["TEAM_POSTGAME_ELO"] == played["HOME_POSTGAME_ELO"]
    assert away["TEAM_POSTGAME_ELO"] == played["AWAY_POSTGAME_ELO"]
    final = team_games(harness, league, "cfp_quarterfinal_2024")
    assert set(final) == {ASHFORD, KINGSLEY}
    assert [side["SITE"] for side in final.values()] == ["NEUTRAL", "NEUTRAL"]
    assert (final[ASHFORD]["IS_HOME_TEAM"], final[KINGSLEY]["IS_HOME_TEAM"]) == (True, False)
    assert (final[ASHFORD]["GAME_RESULT"], final[KINGSLEY]["GAME_RESULT"]) == ("L", "W")
    assert (final[ASHFORD]["POINTS_FOR"], final[ASHFORD]["POINTS_AGAINST"]) == (24, 31)
    assert (final[KINGSLEY]["OPPONENT_ID"], final[KINGSLEY]["OPPONENT_NAME"]) == (
        ASHFORD,
        "Ashford",
    )


def test_team_game_carries_the_game_flags(harness: Harness, league: League) -> None:
    final = team_games(harness, league, "cfp_quarterfinal_2024")
    for side in final.values():
        assert (side["POSTSEASON_KIND"], side["CFP_ROUND"]) == ("CFP", "quarterfinal")
        assert (side["PARTICIPANTS_RULE"], side["WEEK_SEQ"], side["SEASON_TYPE"]) == (
            "PREVIOUS_ROUND",
            101,
            "postseason",
        )
        assert side["IS_CONF_CHAMPIONSHIP"] is False
    bowl = team_games(harness, league, "desert_bowl_2023")
    for side in bowl.values():
        assert (side["POSTSEASON_KIND"], side["CFP_ROUND"]) == ("BOWL", None)
        assert side["PARTICIPANTS_RULE"] == "SELECTION"
    title = team_games(harness, league, "sec_championship_2024")
    for side in title.values():
        assert side["IS_CONF_CHAMPIONSHIP"] is True and side["IS_CONFERENCE_GAME"] is True
        assert side["PARTICIPANTS_RULE"] == "CONF_CHAMPIONSHIP"
        assert (side["POSTSEASON_KIND"], side["WEEK_SEQ"]) == (None, 15)
    ordinary = team_games(harness, league, "ashford_brookvale_2024")
    for side in ordinary.values():
        assert side["IS_CONF_CHAMPIONSHIP"] is False and side["IS_CONFERENCE_GAME"] is True
        assert side["POSTSEASON_KIND"] is None and side["PARTICIPANTS_RULE"] == "FIXED"
    later = team_games(harness, league, "aac_week15_2023")
    assert {side["IS_CONFERENCE_GAME"] for side in later.values()} == {False}
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.TEAM_GAME t JOIN {harness.model}.GAME g "
        "ON g.GAME_ID = t.GAME_ID WHERE t.IS_CONFERENCE_GAME <> g.IS_CONFERENCE_GAME "
        "OR t.IS_CONF_CHAMPIONSHIP <> g.IS_CONF_CHAMPIONSHIP "
        "OR t.PARTICIPANTS_RULE <> g.PARTICIPANTS_RULE OR t.WEEK_SEQ <> g.WEEK_SEQ "
        "OR (t.POSTSEASON_KIND IS NULL) <> (g.POSTSEASON_KIND IS NULL)"
    ) == [(0,)]


def test_team_game_follows_the_game_sides(harness: Harness, league: League) -> None:
    stored = league_games(league)
    expected = {
        (row["ID"], row[f"{side}_ID"])
        for row in stored
        for side in ("HOME", "AWAY")
        if row[f"{side}_ID"] is not None
    }
    found = harness.query(f"SELECT GAME_ID, TEAM_ID FROM {harness.model}.TEAM_GAME")
    assert {(int(g), int(t)) for g, t in found} == expected
    assert len(found) == len(expected)


# --- head-to-head ------------------------------------------------------------------------------


def head_to_head(harness: Harness, team_a: str, team_b: str) -> dict[str, Any]:
    text = HEAD_TO_HEAD.read_text(encoding="utf-8")
    text = text.replace("{TEAM_A}", team_a).replace("{TEAM_B}", team_b)
    sql = fill_placeholders(text, harness.namespace).strip().removesuffix(";")
    rows = harness.query_dicts(sql)
    assert len(rows) == 1
    return rows[0]


def test_head_to_head_from_stored_games(harness: Harness, league: League, mock: Mock) -> None:
    """Scenario games: The head-to-head series comes from stored games. The league also stores
    the 1985 Ashford win of the SEC schedule scenarios, so four meetings are played."""
    meetings = sorted(
        (
            row
            for row in league_games(league)
            if {row["HOME_ID"], row["AWAY_ID"]} == {ASHFORD, BROOKVALE}
        ),
        key=lambda row: (row["SEASON"], row["WEEK"]),
    )
    played = [row for row in meetings if row["COMPLETED"]]
    ashford_wins = sum(
        1
        for row in played
        if (row["HOME_POINTS"] > row["AWAY_POINTS"]) == (row["HOME_ID"] == ASHFORD)
        and row["HOME_POINTS"] != row["AWAY_POINTS"]
    )
    ties = sum(1 for row in played if row["HOME_POINTS"] == row["AWAY_POINTS"])
    assert (len(played), ashford_wins, ties) == (4, 2, 1)
    series = head_to_head(harness, "Ashford", "Brookvale")
    assert (series["TEAM_A"], series["TEAM_B"]) == ("Ashford", "Brookvale")
    assert (series["MEETINGS"], series["A_WINS"], series["B_WINS"], series["TIES"]) == (
        len(played),
        ashford_wins,
        len(played) - ashford_wins - ties,
        ties,
    )
    assert (series["LAST_GAME_ID"], series["LAST_SEASON"], series["LAST_WEEK"]) == (
        league.game_id("ashford_brookvale_2024"),
        2024,
        14,
    )
    assert (series["LAST_RESULT_FOR_A"], series["LAST_POINTS_A"], series["LAST_POINTS_B"]) == (
        "W",
        31,
        24,
    )
    assert (series["NEXT_GAME_ID"], series["NEXT_SEASON"], series["NEXT_WEEK"]) == (
        league.game_id("brookvale_ashford_2026_w12"),
        2026,
        12,
    )
    assert harness.mock_hits() == 0


def test_head_to_head_is_symmetric_and_handles_strangers(harness: Harness, league: League) -> None:
    forward = head_to_head(harness, "Ashford", "Brookvale")
    backward = head_to_head(harness, "Brookvale", "Ashford")
    assert (backward["A_WINS"], backward["B_WINS"]) == (forward["B_WINS"], forward["A_WINS"])
    assert (backward["MEETINGS"], backward["TIES"]) == (forward["MEETINGS"], forward["TIES"])
    assert backward["LAST_GAME_ID"] == forward["LAST_GAME_ID"]
    assert backward["LAST_RESULT_FOR_A"] == "L"
    assert backward["NEXT_GAME_ID"] == forward["NEXT_GAME_ID"]
    none = head_to_head(harness, "Ashford", "No Such School")
    assert (none["MEETINGS"], none["A_WINS"], none["B_WINS"], none["TIES"]) == (0, 0, 0, 0)
    assert none["LAST_GAME_ID"] is None and none["NEXT_GAME_ID"] is None
