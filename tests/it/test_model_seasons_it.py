"""Integration tests of model/seasons-eras: `SEASON` and `SEASON_WEEK`.

Read-only module over the synthetic league (marker `model_readonly`). Expectations come from the
spec and from the league's own bodies (calendar rows, kickoffs), never from the SQL under test.
The division-label scenario reads `TEAM_SEASON` and belongs to `210_team_season.sql`.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

import pytest

from tests.it.harness import Harness
from tests.synthetic.league import CURRENT_SEASON, STORED_SEASONS, League, season_key
from tests.synthetic.slices import mirror_columns, slice_rows

PLAN_VARIANT = "model"
pytestmark = [pytest.mark.it, pytest.mark.model_readonly]

FIRST_SEASON = 1869
ERA_COLUMNS = ("DIVISION_ERA_CODE", "POSTSEASON_ERA_CODE", "CHAMPION_ERA_CODE", "OVERTIME_ERA_CODE")


def season(harness: Harness, year: int) -> dict[str, Any]:
    rows = harness.query_dicts(f"SELECT * FROM {harness.model}.SEASON WHERE SEASON = {year}")
    assert len(rows) == 1, year
    return rows[0]


def eras(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row[column] for column in ERA_COLUMNS)


def weeks(harness: Harness, year: int) -> list[dict[str, Any]]:
    return harness.query_dicts(
        f"SELECT * FROM {harness.model}.SEASON_WEEK WHERE SEASON = {year} ORDER BY WEEK_SEQ"
    )


def moment(value: Any) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))


def league_rows(league: League, table: str, year: int) -> list[dict[str, Any]]:
    """The mirror rows of the league's season slice of `table`."""
    item = league.get(table, season_key(table, year))
    return [dict(zip(mirror_columns(table), row, strict=True)) for row in slice_rows(item)]


# --- seasons and eras -------------------------------------------------------------------------


def test_early_season_eras(harness: Harness, league: League) -> None:
    """Scenario seasons-eras: Early seasons carry their eras."""
    early = season(harness, 1885)
    assert eras(early) == ("PRE_NCAA", "NO_BOWLS", "SELECTORS", "TIES")
    assert early["TIES_POSSIBLE"] is True and early["CFP_TEAMS"] == 0
    middle = season(harness, 1950)
    assert eras(middle) == ("NCAA_UNDIVIDED", "BOWLS", "POLLS", "TIES")
    assert middle["TIES_POSSIBLE"] is True


def test_modern_season_eras(harness: Harness, league: League) -> None:
    """Scenario seasons-eras: Modern seasons carry their eras."""
    assert eras(season(harness, 1985))[:2] == ("I_A_I_AA", "BOWLS")
    assert eras(season(harness, 2000))[:2] == ("I_A_I_AA", "BCS")
    playoff4 = season(harness, 2014)
    assert eras(playoff4)[:3] == ("FBS_FCS", "CFP_4", "CFP")
    assert playoff4["CFP_TEAMS"] == 4
    playoff12 = season(harness, 2024)
    assert eras(playoff12) == ("FBS_FCS", "CFP_12", "CFP", "OVERTIME")
    assert playoff12["CFP_TEAMS"] == 12 and playoff12["TIES_POSSIBLE"] is False


def test_season_without_games_listed(harness: Harness, league: League) -> None:
    """Scenario seasons-eras: A season without games is still listed."""
    gap = season(harness, 1871)
    assert gap["HAS_GAMES"] is False and gap["GAME_COUNT"] == 0
    assert gap["COMPLETED_GAME_COUNT"] == 0 and gap["FBS_TEAM_COUNT"] == 0
    assert gap["FIRST_GAME_AT"] is None and gap["LAST_GAME_AT"] is None
    assert eras(gap) == ("PRE_NCAA", "NO_BOWLS", "SELECTORS", "TIES")
    span = harness.query(
        "SELECT COUNT(*), COUNT(DISTINCT SEASON), MIN(SEASON), MAX(SEASON) "
        f"FROM {harness.model}.SEASON"
    )
    count = CURRENT_SEASON - FIRST_SEASON + 1
    assert span == [(count, count, FIRST_SEASON, CURRENT_SEASON)]
    with_games = harness.query(
        f"SELECT SEASON FROM {harness.model}.SEASON WHERE HAS_GAMES ORDER BY SEASON"
    )
    assert [row[0] for row in with_games] == list(STORED_SEASONS)


def test_season_counts_games_and_fbs_teams(harness: Harness, league: League) -> None:
    games = league_rows(league, "GAMES", 2024)
    row = season(harness, 2024)
    assert row["GAME_COUNT"] == len(games)
    assert row["COMPLETED_GAME_COUNT"] == sum(1 for g in games if g["COMPLETED"])
    fbs = {g["HOME_ID"] for g in games if g["HOME_CLASSIFICATION"] == "fbs"} | {
        g["AWAY_ID"] for g in games if g["AWAY_CLASSIFICATION"] == "fbs"
    }
    assert row["FBS_TEAM_COUNT"] == len(fbs)
    assert moment(row["FIRST_GAME_AT"]) == min(g["START_DATE"] for g in games)
    assert moment(row["LAST_GAME_AT"]) == max(g["START_DATE"] for g in games)


