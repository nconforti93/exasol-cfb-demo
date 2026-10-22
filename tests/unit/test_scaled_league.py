import itertools
import json
from datetime import datetime
from typing import Any

import pytest

from loader.plan import default_catalog, load_plan
from loader.templates import compile_plan
from tests.synthetic import fake
from tests.synthetic import scaled_league as sl
from tests.synthetic.league import FETCHED_2026, FETCHED_EARLY, season_key
from tests.synthetic.rules import detect_championships
from tests.synthetic.seed_sql import championship_ranges
from tests.synthetic.slices import Slice, mirror_columns, slice_rows

Row = dict[str, Any]
SAMPLE = (1869, 1885, 1950, 1985, 2000, 2013, 2014, 2024, 2026)


def rows_of(item: Slice) -> list[Row]:
    columns = mirror_columns(item.table)
    return [dict(zip(columns, row, strict=True)) for row in slice_rows(item)]


def slice_of(season: int, table: str) -> Slice:
    return next(s for s in sl.season_slices(season) if s.table == table)


def games_of(season: int) -> list[Row]:
    return rows_of(slice_of(season, "GAMES"))


# --- size and keys -----------------------------------------------------------------------------


def test_the_league_covers_157_seasons_without_the_1871_gap() -> None:
    assert len(sl.seasons()) == 157
    assert sl.seasons()[0] == 1869 and sl.seasons()[-1] == 2026
    assert 1871 not in sl.seasons()
    assert sl.game_count(1871) == 0 and sl.season_slices(1871) == []


def test_the_league_has_about_250000_games() -> None:
    assert 240_000 <= sl.total_games() <= 260_000
    assert sl.ScaledLeague().total_games() == sl.total_games()


def test_modern_seasons_have_about_two_thousand_games() -> None:
    counts = [sl.game_count(season) for season in range(1990, 2026)]
    assert all(1900 <= count <= 2300 for count in counts)


@pytest.mark.parametrize("season", SAMPLE)
def test_the_planned_count_equals_the_built_games(season: int) -> None:
    assert len(slice_of(season, "GAMES").body) == sl.game_count(season)


def test_game_ids_are_unique_within_a_season_and_across_seasons() -> None:
    seen: set[int] = set()
    for season in (1885, 2014, 2024):
        ids = [g["id"] for g in slice_of(season, "GAMES").body]
        assert len(ids) == len(set(ids))
        assert not seen & set(ids)
        seen |= set(ids)


def test_slice_keys_are_the_load_plan_keys_and_unique() -> None:
    templates = {(t.table, t.slicing): t for t in compile_plan(load_plan(), default_catalog())}
    keys = [key for season in sl.seasons() for key in sl.slice_keys(season)]
    assert len(keys) == len(set(keys))
    for table, key in keys:
        assert key == templates[(table, "season")].slice_key(int(key.rsplit("year=", 1)[1]))
    by_season = {season: {t for t, _ in sl.slice_keys(season)} for season in sl.seasons()}
    assert by_season[2012] == {"GAMES"}
    assert by_season[2013] == {"GAMES", "CALENDAR", "METRICS_WP_PREGAME", "RATINGS_SP"}
    assert by_season[2014] == by_season[2013] | {"RANKINGS_POLLS_RANKS"}


@pytest.mark.parametrize("season", SAMPLE)
def test_built_slices_carry_the_planned_keys_and_one_fetch_state(season: int) -> None:
    built = sl.season_slices(season)
    assert [(s.table, s.slice_key) for s in built] == sl.slice_keys(season)
    for item in built:
        current = season == 2026
        assert item.is_final is (not current)
        expected = FETCHED_2026 if current else FETCHED_EARLY
        if item.table != "RANKINGS_POLLS_RANKS":
            assert item.fetched_at == expected


def test_static_slices_use_the_plan_keys_and_the_two_affiliation_slices() -> None:
    static = sl.static_slices()
    assert [(s.table, s.slice_key) for s in static] == [
        ("TEAMS", "/teams"),
        ("VENUES", "/venues"),
        ("CONFERENCES", "/conferences"),
        ("CONFERENCES_AFFILIATIONS", "/conferences/affiliations?maxYear=2025&minYear=1869"),
        ("CONFERENCES_AFFILIATIONS", "/conferences/affiliations?year=2026"),
    ]
    assert [s.is_final for s in static] == [True, True, True, True, False]
    ranges = rows_of(static[3])
    assert {(r["P_MIN_YEAR"], r["P_MAX_YEAR"]) for r in ranges} == {(1869, 2025)}
    assert {r["TEAM_ID"] for r in ranges} == {t.id for t in sl.teams()}


