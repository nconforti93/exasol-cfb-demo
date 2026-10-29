"""Completed series and next scheduled meeting for an ordered team pair."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.backend import models
from app.backend.routes.read import ReadService, Row, _iso, _number
from app.backend.sessions import SessionStore
from app.backend.settings import Settings


class MatchupService(ReadService):
    def matchup(self, team_id: int, opponent_id: int) -> Row:
        if team_id <= 0 or opponent_id <= 0 or team_id == opponent_id:
            raise HTTPException(400, "choose two different teams")
        assets = self._assets()
        if team_id not in assets or opponent_id not in assets:
            raise HTTPException(404, "a team in this matchup is unavailable")
        args = {"team_id": team_id, "opponent_id": opponent_id}
        series_rows = self.fetch("matchup_series", **args)
        games = self.fetch("matchup_games", **args)
        next_rows = self.fetch("matchup_next", **args)
        current, _, _ = self.point(None, None)
        series = series_rows[0] if series_rows else None
        next_game = next_rows[0] if next_rows else None
        return {
            "team": self._team(team_id, assets),
            "opponent": self._team(opponent_id, assets),
            "never_met": series is None,
            "series": {
                "team_id": series["TEAM_ID"],
                "opponent_team_id": series["OPPONENT_TEAM_ID"],
                "school": series["SCHOOL"],
                "opponent_school": series["OPPONENT_SCHOOL"],
                "meetings": series["MEETINGS"],
                "wins": series["WINS"],
                "losses": series["LOSSES"],
                "ties": series["TIES"],
                "points_for": series["POINTS_FOR"],
                "points_against": series["POINTS_AGAINST"],
                "first_season": series["FIRST_SEASON"],
                "last_season": series["LAST_SEASON"],
                "last_game_id": series["LAST_GAME_ID"],
                "streak_kind": series["STREAK_KIND"],
                "streak_length": series["STREAK_LENGTH"],
                "wins_home": series["WINS_HOME"],
                "wins_away": series["WINS_AWAY"],
                "wins_neutral": series["WINS_NEUTRAL"],
                "biggest_win_margin": series["BIGGEST_WIN_MARGIN"],
                "biggest_win_season": series["BIGGEST_WIN_SEASON"],
                "biggest_loss_margin": series["BIGGEST_LOSS_MARGIN"],
                "biggest_loss_season": series["BIGGEST_LOSS_SEASON"],
            }
            if series is not None
            else None,
            "games": [
                {
                    "game_id": row["GAME_ID"],
                    "season": row["SEASON"],
                    "season_type": row["SEASON_TYPE"],
                    "week": row["WEEK"],
                    "start_at": _iso(row["START_AT"]),
                    "site": row["SITE"],
                    "team_points": row["TEAM_POINTS"],
                    "opponent_points": row["OPPONENT_POINTS"],
                    "game_result": row["GAME_RESULT"],
                    "prob_source": row["PROB_SOURCE"],
                    "team_pregame_win_prob": _number(row["TEAM_PREGAME_WIN_PROB"]),
                    "upset_magnitude": _number(row["UPSET_MAGNITUDE"]),
                }
                for row in games
            ],
            "next": {
                "game_id": next_game["GAME_ID"],
                "season": next_game["SEASON"],
                "week_seq": next_game["WEEK_SEQ"],
                "start_at": _iso(next_game["START_AT"]),
                "site": next_game["SITE"],
                "prediction_kind": next_game["PREDICTION_KIND"],
                "team_win_prob": _number(next_game["TEAM_WIN_PROB"]),
                "pred_team_points": _number(next_game["PRED_TEAM_POINTS"]),
                "pred_opponent_points": _number(next_game["PRED_OPPONENT_POINTS"]),
                "stakes_run_id": next_game["STAKES_RUN_ID"],
                "team_p_in_cfp_if_win": _number(next_game["TEAM_P_IN_CFP_IF_WIN"]),
                "team_p_in_cfp_if_lose": _number(next_game["TEAM_P_IN_CFP_IF_LOSE"]),
                "opponent_p_in_cfp_if_win": _number(next_game["OPPONENT_P_IN_CFP_IF_WIN"]),
                "opponent_p_in_cfp_if_lose": _number(next_game["OPPONENT_P_IN_CFP_IF_LOSE"]),
            }
            if next_game is not None
            else None,
            "next_as_of_label": current["AS_OF_LABEL"],
        }


def make_router(settings: Settings, store: SessionStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/matchup", response_model=models.MatchupResponse)
    def matchup(request: Request, team: int, opponent: int) -> Row:
        return MatchupService(request, settings, store).matchup(team, opponent)

    return router
