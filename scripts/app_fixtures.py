# ruff: noqa: E501
"""`make app-fixtures`: a real-data snapshot of the thin-slice responses for `make app-preview REAL=1`.

Reads the stored model (`CFB_MODEL`) as the owner (`vs/host/target.py`), writes response-shaped JSON
validated by `app/backend/models.py` to `~/.cfb-demo/fixtures/<target>-<season>/` (outside the
repository: CFBD terms keep raw data out of git) and points `~/.cfb-demo/fixtures/latest` at it.
No statement names `CFBD_VS`, so a run costs 0 CFBD calls. Odds, predictions and upsets stay with the
synthetic fixtures; the team outlook is the empty variant (no simulation run exists).

The statements are shared by every page, so the inspector entries hold the exact text that ran, one
entry per statement; each response's `queries` map names the entry behind each panel.

`--target local|aws` (or `TARGET`) selects the deployment, `--season` the season (default: the
newest season with a finished week), `--out` the root directory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.backend import models
from app.backend.routes.logos import has_logo as logo_cached
from scripts.logos import resolve_cache_dir
from vs.host import target

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path.home() / ".cfb-demo" / "fixtures"
LATEST_LINK = "latest"
MODEL = "CFB_MODEL"
SNAPSHOT_USER = "SYS (snapshot)"
SQL_PREFIX = "-- make app-fixtures: owner read of the stored model, 0 CFBD calls\n"
POWER_CONFERENCES = ("SEC", "Big Ten", "Big 12", "ACC")
INDEPENDENTS = "FBS Independents"
CLOSE_LINE_POINTS = 7
UPSET_ALERT_BELOW = 0.65
ROAD_WARRIORS = 5
MOVERS = 5
STREAKS = 10
SP_SEASONS = 2
SEASON_KEY = 1000
POSTSEASON_OFFSET = 100
COLOR = re.compile(r"#?[0-9a-fA-F]{6}")

EXIT_OK = 0
EXIT_ERROR = 2

Row = dict[str, Any]


class ExportError(RuntimeError):
    """The export cannot run; the message never contains a secret."""


@dataclass(frozen=True)
class Statement:
    """One read: the page it serves and its text."""

    page: str
    sql: str


@dataclass(frozen=True)
class Executed:
    """A statement that ran: its text, when, how long, and the rows (column names in upper case)."""

    name: str
    page: str
    sql: str
    started_at: str
    ms: int
    rows: list[Row]


@dataclass(frozen=True)
class Snapshot:
    """Relative path to validated JSON, plus counts for the manifest."""

    files: dict[str, Any]
    counts: dict[str, int]
    exported_at: str
    target_name: str
    season: int


@dataclass(frozen=True)
class Point:
    season: int
    week_seq: int
    week: int
    season_type: str
    next_week_seq: int | None

    @property
    def label(self) -> str:
        kind = "" if self.season_type == "regular" else "postseason "
        return f"{self.season}, after {kind}week {self.week}"


def weeks_sql(season: int) -> str:
    """The season's weeks and whether each is over."""
    return (
        f"{SQL_PREFIX}SELECT SEASON_TYPE, WEEK, WEEK_SEQ, IS_DONE FROM {MODEL}.SEASON_WEEK "
        f"WHERE SEASON = {int(season)} ORDER BY WEEK_SEQ"
    )


def latest_season_sql() -> str:
    """The newest season with a finished week."""
    return f"SELECT MAX(SEASON) AS SEASON FROM {MODEL}.SEASON_WEEK WHERE IS_DONE"


def statements(season: int, point_seq: int, next_week_seq: int | None) -> dict[str, Statement]:
    """Every read of the snapshot after the point is known, by name."""
    s, p = int(season), int(point_seq)
    found: dict[str, Statement] = {
        "map": Statement("home", _map_sql(s, p)),
        "games": Statement("team", _games_sql(s)),
        "ranks": Statement("team", _ranks_sql(s)),
        "sp": Statement("team", _sp_sql(s)),
    }
    if next_week_seq is not None:
        found["week_games"] = Statement("week", _week_games_sql(s, int(next_week_seq)))
    found["road_warriors"] = Statement("stats", _road_warriors_sql(s, p))
    found["movers"] = Statement("stats", _movers_sql(s))
    found["conference_strength"] = Statement("stats", _conference_sql(s, p))
    found["streaks"] = Statement("stats", _streaks_sql(s, p))
    return found


def _latest_poll(season: int, point: int) -> str:
    return (
        "SELECT r.TEAM_ID, r.POLL_CODE, r.POLL_RANK "
        f"FROM {MODEL}.RANKING r "
        "JOIN (SELECT POLL_CODE, MAX(VISIBLE_AT_WEEK_SEQ) AS VISIBLE "
        f"FROM {MODEL}.RANKING WHERE SEASON = {season} AND POLL_CODE IN ('AP', 'CFP') "
        f"AND VISIBLE_AT_WEEK_SEQ <= {point} GROUP BY POLL_CODE) m "
        "ON m.POLL_CODE = r.POLL_CODE AND m.VISIBLE = r.VISIBLE_AT_WEEK_SEQ "
        f"WHERE r.SEASON = {season} AND r.POLL_CODE IN ('AP', 'CFP')"
    )