def test_iter_slices_is_lazy_and_starts_with_the_static_slices() -> None:
    first = list(itertools.islice(sl.ScaledLeague().iter_slices(), 7))
    assert [s.table for s in first][:5] == [
        "TEAMS",
        "VENUES",
        "CONFERENCES",
        "CONFERENCES_AFFILIATIONS",
        "CONFERENCES_AFFILIATIONS",
    ]
    assert [s.slice_key for s in first][5:] == [
        season_key("GAMES", 1869),
        season_key("GAMES", 1870),
    ]


def test_the_league_is_deterministic() -> None:
    for season in (1885, 2013, 2026):
        again = [(s.table, s.slice_key, s.body) for s in sl.season_slices(season)]
        first = [(s.table, s.slice_key, s.body) for s in sl.season_slices(season)]
        assert json.dumps(again, sort_keys=True) == json.dumps(first, sort_keys=True)
    assert [t.name for t in sl.teams()] == [t.name for t in sl.teams()]


# --- the season shapes of the base league ------------------------------------------------------


def test_every_season_shape_of_the_base_league_is_represented() -> None:
    assert sorted({sl.shape_of(season) for season in sl.seasons()}) == [
        1885,
        1950,
        1985,
        2000,
        2013,
        2014,
        2023,
        2024,
        2025,
        2026,
    ]
    assert [sl.shape_of(s) for s in (1869, 1889, 1890, 1977, 1978, 1999, 2000, 2012)] == [
        1885,
        1885,
        1950,
        1950,
        1985,
        1985,
        2000,
        2000,
    ]
    assert [sl.shape_of(s) for s in (2013, 2014, 2022, 2023, 2024, 2025, 2026)] == [
        2013,
        2014,
        2014,
        2023,
        2024,
        2025,
        2026,
    ]


def test_there_is_no_elo_before_1890_and_elo_afterwards() -> None:
    early = games_of(1885)
    assert all(r["HOME_PREGAME_ELO"] is None and r["HOME_POSTGAME_ELO"] is None for r in early)
    later = games_of(1950)
    assert all(r["HOME_PREGAME_ELO"] is not None for r in later)
    assert all(r["HOME_POSTGAME_ELO"] is not None for r in later if r["COMPLETED"])


def test_ties_exist_until_1995_and_never_after() -> None:
    tied_before = sum(
        r["HOME_POINTS"] == r["AWAY_POINTS"]
        for s in (1950, 1985)
        for r in games_of(s)
        if r["COMPLETED"]
    )
    assert tied_before > 0
    assert not any(r["HOME_POINTS"] == r["AWAY_POINTS"] for r in games_of(2000) if r["COMPLETED"])


def test_one_games_call_carries_every_division() -> None:
    codes = {r["HOME_CLASSIFICATION"] for r in games_of(2013)}
    assert codes == {"fbs", "fcs", "ii", "iii"}
    assert {r["HOME_CLASSIFICATION"] for r in games_of(1885)} <= {"fbs", "fcs", "ii", "iii"}


def test_lines_and_sp_start_in_2013_and_polls_in_2014() -> None:
    assert [t for t, _ in sl.slice_keys(2012)] == ["GAMES"]
    lines = rows_of(slice_of(2013, "METRICS_WP_PREGAME"))
    assert lines and all(
        r["SPREAD"] is not None and 0 < r["HOME_WIN_PROBABILITY"] < 1 for r in lines
    )
    ratings = rows_of(slice_of(2013, "RATINGS_SP"))
    assert any(r["TEAM"] == "nationalAverages" for r in ratings)
    assert "RANKINGS_POLLS_RANKS" not in {t for t, _ in sl.slice_keys(2013)}
    polls = rows_of(slice_of(2014, "RANKINGS_POLLS_RANKS"))
    assert {r["POLL"] for r in polls} == {"AP Top 25", "Coaches Poll", "Playoff Committee Rankings"}
    assert max(r["RANK"] for r in polls) == 25


