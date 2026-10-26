"""Stored-data routes for the map, team page and league tabs."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request

from app.backend import models, sqlcatalog
from app.backend.routes.logos import has_logo
from app.backend.sessions import Session, SessionStore
from app.backend.settings import Settings

POWER_CONFERENCES = ("SEC", "Big Ten", "Big 12", "ACC")
INDEPENDENTS = "FBS Independents"
COLOR = re.compile(r"#?[0-9a-fA-F]{6}\Z")
MAX_TRAVEL = 5
MAX_MOVERS = 5
MAX_STREAKS = 10

Row = dict[str, Any]


def _number(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, (str, Decimal)):
        value = float(value)
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value).replace(" ", "T")[:19] + "Z"


def _color(value: Any) -> str | None:
    if not isinstance(value, str) or not COLOR.fullmatch(value):
        return None
    return value if value.startswith("#") else f"#{value}"


def _conference_order(names: set[str]) -> list[str]:
    power = [name for name in POWER_CONFERENCES if name in names]
    rest = sorted(names - set(power) - {INDEPENDENTS})
    return power + rest + ([INDEPENDENTS] if INDEPENDENTS in names else [])


def _interest(game: Row) -> tuple[int, float, str]:
    ranks = game["home_rank"], game["away_rank"]
    if all(rank is not None for rank in ranks):
        return 0, float(ranks[0] + ranks[1]), game["start_at"] or ""
    spread = game["spread"]
    if spread is not None and abs(spread) <= 7:
        return 1, abs(float(spread)), game["start_at"] or ""
    return 2, 0, game["start_at"] or ""


class ReadService:
    """One request's fixed statements and response shaping."""

    def __init__(self, request: Request, settings: Settings, store: SessionStore) -> None:
        self.pool = request.app.state.database.pool
        self.settings = settings
        self.session: Session = store.session(request.state.session_id)
        self.ids: dict[str, str] = {}
        self._context = f"read-{id(self)}"

    def fetch(self, name: str, **values: Any) -> list[Row]:
        statement = sqlcatalog.CATALOG[name]
        params = sqlcatalog.parameters(
            statement,
            api_schema=self.settings.api_schema,
            api_live_schema=self.settings.api_live_schema,
            **values,
        )
        result = self.pool.run(
            statement.sql,
            params,
            log=self.session.log,
            page=name,
            context=f"{self._context}:{name}",
        )
        for entry in self.session.log.entries():
            if entry.context == f"{self._context}:{name}":
                self.ids[name] = str(entry.id)
                break
        return cast(list[Row], result.dicts())

    def point(self, season: int | None, week: int | None) -> tuple[Row, Row, Row | None]:
        current_rows = self.fetch("current_point")
        if not current_rows:
            raise HTTPException(503, "no current point is available")
        current = current_rows[0]
        if season is None and week is None:
            return current, current, None
        if season is None or week is None or season < 1869 or week < 0:
            raise HTTPException(400, "season and week must be supplied together")
        points = self.fetch("as_of_points", season=season)
        selected = next(
            (row for row in points if int(row["AS_OF_WEEK_SEQ"]) == week),
            None,
        )
        if selected is None:
            notice = {
                "code": "point_fallback",
                "message": "That point is unavailable; showing the current point.",
            }
            return current, current, notice
        return selected, current, None

    @staticmethod
    def resolved(point: Row, current: Row) -> Row:
        return {
            "season": int(point["AS_OF_SEASON"]),
            "week_seq": int(point["AS_OF_WEEK_SEQ"]),
            "next_week_seq": _number(point["NEXT_WEEK_SEQ"]),
            "label": point["AS_OF_LABEL"],
            "is_current": (
                int(point["AS_OF_SEASON"]) == int(current["AS_OF_SEASON"])
                and int(point["AS_OF_WEEK_SEQ"]) == int(current["AS_OF_WEEK_SEQ"])
            ),
        }

    def points(self, season: int | None) -> Row:
        current, _, _ = self.point(None, None)
        seasons = [int(row["AS_OF_SEASON"]) for row in self.fetch("as_of_seasons")]
        selected_season = current["AS_OF_SEASON"] if season is None else season
        notice = None
        if selected_season not in seasons:
            selected_season = current["AS_OF_SEASON"]
            notice = {
                "code": "point_fallback",
                "message": "That season is unavailable; showing the current season.",
            }
        rows = self.fetch("as_of_points", season=int(selected_season))
        return {
            "current": {
                "as_of_season": current["AS_OF_SEASON"],
                "as_of_week": current["AS_OF_WEEK"],
                "as_of_season_type": current["AS_OF_SEASON_TYPE"],
                "as_of_week_seq": current["AS_OF_WEEK_SEQ"],
                "next_week_seq": current["NEXT_WEEK_SEQ"],
                "as_of_label": current["AS_OF_LABEL"],
                "publication_id": current.get("PUBLICATION_ID"),
                "whatif_publication_id": current.get("WHATIF_PUBLICATION_ID"),
                "whatif_run_id": current.get("WHATIF_RUN_ID"),
                "whatif_as_of_week_seq": current.get("WHATIF_AS_OF_WEEK_SEQ"),
                "whatif_views_current": bool(current.get("WHATIF_VIEWS_CURRENT")),
            },
            "season": selected_season,
            "seasons": seasons,
            "points": [
                {
                    "as_of_season": row["AS_OF_SEASON"],
                    "as_of_week": row["AS_OF_WEEK"],
                    "as_of_season_type": row["AS_OF_SEASON_TYPE"],
                    "as_of_week_seq": row["AS_OF_WEEK_SEQ"],
                    "next_week_seq": row["NEXT_WEEK_SEQ"],
                    "as_of_label": row["AS_OF_LABEL"],
                    "is_current": bool(row["IS_CURRENT"]),
                    "is_after_current": bool(row["IS_AFTER_CURRENT"]),
                    "has_publication": bool(row["HAS_PUBLICATION"]),
                    "has_predictions": bool(row["HAS_PREDICTIONS"]),
                    "has_grades": bool(row["HAS_GRADES"]),
                }
                for row in rows
            ],
            "notice": notice,
        }

    def _assets(self) -> dict[int, Row]:
        return {int(row["TEAM_ID"]): row for row in self.fetch("teams")}

    def _has_logo(self, team_id: int) -> bool:
        return has_logo(self.settings.logo_dir, team_id, enabled=self.settings.logos_enabled)

    def _team(self, team_id: int | None, assets: dict[int, Row], fallback: str = "Unknown") -> Row:
        row = assets.get(team_id or -1, {})
        ident = team_id or 0
        return {
            "team_id": ident,
            "school": row.get("SCHOOL") or fallback,
            "conference": row.get("CURRENT_CONFERENCE"),
            "abbreviation": row.get("ABBREVIATION"),
            "color": _color(row.get("COLOR")),
            "alternate_color": _color(row.get("ALTERNATE_COLOR")),
            "has_logo": self._has_logo(ident),
        }

    @staticmethod
    def _ranks(rows: list[Row]) -> dict[tuple[int, str], list[tuple[int, int]]]:
        found: dict[tuple[int, str], list[tuple[int, int]]] = defaultdict(list)
        for row in rows:
            if row["TEAM_ID"] is not None and row["POLL_CODE"] in {"AP", "CFP"}:
                found[int(row["TEAM_ID"]), row["POLL_CODE"]].append(
                    (int(row["VISIBLE_AT_WEEK_SEQ"]), int(row["POLL_RANK"]))
                )
        return found

    @staticmethod
    def _rank(
        ranks: dict[tuple[int, str], list[tuple[int, int]]],
        team_id: int | None,
        poll: str,
        week: int,
    ) -> int | None:
        if team_id is None:
            return None
        choices = [pair for pair in ranks.get((team_id, poll), []) if pair[0] <= week]
        return max(choices, key=lambda pair: (pair[0], -pair[1]))[1] if choices else None

    def _map_rows(
        self,
        season: int,
        week_seq: int,
    ) -> tuple[list[Row], dict[int, Row], dict[tuple[int, str], list[tuple[int, int]]]]:
        assets = self._assets()
        season_rows = self.fetch("fbs_team_seasons", season=season)
        results = self.fetch("team_results", season=season, week_seq=week_seq)
        ranks = self._ranks(self.fetch("point_rankings", season=season, week_seq=week_seq))
        records: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
        for row in results:
            ident = int(row["TEAM_ID"])
            win, loss = row["GAME_RESULT"] == "W", row["GAME_RESULT"] == "L"
            records[ident][0] += int(win)
            records[ident][1] += int(loss)
            if row["IS_CONFERENCE_GAME"] and not row["IS_CONF_CHAMPIONSHIP"]:
                records[ident][2] += int(win)
                records[ident][3] += int(loss)
        mapped = []
        for season_row in season_rows:
            ident = int(season_row["TEAM_ID"])
            team = assets.get(ident, {})
            wins, losses, conf_wins, conf_losses = records[ident]
            mapped.append(
                {
                    "team_id": ident,
                    "school": season_row["SCHOOL"],
                    "mascot": team.get("MASCOT"),
                    "abbreviation": team.get("ABBREVIATION"),
                    "conference": season_row["CONFERENCE_NAME"],
                    "color": _color(team.get("COLOR")),
                    "alternate_color": _color(team.get("ALTERNATE_COLOR")),
                    "has_logo": self._has_logo(ident),
                    "latitude": _number(team.get("LATITUDE")) if team.get("HAS_LOCATION") else None,
                    "longitude": _number(team.get("LONGITUDE"))
                    if team.get("HAS_LOCATION")
                    else None,
                    "wins": wins,
                    "losses": losses,
                    "conf_wins": conf_wins,
                    "conf_losses": conf_losses,
                    "ap_rank": self._rank(ranks, ident, "AP", week_seq),
                    "cfp_rank": self._rank(ranks, ident, "CFP", week_seq),
                }
            )
        mapped.sort(key=lambda row: row["school"])
        return mapped, assets, ranks

    def teams(self) -> Row:
        rows = self.fetch("teams")
        return {
            "teams": [
                self._team(int(row["TEAM_ID"]), {int(row["TEAM_ID"]): row})
                for row in rows
                if row["IS_FBS"]
            ]
        }

    def teams_map(self, season: int | None, week: int | None) -> Row:
        point, current, notice = self.point(season, week)
        rows, _, _ = self._map_rows(int(point["AS_OF_SEASON"]), int(point["AS_OF_WEEK_SEQ"]))
        conferences = {row["conference"] for row in rows if row["conference"]}
        return {
            "point": self.resolved(point, current),
            "notice": notice,
            "conferences": _conference_order(conferences),
            "teams": [
                {key: value for key, value in row.items() if key != "cfp_rank"} for row in rows
            ],
            "queries": {"teams": self.ids["fbs_team_seasons"]},
        }

    def team_page(self, team_id: int, season: int | None, week: int | None) -> Row:
        point, current, notice = self.point(season, week)
        point_season, point_week = int(point["AS_OF_SEASON"]), int(point["AS_OF_WEEK_SEQ"])
        rows, assets, ranks = self._map_rows(point_season, point_week)
        hero = next((row for row in rows if row["team_id"] == team_id), None)
        if hero is None:
            raise HTTPException(404, f"no FBS team {team_id} in season {point_season}")
        schedule = self.fetch("team_schedule", season=point_season, team_id=team_id)
        ratings = self.fetch(
            "team_sp", team_id=team_id, prior_season=point_season - 1, season=point_season
        )
        games = []
        for row in schedule:
            opponent_id = int(row["OPPONENT_ID"]) if row["OPPONENT_ID"] is not None else None
            game_week = int(row["WEEK_SEQ"])
            final = bool(row["HAS_RESULT"]) and game_week <= point_week
            probability = _number(row["TEAM_PREGAME_WIN_PROB"]) if not final else None
            games.append(
                {
                    "game_id": row["GAME_ID"],
                    "week": row["WEEK"],
                    "season_type": row["SEASON_TYPE"],
                    "start_at": _iso(row["START_AT"]),
                    "status": "FINAL" if final else "UPCOMING",
                    "site": row["SITE"],
                    "opponent": self._team(opponent_id, assets, row["OPPONENT_NAME"] or "Unknown"),
                    "opponent_rank": self._rank(
                        ranks, opponent_id, "AP", min(game_week - 1, point_week)
                    ),
                    "team_points": _number(row["POINTS_FOR"]) if final else None,
                    "opponent_points": _number(row["POINTS_AGAINST"]) if final else None,
                    "result": row["GAME_RESULT"] if final else None,
                    "spread": _number(row["TEAM_SPREAD"]) if not final else None,
                    "win_prob": probability,
                    "win_prob_source": "CFBD" if probability is not None else None,
                }
            )
        sp = [
            {
                "season": int(row["SEASON"]),
                "overall": _number(row["SP_RATING"]),
                "overall_rank": row["SP_RANKING"],
                "offense": _number(row["OFFENSE_RATING"]),
                "offense_rank": row["OFFENSE_RANKING"],
                "defense": _number(row["DEFENSE_RATING"]),
                "defense_rank": row["DEFENSE_RANKING"],
            }
            for row in ratings
            if all(
                row[key] is not None
                for key in (
                    "SP_RATING",
                    "SP_RANKING",
                    "OFFENSE_RATING",
                    "OFFENSE_RANKING",
                    "DEFENSE_RATING",
                    "DEFENSE_RANKING",
                )
            )
        ]
        weeks = sorted(
            {
                visible
                for (ident, _), entries in ranks.items()
                if ident == team_id
                for visible, _ in entries
            }
        )
        polls = [
            {
                "week_seq": visible,
                "label": "preseason" if visible == 0 else f"after week {visible}",
                "ap_rank": self._rank(ranks, team_id, "AP", visible)
                if any(w == visible for w, _ in ranks.get((team_id, "AP"), []))
                else None,
                "cfp_rank": self._rank(ranks, team_id, "CFP", visible)
                if any(w == visible for w, _ in ranks.get((team_id, "CFP"), []))
                else None,
            }
            for visible in weeks
        ]
        return {
            "point": self.resolved(point, current),
            "notice": notice,
            "team": {
                key: value for key, value in hero.items() if key not in {"latitude", "longitude"}
            },
            "games": games,
            "sp": sp,
            "polls": polls,
            "queries": {
                "hero": self.ids["fbs_team_seasons"],
                "schedule": self.ids["team_schedule"],
                "ratings": self.ids["team_sp"],
                "polls": self.ids["point_rankings"],
            },
        }

    def league_week(self, season: int | None, week: int | None) -> Row:
        point, current, notice = self.point(season, week)
        next_week = point["NEXT_WEEK_SEQ"]
        if next_week is None:
            return {
                "point": self.resolved(point, current),
                "notice": notice,
                "week_seq": None,
                "run_id": None,
                "games": [],
                "queries": {},
            }
        rows = self.fetch(
            "slate_games", season=point["AS_OF_SEASON"], week_seq=point["AS_OF_WEEK_SEQ"]
        )
        assets = self._assets()
        games = []
        for row in rows:
            if not row["IS_FBS_GAME"]:
                continue
            home_id = int(row["HOME_TEAM_ID"])
            away_id = int(row["AWAY_TEAM_ID"])
            home_rank = _number(row["HOME_AP_RANK"])
            away_rank = _number(row["AWAY_AP_RANK"])
            probability = _number(row["HOME_PREGAME_WIN_PROB"])
            alert = None
            if probability is not None:
                candidates = []
                if home_rank is not None and probability < 0.65:
                    candidates.append((probability, home_id))
                if away_rank is not None and 1 - probability < 0.65:
                    candidates.append((1 - probability, away_id))
                alert = min(candidates)[1] if candidates else None
            games.append(
                {
                    "game_id": row["GAME_ID"],
                    "week_seq": row["WEEK_SEQ"],
                    "start_at": _iso(row["START_AT"]),
                    "venue": row["VENUE_NAME"],
                    "home": self._team(home_id, assets, row["HOME_TEAM"]),
                    "away": self._team(away_id, assets, row["AWAY_TEAM"]),
                    "home_rank": home_rank,
                    "away_rank": away_rank,
                    "spread": _number(row["PREGAME_SPREAD"]),
                    "home_win_prob": probability,
                    "win_prob_source": "CFBD" if probability is not None else None,
                    "stakes": None,
                    "game_of_week": False,
                    "upset_alert_team_id": alert,
                }
            )
        games.sort(key=_interest)
        if games:
            games[0]["game_of_week"] = True
        return {
            "point": self.resolved(point, current),
            "notice": notice,
            "week_seq": next_week,
            "run_id": None,
            "games": games,
            "queries": {"games": self.ids["slate_games"]},
        }

    def league_stats(self, season: int | None, week: int | None) -> Row:
        point, current, notice = self.point(season, week)
        season_id, week_seq = int(point["AS_OF_SEASON"]), int(point["AS_OF_WEEK_SEQ"])
        args = {"season": season_id, "week_seq": week_seq}
        travel = self.fetch("road_warriors", **args)
        movers = self.fetch("sp_movers", season=season_id)
        conferences = self.fetch("conference_strength", **args)
        streaks = self.fetch("win_streaks", **args)
        assets = self._assets()

        def team(row: Row) -> Row:
            return self._team(int(row["TEAM_ID"]), assets, row["SCHOOL"])

        def mover(row: Row) -> Row:
            return {
                "team": team(row),
                "sp_now": _number(row["SP_RATING"]),
                "sp_before": _number(row["SP_PRIOR_RATING"]),
                "delta": _number(row["SP_DELTA"]),
            }

        valid_movers = [
            row
            for row in movers
            if row["SP_RATING"] is not None and row["SP_PRIOR_RATING"] is not None
        ]
        return {
            "point": self.resolved(point, current),
            "notice": notice,
            "road_warriors": [
                {
                    "team": team(row),
                    "km": _number(row["KM_TRAVELED"]),
                    "trips": row["TRIPS"],
                    "longest_trip_km": _number(row["LONGEST_TRIP_KM"]),
                    "longest_trip_to": row["LONGEST_TRIP_TO"] or "",
                }
                for row in travel[:MAX_TRAVEL]
            ],
            "risers": [mover(row) for row in valid_movers if int(row["RISE_RANK"]) <= MAX_MOVERS],
            "fallers": [
                mover(row)
                for row in sorted(valid_movers, key=lambda row: row["FALL_RANK"])
                if int(row["FALL_RANK"]) <= MAX_MOVERS
            ],
            "conference_strength": [
                {
                    "conference": row["CONFERENCE_NAME"],
                    "teams": row["TEAM_COUNT"],
                    "mean_sp": _number(row["MEAN_SP_RATING"]),
                    "nonconf_wins": row["NONCONF_WINS"] or 0,
                    "nonconf_losses": row["NONCONF_LOSSES"] or 0,
                    "ranked_teams": row["AP_RANKED_TEAMS"] or 0,
                }
                for row in conferences
                if row["MEAN_SP_RATING"] is not None
            ],
            "streaks": [
                {
                    "team": team(row),
                    "length": row["STREAK_LENGTH"],
                    "since_season": row["SINCE_SEASON"],
                    "since_week": row["SINCE_WEEK_SEQ"],
                }
                for row in streaks
                if row["STREAK_KIND"] == "W"
                and row["WIN_STREAK_RANK"] is not None
                and int(row["WIN_STREAK_RANK"]) <= MAX_STREAKS
            ],
            "queries": {
                "road_warriors": self.ids["road_warriors"],
                "movers": self.ids["sp_movers"],
                "conference_strength": self.ids["conference_strength"],
                "streaks": self.ids["win_streaks"],
            },
        }


