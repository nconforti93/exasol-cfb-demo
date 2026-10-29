import json
from pathlib import Path
from typing import Any

import pytest

from app.backend import models
from scripts import app_fixtures as export

EXPORTED_AT = "2026-10-23T12:00:00Z"
SEASON = 2026
INDIANA, OHIO, FCS_TEAM = 84, 194, 9001


def executed(name: str, rows: list[dict[str, Any]]) -> export.Executed:
    return export.Executed(name, "page", f"SELECT {name}", "2026-10-23T11:59:00Z", 12, rows)


def weeks(done_through: int = 7) -> list[dict[str, Any]]:
    return [
        {"SEASON_TYPE": "regular", "WEEK": week, "WEEK_SEQ": week, "IS_DONE": week <= done_through}
        for week in range(1, 10)
    ]


def map_row(team_id: int, school: str, conference: str, **extra: object) -> dict[str, Any]:
    row = {
        "TEAM_ID": team_id,
        "SCHOOL": school,
        "MASCOT": "Hoosiers",
        "ABBREVIATION": "IU",
        "CONFERENCE": conference,
        "COLOR": "#990000",
        "ALTERNATE_COLOR": "#ffffff",
        "LATITUDE": 39.18,
        "LONGITUDE": -86.52,
        "WINS": 6,
        "LOSSES": 1,
        "CONF_WINS": 3,
        "CONF_LOSSES": 0,
        "AP_RANK": 1,
        "CFP_RANK": None,
    }
    return row | extra


def game_row(
    game_id: int, team_id: int, week: int, opponent_id: int, **extra: object
) -> dict[str, Any]:
    row = {
        "GAME_ID": game_id,
        "WEEK": week,
        "SEASON_TYPE": "regular",
        "WEEK_SEQ": week,
        "START_AT": f"2026-09-{week:02d} 16:00:00.000000",
        "TEAM_ID": team_id,
        "OPPONENT_ID": opponent_id,
        "OPPONENT_NAME": "Ohio State",
        "OPPONENT_CONFERENCE": "Big Ten",
        "OPP_ABBREVIATION": "OSU",
        "OPP_COLOR": "#bb0000",
        "OPP_ALTERNATE_COLOR": "#666666",
        "SITE": "HOME",
        "HAS_RESULT": True,
        "GAME_RESULT": "W",
        "POINTS_FOR": 31,
        "POINTS_AGAINST": 10,
        "TEAM_SPREAD": "-7.5",
        "TEAM_PREGAME_WIN_PROB": "0.7100",
    }
    return row | extra


