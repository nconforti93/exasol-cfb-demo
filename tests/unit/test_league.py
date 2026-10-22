import json
from collections import Counter
from datetime import datetime
from typing import Any

import pytest

from loader.plan import default_catalog, load_plan
from loader.templates import compile_plan
from tests.synthetic import fake
from tests.synthetic import league_teams as lt
from tests.synthetic.league import (
    CURRENT_SEASON,
    FETCHED_2026,
    FETCHED_2026_RANKINGS,
    FETCHED_EARLY,
    STORED_SEASONS,
    League,
    base_league,
    mutate_after,
    season_key,
    with_duplicate_rows,
    with_incomplete_week9_game,
    with_nonfinal_prior,
    with_renamed_conference,
    with_thursday_result,
    with_unaffiliated_fbs_team,
)
from tests.synthetic.league_games import game_week_seq
from tests.synthetic.rules import detect_championships
from tests.synthetic.seed_sql import championship_ranges, power_conferences
from tests.synthetic.slices import mirror_columns, slice_rows
from vs.cfbd_vs.flatten import data_columns, flatten
from vs.cfbd_vs.plan import build_slice_key
from vs.cfbd_vs.replay import decode_recording, encode_recording

Row = dict[str, Any]


@pytest.fixture(scope="module")
def league() -> League:
    return base_league()


@pytest.fixture(scope="module")
def tables(league: League) -> dict[str, list[Row]]:
    return {
        table: [dict(zip(mirror_columns(table), row, strict=True)) for row in rows]
        for table, rows in league.table_rows().items()
    }


def games(tables: dict[str, list[Row]], season: int) -> list[Row]:
    return [row for row in tables["GAMES"] if row["SEASON"] == season]


def game(league: League, tables: dict[str, list[Row]], name: str) -> Row:
    identifier = league.game_id(name)
    return next(row for row in tables["GAMES"] if row["ID"] == identifier)


def fbs_pairs(rows: list[Row]) -> list[Row]:
    return [
        r for r in rows if r["HOME_CLASSIFICATION"] == "fbs" and r["AWAY_CLASSIFICATION"] == "fbs"
    ]


def record(rows: list[Row], team: str, season_type: str | None = None) -> tuple[int, int]:
    wins = losses = 0
    for row in rows:
        if not row["COMPLETED"] or team not in (row["HOME_TEAM"], row["AWAY_TEAM"]):
            continue
        if season_type and row["SEASON_TYPE"] != season_type:
            continue
        mine, theirs = (
            (row["HOME_POINTS"], row["AWAY_POINTS"])
            if row["HOME_TEAM"] == team
            else (row["AWAY_POINTS"], row["HOME_POINTS"])
        )
        wins += mine > theirs
        losses += mine < theirs
    return wins, losses


# --- shape and typing --------------------------------------------------------------------------


def test_the_league_stores_the_demo_profile_tables_and_no_other(league: League) -> None:
    assert set(league.tables()) == {
        "TEAMS",
        "VENUES",
        "CONFERENCES",
        "CONFERENCES_AFFILIATIONS",
        "GAMES",
        "CALENDAR",
        "METRICS_WP_PREGAME",
        "RATINGS_SP",
        "RANKINGS_POLLS_RANKS",
    }


def test_rows_are_typed_per_catalog_mirror_column(league: League) -> None:
    kinds = {
        "DECIMAL": int,
        "DOUBLE": float,
        "BOOLEAN": bool,
        "TIMESTAMP": datetime,
        "VARCHAR": str,
    }
    for table, rows in league.table_rows().items():
        columns = fake.catalog().table(table).columns
        for row in rows:
            assert len(row) == len(columns)
            for column, value in zip(columns, row, strict=True):
                if value is None:
                    continue
                kind = (column.sql_type or "").split("(")[0].split()[0]
                assert type(value) is kinds[kind], (table, column.name, value)


def test_slice_keys_come_from_the_load_plan_templates(league: League) -> None:
    templates = {(t.table, t.slicing): t for t in compile_plan(load_plan(), default_catalog())}
    assert templates[("GAMES", "season")].slice_key(2024) == season_key("GAMES", 2024)
    planned = {
        (item.table, item.slice_key)
        for item in league.slices
        if item.table in {"TEAMS", "VENUES", "CONFERENCES"}
    }
    assert planned == {("TEAMS", "/teams"), ("VENUES", "/venues"), ("CONFERENCES", "/conferences")}
    assert season_key("GAMES", 2024) == "/games?seasonType=both&year=2024"
    assert season_key("METRICS_WP_PREGAME", 2024) == "/metrics/wp/pregame?seasonType=both&year=2024"
    assert season_key("RATINGS_SP", 2024) == "/ratings/sp?year=2024"
    assert season_key("RANKINGS_POLLS_RANKS", 2024) == "/rankings?seasonType=both&year=2024"
    assert season_key("CALENDAR", 2024) == "/calendar?year=2024"
    table = fake.catalog().table("GAMES")
    assert season_key("GAMES", 2024) == build_slice_key(table, {"year": 2024, "seasonType": "both"})


def test_echo_and_request_columns_follow_the_slice(tables: dict[str, list[Row]]) -> None:
    sample = next(r for r in tables["GAMES"] if r["SEASON"] == 2024)
    assert (sample["P_YEAR"], sample["P_SEASON_TYPE"], sample["P_WEEK"]) == (2024, "both", None)
    assert sample["REQ_SLICE"] == "/games?seasonType=both&year=2024"
    assert (sample["REQ_STATUS"], sample["REQ_ATTEMPTS"]) == (200, 1)
    assert sample["REQ_AT"] == FETCHED_EARLY
    ranges = [r for r in tables["CONFERENCES_AFFILIATIONS"] if r["P_MIN_YEAR"] is not None]
    assert {(r["P_MIN_YEAR"], r["P_MAX_YEAR"], r["P_YEAR"]) for r in ranges} == {(1869, 2025, None)}
    current = [r for r in tables["CONFERENCES_AFFILIATIONS"] if r["P_YEAR"] == 2026]
    assert current and all(r["REQ_AT"] == FETCHED_2026 for r in current)