def test_only_newest_season_current(harness: Harness, league: League) -> None:
    """Scenario seasons-eras: Only the newest season is current."""
    current = harness.query(f"SELECT SEASON FROM {harness.model}.SEASON WHERE IS_CURRENT_SEASON")
    assert current == [(CURRENT_SEASON,)]
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.SEASON WHERE IS_CURRENT_SEASON IS NULL"
    ) == [(0,)]


# --- weeks ------------------------------------------------------------------------------------


def test_week_dates_from_calendar_or_games(harness: Harness, league: League) -> None:
    """Scenario seasons-eras: Weeks take their dates from the calendar when it is stored."""
    calendar = {
        (row["SEASON_TYPE"], row["WEEK"]): row for row in league_rows(league, "CALENDAR", 2024)
    }
    stored = weeks(harness, 2024)
    assert {(w["SEASON_TYPE"], w["WEEK"]) for w in stored} == set(calendar)
    for week in stored:
        expected = calendar[(week["SEASON_TYPE"], week["WEEK"])]
        assert week["DATES_SOURCE"] == "CALENDAR"
        assert moment(week["START_AT"]) == expected["START_DATE"]
        assert moment(week["END_AT"]) == expected["END_DATE"]
        assert moment(week["FIRST_GAME_AT"]) == expected["FIRST_GAME_START"]
    games = league_rows(league, "GAMES", 1950)
    early = weeks(harness, 1950)
    assert [w["WEEK"] for w in early] == sorted({g["WEEK"] for g in games})
    for week in early:
        kickoffs = [g["START_DATE"] for g in games if g["WEEK"] == week["WEEK"]]
        assert week["DATES_SOURCE"] == "GAMES"
        assert moment(week["START_AT"]) == min(kickoffs)
        assert moment(week["END_AT"]) == max(kickoffs)
        assert moment(week["FIRST_GAME_AT"]) == min(kickoffs)
        assert week["GAME_COUNT"] == len(kickoffs)


def test_week_sequence_order(harness: Harness, league: League) -> None:
    """Scenario seasons-eras: The postseason follows the regular season in one week order."""
    stored = weeks(harness, 2024)
    regular = [w for w in stored if w["SEASON_TYPE"] == "regular"]
    assert [(w["WEEK"], w["WEEK_SEQ"]) for w in regular] == [(n, n) for n in range(1, 16)]
    assert (stored[-1]["SEASON_TYPE"], stored[-1]["WEEK"], stored[-1]["WEEK_SEQ"]) == (
        "postseason",
        1,
        101,
    )
    shared = harness.query(
        f"SELECT SEASON, WEEK_SEQ FROM {harness.model}.SEASON_WEEK "
        "GROUP BY SEASON, WEEK_SEQ HAVING COUNT(*) > 1"
    )
    assert shared == []


def test_week_done(harness: Harness, league: League) -> None:
    """Scenario seasons-eras: A week is done when its games are played or its time has passed."""
    current = {w["WEEK_SEQ"]: w for w in weeks(harness, CURRENT_SEASON)}
    assert {seq for seq, w in current.items() if w["IS_DONE"]} == set(range(1, 10))
    assert current[10]["IS_DONE"] is False
    assert current[10]["COMPLETED_GAME_COUNT"] == 0 and current[10]["GAME_COUNT"] > 0
    assert moment(current[10]["FIRST_GAME_AT"]) == datetime(2026, 10, 29, 23, 30)
    assert moment(current[9]["FIRST_GAME_AT"]) == datetime(2026, 10, 22, 23, 30)
    early = {w["WEEK"]: w for w in weeks(harness, 1950)}
    assert early[5]["COMPLETED_GAME_COUNT"] == 0 and early[5]["IS_DONE"] is True
    assert moment(early[5]["GAMES_FETCHED_AT"]) == datetime(2026, 10, 20, 10, 0)
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.model}.SEASON_WEEK WHERE IS_DONE IS NULL"
    ) == [(0,)]


def test_every_week_with_games_is_listed(harness: Harness, league: League) -> None:
    games = [dict(zip(mirror_columns("GAMES"), r, strict=True)) for r in league.rows("GAMES")]
    expected = Counter((g["SEASON"], g["SEASON_TYPE"], g["WEEK"]) for g in games)
    stored = harness.query(
        f"SELECT SEASON, SEASON_TYPE, WEEK, GAME_COUNT FROM {harness.model}.SEASON_WEEK "
        "WHERE GAME_COUNT > 0"
    )
    assert {(int(s), str(t), int(w)): int(c) for s, t, w, c in stored} == dict(expected)
