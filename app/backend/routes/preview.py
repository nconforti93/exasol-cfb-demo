"""Zero-call preview plan for the server-chosen game of an ordered pair."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.backend import live, models
from app.backend.routes.read import ReadService, Row, _number
from app.backend.sessions import FlightTimeout, SessionStore
from app.backend.settings import Settings
from vs.host import ledger


class PreviewPlanService(ReadService):
    def _choose(self, team_id: int, opponent_id: int) -> tuple[int | None, str]:
        if team_id <= 0 or opponent_id <= 0 or team_id == opponent_id:
            raise HTTPException(400, "choose two different teams")
        assets = self._assets()
        if team_id not in assets or opponent_id not in assets:
            raise HTTPException(404, "a team in this matchup is unavailable")
        pair = {"team_id": team_id, "opponent_id": opponent_id}
        upcoming = self.fetch("matchup_next", **pair)
        series = self.fetch("matchup_series", **pair)
        if upcoming:
            return int(upcoming[0]["GAME_ID"]), "next"
        if series:
            return int(series[0]["LAST_GAME_ID"]), "latest"
        return None, "none"

    def _executor(self, book: ledger.Ledger) -> live.LiveExecutor:
        operator = self.request.app.state.operator

        def busy() -> str | None:
            running = operator.running_runs(self.settings.namespace)
            return None if not running else f"{running[0][1]} run {running[0][0]}"

        return live.LiveExecutor(
            self.settings.namespace,
            self.request.app.state.operator,
            book,
            target=self.settings.target,
            ledger_plan=self.settings.ledger_plan,
            gate=getattr(self.request.app.state, "live_gate", None),
            busy=busy,
            reconcile_pending=lambda: not self.request.app.state.reconciled,
            on_record_failure=lambda: setattr(self.request.app.state, "reconciled", False),
        )

    def plan(self, team_id: int, opponent_id: int, book: ledger.Ledger) -> Row:
        game_id, basis = self._choose(team_id, opponent_id)
        state = self.request.app.state
        reason = None
        plan = {"source": "NONE", "live_calls": 0, "fetch_mode": None}
        if game_id is not None:
            if state.operator.enabled:
                measured = self._executor(book).plan_preview(game_id)
                plan = {
                    "source": measured.source,
                    "live_calls": measured.live_calls,
                    "fetch_mode": measured.fetch_mode,
                }
            if not state.operator.enabled:
                reason = state.operator.reason
            elif not state.reconciled:
                reason = "preview execution is unavailable until ledger reconciliation finishes"
        cached = self.session.previews.get(game_id) if game_id is not None else None
        cached_value = cast(Row, cached.value) if cached is not None else None
        return {
            "team_id": team_id,
            "opponent_id": opponent_id,
            "game_id": game_id,
            "game_basis": basis,
            "plan": plan,
            "cached": {
                "cached_at": datetime.fromtimestamp(cached.cached_at, UTC).isoformat(),
                "source": cached_value["source"],
                "req_ms": cached_value["req_ms"],
            }
            if cached is not None and cached_value is not None
            else None,
            "disabled_reason": reason,
            "busy_reason": None,
        }

    def execute(self, body: models.PreviewRequest, book: ledger.Ledger) -> Row:
        if type(body.team) is not int or type(body.opponent) is not int:
            raise ValueError("team ids must be integers")
        if type(body.max_calls) is not int or body.max_calls < 0:
            raise ValueError("max_calls must be a non-negative integer")
        game_id, _ = self._choose(body.team, body.opponent)
        if game_id is None:
            raise HTTPException(404, "the teams have no meeting to preview")
        if not self.request.app.state.reconciled:
            raise live.LiveRefused("reconcile_pending", "ledger reconciliation has not finished")
        executor = self._executor(book)
        max_calls = int(body.max_calls)

        def fetch() -> Row:
            result = executor.run_preview(
                game_id, max_calls=max_calls, action_id=secrets.token_hex(12)
            )
            return _shape_result(game_id, result)

        flight = self.session.preview_flight(game_id, fetch, force=body.force)
        response = dict(flight.value)
        if flight.cached:
            response["cached"] = True
            response["calls_spent"] = 0
        return response


def _shape_result(game_id: int, result: live.LiveResult) -> Row:
    if not result.rows:
        raise live.LiveFailed("the preview returned no game", result.calls_charged)
    row = dict(zip(result.columns, result.rows[0], strict=True))
    if int(row["GAME_ID"]) != game_id:
        raise live.LiveFailed("the preview returned a different game", result.calls_charged)

    def team(side: str) -> Row:
        prefix = f"ANALYSIS_{side}_RATINGS_DATA_"
        record = f"ANALYSIS_{side}_RECORD_DATA_"
        wins, losses, ties = (row.get(record + name) for name in ("WINS", "LOSSES", "TIES"))
        record_label = None if wins is None or losses is None else f"{wins}-{losses}"
        if record_label is not None and ties:
            record_label += f"-{ties}"
        return {
            "team_id": row.get(f"GAME_{side}_TEAM_ID"),
            "school": row.get(f"GAME_{side}_TEAM_NAME") or "Unknown",
            "record": record_label,
            "sp_rating": _number(row.get(prefix + "SP_OVERALL_RATING")),
            "srs_rating": _number(row.get(prefix + "SRS_RATING")),
            "elo": _number(row.get(prefix + "ELO")),
            "fpi": _number(row.get(prefix + "FPI_OVERALL_RATING")),
            "points": _number(row.get(f"GAME_{side}_TEAM_POINTS")),
        }

    odds_prefix = "ANALYSIS_ODDS_DATA_"
    odds_fields = {
        "provider": row.get(odds_prefix + "PROVIDER"),
        "spread": _number(row.get(odds_prefix + "SPREAD")),
        "over_under": _number(row.get(odds_prefix + "OVER_UNDER")),
        "home_moneyline": _number(row.get(odds_prefix + "HOME_MONEYLINE")),
        "away_moneyline": _number(row.get(odds_prefix + "AWAY_MONEYLINE")),
    }
    source = row.get("REQ_SOURCE") or result.plan.source
    return {
        "game_id": game_id,
        "cached": False,
        "fetched_at": datetime.now(UTC).isoformat(),
        "source": source,
        "calls_spent": result.calls_charged,
        "req_source": source,
        "req_ms": _number(row.get("REQ_MS")),
        "venue": row.get("GAME_VENUE_NAME"),
        "status": row.get("GAME_STATUS"),
        "home": team("HOME"),
        "away": team("AWAY"),
        "odds": odds_fields if any(value is not None for value in odds_fields.values()) else None,
        "fetch_log_ok": result.fetch_log_ok,
    }


def make_router(settings: Settings, store: SessionStore, book: ledger.Ledger) -> APIRouter:
    router = APIRouter()

    @router.get("/api/preview/plan", response_model=models.PreviewPlanResponse)
    def plan(request: Request, team: int, opponent: int) -> Row:
        return PreviewPlanService(request, settings, store).plan(team, opponent, book)

    @router.post("/api/preview", response_model=models.PreviewResponse)
    def execute(request: Request, body: models.PreviewRequest) -> Any:
        try:
            return PreviewPlanService(request, settings, store).execute(body, book)
        except ValueError as error:
            return JSONResponse(
                {"error": "invalid_argument", "message": str(error)}, status_code=400
            )
        except live.LiveRefused as error:
            return JSONResponse(
                {"error": error.code, "message": str(error)}, status_code=error.status
            )
        except live.LiveFailed as error:
            return JSONResponse(
                {
                    "error": "cfbd_failed",
                    "message": str(error),
                    "calls_charged": error.calls_charged,
                },
                status_code=502,
            )
        except FlightTimeout:
            return JSONResponse(
                {"error": "live_busy", "message": "the preview is still running"}, status_code=409
            )

    return router