def test_the_flattener_reproduces_the_rows_from_the_bodies(league: League) -> None:
    item = league.get("GAMES", season_key("GAMES", 2024))
    table = fake.catalog().table("GAMES")
    assert len(flatten(table, item.body)) == len(slice_rows(item)) == len(item.body)
    assert [c.name for c in data_columns(table)][:3] == ["ROW_NO", "ID", "SEASON"]


def test_the_league_is_deterministic(league: League) -> None:
    again = base_league()

    def dump(lg: League) -> str:
        return json.dumps([(s.table, s.slice_key, s.body) for s in lg.slices], sort_keys=True)

    assert dump(again) == dump(league)
    assert again.names == league.names


def test_the_row_order_is_not_the_id_order(tables: dict[str, list[Row]]) -> None:
    ids = [r["ID"] for r in games(tables, 2024)]
    assert ids != sorted(ids)


def test_fetch_times_and_finality_follow_the_spec(league: League) -> None:
    for item in league.slices:
        season = int(item.slice_key.rsplit("year=", 1)[1]) if "year=" in item.slice_key else None
        if season == CURRENT_SEASON:
            expected = (
                FETCHED_2026_RANKINGS if item.table == "RANKINGS_POLLS_RANKS" else FETCHED_2026
            )
            assert (item.fetched_at, item.is_final) == (expected, False), item.slice_key
        else:
            assert (item.fetched_at, item.is_final) == (FETCHED_EARLY, True), item.slice_key
    log = league.load_log_rows()
    assert len(log) == len(league.slices)
    assert all(
        row[4] == s.fetched_at and row[3] > 0 for row, s in zip(log, league.slices, strict=True)
    )


def test_stored_seasons_have_games_and_the_gap_has_no_slice(tables: dict[str, list[Row]]) -> None:
    assert sorted({r["SEASON"] for r in tables["GAMES"]}) == sorted(STORED_SEASONS)
    assert 1871 not in {r["SEASON"] for r in tables["GAMES"]}


# --- the facts of the specs --------------------------------------------------------------------


def test_2013_has_25_fbs_games_with_24_lines_and_one_fbs_vs_fcs_game_without(
    league: League, tables: dict[str, list[Row]]
) -> None:
    rows = games(tables, 2013)
    completed = [r for r in fbs_pairs(rows) if r["COMPLETED"]]
    lines = {r["GAME_ID"] for r in tables["METRICS_WP_PREGAME"] if r["SEASON"] == 2013}
    assert len(completed) == 25
    assert sum(r["ID"] in lines for r in completed) == 24
    mixed = game(league, tables, "fairhaven_2013")
    assert (mixed["HOME_CLASSIFICATION"], mixed["AWAY_CLASSIFICATION"]) == ("fbs", "fcs")
    assert mixed["COMPLETED"] and mixed["ID"] not in lines
    fairhaven = [r for r in rows if "Fairhaven" in (r["HOME_TEAM"], r["AWAY_TEAM"])]
    assert [r["ID"] for r in fairhaven] == [mixed["ID"]]
    assert mixed["AWAY_CLASSIFICATION"] == "fcs" and mixed["AWAY_CONFERENCE"] == "Mountain West"


def test_2014_has_25_fbs_games_with_20_lines(league: League, tables: dict[str, list[Row]]) -> None:
    rows = games(tables, 2014)
    completed = [r for r in fbs_pairs(rows) if r["COMPLETED"]]
    lines = {r["GAME_ID"] for r in tables["METRICS_WP_PREGAME"] if r["SEASON"] == 2014}
    assert len(completed) == 25
    assert sum(r["ID"] in lines for r in completed) == 20
    assert all(
        league.game_id(n) in lines for n in ("sec_championship_2014", "mw_championship_2014")
    )


def test_ashford_2024_is_10_2_in_the_regular_season_and_11_3_overall(
    tables: dict[str, list[Row]],
) -> None:
    rows = games(tables, 2024)
    regular = [
        r for r in rows if r["SEASON_TYPE"] == "regular" and r["NOTES"] != "SEC Championship"
    ]
    assert record(regular, "Ashford") == (10, 2)
    assert record(rows, "Ashford") == (11, 3)
    conference = [
        r for r in regular if r["CONFERENCE_GAME"] and "Ashford" in (r["HOME_TEAM"], r["AWAY_TEAM"])
    ]
    assert len(conference) == 8
    assert record(conference, "Ashford") == (7, 1)
    weeks = {r["WEEK"] for r in regular if "Ashford" in (r["HOME_TEAM"], r["AWAY_TEAM"])}
    assert weeks == {1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 13, 14}


def test_ashford_2026_is_7_2_after_week_9(tables: dict[str, list[Row]]) -> None:
    rows = [r for r in games(tables, 2026) if "Ashford" in (r["HOME_TEAM"], r["AWAY_TEAM"])]
    assert record(rows, "Ashford") == (7, 2)
    results = []
    for week in range(1, 10):
        row = next(r for r in rows if r["WEEK"] == week)
        won = (row["HOME_POINTS"] > row["AWAY_POINTS"]) == (row["HOME_TEAM"] == "Ashford")
        results.append("W" if won else "L")
    assert "".join(results) == "WWLWWWLWW"
    week10 = next(r for r in rows if r["WEEK"] == 10)
    assert week10["COMPLETED"] is False
    assert {r["WEEK"] for r in rows if not r["COMPLETED"]} == {10, 12, 13}
    week12 = next(r for r in rows if r["WEEK"] == 12)
    assert (week12["HOME_TEAM"], week12["AWAY_TEAM"]) == ("Brookvale", "Ashford")


