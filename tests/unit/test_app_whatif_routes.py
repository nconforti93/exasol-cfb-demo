"""The stored what-if route uses a single current run and validates game selections."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from analyses.whatif import ConditionError
from app.backend import models
from app.backend.routes.whatif import RunChanged, WhatIfGameCache, WhatIfService
from app.backend.sessions import Session
from app.backend.settings import Settings
from tests.unit.db_fakes import query_error


class FakeWhatIf(WhatIfService):
    def __init__(self, sample: int = 612) -> None:
        self.sample = sample
        self.settings = cast(Settings, SimpleNamespace(api_schema="TEST_CFB_API"))
        self.session = cast(Session, SimpleNamespace(log=None))
        self._context = "test"
        self.sql = ""
        self.pool = SimpleNamespace(run=self.run_sql)

    def run_sql(self, sql: str, **_: Any) -> Any:
        self.sql = sql
        return SimpleNamespace(
            dicts=lambda: [
                {
                    "TEAM_ID": 1,
                    "N_MATCHING": self.sample,
                    "N_IN_CFP": min(self.sample, 300),
                    "N_CHAMPION": min(self.sample, 20),
                }
            ]
        )

    def fetch(self, name: str, **_: Any) -> list[dict[str, Any]]:
        rows: dict[str, list[dict[str, Any]]] = {
            "whatif_run": [
                {
                    "RUN_ID": 7,
                    "AS_OF_SEASON": 2026,
                    "AS_OF_WEEK_SEQ": 9,
                    "AS_OF_LABEL": "2026, after week 9",
                    "N_SIMS": 2000,
                }
            ],
            "whatif_games": [
                {
                    "RUN_ID": 7,
                    "GAME_ID": 9001,
                    "PATH_POS": 13,
                    "SEASON": 2026,
                    "WEEK_SEQ": 10,
                    "IS_NEXT_WEEK": True,
                    "START_AT": None,
                    "HOME_TEAM_ID": 1,
                    "HOME_SCHOOL": "Auburn",
                    "AWAY_TEAM_ID": 2,
                    "AWAY_SCHOOL": "Ole Miss",
                    "HOME_WIN_PROB": 0.55,
                }
            ],
            "analysis_settings": [{"SETTING_NAME": "MIN_EFFECTIVE_SAMPLE", "SETTING_NUMBER": 1000}],
            "whatif_base_odds": [
                {
                    "TEAM_ID": 1,
                    "SCHOOL": "Auburn",
                    "CONFERENCE": "SEC",
                    "P_IN_CFP": 0.1,
                    "P_CHAMPION": 0.01,
                }
            ],
        }
        return rows.get(name, [])


def request(run_id: int = 7, game_id: int = 9001) -> models.WhatIfOddsRequest:
    return models.WhatIfOddsRequest.model_validate(
        {
            "run_id": run_id,
            "conditions": [{"game_id": game_id, "winner": "home"}],
        }
    )


def test_games_and_conditional_odds_share_current_run() -> None:
    service = FakeWhatIf()
    games = models.WhatIfGamesResponse.model_validate(service.games())
    odds = models.WhatIfOddsResponse.model_validate(service.conditional_odds(request()))
    assert games.run is not None and games.run.run_id == odds.run_id == 7
    assert games.games[0].is_next_week and games.max_conditions == 16
    assert odds.effective_sample == 612 and odds.low_sample
    assert odds.rows[0].p_in_cfp == 300 / 612
    assert "SUBSTR(o.\"OUTCOMES\", 13, 1) = '1'" in service.sql
    assert '"TEST_CFB_API"."WHATIF_OUTCOMES"' in service.sql


def test_stale_run_and_unknown_game_are_rejected_before_sql() -> None:
    service = FakeWhatIf()
    with pytest.raises(RunChanged):
        service.conditional_odds(request(run_id=8))
    with pytest.raises(ConditionError, match="not in this what-if run"):
        service.conditional_odds(request(game_id=9999))
    assert service.sql == ""


def test_zero_matching_seasons_does_not_publish_fake_probabilities() -> None:
    response = FakeWhatIf(sample=0).conditional_odds(request())
    odds = models.WhatIfOddsResponse.model_validate(response)
    assert odds.effective_sample == 0 and odds.rows == [] and odds.low_sample


class FakeGuide(FakeWhatIf):
    def run_sql(self, sql: str, **_: Any) -> Any:
        self.sql = sql
        return SimpleNamespace(
            dicts=lambda: [
                {
                    "GAME_ID": 9002,
                    "SEASON": 2026,
                    "WEEK_SEQ": 10,
                    "IS_NEXT_WEEK": True,
                    "HOME_TEAM_ID": 3,
                    "HOME_SCHOOL": "Georgia",
                    "AWAY_TEAM_ID": 4,
                    "AWAY_SCHOOL": "Florida",
                    "N_HOME": 400,
                    "N_HOME_IN_CFP": 100,
                    "N_AWAY": 200,
                    "N_AWAY_IN_CFP": 120,
                }
            ]
        )

    def fetch(self, name: str, **values: Any) -> list[dict[str, Any]]:
        if name == "analysis_settings":
            return [{"SETTING_NAME": "MIN_BRANCH_SAMPLE", "SETTING_NUMBER": 300}]
        return super().fetch(name, **values)


def guide_request(game_id: int = 9001, run_id: int = 7) -> models.WhatIfGuideRequest:
    return models.WhatIfGuideRequest.model_validate(
        {
            "run_id": run_id,
            "team_id": 1,
            "conditions": [{"game_id": game_id, "winner": "home"}],
        }
    )


def test_conditional_guide_uses_matching_seasons_and_excludes_set_game() -> None:
    service = FakeGuide()
    guide = models.GuideResponse.model_validate(service.conditional_guide(guide_request()))
    assert guide.conditional and guide.run_id == 7 and guide.top == []
    assert guide.coming_week[0].game_id == 9002
    assert guide.coming_week[0].root_for_school == "Florida"
    assert guide.coming_week[0].p_if_home_win == 0.25
    assert guide.coming_week[0].p_if_away_win == 0.6
    assert guide.coming_week[0].low_sample
    assert '"GAME_ID" NOT IN (9001)' in service.sql
    assert "SUBSTR(o.\"OUTCOMES\", 13, 1) = '1'" in service.sql
    assert '"TEST_CFB_API"."WHATIF_PLAYOFF"' in service.sql


def test_conditional_guide_rejects_invalid_input_before_sql() -> None:
    service = FakeGuide()
    with pytest.raises(RunChanged):
        service.conditional_guide(guide_request(run_id=8))
    with pytest.raises(ConditionError, match="not in this what-if run"):
        service.conditional_guide(guide_request(game_id=9999))
    assert service.sql == ""


def test_game_cache_reuses_rows_for_the_same_run() -> None:
    class CachedWhatIf(FakeWhatIf):
        reads = 0

        def fetch(self, name: str, **values: Any) -> list[dict[str, Any]]:
            if name == "whatif_games":
                self.reads += 1
            return super().fetch(name, **values)

    service = CachedWhatIf()
    service._game_cache = WhatIfGameCache()
    assert len(service.run_and_games()[1]) == 1
    assert len(service.run_and_games()[1]) == 1
    assert service.reads == 1


def test_unknown_column_during_conditional_guide_means_run_changed() -> None:
    service = FakeGuide()
    service.pool = SimpleNamespace(
        run=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            query_error("42000", "column OUTCOMES not found")
        )
    )
    with pytest.raises(RunChanged):
        service.conditional_guide(guide_request())