def test_postseason_has_bowls_and_the_playoff_of_its_era() -> None:
    def postseason(season: int) -> list[Row]:
        return [r for r in games_of(season) if r["SEASON_TYPE"] == "postseason"]

    assert not postseason(1885)
    assert any(r["NOTES"].startswith("Bowl") for r in postseason(1950))
    cfp_2013 = [r for r in postseason(2013) if r["PLAYOFF_COMPETITION"]]
    cfp_2014 = [r for r in postseason(2014) if r["PLAYOFF_COMPETITION"]]
    cfp_2024 = [r for r in postseason(2024) if r["PLAYOFF_COMPETITION"]]
    assert (len(cfp_2013), len(cfp_2014), len(cfp_2024)) == (0, 3, 11)
    assert sorted({r["PLAYOFF_ROUND"] for r in cfp_2024}) == [
        "championship",
        "firstRound",
        "quarterfinal",
        "semifinal",
    ]
    assert {r["WEEK"] for r in postseason(2024)} == {1}
    assert any(r["HOME_CLASSIFICATION"] == "fcs" for r in postseason(2024))


def test_the_current_season_is_in_progress() -> None:
    rows = games_of(2026)
    assert all(r["COMPLETED"] is (r["WEEK"] <= 9) for r in rows)
    assert {r["WEEK"] for r in rows} == set(range(1, 14))
    assert not any(r["SEASON_TYPE"] == "postseason" for r in rows)
    assert all(not s.is_final for s in sl.season_slices(2026))
    assert {
        r["WEEK"]
        for r in rows_of(slice_of(2026, "RANKINGS_POLLS_RANKS"))
        if r["POLL"] == "AP Top 25"
    } == set(range(1, 11))


def test_conference_championship_games_exist_only_inside_the_seeded_seasons() -> None:
    seeded = championship_ranges()
    for season, expected_conferences in ((1985, 0), (1995, 1), (2012, 6), (2024, 9)):
        rows = games_of(season)
        found = detect_championships(rows, seeded)
        assert len(found) == expected_conferences, (season, len(found))
        for row in rows:
            if row["ID"] in found:
                assert row["NOTES"].endswith("Championship")
                assert row["HOME_CONFERENCE"] == row["AWAY_CONFERENCE"]


def test_the_championship_game_is_alone_in_its_week_for_each_seeded_conference() -> None:
    rows = games_of(2024)
    found = detect_championships(rows, championship_ranges())
    weeks = {r["WEEK"] for r in rows if r["ID"] in found}
    assert weeks == {13}


# --- rows --------------------------------------------------------------------------------------


@pytest.mark.parametrize("season", [1885, 2014, 2026])
def test_rows_are_typed_per_catalog_mirror_column(season: int) -> None:
    kinds = {
        "DECIMAL": int,
        "DOUBLE": float,
        "BOOLEAN": bool,
        "TIMESTAMP": datetime,
        "VARCHAR": str,
    }
    for item in sl.season_slices(season):
        columns = fake.catalog().table(item.table).columns
        for row in itertools.islice(slice_rows(item), 400):
            for column, value in zip(columns, row, strict=True):
                if value is not None:
                    kind = (column.sql_type or "").split("(")[0].split()[0]
                    assert type(value) is kinds[kind], (item.table, column.name, value)


def test_every_game_team_is_in_the_team_list_with_a_membership() -> None:
    teams = {t.name: t for t in sl.teams()}
    assert len({t.id for t in sl.teams()}) == len(sl.teams()) == len(teams)
    memberships = {r["TEAM_ID"] for r in rows_of(sl.static_slices()[3])}
    for row in games_of(2014):
        for side in ("HOME", "AWAY"):
            team = teams[row[f"{side}_TEAM"]]
            assert row[f"{side}_ID"] == team.id and team.id in memberships
            assert row[f"{side}_CLASSIFICATION"] == team.division


def test_the_pac_10_and_the_big_east_are_renamed_in_the_affiliations() -> None:
    spells = rows_of(sl.static_slices()[3])
    names = {r["CONFERENCE"] for r in spells}
    assert {"Pac-10", "Pac-12", "Big East", "American Athletic", "FBS Independents"} <= names
    pac_12_2010 = {r["HOME_CONFERENCE"] for r in games_of(2010)}
    assert "Pac-10" in pac_12_2010 and "Pac-12" not in pac_12_2010


def test_no_team_plays_twice_in_a_regular_week() -> None:
    seen: set[tuple[int, int]] = set()
    for row in games_of(2024):
        if row["SEASON_TYPE"] != "regular":
            continue
        for side in ("HOME", "AWAY"):
            key = (row["WEEK"], row[f"{side}_ID"])
            assert key not in seen, key
            seen.add(key)