def test_2024_week_14_ashford_game_has_its_line_elo_and_venue(
    league: League, tables: dict[str, list[Row]]
) -> None:
    row = game(league, tables, "ashford_brookvale_2024")
    assert (row["WEEK"], row["HOME_TEAM"], row["HOME_POINTS"], row["AWAY_POINTS"]) == (
        14,
        "Ashford",
        31,
        24,
    )
    assert row["VENUE"] == "Ashford Field" and row["HOME_LINE_SCORES"] and row["AWAY_LINE_SCORES"]
    assert row["HOME_PREGAME_ELO"] and row["AWAY_PREGAME_ELO"]
    line = next(r for r in tables["METRICS_WP_PREGAME"] if r["GAME_ID"] == row["ID"])
    assert (line["SPREAD"], line["HOME_WIN_PROBABILITY"]) == (-7.5, 0.7)
    venues = {r["NAME"]: r for r in tables["VENUES"]}
    assert (venues["Ashford Field"]["LATITUDE"], venues["Ashford Field"]["LONGITUDE"]) == (
        33.0,
        -87.0,
    )
    assert (venues["Brookvale Bowl"]["LATITUDE"], venues["Brookvale Bowl"]["LONGITUDE"]) == (
        34.0,
        -87.0,
    )
    assert (venues["Neutral Dome"]["LATITUDE"], venues["Neutral Dome"]["LONGITUDE"]) == (
        29.95,
        -90.08,
    )
    assert venues["Old Grounds"]["LATITUDE"] is None and venues["Old Grounds"]["LONGITUDE"] is None


def test_only_ashford_field_and_brookvale_bowl_lie_in_the_polygon(
    tables: dict[str, list[Row]],
) -> None:
    inside = [
        r["NAME"]
        for r in tables["VENUES"]
        if r["LATITUDE"] is not None and 32 <= r["LATITUDE"] <= 35 and -88 <= r["LONGITUDE"] <= -85
    ]
    assert sorted(inside) == ["Ashford Field", "Brookvale Bowl"]


def test_ashford_elo_in_2024(tables: dict[str, list[Row]]) -> None:
    rows = sorted(
        (
            r
            for r in games(tables, 2024)
            if r["SEASON_TYPE"] == "regular" and "Ashford" in (r["HOME_TEAM"], r["AWAY_TEAM"])
        ),
        key=lambda r: r["WEEK"],
    )
    first, week8 = rows[0], next(r for r in rows if r["WEEK"] == 8)

    def side(row: Row, kind: str) -> int:
        value: int = row["HOME_" + kind] if row["HOME_TEAM"] == "Ashford" else row["AWAY_" + kind]
        return value

    assert side(first, "PREGAME_ELO") == 1650
    assert side(week8, "POSTGAME_ELO") == 1702
    assert side(next(r for r in rows if r["WEEK"] == 9 or r["WEEK"] == 10), "PREGAME_ELO") == 1702


def test_early_seasons(league: League, tables: dict[str, list[Row]]) -> None:
    old = games(tables, 1885)
    assert len(old) == 2 and {r["VENUE"] for r in old} == {"Old Grounds"}
    assert all(r["HOME_PREGAME_ELO"] is None and r["AWAY_POSTGAME_ELO"] is None for r in old)
    assert "Marston" not in {r["SCHOOL"] for r in tables["TEAMS"]}
    assert "Marston" in {r["HOME_TEAM"] for r in old} | {r["AWAY_TEAM"] for r in old}
    rows_1950 = games(tables, 1950)
    tie = game(league, tables, "tie_1950")
    assert (tie["HOME_POINTS"], tie["AWAY_POINTS"]) == (7, 7)
    assert all(r["HOME_PREGAME_ELO"] is not None for r in rows_1950)
    unplayed = game(league, tables, "unplayed_1950")
    assert (unplayed["WEEK"], unplayed["COMPLETED"], unplayed["HOME_POINTS"]) == (5, False, None)
    assert {unplayed["HOME_TEAM"], unplayed["AWAY_TEAM"]} == {"Brookvale", "Lowell"}
    assert len(games(tables, 2000)) == 1


def test_lowell_1985_records_big_sky_and_the_sec_has_the_2024_shape(
    tables: dict[str, list[Row]],
) -> None:
    rows = games(tables, 1985)
    lowell = [r for r in rows if "Lowell" in (r["HOME_TEAM"], r["AWAY_TEAM"])]
    assert len(lowell) == 2
    for row in lowell:
        side = "HOME" if row["HOME_TEAM"] == "Lowell" else "AWAY"
        assert (row[side + "_CLASSIFICATION"], row[side + "_CONFERENCE"]) == ("fcs", "Big Sky")
    sec = Counter(r["WEEK"] for r in rows if r["HOME_CONFERENCE"] == r["AWAY_CONFERENCE"] == "SEC")
    assert sec == {13: 2, 14: 1}