def results() -> dict[str, export.Executed]:
    return {
        "weeks": executed("weeks", weeks()),
        "map": executed(
            "map",
            [
                map_row(INDIANA, "Indiana", "Big Ten"),
                map_row(
                    OHIO,
                    "Ohio State",
                    "Big Ten",
                    ABBREVIATION="OSU",
                    AP_RANK=None,
                    LATITUDE=None,
                    LONGITUDE=None,
                ),
            ],
        ),
        "games": executed(
            "games",
            [
                game_row(1, INDIANA, 1, OHIO),
                game_row(
                    2,
                    INDIANA,
                    7,
                    FCS_TEAM,
                    OPPONENT_NAME="Southern",
                    OPPONENT_CONFERENCE=None,
                    OPP_COLOR="#null",
                ),
                game_row(
                    3,
                    INDIANA,
                    8,
                    OHIO,
                    HAS_RESULT=True,
                    GAME_RESULT="L",
                    POINTS_FOR=3,
                    POINTS_AGAINST=40,
                ),
                game_row(
                    4,
                    INDIANA,
                    9,
                    OHIO,
                    HAS_RESULT=False,
                    GAME_RESULT=None,
                    POINTS_FOR=None,
                    POINTS_AGAINST=None,
                ),
                game_row(
                    5,
                    OHIO,
                    8,
                    INDIANA,
                    HAS_RESULT=False,
                    GAME_RESULT=None,
                    POINTS_FOR=None,
                    POINTS_AGAINST=None,
                ),
            ],
        ),
        "ranks": executed(
            "ranks",
            [
                {"POLL_CODE": "AP", "VISIBLE_AT_WEEK_SEQ": 0, "POLL_RANK": 5, "TEAM_ID": INDIANA},
                {"POLL_CODE": "AP", "VISIBLE_AT_WEEK_SEQ": 7, "POLL_RANK": 1, "TEAM_ID": INDIANA},
                {"POLL_CODE": "AP", "VISIBLE_AT_WEEK_SEQ": 7, "POLL_RANK": 3, "TEAM_ID": OHIO},
                {"POLL_CODE": "AP", "VISIBLE_AT_WEEK_SEQ": 1, "POLL_RANK": 3, "TEAM_ID": OHIO},
            ],
        ),
        "sp": executed(
            "sp",
            [
                {
                    "SEASON": 2026,
                    "TEAM_ID": INDIANA,
                    "SP_RATING": 30.1,
                    "SP_RANKING": 2,
                    "OFFENSE_RATING": 40.0,
                    "OFFENSE_RANKING": 3,
                    "DEFENSE_RATING": 10.0,
                    "DEFENSE_RANKING": 4,
                },
                {
                    "SEASON": 2025,
                    "TEAM_ID": INDIANA,
                    "SP_RATING": 32.4,
                    "SP_RANKING": 1,
                    "OFFENSE_RATING": 41.0,
                    "OFFENSE_RANKING": 2,
                    "DEFENSE_RATING": 9.0,
                    "DEFENSE_RANKING": 1,
                },
            ],
        ),
        "week_games": executed(
            "week_games",
            [
                {
                    "GAME_ID": 5,
                    "WEEK_SEQ": 8,
                    "START_AT": "2026-10-24 19:30:00.000000",
                    "VENUE_NAME": "Ohio Stadium",
                    "HOME_TEAM_ID": OHIO,
                    "HOME_TEAM": "Ohio State",
                    "AWAY_TEAM_ID": INDIANA,
                    "AWAY_TEAM": "Indiana",
                    "PREGAME_SPREAD": "-3",
                    "HOME_PREGAME_WIN_PROB": "0.58",
                },
                {
                    "GAME_ID": 6,
                    "WEEK_SEQ": 8,
                    "START_AT": "2026-10-24 16:00:00.000000",
                    "VENUE_NAME": "Somewhere",
                    "HOME_TEAM_ID": FCS_TEAM,
                    "HOME_TEAM": "Southern",
                    "AWAY_TEAM_ID": INDIANA,
                    "AWAY_TEAM": "Indiana",
                    "PREGAME_SPREAD": None,
                    "HOME_PREGAME_WIN_PROB": None,
                },
            ],
        ),
        "road_warriors": executed(
            "road_warriors",
            [
                {
                    "TEAM_ID": INDIANA,
                    "KM": 3210.5,
                    "TRIPS": 3,
                    "LONGEST_TRIP_KM": 1800.2,
                    "LONGEST_TRIP_TO": "Eugene",
                }
            ],
        ),
        "movers": executed(
            "movers",
            [
                {
                    "TEAM_ID": INDIANA,
                    "SP_NOW": 30.1,
                    "SP_BEFORE": 20.0,
                    "DELTA": 10.1,
                    "RISE_RANK": 1,
                    "FALL_RANK": 90,
                },
                {
                    "TEAM_ID": OHIO,
                    "SP_NOW": 10.0,
                    "SP_BEFORE": 25.0,
                    "DELTA": -15.0,
                    "RISE_RANK": 90,
                    "FALL_RANK": 1,
                },
            ],
        ),
        "conference_strength": executed(
            "conference_strength",
            [
                {
                    "CONFERENCE": "Big Ten",
                    "TEAM_COUNT": 18,
                    "MEAN_SP": 9.5,
                    "NONCONF_WINS": 30,
                    "NONCONF_LOSSES": 2,
                    "RANKED_TEAMS": 6,
                }
            ],
        ),
        "streaks": executed(
            "streaks", [{"TEAM_ID": INDIANA, "STREAK_LENGTH": 23, "SINCE_KEY": 2025003}]
        ),
    }


def snapshot() -> export.Snapshot:
    return export.build_snapshot(
        SEASON,
        "aws",
        results(),
        has_logo=lambda team_id: team_id == INDIANA,
        exported_at=EXPORTED_AT,
    )


def test_every_file_validates_against_the_contract_models() -> None:
    files = snapshot().files
    models.TeamsMapResponse.model_validate(files["teams-map.json"])
    models.TeamsResponse.model_validate(files["teams.json"])
    models.PointsResponse.model_validate(files["points.json"])
    models.LeagueWeekResponse.model_validate(files["league-week.json"])
    models.LeagueStatsResponse.model_validate(files["league-stats.json"])
    models.TeamResponse.model_validate(files[f"team/{INDIANA}.json"])
    models.TeamOutlookResponse.model_validate(files[f"team/{INDIANA}.outlook.json"])
    models.InspectorResponse.model_validate(files["inspector.json"])


def test_the_point_is_the_last_done_week_and_the_record_comes_from_the_query() -> None:
    teams_map = snapshot().files["teams-map.json"]
    assert teams_map["point"]["week_seq"] == 7
    assert teams_map["point"]["next_week_seq"] == 8
    assert teams_map["point"]["label"] == "2026, after week 7"
    indiana = next(team for team in teams_map["teams"] if team["team_id"] == INDIANA)
    assert (indiana["wins"], indiana["losses"], indiana["ap_rank"], indiana["has_logo"]) == (
        6,
        1,
        1,
        True,
    )
    assert teams_map["conferences"] == ["Big Ten"]