def _map_sql(season: int, point: int) -> str:
    return (
        f"{SQL_PREFIX}WITH RECORD AS (\n"
        "  SELECT TEAM_ID,\n"
        "         SUM(CASE WHEN GAME_RESULT = 'W' THEN 1 ELSE 0 END) AS WINS,\n"
        "         SUM(CASE WHEN GAME_RESULT = 'L' THEN 1 ELSE 0 END) AS LOSSES,\n"
        "         SUM(CASE WHEN GAME_RESULT = 'W' AND IS_CONFERENCE_GAME THEN 1 ELSE 0 END) AS CONF_WINS,\n"
        "         SUM(CASE WHEN GAME_RESULT = 'L' AND IS_CONFERENCE_GAME THEN 1 ELSE 0 END) AS CONF_LOSSES\n"
        f"    FROM {MODEL}.TEAM_GAME\n"
        f"   WHERE SEASON = {season} AND WEEK_SEQ <= {point} AND HAS_RESULT\n"
        "   GROUP BY TEAM_ID),\n"
        f"POLLS AS ({_latest_poll(season, point)})\n"
        "SELECT t.TEAM_ID, t.SCHOOL, t.MASCOT, t.ABBREVIATION, t.CURRENT_CONFERENCE AS CONFERENCE,\n"
        "       t.COLOR, t.ALTERNATE_COLOR, t.LATITUDE, t.LONGITUDE,\n"
        "       COALESCE(rec.WINS, 0) AS WINS, COALESCE(rec.LOSSES, 0) AS LOSSES,\n"
        "       COALESCE(rec.CONF_WINS, 0) AS CONF_WINS, COALESCE(rec.CONF_LOSSES, 0) AS CONF_LOSSES,\n"
        "       ap.POLL_RANK AS AP_RANK, cfp.POLL_RANK AS CFP_RANK\n"
        f"  FROM {MODEL}.TEAM t\n"
        "  LEFT JOIN RECORD rec ON rec.TEAM_ID = t.TEAM_ID\n"
        "  LEFT JOIN POLLS ap ON ap.TEAM_ID = t.TEAM_ID AND ap.POLL_CODE = 'AP'\n"
        "  LEFT JOIN POLLS cfp ON cfp.TEAM_ID = t.TEAM_ID AND cfp.POLL_CODE = 'CFP'\n"
        " WHERE t.CURRENT_CLASSIFICATION = 'fbs'\n"
        " ORDER BY t.SCHOOL"
    )


def _games_sql(season: int) -> str:
    return (
        f"{SQL_PREFIX}SELECT tg.GAME_ID, tg.WEEK, tg.SEASON_TYPE, tg.WEEK_SEQ, tg.START_AT, tg.TEAM_ID,\n"
        "       tg.OPPONENT_ID, tg.OPPONENT_NAME, tg.OPPONENT_CONFERENCE, tg.SITE, tg.HAS_RESULT,\n"
        "       tg.GAME_RESULT, tg.POINTS_FOR, tg.POINTS_AGAINST, tg.TEAM_SPREAD, tg.TEAM_PREGAME_WIN_PROB,\n"
        "       o.ABBREVIATION AS OPP_ABBREVIATION, o.COLOR AS OPP_COLOR,\n"
        "       o.ALTERNATE_COLOR AS OPP_ALTERNATE_COLOR\n"
        f"  FROM {MODEL}.TEAM_GAME tg\n"
        f"  LEFT JOIN {MODEL}.TEAM o ON o.TEAM_ID = tg.OPPONENT_ID\n"
        f" WHERE tg.SEASON = {season} AND tg.TEAM_CLASSIFICATION = 'fbs'\n"
        " ORDER BY tg.TEAM_ID, tg.WEEK_SEQ, tg.START_AT"
    )


def _ranks_sql(season: int) -> str:
    return (
        f"{SQL_PREFIX}SELECT POLL_CODE, VISIBLE_AT_WEEK_SEQ, POLL_RANK, TEAM_ID\n"
        f"  FROM {MODEL}.RANKING\n"
        f" WHERE SEASON = {season} AND POLL_CODE IN ('AP', 'CFP') AND TEAM_ID IS NOT NULL\n"
        " ORDER BY VISIBLE_AT_WEEK_SEQ, POLL_CODE, POLL_RANK"
    )


def _sp_sql(season: int) -> str:
    return (
        f"{SQL_PREFIX}SELECT SEASON, TEAM_ID, SP_RATING, SP_RANKING, OFFENSE_RATING, OFFENSE_RANKING,\n"
        "       DEFENSE_RATING, DEFENSE_RANKING\n"
        f"  FROM {MODEL}.RATING_SP\n"
        f" WHERE SEASON BETWEEN {season - SP_SEASONS + 1} AND {season} AND TEAM_ID IS NOT NULL\n"
        " ORDER BY TEAM_ID, SEASON DESC"
    )