def test_the_2024_playoff_and_postseason_games(
    league: League, tables: dict[str, list[Row]]
) -> None:
    first = game(league, tables, "cfp_first_round_2024")
    quarter = game(league, tables, "cfp_quarterfinal_2024")
    assert (
        first["HOME_TEAM"],
        first["AWAY_TEAM"],
        first["PLAYOFF_HOME_SEED"],
        first["PLAYOFF_AWAY_SEED"],
    ) == (
        "Kingsley",
        "Northam",
        8,
        9,
    )
    assert (quarter["HOME_TEAM"], quarter["AWAY_TEAM"], quarter["PLAYOFF_BOWL_NAME"]) == (
        "Ashford",
        "Kingsley",
        "Sugar Bowl",
    )
    assert (quarter["PLAYOFF_ROUND"], quarter["NEUTRAL_SITE"], quarter["VENUE"]) == (
        "quarterfinal",
        True,
        "Neutral Dome",
    )
    assert quarter["AWAY_POINTS"] > quarter["HOME_POINTS"]
    harbor = game(league, tables, "harbor_bowl_2024")
    assert harbor["PLAYOFF_COMPETITION"] is None and harbor["SEASON_TYPE"] == "postseason"
    fcs = game(league, tables, "fcs_playoff_2024")
    assert (fcs["HOME_CLASSIFICATION"], fcs["AWAY_CLASSIFICATION"]) == ("fcs", "fcs")
    desert = game(league, tables, "desert_bowl_2023")
    assert desert["PLAYOFF_COMPETITION"] is None and desert["SEASON_TYPE"] == "postseason"
    assert {desert["HOME_TEAM"], desert["AWAY_TEAM"]} == {"Carrow", "Eastport"}
    postseason = {r["WEEK"] for r in games(tables, 2024) if r["SEASON_TYPE"] == "postseason"}
    assert postseason == {1}


def test_the_2026_calendar_dates_and_placeholder(
    league: League, tables: dict[str, list[Row]]
) -> None:
    calendar = {(r["SEASON_TYPE"], r["WEEK"]): r for r in tables["CALENDAR"] if r["SEASON"] == 2026}
    assert calendar[("regular", 9)]["FIRST_GAME_START"] == datetime(2026, 10, 22, 23, 30)
    assert calendar[("regular", 10)]["FIRST_GAME_START"] == datetime(2026, 10, 29, 23, 30)
    thursday = game(league, tables, "thursday_2026_w10")
    assert thursday["START_DATE"] == datetime(2026, 10, 29, 23, 30)
    assert datetime(2026, 10, 29).strftime("%A") == "Thursday"
    placeholder = game(league, tables, "sec_placeholder_2026")
    assert (
        placeholder["HOME_TEAM"] is None
        and placeholder["HOME_ID"] is None
        and placeholder["WEEK"] == 15
    )
    in_2024 = {(r["SEASON_TYPE"], r["WEEK"]) for r in tables["CALENDAR"] if r["SEASON"] == 2024}
    assert in_2024 == {("regular", w) for w in range(1, 16)} | {("postseason", 1)}
    assert not any(r["SEASON"] == 1950 for r in tables["CALENDAR"])


def test_every_game_week_falls_in_a_calendar_week_of_its_season(
    tables: dict[str, list[Row]],
) -> None:
    for season in (2024, 2025, 2026):
        weeks = {(r["SEASON_TYPE"], r["WEEK"]) for r in tables["CALENDAR"] if r["SEASON"] == season}
        assert {(r["SEASON_TYPE"], r["WEEK"]) for r in games(tables, season)} <= weeks


def test_2026_lines_are_for_weeks_10_and_11_only(tables: dict[str, list[Row]]) -> None:
    lines = [r for r in tables["METRICS_WP_PREGAME"] if r["SEASON"] == 2026]
    assert lines and {r["WEEK"] for r in lines} == {10, 11}
    assert {r["REQ_AT"] for r in lines} == {FETCHED_2026}
    by_week = {w: {r["GAME_ID"] for r in lines if r["WEEK"] == w} for w in (10, 11)}
    assert len(by_week[10]) >= 2 and len(by_week[11]) >= 2
    weeks_2024 = {r["WEEK"] for r in tables["METRICS_WP_PREGAME"] if r["SEASON"] == 2024}
    assert {9, 10, 14} <= weeks_2024


def test_sp_ratings(tables: dict[str, list[Row]]) -> None:
    def rating(season: int, team: str) -> float | None:
        return next(
            (
                r["RATING"]
                for r in tables["RATINGS_SP"]
                if r["YEAR_"] == season and r["TEAM"] == team
            ),
            None,
        )

    assert rating(2024, "Ashford") == 25.1
    assert rating(2023, "Carrow") == 20.0
    assert rating(2024, "Ashford St.") is not None
    assert rating(2024, "nationalAverages") is not None
    assert rating(2012, "Ashford") is None
    assert rating(2025, "Ashford") is not None and rating(2026, "Ashford") not in (None, 24.0)
    teams = {r["SCHOOL"] for r in tables["TEAMS"]}
    assert "Ashford St." not in teams


def test_rankings(tables: dict[str, list[Row]]) -> None:
    def weeks(season: int, poll: str, season_type: str) -> set[int]:
        return {
            r["WEEK"]
            for r in tables["RANKINGS_POLLS_RANKS"]
            if r["SEASON"] == season and r["POLL"] == poll and r["SEASON_TYPE"] == season_type
        }

    assert weeks(2024, "AP Top 25", "regular") == set(range(1, 17))
    assert weeks(2024, "AP Top 25", "postseason") == {1}
    assert weeks(2024, "Playoff Committee Rankings", "regular") == set(range(10, 16))
    assert weeks(2024, "Mystery Poll", "regular")
    assert weeks(2025, "Playoff Committee Rankings", "regular") == set(range(10, 15))
    assert weeks(2025, "Playoff Committee Rankings", "postseason") == {1}
    assert weeks(2026, "AP Top 25", "regular") == set(range(1, 11))
    assert weeks(2026, "Playoff Committee Rankings", "regular") == {10}
    assert {r["REQ_AT"] for r in tables["RANKINGS_POLLS_RANKS"] if r["SEASON"] == 2026} == {
        FETCHED_2026_RANKINGS
    }

    def rank(season: int, poll: str, season_type: str, week: int) -> int:
        found: int = next(
            r["RANK"]
            for r in tables["RANKINGS_POLLS_RANKS"]
            if (r["SEASON"], r["POLL"], r["SEASON_TYPE"], r["WEEK"])
            == (season, poll, season_type, week)
            and r["SCHOOL"] == "Ashford"
        )
        return found

    assert rank(2024, "AP Top 25", "postseason", 1) == 5
    assert rank(2024, "Playoff Committee Rankings", "regular", 15) == 1


