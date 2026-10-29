"""Week, all-time and betting-line upset lists from the stored API view."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.backend import models
from app.backend.routes.read import ReadService, Row, _number
from app.backend.sessions import SessionStore
from app.backend.settings import Settings


def _upset(row: Row) -> Row:
    return {
        "game_id": row["GAME_ID"],
        "season": row["SEASON"],
        "season_type": row["SEASON_TYPE"],
        "week": row["WEEK"],
        "week_seq": row["WEEK_SEQ"],
        "winner_team_id": row["WINNER_TEAM_ID"],
        "winner_school": row["WINNER_SCHOOL"],
        "loser_team_id": row["LOSER_TEAM_ID"],
        "loser_school": row["LOSER_SCHOOL"],
        "winner_points": row["WINNER_POINTS"],
        "loser_points": row["LOSER_POINTS"],
        "winner_site": row["WINNER_SITE"],
        "prob_source": row["PROB_SOURCE"],
        "winner_pregame_win_prob": _number(row["WINNER_PREGAME_WIN_PROB"]),
        "upset_magnitude": _number(row["UPSET_MAGNITUDE"]),
        "week_rank": row["WEEK_RANK"],
        "season_rank": row["SEASON_RANK"],
        "all_time_rank": row["ALL_TIME_RANK"],
        "all_time_tied": bool(row["ALL_TIME_TIED"]),
        "all_time_from_season": row["ALL_TIME_FROM_SEASON"],
        "line_era_rank": row["LINE_ERA_RANK"],
        "line_era_tied": bool(row["LINE_ERA_TIED"]),
        "model_winner_win_prob": _number(row["MODEL_WINNER_WIN_PROB"]),
    }


class UpsetsService(ReadService):
    def upsets(self, season: int | None, week: int | None) -> Row:
        point, current, notice = self.point(season, week)
        settings = self.fetch("analysis_settings")
        from_row = next(
            (row for row in settings if row["SETTING_NAME"] == "ALL_TIME_FROM_SEASON"), None
        )
        if from_row is None:
            raise HTTPException(503, "the upset ranking start season is unavailable")
        from_season = int(from_row["SETTING_NUMBER"])
        weekly = self.fetch(
            "upsets_week",
            season=int(point["AS_OF_SEASON"]),
            week_seq=int(point["AS_OF_WEEK_SEQ"]),
        )
        all_time = self.fetch("upsets_all_time")
        line_era = self.fetch("upsets_line_era")
        return {
            "point": self.resolved(point, current),
            "notice": notice,
            "week": [_upset(row) for row in weekly],
            "all_time": [_upset(row) for row in all_time],
            "line_era": [_upset(row) for row in line_era],
            "all_time_from_season": from_season,
        }


def make_router(settings: Settings, store: SessionStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/upsets", response_model=models.UpsetsResponse)
    def upsets(request: Request, season: int | None = None, week: int | None = None) -> Row:
        return UpsetsService(request, settings, store).upsets(season, week)

    return router
