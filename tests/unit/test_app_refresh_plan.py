"""The refresh plan validates targets and explains whether its calendar week will publish."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi import HTTPException

from app.backend import models
from app.backend.routes import refresh
from app.backend.routes.read import ReadService


class FakeRead:
    def __init__(self, current_week: int, games: list[dict[str, Any]]) -> None:
        self.current_week = current_week
        self.games = games

    def fetch(self, name: str, **values: Any) -> list[dict[str, Any]]:
        assert name == "week_games"
        return self.games

    def point(self, season: None, week: None) -> tuple[dict[str, Any], dict[str, Any], None]:
        current = {
            "AS_OF_SEASON": 2026,
            "AS_OF_WEEK_SEQ": self.current_week,
            "AS_OF_WEEK": self.current_week,
        }
        return current, current, None


def test_refresh_target_rejects_fractional_or_invalid_week() -> None:
    for season, week in ((2026.5, 8), (2026, 8.5), (2026, 0), (2026, 100)):
        with pytest.raises(HTTPException) as error:
            refresh._target(models.RefreshTarget(season=season, week=week, season_type="regular"))
        assert error.value.status_code == 400


def test_completed_postseason_week_may_advance_the_point() -> None:
    now = datetime(2026, 12, 1, tzinfo=UTC)
    games = [{"START_AT": "2026-11-30 12:00:00", "IS_COMPLETED": True}]
    state = refresh._week(cast(ReadService, FakeRead(15, games)), 2026, 1, "postseason", False, now)
    assert state["point_will_move"] is True
    assert state["games"] == 1 and state["completed"] == 1


def test_unfinished_week_does_not_claim_a_point_move() -> None:
    now = datetime(2026, 10, 27, tzinfo=UTC)
    games = [{"START_AT": "2026-10-30 23:00:00", "IS_COMPLETED": False}]
    state = refresh._week(cast(ReadService, FakeRead(7, games)), 2026, 8, "regular", False, now)
    assert state["point_will_move"] is False
    assert state["not_kicked_off"] == 1
