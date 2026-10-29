"""Predictions keep the selected point's freeze and grading boundaries."""

from __future__ import annotations

from typing import Any

from app.backend import models, sqlcatalog
from app.backend.routes.predictions import PredictionsService
from app.backend.routes.read import Row

POINT: Row = {
    "AS_OF_SEASON": 2026,
    "AS_OF_WEEK_SEQ": 7,
    "NEXT_WEEK_SEQ": 8,
    "AS_OF_LABEL": "2026, after week 7",
}


class FakePredictions(PredictionsService):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def point(self, season: int | None, week: int | None) -> tuple[Row, Row, Row | None]:
        return POINT, POINT, None

    def fetch(self, name: str, **_: Any) -> list[Row]:
        self.calls.append(name)
        if name == "scorecard_all_weeks":
            return [
                {"WEEK_SEQ": 7, **score_row()},
                {"WEEK_SEQ": 8, **score_row()},
            ]
        if name == "scorecard_season":
            return [score_row()]
        return []


def score_row() -> Row:
    return {
        "POPULATION": "FBS_INVOLVED",
        "FREEZE_KIND": "ALL",
        "IS_HEADLINE": False,
        "GAMES_GRADED": 50,
        "RECORD_LABEL": "41-9",
        "PICK_ACCURACY": 0.82,
        "BRIER": 0.151,
        "MAE_MARGIN": 10.4,
        "LINE_CORRECT": 39,
        "LINE_WRONG": 11,
        "LINE_BRIER": 0.143,
        "MODEL_BRIER_ON_LINE_GAMES": 0.153,
    }


def test_upcoming_predictions_query_requires_the_selected_freeze_point() -> None:
    sql = sqlcatalog.render(
        sqlcatalog.CATALOG["predictions_for_week"],
        api_schema="TEST_CFB_API",
        api_live_schema="TEST_CFB_API_LIVE",
        season=2026,
        week_seq=7,
        next_week_seq=8,
    )
    assert '"FREEZE_WEEK_SEQ" = 7' in sql
    assert '"WEEK_SEQ" = 8' in sql
    assert '"FREEZE_SEASON" = 2026' in sql


def test_scorecard_hides_season_totals_that_include_a_later_week() -> None:
    service = FakePredictions()
    response = models.ScorecardResponse.model_validate(service.scorecard(2026, 7, "FBS_INVOLVED"))
    assert response.week[0].freeze_kind == "ALL"
    assert response.week[0].is_headline
    assert response.season == [] and response.calibration == []
    assert "scorecard_season" not in service.calls
