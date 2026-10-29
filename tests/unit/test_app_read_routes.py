"""The live read slice with small, synthetic serving-view rows."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from app.backend import models, sqlcatalog
from app.backend.routes.read import ReadService
from app.backend.settings import Settings

CURRENT = {
    "AS_OF_SEASON": 2026,
    "AS_OF_WEEK": 8,
    "AS_OF_SEASON_TYPE": "regular",
    "AS_OF_WEEK_SEQ": 8,
    "NEXT_WEEK_SEQ": 9,
    "AS_OF_LABEL": "2026, after week 8",
    "PUBLICATION_ID": None,
    "WHATIF_PUBLICATION_ID": None,
    "WHATIF_RUN_ID": None,
    "WHATIF_AS_OF_WEEK_SEQ": None,
    "WHATIF_VIEWS_CURRENT": False,
    "IS_CURRENT": True,
    "IS_AFTER_CURRENT": False,
    "HAS_PUBLICATION": False,
    "HAS_PREDICTIONS": False,
    "HAS_GRADES": False,
}
TEAM = {
    "TEAM_ID": 1,
    "SCHOOL": "Auburn",
    "MASCOT": "Tigers",
    "ABBREVIATION": "AUB",
    "CURRENT_CONFERENCE": "SEC",
    "COLOR": "#123456",
    "ALTERNATE_COLOR": "#abcdef",
    "LATITUDE": 32.6,
    "LONGITUDE": -85.5,
    "HAS_LOCATION": True,
    "IS_FBS": True,
}
OTHER = {
    "TEAM_ID": 2,
    "SCHOOL": "Ole Miss",
    "MASCOT": "Rebels",
    "ABBREVIATION": "MISS",
    "CURRENT_CONFERENCE": "SEC",
    "COLOR": "#aabbcc",
    "ALTERNATE_COLOR": "#112233",
    "LATITUDE": 34.4,
    "LONGITUDE": -89.5,
    "HAS_LOCATION": True,
    "IS_FBS": True,
}


class FakeRead(ReadService):
    def __init__(self, rows: dict[str, list[dict[str, Any]]], tmp_path: Path) -> None:
        self.rows = rows
        self.ids: dict[str, str] = {}
        self.settings = cast(Settings, SimpleNamespace(logo_dir=tmp_path, logos_enabled=False))

    def fetch(self, name: str, **values: Any) -> list[dict[str, Any]]:
        self.ids[name] = f"q-{name}"
        return self.rows.get(name, [])


def base_rows() -> dict[str, list[dict[str, Any]]]:
    return {
        "current_point": [CURRENT],
        "as_of_seasons": [{"AS_OF_SEASON": 2026}],
        "as_of_points": [CURRENT],
        "teams": [TEAM, OTHER],
        "fbs_team_seasons": [
            {"TEAM_ID": 1, "SCHOOL": "Auburn", "CONFERENCE_NAME": "SEC"},
            {"TEAM_ID": 2, "SCHOOL": "Ole Miss", "CONFERENCE_NAME": "SEC"},
        ],
        "team_results": [
            {
                "TEAM_ID": 1,
                "GAME_RESULT": "W",
                "IS_CONFERENCE_GAME": True,
                "IS_CONF_CHAMPIONSHIP": False,
            },
            {
                "TEAM_ID": 1,
                "GAME_RESULT": "L",
                "IS_CONFERENCE_GAME": True,
                "IS_CONF_CHAMPIONSHIP": True,
            },
        ],
        "point_rankings": [
            {"TEAM_ID": 1, "POLL_CODE": "AP", "VISIBLE_AT_WEEK_SEQ": 8, "POLL_RANK": 20},
        ],
    }


def test_unknown_point_falls_back_and_map_records_exclude_championship(
    tmp_path: Path,
) -> None:
    service = FakeRead(base_rows(), tmp_path)
    response = models.TeamsMapResponse.model_validate(service.teams_map(2026, 99))
    auburn = next(team for team in response.teams if team.school == "Auburn")
    assert response.point.is_current and response.notice is not None
    assert (auburn.wins, auburn.losses, auburn.conf_wins, auburn.conf_losses) == (1, 1, 1, 0)
    assert auburn.ap_rank == 20 and not auburn.has_logo
    assert response.queries["teams"] == "q-fbs_team_seasons"


def test_team_page_keeps_future_results_hidden_at_the_selected_point(tmp_path: Path) -> None:
    rows = base_rows()
    rows["team_schedule"] = [
        {
            "GAME_ID": 12,
            "WEEK": 9,
            "WEEK_SEQ": 9,
            "SEASON_TYPE": "regular",
            "START_AT": "2026-10-27 23:00:00",
            "HAS_RESULT": True,
            "SITE": "AWAY",
            "OPPONENT_ID": 2,
            "OPPONENT_NAME": "Ole Miss",
            "TEAM_PREGAME_WIN_PROB": 0.4,
            "POINTS_FOR": 31,
            "POINTS_AGAINST": 24,
            "GAME_RESULT": "W",
            "TEAM_SPREAD": 7.0,
        },
    ]
    rows["team_sp"] = []
    page = models.TeamResponse.model_validate(FakeRead(rows, tmp_path).team_page(1, 2026, 8))
    assert page.games[0].status == "UPCOMING"
    assert page.games[0].team_points is None and page.games[0].result is None
    assert page.games[0].win_prob == 0.4
    assert page.games[0].opponent.school == "Ole Miss"


def test_week_only_shows_fbs_games_and_has_one_game_of_the_week(tmp_path: Path) -> None:
    rows = base_rows()
    game = {
        "GAME_ID": 12,
        "WEEK_SEQ": 9,
        "START_AT": "2026-10-27 23:00:00",
        "VENUE_NAME": "Vaught-Hemingway",
        "HOME_TEAM_ID": 2,
        "HOME_TEAM": "Ole Miss",
        "AWAY_TEAM_ID": 1,
        "AWAY_TEAM": "Auburn",
        "HOME_AP_RANK": None,
        "AWAY_AP_RANK": 20,
        "PREGAME_SPREAD": -7.0,
        "HOME_PREGAME_WIN_PROB": "0.6000",
        "IS_FBS_GAME": True,
    }
    rows["slate_games"] = [game, {**game, "GAME_ID": 13, "IS_FBS_GAME": False}]
    week = models.LeagueWeekResponse.model_validate(FakeRead(rows, tmp_path).league_week(2026, 8))
    assert len(week.games) == 1 and week.games[0].game_of_week
    assert week.games[0].away.has_logo is False
    assert week.queries["games"] == "q-slate_games"


def test_outlook_and_week_use_frozen_prediction_odds_and_stakes(tmp_path: Path) -> None:
    rows = base_rows()
    rows["current_point"] = [{**CURRENT, "RUN_ID": 17}]
    rows["team_odds_history"] = [
        {
            "AS_OF_SEASON": 2026,
            "AS_OF_WEEK_SEQ": 8,
            "AS_OF_LABEL": CURRENT["AS_OF_LABEL"],
            "P_IN_CFP": 0.46,
            "P_BYE": 0.11,
            "P_CHAMPION": 0.04,
            "MOVE": "UP",
            "DELTA_P_IN_CFP": 0.03,
        }
    ]
    rows["slate_games"] = [
        {
            "GAME_ID": 12,
            "WEEK_SEQ": 9,
            "START_AT": "2026-10-27 23:00:00",
            "VENUE_NAME": "Jordan-Hare",
            "HOME_TEAM_ID": 1,
            "HOME_TEAM": "Auburn",
            "AWAY_TEAM_ID": 2,
            "AWAY_TEAM": "Ole Miss",
            "HOME_AP_RANK": 20,
            "AWAY_AP_RANK": None,
            "PREGAME_SPREAD": -3.5,
            "HOME_PREGAME_WIN_PROB": 0.59,
            "MODEL_HOME_WIN_PROB": 0.72,
            "HOME_POINTS_PRED": 31.2,
            "AWAY_POINTS_PRED": 22.4,
            "PREDICTION_KIND": "FROZEN",
            "IS_FBS_GAME": True,
            "IS_NEUTRAL_SITE": False,
            "STAKES": 0.23,
        }
    ]
    rows["rooting_guide_top"] = [
        {
            "RUN_ID": 17,
            "TEAM_ID": 1,
            "GAME_ID": 12,
            "SEASON": 2026,
            "WEEK_SEQ": 9,
            "IS_NEXT_WEEK": True,
            "GUIDE_RANK": 1,
            "NEXT_WEEK_RANK": 1,
            "HOME_TEAM_ID": 1,
            "HOME_SCHOOL": "Auburn",
            "AWAY_TEAM_ID": 2,
            "AWAY_SCHOOL": "Ole Miss",
            "ROOT_FOR_TEAM_ID": 1,
            "ROOT_FOR_SCHOOL": "Auburn",
            "P_IF_HOME_WIN": 0.6,
            "P_IF_AWAY_WIN": 0.37,
            "LEVERAGE": 0.23,
            "LOW_SAMPLE": False,
        }
    ]
    rows["rooting_guide_coming"] = rows["rooting_guide_top"]
    service = FakeRead(rows, tmp_path)
    outlook = models.TeamOutlookResponse.model_validate(service.team_outlook(1, None, None))
    week = models.LeagueWeekResponse.model_validate(service.league_week(None, None))
    assert outlook.run_id == week.run_id == 17
    assert outlook.odds.current is not None and outlook.odds.current.p_in_cfp == 0.46
    assert outlook.this_week.game is not None
    assert outlook.this_week.game.team_win_prob == 0.72
    assert outlook.this_week.game.pred_team_points == 31.2
    assert outlook.watch.guide is not None and outlook.watch.guide.top[0].leverage == 0.23
    assert week.games[0].home_win_prob == 0.72
    assert week.games[0].win_prob_source == "OURS" and week.games[0].stakes == 0.23
    assert week.games[0].game_of_week


def test_odds_route_uses_shown_run_and_current_whatif(tmp_path: Path) -> None:
    rows = base_rows()
    rows["current_point"] = [{**CURRENT, "RUN_ID": 17}]
    rows["odds_rows"] = [
        {
            "ODDS_RANK": 1,
            "TEAM_ID": 1,
            "SCHOOL": "Auburn",
            "CONFERENCE": "SEC",
            "P_IN_CFP": 0.82,
            "P_BYE": 0.24,
            "P_QUARTERFINAL": 0.42,
            "P_SEMIFINAL": 0.3,
            "P_FINAL": 0.17,
            "P_CHAMPION": 0.09,
            "P_CONF_CHAMPION": 0.5,
            "EXPECTED_WINS": 10.1,
            "MEAN_SEED": 3.2,
            "PREV_AS_OF_LABEL": "2026, after week 7",
            "PREV_P_IN_CFP": 0.79,
            "DELTA_P_IN_CFP": 0.03,
            "DELTA_P_CHAMPION": 0.01,
            "RANK_CHANGE": 2,
            "MOVE": "UP",
        }
    ]
    rows["published_run"] = [
        {
            "RUN_ID": 17,
            "RUN_ORIGIN": "MANUAL",
            "N_SIMS": 1000000,
            "SECONDS_TOTAL": 30.5,
            "SIMS_PER_SECOND": 32786.9,
            "NODES": 3,
            "INSTANCES": 12,
            "ENDED_AT": "2026-10-27 12:00:00",
        }
    ]
    rows["whatif_run"] = [{"RUN_ID": 17, "AS_OF_LABEL": CURRENT["AS_OF_LABEL"]}]
    rows["analysis_settings"] = [{"SETTING_NAME": "MOVE_THRESHOLD", "SETTING_NUMBER": 0.007}]
    odds = models.OddsResponse.model_validate(FakeRead(rows, tmp_path).odds(None, None))
    assert odds.run is not None and odds.run.run_id == 17
    assert odds.rows[0].p_in_cfp == 0.82 and odds.rows[0].move == "UP"
    assert odds.move_threshold == 0.007
    assert odds.whatif.available and odds.whatif.run_id == 17


def test_point_filtered_statements_render_without_live_sources() -> None:
    for name in ("team_results", "slate_games", "road_warriors", "conference_strength"):
        rendered = sqlcatalog.render(
            sqlcatalog.CATALOG[name],
            api_schema="TEST_CFB_API",
            api_live_schema="TEST_CFB_API_LIVE",
            season=2026,
            week_seq=8,
        )
        assert '"TEST_CFB_API".' in rendered
        assert "CFBD_VS" not in rendered
        assert "2026" in rendered and "8" in rendered