def _week_games_sql(season: int, week_seq: int) -> str:
    return (
        f"{SQL_PREFIX}SELECT GAME_ID, WEEK_SEQ, START_AT, VENUE_NAME, HOME_TEAM_ID, HOME_TEAM,\n"
        "       AWAY_TEAM_ID, AWAY_TEAM, PREGAME_SPREAD, HOME_PREGAME_WIN_PROB\n"
        f"  FROM {MODEL}.GAME\n"
        f" WHERE SEASON = {season} AND WEEK_SEQ = {week_seq} AND IS_FBS_GAME AND NOT IS_COMPLETED\n"
        " ORDER BY START_AT, GAME_ID"
    )


def _road_warriors_sql(season: int, point: int) -> str:
    return (
        f"{SQL_PREFIX}WITH LEGS AS (\n"
        "  SELECT tg.TEAM_ID, COALESCE(v.CITY, g.VENUE_NAME) AS PLACE,\n"
        f"         {MODEL}.GREAT_CIRCLE_KM(h.LATITUDE, h.LONGITUDE, v.LATITUDE, v.LONGITUDE) AS KM\n"
        f"    FROM {MODEL}.TEAM_GAME tg\n"
        f"    JOIN {MODEL}.GAME g ON g.GAME_ID = tg.GAME_ID\n"
        f"    JOIN {MODEL}.TEAM t ON t.TEAM_ID = tg.TEAM_ID\n"
        f"    JOIN {MODEL}.VENUE h ON h.VENUE_ID = t.HOME_VENUE_ID\n"
        f"    JOIN {MODEL}.VENUE v ON v.VENUE_ID = g.VENUE_ID\n"
        f"   WHERE tg.SEASON = {season} AND tg.WEEK_SEQ <= {point} AND tg.TEAM_CLASSIFICATION = 'fbs'\n"
        "     AND tg.HAS_RESULT AND tg.SITE <> 'HOME'),\n"
        "RANKED AS (\n"
        "  SELECT TEAM_ID, PLACE, KM, ROW_NUMBER() OVER (PARTITION BY TEAM_ID ORDER BY KM DESC) AS LONGEST\n"
        "    FROM LEGS WHERE KM IS NOT NULL)\n"
        "SELECT TEAM_ID, SUM(KM) AS KM, COUNT(*) AS TRIPS, MAX(KM) AS LONGEST_TRIP_KM,\n"
        "       MAX(CASE WHEN LONGEST = 1 THEN PLACE END) AS LONGEST_TRIP_TO\n"
        "  FROM RANKED GROUP BY TEAM_ID ORDER BY KM DESC\n"
        f" LIMIT {ROAD_WARRIORS}"
    )


def _movers_sql(season: int) -> str:
    return (
        f"{SQL_PREFIX}SELECT TEAM_ID, SP_NOW, SP_BEFORE, DELTA, RISE_RANK, FALL_RANK FROM (\n"
        "  SELECT TEAM_ID, SP_RATING AS SP_NOW, SP_PRIOR_RATING AS SP_BEFORE,\n"
        "         SP_RATING - SP_PRIOR_RATING AS DELTA,\n"
        "         ROW_NUMBER() OVER (ORDER BY SP_RATING - SP_PRIOR_RATING DESC) AS RISE_RANK,\n"
        "         ROW_NUMBER() OVER (ORDER BY SP_RATING - SP_PRIOR_RATING) AS FALL_RANK\n"
        f"    FROM {MODEL}.TEAM_SEASON\n"
        f"   WHERE SEASON = {season} AND CLASSIFICATION = 'fbs'\n"
        "     AND SP_RATING IS NOT NULL AND SP_PRIOR_RATING IS NOT NULL)\n"
        f" WHERE RISE_RANK <= {MOVERS} OR FALL_RANK <= {MOVERS}\n"
        " ORDER BY RISE_RANK"
    )


def _conference_sql(season: int, point: int) -> str:
    return (
        f"{SQL_PREFIX}WITH RANKED AS ({_latest_poll(season, point)}),\n"
        "NONCONF AS (\n"
        "  SELECT TEAM_CONFERENCE AS CONFERENCE,\n"
        "         SUM(CASE WHEN GAME_RESULT = 'W' THEN 1 ELSE 0 END) AS WINS,\n"
        "         SUM(CASE WHEN GAME_RESULT = 'L' THEN 1 ELSE 0 END) AS LOSSES\n"
        f"    FROM {MODEL}.TEAM_GAME\n"
        f"   WHERE SEASON = {season} AND WEEK_SEQ <= {point} AND HAS_RESULT\n"
        "     AND TEAM_CLASSIFICATION = 'fbs' AND OPPONENT_CLASSIFICATION = 'fbs'\n"
        "     AND NOT IS_CONFERENCE_GAME\n"
        "   GROUP BY TEAM_CONFERENCE)\n"
        "SELECT s.CONFERENCE_NAME AS CONFERENCE, COUNT(*) AS TEAM_COUNT, AVG(s.SP_RATING) AS MEAN_SP,\n"
        "       MAX(n.WINS) AS NONCONF_WINS, MAX(n.LOSSES) AS NONCONF_LOSSES,\n"
        "       SUM(CASE WHEN r.TEAM_ID IS NOT NULL THEN 1 ELSE 0 END) AS RANKED_TEAMS\n"
        f"  FROM {MODEL}.TEAM_SEASON s\n"
        "  LEFT JOIN NONCONF n ON n.CONFERENCE = s.CONFERENCE_NAME\n"
        "  LEFT JOIN RANKED r ON r.TEAM_ID = s.TEAM_ID AND r.POLL_CODE = 'AP'\n"
        f" WHERE s.SEASON = {season} AND s.CLASSIFICATION = 'fbs'\n"
        " GROUP BY s.CONFERENCE_NAME ORDER BY MEAN_SP DESC"
    )