def test_affiliations_follow_the_slice_rules(tables: dict[str, list[Row]]) -> None:
    rows = tables["CONFERENCES_AFFILIATIONS"]

    def spells(team: str, **slice_echo: Any) -> list[tuple[str, int, int | None, str | None]]:
        return sorted(
            (r["CONFERENCE"], r["START_YEAR"], r["END_YEAR"], r["CONFERENCE_DIVISION"])
            for r in rows
            if r["TEAM"] == team and all(r[k] == v for k, v in slice_echo.items())
        )

    assert spells("Carrow", P_MIN_YEAR=1869) == [
        ("Pac-10", 1978, 2010, None),
        ("Pac-12", 2011, 2023, None),
        ("SEC", 2024, None, None),
    ]
    assert ("Pac-12", 2011, None, None) in spells("Dunmore", P_MIN_YEAR=1869)
    assert spells("Dunmore", P_YEAR=2026) == [("Mountain West", 2026, None, None)]
    assert spells("Northam", P_MIN_YEAR=1869) == [
        ("Mountain West", 2010, 2014, None),
        ("Sun Belt", 2014, None, None),
    ]
    eastport = spells("Eastport", P_MIN_YEAR=1869)
    assert ("Mountain West", 1999, 2022, "Mountain") in eastport
    assert ("Mountain West", 2023, None, None) in eastport
    assert spells("Lowell") == [] and spells("Marston") == []
    assert {r["CONFERENCE"] for r in rows if r["TEAM"] == "Fairhaven"} == {"Mountain West"}
    assert {r["CLASSIFICATION"] for r in rows if r["TEAM"] == "Fairhaven"} == {"fbs"}


def test_the_team_list_shows_current_conferences(tables: dict[str, list[Row]]) -> None:
    current = {r["SCHOOL"]: r["CONFERENCE"] for r in tables["TEAMS"]}
    assert current["Carrow"] == "SEC"
    assert current["Dunmore"] == "Mountain West"
    assert current["Lowell"] == "Big Sky"


# --- championship cases, by the detection rule -------------------------------------------------


def test_the_rule_finds_exactly_the_intended_championship_games(
    league: League, tables: dict[str, list[Row]]
) -> None:
    detected = detect_championships(tables["GAMES"], championship_ranges())
    expected = {
        league.game_id(name)
        for name in ("sec_championship_2014", "aac_championship_2023", "sec_championship_2024")
    }
    assert detected == expected


def test_the_missed_the_no_championship_and_the_non_conference_cases_exist(
    league: League, tables: dict[str, list[Row]]
) -> None:
    mw = game(league, tables, "mw_championship_2014")
    makeup = game(league, tables, "mw_makeup_2014")
    assert (mw["WEEK"], makeup["WEEK"]) == (15, 15)
    assert "Championship" in mw["NOTES"] and mw["CONFERENCE_GAME"] and makeup["CONFERENCE_GAME"]
    pac = [
        r for r in games(tables, 2024) if r["HOME_CONFERENCE"] == r["AWAY_CONFERENCE"] == "Pac-12"
    ]
    assert Counter(r["WEEK"] for r in pac) == {13: 2, 14: 1}
    week15 = game(league, tables, "aac_week15_2023")
    assert week15["CONFERENCE_GAME"] is False
    assert week15["HOME_CONFERENCE"] == week15["AWAY_CONFERENCE"] == "American Athletic"
    navy = game(league, tables, "independents_week15_2024")
    assert navy["CONFERENCE_GAME"] is False
    assert navy["HOME_CONFERENCE"] == navy["AWAY_CONFERENCE"] == "FBS Independents"
    aac = Counter(r["WEEK"] for r in games(tables, 2023) if r["CONFERENCE_GAME"])
    assert aac == {13: 2, 14: 1}
    sec = Counter(
        r["WEEK"]
        for r in games(tables, 2024)
        if r["CONFERENCE_GAME"] and r["HOME_CONFERENCE"] == "SEC"
    )
    assert sec[14] == 2 and sec[15] == 1 and max(sec) == 15


# --- the structural guard, mirroring the ERROR checks ------------------------------------------


def covering_spells(season: int) -> list[lt.Spell]:
    return [
        spell
        for team in lt.TEAMS
        for spell in team.spells
        if spell.covers(season) and (season <= lt.RANGE_LAST_SEASON) == (spell.start <= 2025)
    ]


@pytest.mark.parametrize("season", [s for s in STORED_SEASONS if s >= 1998])
def test_every_power_conference_has_a_member_in_every_stored_season_from_1998(
    tables: dict[str, list[Row]], season: int
) -> None:
    rows = [
        r
        for r in tables["CONFERENCES_AFFILIATIONS"]
        if ((r["P_MIN_YEAR"] is not None and season <= r["P_MAX_YEAR"]) or r["P_YEAR"] == season)
        and r["START_YEAR"] <= season
        and (r["END_YEAR"] is None or season <= r["END_YEAR"])
    ]
    assert power_conferences(season) <= {r["CONFERENCE"] for r in rows}


