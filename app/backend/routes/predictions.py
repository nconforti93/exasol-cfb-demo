"""Frozen predictions and scorecards from stored serving views."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.backend import models
from app.backend.routes.read import ReadService, Row, _iso, _number
from app.backend.sessions import SessionStore
from app.backend.settings import Settings


def _prediction(row: Row) -> Row:
    return {
        "game_id": row["GAME_ID"],
        "season": row["SEASON"],
        "season_type": row["SEASON_TYPE"],
        "week_seq": row["WEEK_SEQ"],
        "start_at": _iso(row["START_AT"]),
        "home_team_id": row["HOME_TEAM_ID"],
        "home_school": row["HOME_SCHOOL"],
        "away_team_id": row["AWAY_TEAM_ID"],
        "away_school": row["AWAY_SCHOOL"],
        "pred_home_points": _number(row["HOME_POINTS_PRED"]),
        "pred_away_points": _number(row["AWAY_POINTS_PRED"]),
        "pick_team_id": row["PICK_TEAM_ID"],
        "pick_school": row["PICK_SCHOOL"],
        "pick_win_prob": _number(row["PICK_WIN_PROB"]),
        "freeze_kind": row["FREEZE_KIND"],
        "freeze_label": row["FREEZE_LABEL"],
        "is_rescheduled": bool(row["IS_RESCHEDULED"]),
        "home_points": _number(row["HOME_POINTS"]),
        "away_points": _number(row["AWAY_POINTS"]),
        "grade": row["GRADE"],
    }


def _scorecard(rows: list[Row]) -> list[Row]:
    if not rows:
        return []
    headline = next(
        (row["FREEZE_KIND"] for row in rows if row["IS_HEADLINE"]),
        "ALL" if any(row["FREEZE_KIND"] == "ALL" for row in rows) else rows[0]["FREEZE_KIND"],
    )
    shaped = []
    for row in rows:
        if any(row[key] is None for key in ("PICK_ACCURACY", "BRIER", "MAE_MARGIN")):
            continue
        shaped.append(
            {
                "population": row["POPULATION"],
                "freeze_kind": row["FREEZE_KIND"],
                "is_headline": row["FREEZE_KIND"] == headline,
                "games_graded": row["GAMES_GRADED"],
                "record_label": row["RECORD_LABEL"],
                "pick_accuracy": _number(row["PICK_ACCURACY"]),
                "brier": _number(row["BRIER"]),
                "mae_margin": _number(row["MAE_MARGIN"]),
                "line_correct": row["LINE_CORRECT"],
                "line_wrong": row["LINE_WRONG"],
                "line_brier": _number(row["LINE_BRIER"]),
                "model_brier_on_line_games": _number(row["MODEL_BRIER_ON_LINE_GAMES"]),
            }
        )
    return shaped


class PredictionsService(ReadService):
    """Resolve a point before reading its frozen games and graded scores."""

    def predictions(self, season: int | None, week: int | None) -> Row:
        point, current, notice = self.point(season, week)
        season_id, week_seq = int(point["AS_OF_SEASON"]), int(point["AS_OF_WEEK_SEQ"])
        next_week = point["NEXT_WEEK_SEQ"]
        upcoming = (
            self.fetch(
                "predictions_for_week",
                season=season_id,
                week_seq=week_seq,
                next_week_seq=int(next_week),
            )
            if next_week is not None
            else []
        )
        graded = self.fetch("predictions_graded_in_week", season=season_id, week_seq=week_seq)
        graded = [row for row in graded if row["GRADE"] is not None]
        return {
            "point": self.resolved(point, current),
            "notice": notice,
            "this_week": [_prediction(row) for row in upcoming],
            "last_week": [_prediction(row) for row in graded],
            "has_predictions": bool(upcoming),
            "has_grades": bool(graded),
        }

    def scorecard(self, season: int | None, week: int | None, population: models.Population) -> Row:
        point, current, notice = self.point(season, week)
        season_id, week_seq = int(point["AS_OF_SEASON"]), int(point["AS_OF_WEEK_SEQ"])
        weekly = self.fetch("scorecard_all_weeks", season=season_id, population=population)
        at_week = [row for row in weekly if int(row["WEEK_SEQ"]) == week_seq]
        latest_graded = max((int(row["WEEK_SEQ"]) for row in weekly), default=-1)
        # Season totals and calibration have no point key. Hide them when they contain a later week.
        if latest_graded <= week_seq:
            season_rows = self.fetch("scorecard_season", season=season_id, population=population)
            calibration = self.fetch(
                "scorecard_calibration", season=season_id, population=population
            )
        else:
            season_rows, calibration = [], []
        chosen_kind = next((row["FREEZE_KIND"] for row in season_rows if row["IS_HEADLINE"]), "ALL")
        if chosen_kind == "ALL" and not any(row["FREEZE_KIND"] == "ALL" for row in season_rows):
            chosen_kind = "RECONSTRUCTED"
        return {
            "point": self.resolved(point, current),
            "notice": notice,
            "population": population,
            "week": _scorecard(at_week),
            "season": _scorecard(season_rows),
            "calibration": [
                {
                    "bucket": row["BUCKET"],
                    "games_graded": row["GAMES_GRADED"],
                    "mean_pick_win_prob": _number(row["MEAN_PICK_WIN_PROB"]),
                    "pick_win_share": _number(row["PICK_WIN_SHARE"]),
                }
                for row in calibration
                if row["FREEZE_KIND"] == chosen_kind
            ],
        }


def make_router(settings: Settings, store: SessionStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/predictions", response_model=models.PredictionsResponse)
    def predictions(request: Request, season: int | None = None, week: int | None = None) -> Row:
        return PredictionsService(request, settings, store).predictions(season, week)

    @router.get("/api/scorecard", response_model=models.ScorecardResponse)
    def scorecard(
        request: Request,
        season: int | None = None,
        week: int | None = None,
        population: models.Population = "FBS_INVOLVED",
    ) -> Row:
        return PredictionsService(request, settings, store).scorecard(season, week, population)

    return router