def _streaks_sql(season: int, point: int) -> str:
    return (
        f"{SQL_PREFIX}WITH PLAYED AS (\n"
        "  SELECT TEAM_ID, SEASON, WEEK_SEQ, GAME_RESULT,\n"
        "         ROW_NUMBER() OVER (PARTITION BY TEAM_ID ORDER BY SEASON, WEEK_SEQ, START_AT) AS RN\n"
        f"    FROM {MODEL}.TEAM_GAME\n"
        "   WHERE HAS_RESULT\n"
        f"     AND (SEASON < {season} OR (SEASON = {season} AND WEEK_SEQ <= {point}))\n"
        f"     AND TEAM_ID IN (SELECT TEAM_ID FROM {MODEL}.TEAM_SEASON\n"
        f"                      WHERE SEASON = {season} AND CLASSIFICATION = 'fbs')),\n"
        "LAST_BREAK AS (SELECT TEAM_ID, MAX(RN) AS RN FROM PLAYED WHERE GAME_RESULT <> 'W' GROUP BY TEAM_ID)\n"
        f"SELECT p.TEAM_ID, COUNT(*) AS STREAK_LENGTH, MIN(p.SEASON * {SEASON_KEY} + p.WEEK_SEQ) AS SINCE_KEY\n"
        "  FROM PLAYED p LEFT JOIN LAST_BREAK b ON b.TEAM_ID = p.TEAM_ID\n"
        " WHERE p.RN > COALESCE(b.RN, 0)\n"
        " GROUP BY p.TEAM_ID ORDER BY STREAK_LENGTH DESC, SINCE_KEY\n"
        f" LIMIT {STREAKS}"
    )


def current_point(season: int, weeks: Sequence[Row]) -> Point:
    """The season's last finished week, and the week after it."""
    ordered = sorted(weeks, key=lambda row: row["WEEK_SEQ"])
    done = [row for row in ordered if row["IS_DONE"]]
    if not done:
        raise ExportError(f"season {season} has no finished week")
    last = done[-1]
    later = [row["WEEK_SEQ"] for row in ordered if row["WEEK_SEQ"] > last["WEEK_SEQ"]]
    return Point(
        season,
        int(last["WEEK_SEQ"]),
        int(last["WEEK"]),
        str(last["SEASON_TYPE"]),
        int(later[0]) if later else None,
    )


def build_snapshot(
    season: int,
    target_name: str,
    results: Mapping[str, Executed],
    *,
    has_logo: Callable[[int], bool],
    exported_at: str,
) -> Snapshot:
    """Map the statements' rows to contract responses; every file is validated before it is returned."""
    point = current_point(season, results["weeks"].rows)
    resolved = {
        "season": season,
        "week_seq": point.week_seq,
        "next_week_seq": point.next_week_seq,
        "label": point.label,
        "is_current": True,
    }
    ids = {name: _entry_id(index, name) for index, name in enumerate(results)}
    build = _Build(point, resolved, results, ids, has_logo)
    files: dict[str, Any] = {
        "points.json": build.points(),
        "teams.json": build.teams(),
        "teams-map.json": build.teams_map(),
        "league-week.json": build.league_week(),
        "league-stats.json": build.league_stats(),
        "inspector.json": build.inspector(),
    }
    for team_id in build.team_ids():
        files[f"team/{team_id}.json"] = build.team_page(team_id)
        files[f"team/{team_id}.outlook.json"] = build.outlook(team_id)
    _validate(files)
    counts = {
        "teams": len(build.map_rows),
        "on_map": sum(1 for row in build.map_rows if _located(row)),
        "with_logos": sum(1 for row in build.map_rows if has_logo(int(row["TEAM_ID"]))),
        "team_pages": len(build.team_ids()),
        "week_games": len(files["league-week.json"]["games"]),
        "statements": len(results),
    }
    return Snapshot(files, counts, exported_at, target_name, season)


def _entry_id(index: int, name: str) -> str:
    return f"snap-{index + 1:02d}-{name}"


