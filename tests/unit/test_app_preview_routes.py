"""A preview click uses one guarded fetch, then the browser cache."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Request

from app.backend import live, models, spend
from app.backend.routes.preview import PreviewPlanService
from app.backend.sessions import Session
from vs.host import ledger


def result() -> live.LiveResult:
    values = {
        "GAME_ID": 401,
        "GAME_HOME_TEAM_ID": 1,
        "GAME_HOME_TEAM_NAME": "Auburn",
        "GAME_AWAY_TEAM_ID": 2,
        "GAME_AWAY_TEAM_NAME": "Ole Miss",
        "ANALYSIS_HOME_RECORD_DATA_WINS": 6,
        "ANALYSIS_HOME_RECORD_DATA_LOSSES": 2,
        "ANALYSIS_HOME_RATINGS_DATA_ELO": 1520,
        "ANALYSIS_ODDS_DATA_PROVIDER": "synthetic",
        "ANALYSIS_ODDS_DATA_SPREAD": -3.5,
        "REQ_SOURCE": "LIVE",
        "REQ_MS": 120,
    }
    return live.LiveResult(
        tuple(values),
        (tuple(values.values()),),
        spend.SpendPlan("LIVE", 1, "LIVE"),
        1,
        True,
    )


class FakeExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def run_preview(self, game_id: int, *, max_calls: int, action_id: str) -> live.LiveResult:
        self.calls += 1
        assert (game_id, max_calls) == (401, 1)
        assert action_id.isalnum()
        return result()


class FakePreview(PreviewPlanService):
    def __init__(self, reconciled: bool) -> None:
        state = SimpleNamespace(reconciled=reconciled)
        self.request = cast(Request, SimpleNamespace(app=SimpleNamespace(state=state)))
        self.session = Session("browser")
        self.executor = FakeExecutor()

    def _choose(self, team_id: int, opponent_id: int) -> tuple[int | None, str]:
        assert (team_id, opponent_id) == (1, 2)
        return 401, "next"

    def _executor(self, book: ledger.Ledger) -> Any:
        return self.executor


def request() -> models.PreviewRequest:
    return models.PreviewRequest(team=1, opponent=2, max_calls=1, force=False)


def test_preview_refuses_before_any_fetch_without_reconciliation() -> None:
    service = FakePreview(reconciled=False)
    with pytest.raises(live.LiveRefused, match="reconciliation"):
        service.execute(request(), cast(ledger.Ledger, None))
    assert service.executor.calls == 0


def test_preview_uses_the_browser_cache_after_one_fetch() -> None:
    service = FakePreview(reconciled=True)
    book = cast(ledger.Ledger, None)
    first = models.PreviewResponse.model_validate(service.execute(request(), book))
    second = models.PreviewResponse.model_validate(service.execute(request(), book))
    assert service.executor.calls == 1
    assert first.calls_spent == 1 and not first.cached
    assert first.home.record == "6-2" and first.home.elo == 1520
    assert first.odds is not None and first.odds.spread == -3.5
    assert second.cached and second.calls_spent == 0
