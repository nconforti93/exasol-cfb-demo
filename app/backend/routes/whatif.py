"""Stored-season what-if reads over CFB_API serving views."""

from __future__ import annotations

import threading
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pyexasol import ExaQueryError

from analyses.whatif import (
    MAX_CONDITIONS,
    METHOD,
    Condition,
    ConditionError,
    rooting_guide_sql,
    what_if_odds_sql,
)
from app.backend import models
from app.backend.routes.read import ReadService, Row, _iso, _number
from app.backend.sessions import SessionStore
from app.backend.settings import Settings


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status)


def _setting(rows: list[Row], name: str) -> int:
    row = next((row for row in rows if row["SETTING_NAME"] == name), None)
    return int(row["SETTING_NUMBER"]) if row is not None else 0


class WhatIfGameCache:
    """Small per-app cache; run ids are immutable and a new run gets a new id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[int, tuple[Row, ...]] = {}

    def get(self, run_id: int) -> tuple[Row, ...] | None:
        with self._lock:
            return self._rows.get(run_id)

    def put(self, run_id: int, rows: list[Row]) -> None:
        with self._lock:
            self._rows[run_id] = tuple(rows)
            if len(self._rows) > 2:
                del self._rows[next(iter(self._rows))]


class WhatIfService(ReadService):
    """Read a run, validate game ids, and count only its stored simulated seasons."""

    def __init__(
        self, request: Request, settings: Settings, store: SessionStore, cache: WhatIfGameCache
    ) -> None:
        super().__init__(request, settings, store)
        self._game_cache = cache

    def run_and_games(self) -> tuple[Row | None, list[Row]]:
        runs = self.fetch("whatif_run")
        if not runs:
            return None, []
        run = runs[0]
        run_id = int(run["RUN_ID"])
        cache = getattr(self, "_game_cache", None)
        cached = cache.get(run_id) if cache is not None else None
        if cached is not None:
            return run, list(cached)
        games = [row for row in self.fetch("whatif_games") if int(row["RUN_ID"]) == run_id]
        if cache is not None and games:
            cache.put(run_id, games)
        return run, games

    def _query_current(self, sql: str, run_id: int, page: str) -> list[Row]:
        try:
            result = self.pool.run(
                sql, log=self.session.log, page=page, context=f"{self._context}:{page}"
            )
        except ExaQueryError as error:
            self.forget_cached()
            current = self.fetch("whatif_run")
            message = error.message.lower()
            if (
                not current
                or int(current[0]["RUN_ID"]) != run_id
                or ("column" in message and ("not found" in message or "unknown" in message))
            ):
                raise RunChanged from None
            raise
        return cast(list[Row], result.dicts())

    def games(self) -> Row:
        run, games = self.run_and_games()
        return {
            "run": {
                "run_id": run["RUN_ID"],
                "season": run["AS_OF_SEASON"],
                "as_of_week_seq": run["AS_OF_WEEK_SEQ"],
                "as_of_label": run["AS_OF_LABEL"],
                "n_sims": run["N_SIMS"],
            }
            if run is not None
            else None,
            "games": [
                {
                    "game_id": row["GAME_ID"],
                    "season": row["SEASON"],
                    "week_seq": row["WEEK_SEQ"],
                    "is_next_week": bool(row["IS_NEXT_WEEK"]),
                    "start_at": _iso(row["START_AT"]),
                    "home_team_id": row["HOME_TEAM_ID"],
                    "home_school": row["HOME_SCHOOL"],
                    "away_team_id": row["AWAY_TEAM_ID"],
                    "away_school": row["AWAY_SCHOOL"],
                    "home_win_prob": _number(row["HOME_WIN_PROB"]),
                }
                for row in games
            ],
            "max_conditions": MAX_CONDITIONS,
        }

    def conditional_odds(self, body: models.WhatIfOddsRequest) -> Row:
        if type(body.run_id) is not int or body.run_id <= 0:
            raise ConditionError("run id must be a positive integer")
        run, games = self.run_and_games()
        if run is None or body.run_id != int(run["RUN_ID"]):
            raise RunChanged
        positions = {int(row["GAME_ID"]): int(row["PATH_POS"]) for row in games}
        conditions: list[Condition] = []
        for item in body.conditions:
            if type(item.game_id) is not int or item.game_id <= 0:
                raise ConditionError("game id must be a positive integer")
            conditions.append(Condition(item.game_id, item.winner == "home"))
        sql = what_if_odds_sql(
            body.run_id, conditions, positions, api_schema=self.settings.api_schema
        )
        counts = self._query_current(sql, body.run_id, "whatif_odds")
        if not counts:
            raise RunChanged
        sample = int(counts[0]["N_MATCHING"])
        threshold = _setting(self.fetch("analysis_settings"), "MIN_EFFECTIVE_SAMPLE")
        if threshold <= 0:
            raise RuntimeError("MIN_EFFECTIVE_SAMPLE is unavailable")
        base_rows = self.fetch("whatif_base_odds", run_id=body.run_id)
        base = {int(row["TEAM_ID"]): row for row in base_rows}
        if not base:
            raise RunChanged
        current = self.fetch("whatif_run")
        if not current or int(current[0]["RUN_ID"]) != body.run_id:
            raise RunChanged
        low_sample = sample < threshold
        output = []
        for row in counts if sample else []:
            team_id = int(row["TEAM_ID"])
            original = base.get(team_id)
            if original is None:
                raise RunChanged
            p_cfp = int(row["N_IN_CFP"]) / sample if sample else 0.0
            p_champion = int(row["N_CHAMPION"]) / sample if sample else 0.0
            base_cfp = float(original["P_IN_CFP"])
            base_champion = float(original["P_CHAMPION"])
            output.append(
                {
                    "team_id": team_id,
                    "school": original["SCHOOL"],
                    "conference": original["CONFERENCE"],
                    "p_in_cfp": p_cfp,
                    "p_champion": p_champion,
                    "base_p_in_cfp": base_cfp,
                    "base_p_champion": base_champion,
                    "delta_p_in_cfp": p_cfp - base_cfp,
                    "delta_p_champion": p_champion - base_champion,
                    "low_sample": low_sample,
                }
            )
        return {
            "run_id": body.run_id,
            "n_sims": int(run["N_SIMS"]),
            "effective_sample": sample,
            "low_sample": low_sample,
            "method": METHOD,
            "rows": output,
        }

    def conditional_guide(self, body: models.WhatIfGuideRequest) -> Row:
        if type(body.run_id) is not int or body.run_id <= 0:
            raise ConditionError("run id must be a positive integer")
        if type(body.team_id) is not int or body.team_id <= 0:
            raise ConditionError("team id must be a positive integer")
        run, games = self.run_and_games()
        if run is None or body.run_id != int(run["RUN_ID"]):
            raise RunChanged
        positions = {int(row["GAME_ID"]): int(row["PATH_POS"]) for row in games}
        conditions: list[Condition] = []
        for item in body.conditions:
            if type(item.game_id) is not int or item.game_id <= 0:
                raise ConditionError("game id must be a positive integer")
            conditions.append(Condition(item.game_id, item.winner == "home"))
        sql = rooting_guide_sql(
            body.run_id, body.team_id, conditions, positions, api_schema=self.settings.api_schema
        )
        roster = self.fetch("whatif_base_odds", run_id=body.run_id)
        if body.team_id not in {int(row["TEAM_ID"]) for row in roster}:
            raise HTTPException(404, "the team is unavailable in this what-if run")
        rows = self._query_current(sql, body.run_id, "whatif_guide")
        current = self.fetch("whatif_run")
        if not current or int(current[0]["RUN_ID"]) != body.run_id:
            raise RunChanged
        threshold = _setting(self.fetch("analysis_settings"), "MIN_BRANCH_SAMPLE")
        if threshold <= 0:
            raise RuntimeError("MIN_BRANCH_SAMPLE is unavailable")
        shaped: list[Row] = []
        for row in rows:
            n_home, n_away = int(row["N_HOME"]), int(row["N_AWAY"])
            p_home = int(row["N_HOME_IN_CFP"]) / n_home if n_home else None
            p_away = int(row["N_AWAY_IN_CFP"]) / n_away if n_away else None
            root_id = (
                row["HOME_TEAM_ID"]
                if p_home is not None and p_away is not None and p_home > p_away
                else row["AWAY_TEAM_ID"]
                if p_home is not None and p_away is not None and p_away > p_home
                else None
            )
            root_school = (
                row["HOME_SCHOOL"]
                if root_id == row["HOME_TEAM_ID"]
                else row["AWAY_SCHOOL"]
                if root_id == row["AWAY_TEAM_ID"]
                else None
            )
            shaped.append(
                {
                    "game_id": row["GAME_ID"],
                    "season": row["SEASON"],
                    "week_seq": row["WEEK_SEQ"],
                    "is_next_week": bool(row["IS_NEXT_WEEK"]),
                    "guide_rank": None,
                    "next_week_rank": None,
                    "home_team_id": row["HOME_TEAM_ID"],
                    "home_school": row["HOME_SCHOOL"],
                    "away_team_id": row["AWAY_TEAM_ID"],
                    "away_school": row["AWAY_SCHOOL"],
                    "root_for_team_id": root_id,
                    "root_for_school": root_school,
                    "p_if_home_win": p_home,
                    "p_if_away_win": p_away,
                    "leverage": abs(p_home - p_away)
                    if p_home is not None and p_away is not None
                    else None,
                    "low_sample": n_home < threshold or n_away < threshold,
                }
            )
        shaped.sort(
            key=lambda row: (
                row["leverage"] is None,
                -row["leverage"] if row["leverage"] is not None else 0,
                row["game_id"],
            )
        )
        for rank, row in enumerate(shaped, 1):
            row["next_week_rank"] = rank
        return {
            "run_id": body.run_id,
            "team_id": body.team_id,
            "conditional": True,
            "top": [],
            "coming_week": shaped,
            "message": None if shaped else "No unset games remain in the coming week.",
        }

    def guide(self, season: int | None, week: int | None, team_id: int) -> Row:
        if team_id <= 0 or team_id not in self._assets():
            raise HTTPException(404, "the team is unavailable")
        point, _, _ = self.point(season, week)
        runs = self.fetch(
            "published_run",
            season=int(point["AS_OF_SEASON"]),
            week_seq=int(point["AS_OF_WEEK_SEQ"]),
        )
        if not runs:
            return {
                "run_id": None,
                "team_id": team_id,
                "conditional": False,
                "top": [],
                "coming_week": [],
                "message": "No simulation run is published at this point.",
            }
        run_id = int(runs[0]["RUN_ID"])
        top = self.fetch("rooting_guide_top", run_id=run_id, team_id=team_id)
        coming = self.fetch("rooting_guide_coming", run_id=run_id, team_id=team_id)

        def shape(row: Row) -> Row:
            return {
                "game_id": row["GAME_ID"],
                "season": row["SEASON"],
                "week_seq": row["WEEK_SEQ"],
                "is_next_week": bool(row["IS_NEXT_WEEK"]),
                "guide_rank": row["GUIDE_RANK"],
                "next_week_rank": row["NEXT_WEEK_RANK"],
                "home_team_id": row["HOME_TEAM_ID"],
                "home_school": row["HOME_SCHOOL"],
                "away_team_id": row["AWAY_TEAM_ID"],
                "away_school": row["AWAY_SCHOOL"],
                "root_for_team_id": row["ROOT_FOR_TEAM_ID"],
                "root_for_school": row["ROOT_FOR_SCHOOL"],
                "p_if_home_win": _number(row["P_IF_HOME_WIN"]),
                "p_if_away_win": _number(row["P_IF_AWAY_WIN"]),
                "leverage": _number(row["LEVERAGE"]),
                "low_sample": bool(row["LOW_SAMPLE"]),
            }

        return {
            "run_id": run_id,
            "team_id": team_id,
            "conditional": False,
            "top": [shape(row) for row in top],
            "coming_week": [shape(row) for row in coming],
            "message": (
                None if top or coming else "No rooting guide rows are available for this team."
            ),
        }


class RunChanged(Exception):
    """The selected run is no longer served by the current what-if views."""


def make_router(settings: Settings, store: SessionStore) -> APIRouter:
    router = APIRouter()
    cache = WhatIfGameCache()

    @router.get("/api/whatif/games", response_model=models.WhatIfGamesResponse)
    def games(request: Request) -> Row:
        return WhatIfService(request, settings, store, cache).games()

    @router.post("/api/whatif/odds", response_model=models.WhatIfOddsResponse)
    def odds(request: Request, body: models.WhatIfOddsRequest) -> Any:
        try:
            return WhatIfService(request, settings, store, cache).conditional_odds(body)
        except ConditionError as error:
            return _error(400, "invalid_argument", str(error))
        except RunChanged:
            return _error(409, "whatif_run_changed", "the current what-if run changed; reload odds")

    @router.post("/api/whatif/guide", response_model=models.GuideResponse)
    def conditional_guide(request: Request, body: models.WhatIfGuideRequest) -> Any:
        try:
            return WhatIfService(request, settings, store, cache).conditional_guide(body)
        except ConditionError as error:
            return _error(400, "invalid_argument", str(error))
        except RunChanged:
            return _error(409, "whatif_run_changed", "the current what-if run changed; reload odds")

    @router.get("/api/guide", response_model=models.GuideResponse)
    def guide(
        request: Request, team: int, season: int | None = None, week: int | None = None
    ) -> Row:
        return WhatIfService(request, settings, store, cache).guide(season, week, team)

    return router