class _Build:
    def __init__(
        self,
        point: Point,
        resolved: dict[str, Any],
        results: Mapping[str, Executed],
        ids: dict[str, str],
        has_logo: Callable[[int], bool],
    ) -> None:
        self.point = point
        self.resolved = resolved
        self.results = results
        self.ids = ids
        self.has_logo = has_logo
        self.map_rows = list(results["map"].rows)
        self.by_id = {int(row["TEAM_ID"]): row for row in self.map_rows}
        self.ranks = _ranks_by_team(results["ranks"].rows)
        self.poll_weeks = sorted(
            {
                int(row["VISIBLE_AT_WEEK_SEQ"])
                for row in results["ranks"].rows
                if row["VISIBLE_AT_WEEK_SEQ"] <= point.week_seq
            }
        )

    def team_ids(self) -> list[int]:
        return sorted(self.by_id)

    def _rows(self, name: str) -> list[Row]:
        return list(self.results[name].rows) if name in self.results else []

    def _queries(self, mapping: dict[str, str]) -> dict[str, str]:
        return {panel: self.ids[name] for panel, name in mapping.items() if name in self.ids}

    def _team(self, team_id: int | None, fallback: Row | None = None) -> dict[str, Any]:
        row = self.by_id.get(int(team_id)) if team_id is not None else None
        if row is not None:
            return {
                "team_id": int(row["TEAM_ID"]),
                "school": row["SCHOOL"],
                "conference": row["CONFERENCE"],
                "abbreviation": row["ABBREVIATION"],
                "color": _color(row["COLOR"]),
                "alternate_color": _color(row["ALTERNATE_COLOR"]),
                "has_logo": self.has_logo(int(row["TEAM_ID"])),
            }
        extra = fallback or {}
        return {
            "team_id": int(team_id) if team_id is not None else 0,
            "school": extra.get("school", "Unknown"),
            "conference": extra.get("conference"),
            "abbreviation": extra.get("abbreviation"),
            "color": _color(extra.get("color")),
            "alternate_color": _color(extra.get("alternate_color")),
            "has_logo": self.has_logo(int(team_id)) if team_id is not None else False,
        }

    def _rank(self, team_id: int | None, poll: str, limit: int) -> int | None:
        if team_id is None:
            return None
        best: tuple[int, int] | None = None
        for visible, rank in self.ranks.get((int(team_id), poll), []):
            if visible <= limit and (best is None or visible > best[0]):
                best = (visible, rank)
        return None if best is None else best[1]

    def _ap(self, team_id: int | None) -> int | None:
        return self._rank(team_id, "AP", self.point.week_seq)

    def points(self) -> dict[str, Any]:
        point = self.point
        current = {
            "as_of_season": point.season,
            "as_of_week": point.week,
            "as_of_season_type": point.season_type,
            "as_of_week_seq": point.week_seq,
            "next_week_seq": point.next_week_seq,
            "as_of_label": point.label,
            "publication_id": None,
            "whatif_publication_id": None,
            "whatif_run_id": None,
            "whatif_as_of_week_seq": None,
            "whatif_views_current": False,
        }
        entry = {
            **{
                key: value
                for key, value in current.items()
                if key.startswith("as_of") or key == "next_week_seq"
            },
            "is_current": True,
            "is_after_current": False,
            "has_publication": False,
            "has_predictions": False,
            "has_grades": False,
        }
        return {
            "current": current,
            "season": point.season,
            "seasons": [point.season],
            "points": [entry],
            "notice": None,
        }

    def teams(self) -> dict[str, Any]:
        return {"teams": [self._team(int(row["TEAM_ID"])) for row in self.map_rows]}

    def teams_map(self) -> dict[str, Any]:
        teams = [self._map_team(row) for row in self.map_rows]
        return {
            "point": self.resolved,
            "notice": None,
            "conferences": _conference_order(
                row["CONFERENCE"] for row in self.map_rows if row["CONFERENCE"]
            ),
            "teams": teams,
            "queries": self._queries({"teams": "map"}),
        }

    def _map_team(self, row: Row) -> dict[str, Any]:
        team_id = int(row["TEAM_ID"])
        return {
            "team_id": team_id,
            "school": row["SCHOOL"],
            "mascot": row["MASCOT"],
            "abbreviation": row["ABBREVIATION"],
            "conference": row["CONFERENCE"],
            "color": _color(row["COLOR"]),
            "alternate_color": _color(row["ALTERNATE_COLOR"]),
            "has_logo": self.has_logo(team_id),
            "latitude": _number(row["LATITUDE"]) if _located(row) else None,
            "longitude": _number(row["LONGITUDE"]) if _located(row) else None,
            "wins": _number(row["WINS"]),
            "losses": _number(row["LOSSES"]),
            "conf_wins": _number(row["CONF_WINS"]),
            "conf_losses": _number(row["CONF_LOSSES"]),
            "ap_rank": _number(row["AP_RANK"]),
        }

    def team_page(self, team_id: int) -> dict[str, Any]:
        row = self.by_id[team_id]
        hero = {
            key: value
            for key, value in self._map_team(row).items()
            if key not in {"latitude", "longitude"}
        } | {"cfp_rank": _number(row["CFP_RANK"])}
        games = [
            self._game(game) for game in self._rows("games") if int(game["TEAM_ID"]) == team_id
        ]
        sp = [_sp(sp_row) for sp_row in self._rows("sp") if int(sp_row["TEAM_ID"]) == team_id]
        return {
            "point": self.resolved,
            "notice": None,
            "team": hero,
            "games": games,
            "sp": sorted(sp, key=lambda item: -item["season"]),
            "polls": [self._poll(team_id, week) for week in self.poll_weeks],
            "queries": self._queries(
                {"hero": "map", "schedule": "games", "ratings": "sp", "polls": "ranks"}
            ),
        }

    def _game(self, game: Row) -> dict[str, Any]:
        week_seq = int(game["WEEK_SEQ"])
        final = bool(game["HAS_RESULT"]) and week_seq <= self.point.week_seq
        opponent_id = _int_or_none(game["OPPONENT_ID"])
        opponent = self._team(
            opponent_id,
            {
                "school": game["OPPONENT_NAME"],
                "conference": game["OPPONENT_CONFERENCE"],
                "abbreviation": game["OPP_ABBREVIATION"],
                "color": game["OPP_COLOR"],
                "alternate_color": game["OPP_ALTERNATE_COLOR"],
            },
        )
        probability = None if final else _number(game["TEAM_PREGAME_WIN_PROB"])
        return {
            "game_id": int(game["GAME_ID"]),
            "week": int(game["WEEK"]),
            "season_type": game["SEASON_TYPE"],
            "start_at": _iso(game["START_AT"]),
            "status": "FINAL" if final else "UPCOMING",
            "site": game["SITE"],
            "opponent": opponent,
            "opponent_rank": self._rank(opponent_id, "AP", min(week_seq - 1, self.point.week_seq)),
            "team_points": _number(game["POINTS_FOR"]) if final else None,
            "opponent_points": _number(game["POINTS_AGAINST"]) if final else None,
            "result": game["GAME_RESULT"] if final else None,
            "spread": None if final else _number(game["TEAM_SPREAD"]),
            "win_prob": probability,
            "win_prob_source": None if probability is None else "CFBD",
        }

    def _poll(self, team_id: int, week: int) -> dict[str, Any]:
        return {
            "week_seq": week,
            "label": _poll_label(week),
            "ap_rank": self._rank(team_id, "AP", week)
            if self._published(team_id, "AP", week)
            else None,
            "cfp_rank": self._rank(team_id, "CFP", week)
            if self._published(team_id, "CFP", week)
            else None,
        }

    def _published(self, team_id: int, poll: str, week: int) -> bool:
        return any(visible == week for visible, _ in self.ranks.get((team_id, poll), []))

    def outlook(self, team_id: int) -> dict[str, Any]:
        reasons = {
            "odds": "No published run: odds appear after the first simulation run.",
            "this_week": "No prediction yet: this week's picks come with the first simulation run.",
            "watch": "Games to watch appear once a run is published for this week.",
            "rivalries": "Rivalries appear once the series are published.",
        }
        return {
            "point": self.resolved,
            "run_id": None,
            "team_id": team_id,
            "odds": {
                "available": False,
                "reason": reasons["odds"],
                "move": None,
                "delta_p_in_cfp": None,
                "current": None,
                "trajectory": [],
            },
            "this_week": {"available": False, "reason": reasons["this_week"], "game": None},
            "watch": {"available": False, "reason": reasons["watch"], "guide": None},
            "rivalries": {"available": False, "reason": reasons["rivalries"], "series": []},
            "queries": {},
        }

    def league_week(self) -> dict[str, Any]:
        games = [self._week_game(row) for row in self._rows("week_games")]
        games.sort(key=_interest)
        for index, game in enumerate(games):
            game["game_of_week"] = index == 0
        return {
            "point": self.resolved,
            "notice": None,
            "week_seq": self.point.next_week_seq,
            "run_id": None,
            "games": games,
            "queries": self._queries({"games": "week_games"}),
        }

    def _week_game(self, row: Row) -> dict[str, Any]:
        home_id, away_id = _int_or_none(row["HOME_TEAM_ID"]), _int_or_none(row["AWAY_TEAM_ID"])
        home_rank, away_rank = self._ap(home_id), self._ap(away_id)
        probability = _number(row["HOME_PREGAME_WIN_PROB"])
        return {
            "game_id": int(row["GAME_ID"]),
            "week_seq": int(row["WEEK_SEQ"]),
            "start_at": _iso(row["START_AT"]),
            "venue": row["VENUE_NAME"],
            "home": self._team(home_id, {"school": row["HOME_TEAM"]}),
            "away": self._team(away_id, {"school": row["AWAY_TEAM"]}),
            "home_rank": home_rank,
            "away_rank": away_rank,
            "spread": _number(row["PREGAME_SPREAD"]),
            "home_win_prob": probability,
            "win_prob_source": None if probability is None else "CFBD",
            "stakes": None,
            "game_of_week": False,
            "upset_alert_team_id": _upset_alert(
                home_id, home_rank, away_id, away_rank, probability
            ),
        }

    def league_stats(self) -> dict[str, Any]:
        return {
            "point": self.resolved,
            "notice": None,
            "road_warriors": [self._travel(row) for row in self._rows("road_warriors")],
            "risers": [
                self._mover(row) for row in self._rows("movers") if row["RISE_RANK"] <= MOVERS
            ],
            "fallers": [
                self._mover(row)
                for row in sorted(self._rows("movers"), key=lambda item: item["FALL_RANK"])
                if row["FALL_RANK"] <= MOVERS
            ],
            "conference_strength": [
                self._conference(row) for row in self._rows("conference_strength")
            ],
            "streaks": [self._streak(row) for row in self._rows("streaks")],
            "queries": self._queries(
                {
                    "road_warriors": "road_warriors",
                    "movers": "movers",
                    "conference_strength": "conference_strength",
                    "streaks": "streaks",
                }
            ),
        }

    def _travel(self, row: Row) -> dict[str, Any]:
        return {
            "team": self._team(int(row["TEAM_ID"])),
            "km": round(float(row["KM"])),
            "trips": int(row["TRIPS"]),
            "longest_trip_km": round(float(row["LONGEST_TRIP_KM"])),
            "longest_trip_to": row["LONGEST_TRIP_TO"] or "",
        }

    def _mover(self, row: Row) -> dict[str, Any]:
        return {
            "team": self._team(int(row["TEAM_ID"])),
            "sp_now": round(float(row["SP_NOW"]), 1),
            "sp_before": round(float(row["SP_BEFORE"]), 1),
            "delta": round(float(row["DELTA"]), 1),
        }

    def _conference(self, row: Row) -> dict[str, Any]:
        return {
            "conference": row["CONFERENCE"],
            "teams": int(row["TEAM_COUNT"]),
            "mean_sp": round(float(row["MEAN_SP"]), 1),
            "nonconf_wins": int(row["NONCONF_WINS"] or 0),
            "nonconf_losses": int(row["NONCONF_LOSSES"] or 0),
            "ranked_teams": int(row["RANKED_TEAMS"] or 0),
        }

    def _streak(self, row: Row) -> dict[str, Any]:
        season, week_seq = divmod(int(row["SINCE_KEY"]), SEASON_KEY)
        return {
            "team": self._team(int(row["TEAM_ID"])),
            "length": int(row["STREAK_LENGTH"]),
            "since_season": season,
            "since_week": week_seq
            if week_seq < POSTSEASON_OFFSET
            else week_seq - POSTSEASON_OFFSET,
        }

    def inspector(self) -> dict[str, Any]:
        entries: list[dict[str, Any]] = [
            {
                "entry_id": self.ids[name],
                "principal": "app",
                "user": SNAPSHOT_USER,
                "page": executed.page,
                "sql": executed.sql,
                "started_at": executed.started_at,
                "ms": executed.ms,
                "row_count": len(executed.rows),
                "error": None,
                "requests": [],
                "calls_spent": 0,
            }
            for name, executed in self.results.items()
        ]
        return {"entries": list(reversed(entries))}