def test_games_after_the_point_are_upcoming_and_carry_the_line_but_finals_do_not() -> None:
    games = snapshot().files[f"team/{INDIANA}.json"]["games"]
    by_week = {game["week"]: game for game in games}
    assert (
        by_week[1]["status"] == "FINAL"
        and by_week[1]["spread"] is None
        and by_week[1]["win_prob"] is None
    )
    assert by_week[1]["result"] == "W" and by_week[1]["team_points"] == 31
    played_after_point = by_week[8]
    assert played_after_point["status"] == "UPCOMING" and played_after_point["result"] is None
    assert played_after_point["spread"] == -7.5 and played_after_point["win_prob"] == 0.71
    assert played_after_point["win_prob_source"] == "CFBD"
    assert [game["week"] for game in games] == sorted(game["week"] for game in games)


def test_an_opponent_outside_the_team_list_keeps_its_name_and_no_logo() -> None:
    games = snapshot().files[f"team/{INDIANA}.json"]["games"]
    southern = next(game for game in games if game["week"] == 7)["opponent"]
    assert (
        southern["school"] == "Southern"
        and southern["has_logo"] is False
        and southern["color"] is None
    )


def test_opponent_rank_is_the_poll_visible_before_the_game() -> None:
    games = snapshot().files[f"team/{INDIANA}.json"]["games"]
    by_week = {game["week"]: game for game in games}
    assert by_week[1]["opponent_rank"] is None
    assert by_week[9]["opponent_rank"] == 3


def test_sp_is_one_row_per_season_newest_first_and_polls_follow_the_point() -> None:
    team = snapshot().files[f"team/{INDIANA}.json"]
    assert [row["season"] for row in team["sp"]] == [2026, 2025]
    assert team["sp"][1]["overall"] == 32.4
    assert [(poll["week_seq"], poll["ap_rank"]) for poll in team["polls"]] == [
        (0, 5),
        (1, None),
        (7, 1),
    ]
    assert team["polls"][0]["label"] == "preseason"


def test_the_week_is_ordered_by_interest_and_flags_the_ranked_underdog() -> None:
    week = snapshot().files["league-week.json"]
    assert week["week_seq"] == 8 and week["run_id"] is None
    first = week["games"][0]
    assert first["game_id"] == 5 and first["game_of_week"] is True and first["stakes"] is None
    assert (
        first["spread"] == -3
        and first["home_win_prob"] == 0.58
        and first["win_prob_source"] == "CFBD"
    )
    assert first["upset_alert_team_id"] == INDIANA
    assert sum(game["game_of_week"] for game in week["games"]) == 1
    assert week["games"][1]["win_prob_source"] is None


def test_stats_follow_the_contract_shapes() -> None:
    stats = snapshot().files["league-stats.json"]
    assert stats["road_warriors"][0]["longest_trip_to"] == "Eugene"
    assert [row["team"]["school"] for row in stats["risers"]] == ["Indiana"]
    assert [row["team"]["school"] for row in stats["fallers"]] == ["Ohio State"]
    assert stats["streaks"][0] == {
        "team": stats["streaks"][0]["team"],
        "length": 23,
        "since_season": 2025,
        "since_week": 3,
    }
    assert set(stats["queries"]) == {"road_warriors", "movers", "conference_strength", "streaks"}


def test_the_outlook_is_the_empty_variant_and_the_queries_name_real_entries() -> None:
    files = snapshot().files
    outlook = files[f"team/{INDIANA}.outlook.json"]
    assert outlook["run_id"] is None and outlook["odds"]["available"] is False
    entry_ids = {entry["entry_id"] for entry in files["inspector.json"]["entries"]}
    team_queries = files[f"team/{INDIANA}.json"]["queries"]
    assert set(team_queries) == {"hero", "schedule", "ratings", "polls"}
    assert set(team_queries.values()) <= entry_ids
    assert set(files["teams-map.json"]["queries"].values()) <= entry_ids
    assert set(files["league-week.json"]["queries"].values()) <= entry_ids


def test_a_team_without_a_map_row_has_no_page() -> None:
    assert f"team/{FCS_TEAM}.json" not in snapshot().files


def test_no_statement_names_the_virtual_schema() -> None:
    statements = export.statements(SEASON, point_seq=7, next_week_seq=8)
    assert statements
    for statement in statements.values():
        assert "CFBD_VS" not in statement.sql.upper()
    assert "CFBD_VS" not in export.weeks_sql(SEASON).upper()


def test_the_output_directory_must_lie_outside_the_repository(tmp_path: Path) -> None:
    export.ensure_outside_repository(tmp_path / "fixtures")
    with pytest.raises(export.ExportError):
        export.ensure_outside_repository(export.ROOT / "cache" / "fixtures")


def test_write_snapshot_writes_json_and_a_latest_link(tmp_path: Path) -> None:
    built = snapshot()
    target = export.write_snapshot(built, tmp_path, "aws-2026")
    assert json.loads((target / "teams-map.json").read_text())["point"]["season"] == 2026
    assert (target / "manifest.json").is_file()
    assert (tmp_path / "latest").resolve() == target.resolve()
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["counts"]["teams"] == 2 and manifest["exported_at"] == EXPORTED_AT