def test_no_natural_key_is_stored_twice(tables: dict[str, list[Row]]) -> None:
    keys = {
        "GAMES": ("ID",),
        "METRICS_WP_PREGAME": ("GAME_ID",),
        "RATINGS_SP": ("YEAR_", "TEAM"),
        "RANKINGS_POLLS_RANKS": ("POLL", "SEASON", "SEASON_TYPE", "WEEK", "RANK"),
        "CALENDAR": ("SEASON", "SEASON_TYPE", "WEEK"),
        "TEAMS": ("ID",),
        "VENUES": ("ID",),
        "CONFERENCES": ("ID",),
    }
    for table, columns in keys.items():
        found = Counter(tuple(r[c] for c in columns) for r in tables[table])
        assert [k for k, n in found.items() if n > 1] == [], table
    spells = Counter(
        (r["TEAM_ID"], r["CONFERENCE"], r["START_YEAR"], r["P_YEAR"])
        for r in tables["CONFERENCES_AFFILIATIONS"]
    )
    assert max(spells.values()) == 1
    ranked = Counter(
        (r["POLL"], r["SEASON"], r["SEASON_TYPE"], r["WEEK"], r["TEAM_ID"])
        for r in tables["RANKINGS_POLLS_RANKS"]
    )
    assert max(ranked.values()) == 1


def test_every_game_has_a_week_and_a_known_season_type(tables: dict[str, list[Row]]) -> None:
    assert all(r["WEEK"] is not None for r in tables["GAMES"])
    assert {r["SEASON_TYPE"] for r in tables["GAMES"]} == {"regular", "postseason"}


def test_every_non_retroactive_fbs_team_season_has_a_membership_or_a_game_conference(
    tables: dict[str, list[Row]],
) -> None:
    memberships = {
        (r["TEAM_ID"], season)
        for r in tables["CONFERENCES_AFFILIATIONS"]
        for season in range(r["START_YEAR"], (r["END_YEAR"] or 2026) + 1)
    }
    for row in tables["GAMES"]:
        if row["SEASON"] < 1978:
            continue
        for side in ("HOME", "AWAY"):
            if row[f"{side}_CLASSIFICATION"] != "fbs" or row[f"{side}_ID"] is None:
                continue
            assert (row[f"{side}_ID"], row["SEASON"]) in memberships or row[f"{side}_CONFERENCE"], (
                row
            )


def test_conference_flags_only_mark_games_between_teams_of_one_conference(
    tables: dict[str, list[Row]],
) -> None:
    for row in tables["GAMES"]:
        if row["CONFERENCE_GAME"] and row["HOME_ID"] is not None:
            assert row["HOME_CONFERENCE"] == row["AWAY_CONFERENCE"] is not None, row["ID"]


def test_no_team_plays_twice_in_a_regular_week(tables: dict[str, list[Row]]) -> None:
    seen: Counter[tuple[int, int, str]] = Counter()
    for row in tables["GAMES"]:
        if row["SEASON_TYPE"] == "regular" and row["HOME_TEAM"]:
            for team in (row["HOME_TEAM"], row["AWAY_TEAM"]):
                seen[(row["SEASON"], row["WEEK"], team)] += 1
    assert max(seen.values()) == 1


def test_every_sp_row_of_a_team_matches_the_team_list_except_the_unmatched_name(
    tables: dict[str, list[Row]],
) -> None:
    teams = {r["SCHOOL"] for r in tables["TEAMS"]}
    unmatched = {r["TEAM"] for r in tables["RATINGS_SP"]} - teams
    assert unmatched == {"Ashford St.", "nationalAverages"}


# --- recordings and the load log ---------------------------------------------------------------


def test_replay_recordings_cover_the_2026_weekly_slices(league: League) -> None:
    recordings = league.replay_recordings()
    keys = {r.slice_key for r in recordings}
    assert keys == {
        season_key(table, 2026)
        for table in ("GAMES", "METRICS_WP_PREGAME", "RATINGS_SP", "RANKINGS_POLLS_RANKS")
    }
    for recording in recordings:
        item = next(s for s in league.slices if s.slice_key == recording.slice_key)
        assert json.loads(recording.body) == item.body
        assert recording.requested_at.replace(tzinfo=None) == item.fetched_at
        assert decode_recording(encode_recording(recording), recording.slice_key, "x") == recording


# --- variants ----------------------------------------------------------------------------------


def body_of(league: League, table: str, key: str) -> Any:
    return league.get(table, key).body


def test_variants_leave_the_base_league_alone(league: League) -> None:
    before = json.dumps(
        [(s.table, s.slice_key, s.body, s.is_final) for s in league.slices], sort_keys=True
    )
    for variant in (
        with_duplicate_rows,
        with_incomplete_week9_game,
        with_renamed_conference,
        with_thursday_result,
        with_unaffiliated_fbs_team,
        with_nonfinal_prior,
    ):
        variant(league)
    mutate_after(league, 2024, 9)
    after = json.dumps(
        [(s.table, s.slice_key, s.body, s.is_final) for s in league.slices], sort_keys=True
    )
    assert before == after