def _poll_label(week_seq: int) -> str:
    if week_seq == 0:
        return "preseason"
    if week_seq >= POSTSEASON_OFFSET:
        return f"after postseason week {week_seq - POSTSEASON_OFFSET}"
    return f"after week {week_seq}"


def _ranks_by_team(rows: Sequence[Row]) -> dict[tuple[int, str], list[tuple[int, int]]]:
    found: dict[tuple[int, str], list[tuple[int, int]]] = {}
    for row in rows:
        key = (int(row["TEAM_ID"]), str(row["POLL_CODE"]))
        found.setdefault(key, []).append((int(row["VISIBLE_AT_WEEK_SEQ"]), int(row["POLL_RANK"])))
    return found


def _sp(row: Row) -> dict[str, Any]:
    return {
        "season": int(row["SEASON"]),
        "overall": _number(row["SP_RATING"]),
        "overall_rank": int(row["SP_RANKING"]),
        "offense": _number(row["OFFENSE_RATING"]),
        "offense_rank": int(row["OFFENSE_RANKING"]),
        "defense": _number(row["DEFENSE_RATING"]),
        "defense_rank": int(row["DEFENSE_RANKING"]),
    }


def _interest(game: dict[str, Any]) -> tuple[int, float, str]:
    start = game["start_at"] or ""
    if game["home_rank"] is not None and game["away_rank"] is not None:
        return (0, game["home_rank"] + game["away_rank"], start)
    if game["spread"] is not None and abs(game["spread"]) <= CLOSE_LINE_POINTS:
        return (1, abs(game["spread"]), start)
    return (2, 0, start)