def make_router(settings: Settings, store: SessionStore) -> APIRouter:
    """Build the zero-call read router with the app's shared per-browser store."""
    router = APIRouter()

    def service(request: Request) -> ReadService:
        return ReadService(request, settings, store)

    @router.get("/api/points", response_model=models.PointsResponse)
    def points(request: Request, season: int | None = None) -> Row:
        return service(request).points(season)

    @router.get("/api/teams", response_model=models.TeamsResponse)
    def teams(request: Request) -> Row:
        return service(request).teams()

    @router.get("/api/teams/map", response_model=models.TeamsMapResponse)
    def teams_map(request: Request, season: int | None = None, week: int | None = None) -> Row:
        return service(request).teams_map(season, week)

    @router.get("/api/team/{team_id}", response_model=models.TeamResponse)
    def team(
        request: Request, team_id: int, season: int | None = None, week: int | None = None
    ) -> Row:
        return service(request).team_page(team_id, season, week)

    @router.get("/api/league/week", response_model=models.LeagueWeekResponse)
    def league_week(request: Request, season: int | None = None, week: int | None = None) -> Row:
        return service(request).league_week(season, week)

    @router.get("/api/league/stats", response_model=models.LeagueStatsResponse)
    def league_stats(request: Request, season: int | None = None, week: int | None = None) -> Row:
        return service(request).league_stats(season, week)

    return router
