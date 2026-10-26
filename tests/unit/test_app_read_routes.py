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