def _upset_alert(
    home_id: int | None,
    home_rank: int | None,
    away_id: int | None,
    away_rank: int | None,
    home_prob: float | int | None,
) -> int | None:
    if home_prob is None:
        return None
    candidates = []
    if home_rank is not None and home_prob < UPSET_ALERT_BELOW and home_id is not None:
        candidates.append((home_prob, home_id))
    if away_rank is not None and 1 - home_prob < UPSET_ALERT_BELOW and away_id is not None:
        candidates.append((1 - home_prob, away_id))
    return min(candidates)[1] if candidates else None


def _conference_order(names: Sequence[str] | Any) -> list[str]:
    unique = sorted(set(names))
    power = [name for name in POWER_CONFERENCES if name in unique]
    rest = [name for name in unique if name not in power and name != INDEPENDENTS]
    return power + rest + ([INDEPENDENTS] if INDEPENDENTS in unique else [])


def _located(row: Row) -> bool:
    return row["LATITUDE"] is not None and row["LONGITUDE"] is not None


def _color(value: Any) -> str | None:
    if not isinstance(value, str) or not COLOR.fullmatch(value):
        return None
    return value if value.startswith("#") else f"#{value}"


def _int_or_none(value: Any) -> int | None:
    return None if value is None else int(value)


def _number(value: Any) -> int | float | None:
    if value is None:
        return None
    number = float(value) if isinstance(value, (str, Decimal)) else value
    if isinstance(number, float) and number.is_integer():
        return int(number)
    return round(number, 4) if isinstance(number, float) else number


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value).replace(" ", "T")[:19] + "Z"