def test_the_base_league_has_no_duplicates_and_the_variant_adds_them(league: League) -> None:
    changed = with_duplicate_rows(league)
    assert len(changed.slices) == len(league.slices) + 4
    rows = [dict(zip(mirror_columns("GAMES"), r, strict=True)) for r in changed.rows("GAMES")]
    target = league.game_id("carrow_dunmore_2026_w3")
    copies = sorted(
        (r["REQ_AT"], r["HOME_POINTS"], r["AWAY_POINTS"]) for r in rows if r["ID"] == target
    )
    assert [c[1:] for c in copies] == [(21, 14), (21, 17)]
    assert copies[0][0] < copies[1][0]
    lines = [
        dict(zip(mirror_columns("METRICS_WP_PREGAME"), r, strict=True))
        for r in changed.rows("METRICS_WP_PREGAME")
    ]
    assert [r["SPREAD"] for r in lines if r["GAME_ID"] == target] == [-3.5]
    sp = [
        dict(zip(mirror_columns("RATINGS_SP"), r, strict=True)) for r in changed.rows("RATINGS_SP")
    ]
    ashford = sorted(
        (r["REQ_AT"], r["RATING"]) for r in sp if r["YEAR_"] == 2026 and r["TEAM"] == "Ashford"
    )
    assert [a[1] for a in ashford][-1] == 24.0 and len(ashford) == 2
    ranks = [
        dict(zip(mirror_columns("RANKINGS_POLLS_RANKS"), r, strict=True))
        for r in changed.rows("RANKINGS_POLLS_RANKS")
    ]
    ap9 = [r for r in ranks if (r["SEASON"], r["POLL"], r["WEEK"]) == (2026, "AP Top 25", 9)]
    assert len(ap9) == 2 * len({r["TEAM_ID"] for r in ap9})
    assert all(not s.is_final for s in changed.slices[-4:])


def test_the_incomplete_week9_variant_leaves_one_game_open(league: League) -> None:
    changed = with_incomplete_week9_game(league)
    rows = [dict(zip(mirror_columns("GAMES"), r, strict=True)) for r in changed.rows("GAMES")]
    open_week9 = [r for r in rows if r["SEASON"] == 2026 and r["WEEK"] == 9 and not r["COMPLETED"]]
    assert [r["ID"] for r in open_week9] == [league.game_id("week9_game_2026")]
    assert open_week9[0]["HOME_POINTS"] is None


def test_the_thursday_variant_refetches_the_games_with_one_more_result(league: League) -> None:
    changed = with_thursday_result(league)
    item = changed.get("GAMES", season_key("GAMES", 2026))
    assert item.fetched_at == datetime(2026, 10, 30, 12, 0)
    done = [g for g in item.body if g["week"] == 10 and g["completed"]]
    assert [g["id"] for g in done] == [league.game_id("thursday_2026_w10")]


def test_the_quarry_variant_adds_an_fbs_team_without_a_conference(league: League) -> None:
    changed = with_unaffiliated_fbs_team(league)
    rows = [dict(zip(mirror_columns("GAMES"), r, strict=True)) for r in changed.rows("GAMES")]
    quarry = [r for r in rows if r["HOME_TEAM"] == "Quarry" or r["AWAY_TEAM"] == "Quarry"]
    assert len(quarry) == 1
    assert (quarry[0]["HOME_CONFERENCE"], quarry[0]["HOME_CLASSIFICATION"]) == (None, "fbs")
    assert "Quarry" in {r[1] for r in changed.rows("TEAMS")} | {r[2] for r in changed.rows("TEAMS")}
    memberships = [r for r in changed.rows("CONFERENCES_AFFILIATIONS") if "Quarry" in r]
    assert memberships == []


def test_the_renamed_conference_variant_moves_the_2024_sec_to_southeastern(league: League) -> None:
    changed = with_renamed_conference(league)
    rows = [
        dict(zip(mirror_columns("CONFERENCES_AFFILIATIONS"), r, strict=True))
        for r in changed.rows("CONFERENCES_AFFILIATIONS")
    ]
    ashford = sorted(
        (r["START_YEAR"], r["END_YEAR"], r["CONFERENCE"])
        for r in rows
        if r["TEAM"] == "Ashford" and r["P_MIN_YEAR"]
    )
    assert ashford == [(1933, 2023, "SEC"), (2024, None, "Southeastern")]
    games_2024 = changed.game_bodies(2024)
    assert "SEC" not in {g["homeConference"] for g in games_2024}
    assert "Southeastern" in {g["homeConference"] for g in games_2024}
    assert "Southeastern" not in {g["homeConference"] for g in changed.game_bodies(2023)}


def test_the_nonfinal_prior_variant_registers_the_2025_sp_slice_as_not_final(
    league: League,
) -> None:
    changed = with_nonfinal_prior(league)
    flags = {s.slice_key: s.is_final for s in changed.of("RATINGS_SP")}
    assert flags[season_key("RATINGS_SP", 2025)] is False
    assert flags[season_key("RATINGS_SP", 2024)] is True


# --- mutate_after ------------------------------------------------------------------------------


def by_id(league: League, season: int) -> dict[int, Row]:
    return {g["id"]: g for g in league.game_bodies(season)}


def test_mutate_after_2024_week_9_changes_what_is_hidden_and_keeps_what_is_visible(
    league: League,
) -> None:
    changed = mutate_after(league, 2024, 9)
    old, new = by_id(league, 2024), by_id(changed, 2024)
    visible = {i: g for i, g in old.items() if game_week_seq(g) <= 9}
    assert all(new[i] == g for i, g in visible.items())
    for identifier, before in old.items():
        seq = game_week_seq(before)
        after = new[identifier]
        if seq > 9:
            assert (after["completed"], after["homePoints"]) != (
                before["completed"],
                before["homePoints"],
            )
        if seq > 10 and before.get("homePregameElo") is not None:
            assert after["homePregameElo"] != before["homePregameElo"]
        if seq == 10 and before.get("homePregameElo") is not None:
            assert after["homePregameElo"] == before["homePregameElo"]
    assert len(new) == len(old) + 1
    added = (set(new) - set(old)).pop()
    assert new[added]["seasonType"] == "postseason"