MODEL_BY_FILE: dict[str, type[BaseModel]] = {
    "points.json": models.PointsResponse,
    "teams.json": models.TeamsResponse,
    "teams-map.json": models.TeamsMapResponse,
    "league-week.json": models.LeagueWeekResponse,
    "league-stats.json": models.LeagueStatsResponse,
    "inspector.json": models.InspectorResponse,
}


def _validate(files: Mapping[str, Any]) -> None:
    for path, body in files.items():
        if path in MODEL_BY_FILE:
            MODEL_BY_FILE[path].model_validate(body)
        elif path.endswith(".outlook.json"):
            models.TeamOutlookResponse.model_validate(body)
        else:
            models.TeamResponse.model_validate(body)


def ensure_outside_repository(path: Path) -> None:
    """The snapshot holds near-raw CFBD rows, so it never goes inside the repository."""
    resolved = path.expanduser().resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise ExportError(f"{path} is inside the repository; the snapshot must stay outside it")


def write_snapshot(snapshot: Snapshot, root: Path, name: str) -> Path:
    """Write the files below `root/name`, a manifest, and point `root/latest` at the directory."""
    ensure_outside_repository(root)
    directory = root / name
    for relative, body in snapshot.files.items():
        destination = directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
    manifest = {
        "target": snapshot.target_name,
        "season": snapshot.season,
        "exported_at": snapshot.exported_at,
        "counts": snapshot.counts,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=1) + "\n", encoding="utf-8"
    )
    link = root / LATEST_LINK
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(directory.resolve(), target_is_directory=True)
    return directory


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_message(error: Exception) -> str:
    match = re.search(r"message\s*=>\s*(.+)", str(error))
    return match.group(1).strip() if match else type(error).__name__


def _execute(connection: Any, name: str, page: str, sql: str) -> Executed:
    started = _utc_now()
    clock = time.monotonic()
    try:
        cursor = connection.execute(sql)
    except Exception as error:
        raise ExportError(f"statement {name!r} failed: {_first_message(error)}") from None
    columns = list(cursor.columns().keys())
    rows = [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]
    return Executed(name, page, sql, started, round((time.monotonic() - clock) * 1000), rows)


def run(
    deployment: target.Target, season: int | None, root: Path, *, logo_dir: Path
) -> tuple[Path, Snapshot]:
    """Read the stored model on `deployment`, build and write the snapshot."""
    ensure_outside_repository(root)
    connection = deployment.connect()
    try:
        if season is None:
            season = int(
                _execute(connection, "season", "points", latest_season_sql()).rows[0]["SEASON"]
            )
        results: dict[str, Executed] = {
            "weeks": _execute(connection, "weeks", "points", weeks_sql(season))
        }
        point = current_point(season, results["weeks"].rows)
        for name, statement in statements(season, point.week_seq, point.next_week_seq).items():
            results[name] = _execute(connection, name, statement.page, statement.sql)
    finally:
        connection.close()
    snapshot = build_snapshot(
        season,
        deployment.name,
        results,
        has_logo=lambda team_id: logo_cached(logo_dir, team_id, enabled=True),
        exported_at=_utc_now(),
    )
    return write_snapshot(snapshot, root, f"{deployment.name}-{season}"), snapshot


def summary(directory: Path, snapshot: Snapshot) -> str:
    counts = snapshot.counts
    return (
        f"{snapshot.target_name} {snapshot.season}: {counts['teams']} teams, {counts['on_map']} on the map, "
        f"{counts['with_logos']} with logos, {counts['team_pages']} team pages, {counts['week_games']} games "
        f"this week, {counts['statements']} statements, 0 CFBD calls -> {directory}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.app_fixtures", description=__doc__)
    parser.add_argument(
        "--target", default=None, help="local or aws (default: $TARGET, else local)"
    )
    parser.add_argument(
        "--season", type=int, default=None, help="default: the newest season with a finished week"
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_ROOT, help="root directory of the snapshots"
    )
    args = parser.parse_args(argv)
    try:
        deployment = target.resolve(args.target)
        directory, snapshot = run(
            deployment, args.season, args.out, logo_dir=resolve_cache_dir(os.environ)
        )
    except (ExportError, target.TargetError) as error:
        print(f"app-fixtures: {error}", file=sys.stderr)
        return EXIT_ERROR
    print(summary(directory, snapshot))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