def test_mutate_after_swaps_the_participants_of_the_championship_and_the_quarterfinal(
    league: League,
) -> None:
    changed = mutate_after(league, 2024, 9)
    old, new = by_id(league, 2024), by_id(changed, 2024)
    final = league.game_id("sec_championship_2024")
    quarter = league.game_id("cfp_quarterfinal_2024")
    for identifier in (final, quarter):
        assert (new[identifier]["homeTeam"], new[identifier]["awayTeam"]) != (
            old[identifier]["homeTeam"],
            old[identifier]["awayTeam"],
        )
    assert new[final]["homeConference"] == "SEC" == new[final]["awayConference"]
    first_round = league.game_id("cfp_first_round_2024")
    assert new[first_round]["homeTeam"] == old[first_round]["homeTeam"]


def test_mutate_after_at_week_14_keeps_the_championship_teams(league: League) -> None:
    changed = mutate_after(league, 2024, 14)
    final = league.game_id("sec_championship_2024")
    quarter = league.game_id("cfp_quarterfinal_2024")
    assert by_id(changed, 2024)[final]["homeTeam"] == by_id(league, 2024)[final]["homeTeam"]
    assert by_id(changed, 2024)[quarter]["homeTeam"] != by_id(league, 2024)[quarter]["homeTeam"]


def test_mutate_after_changes_hidden_lines_polls_sp_and_calendar(league: League) -> None:
    changed = mutate_after(league, 2024, 9)
    ids = {g["id"]: game_week_seq(g) for g in league.game_bodies(2024)}
    key = season_key("METRICS_WP_PREGAME", 2024)
    for before, after in zip(
        body_of(league, "METRICS_WP_PREGAME", key),
        body_of(changed, "METRICS_WP_PREGAME", key),
        strict=True,
    ):
        assert (before["spread"] == after["spread"]) == (ids[before["gameId"]] <= 9)
    rkey = season_key("RANKINGS_POLLS_RANKS", 2024)
    old_ranks, new_ranks = (
        body_of(league, "RANKINGS_POLLS_RANKS", rkey),
        body_of(changed, "RANKINGS_POLLS_RANKS", rkey),
    )
    for before, after in zip(old_ranks, new_ranks, strict=True):
        week_9_visible = before["seasonType"] == "regular" and before["week"] <= 10
        ap_before = (
            next(p for p in before["polls"] if p["poll"] == "AP Top 25")
            if any(p["poll"] == "AP Top 25" for p in before["polls"])
            else None
        )
        if ap_before is None:
            continue
        ap_after = next(p for p in after["polls"] if p["poll"] == "AP Top 25")
        assert (ap_before == ap_after) == week_9_visible, (before["seasonType"], before["week"])
    skey = season_key("RATINGS_SP", 2024)
    assert (
        body_of(changed, "RATINGS_SP", skey)[0]["rating"]
        != body_of(league, "RATINGS_SP", skey)[0]["rating"]
    )
    previous = season_key("RATINGS_SP", 2023)
    assert changed.get("RATINGS_SP", previous).is_final is False
    ckey = season_key("CALENDAR", 2024)
    for before, after in zip(
        body_of(league, "CALENDAR", ckey), body_of(changed, "CALENDAR", ckey), strict=True
    ):
        week = before["week"] if before["seasonType"] == "regular" else 100 + before["week"]
        assert (before == after) == (week <= 10), week


def test_mutate_after_2026_week_5_hides_the_later_lines_and_never_moves_the_next_kickoff(
    league: League,
) -> None:
    changed = mutate_after(league, 2026, 5)
    key = season_key("METRICS_WP_PREGAME", 2026)
    for before, after in zip(
        body_of(league, "METRICS_WP_PREGAME", key),
        body_of(changed, "METRICS_WP_PREGAME", key),
        strict=True,
    ):
        assert before["spread"] != after["spread"]
    old, new = by_id(league, 2026), by_id(changed, 2026)
    week6 = [i for i, g in old.items() if g["week"] == 6]
    assert all(new[i]["startDate"] == old[i]["startDate"] for i in week6)
    assert all(new[i]["homePregameElo"] == old[i]["homePregameElo"] for i in week6)
    assert Counter(
        g["homePoints"] == new[i]["homePoints"] for i, g in old.items() if g["week"] <= 5
    ) == {True: sum(1 for g in old.values() if g["week"] <= 5)}
    ckey = season_key("CALENDAR", 2026)
    calendar_old, calendar_new = (
        body_of(league, "CALENDAR", ckey),
        body_of(changed, "CALENDAR", ckey),
    )
    week6_row = next(i for i, r in enumerate(calendar_old) if r["week"] == 6)
    assert calendar_old[week6_row] == calendar_new[week6_row]
    assert calendar_old[week6_row + 1] != calendar_new[week6_row + 1]


def test_2026_weeks_1_to_9_are_completed_and_later_weeks_are_scheduled(
    tables: dict[str, list[Row]],
) -> None:
    for row in games(tables, 2026):
        if row["HOME_TEAM"] is None:
            continue
        assert row["COMPLETED"] is (row["WEEK"] <= 9), (row["ID"], row["WEEK"])
        assert (row["HOME_POINTS"] is None) is (not row["COMPLETED"])
        assert row["HOME_PREGAME_ELO"] is not None
        assert (row["HOME_POSTGAME_ELO"] is None) is (not row["COMPLETED"])
    assert {r["WEEK"] for r in games(tables, 2026)} == set(range(1, 16))
    assert all(r["COMPLETED"] for s in (2024, 2025) for r in games(tables, s))
