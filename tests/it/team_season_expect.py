"""The `TEAM_SEASON` rows a league should give, restated in Python from model/teams-conferences.

The inputs are the league's own bodies (affiliation spells with their slice's request, game
bodies, the team list) and the reference seeds, never the SQL under test. Two facts come from other
model views that their own tasks test: `GAME.PARTICIPANTS_RULE` and `GAME.IS_CONF_CHAMPIONSHIP`
(`game_rules`), because restating the championship detection here would duplicate `GAME`.

- Memberships: a slice speaks only for the seasons of its request (`P_YEAR`, else `P_MIN_YEAR` to
  `P_MAX_YEAR`); the newest fetch (then the later slice key) wins per team and season; within it the
  later `startYear` (then the later row) wins, and two conferences are a conflict.
- Conference and classification from games: only games with `FIXED` participants count; the code
  on most of them wins, ties go to the code of the latest such game.
- Records count completed games; conference records leave out championship games.
"""

from __future__ import annotations

import functools
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tests.it.harness import Harness
from tests.synthetic import league_teams as lt
from tests.synthetic.league import STORED_SEASONS, League
from tests.synthetic.seed_sql import seed_rows
from tests.synthetic.slices import echo_values

FIRST_SEASON = 1869
FIXED = "FIXED"
POWER_TIERS = frozenset({"AQ", "P5", "P4"})

TeamSeason = tuple[int, int]


@dataclass(frozen=True)
class Membership:
    conference: str | None
    division: str | None
    classification: str | None
    conflict: bool


@dataclass(frozen=True)
class Side:
    """One team's side of a stored game."""

    game_id: int
    start: datetime
    name: str
    conference: str | None
    classification: str | None
    fixed: bool
    championship: bool
    conference_game: bool
    result: str | None
    points_for: int | None
    points_against: int | None
    pregame_elo: int | None
    postgame_elo: int | None

    @property
    def order(self) -> tuple[datetime, int]:
        return (self.start, self.game_id)


@dataclass
class Expected:
    """The expected values of one team-season."""

    team_id: int
    season: int
    school: str
    conference: str | None = None
    conference_source: str = "NONE"
    division: str | None = None
    conflict: bool = False
    classification: str | None = None
    classification_source: str = "NONE"
    tier: str | None = None
    is_power: bool = False
    sides: list[Side] = field(default_factory=list)

    @property
    def completed(self) -> list[Side]:
        return [side for side in self.sides if side.result is not None]

    @property
    def conference_completed(self) -> list[Side]:
        return [s for s in self.completed if s.conference_game and not s.championship]

    def record(self) -> dict[str, int]:
        done, conf = self.completed, self.conference_completed
        return {
            "GAMES": len(self.sides),
            "FIXED_GAMES": sum(1 for side in self.sides if side.fixed),
            "WINS": sum(1 for side in done if side.result == "W"),
            "LOSSES": sum(1 for side in done if side.result == "L"),
            "TIES": sum(1 for side in done if side.result == "T"),
            "CONF_WINS": sum(1 for side in conf if side.result == "W"),
            "CONF_LOSSES": sum(1 for side in conf if side.result == "L"),
            "CONF_TIES": sum(1 for side in conf if side.result == "T"),
            "POINTS_FOR": sum(side.points_for or 0 for side in done),
            "POINTS_AGAINST": sum(side.points_against or 0 for side in done),
            "CONF_POINT_DIFF": sum(
                (side.points_for or 0) - (side.points_against or 0) for side in conf
            ),
        }

    def first_pregame_elo(self) -> int | None:
        rated = [side for side in self.sides if side.pregame_elo is not None]
        return min(rated, key=lambda side: side.order).pregame_elo if rated else None

    def last_postgame_elo(self) -> int | None:
        rated = [side for side in self.sides if side.postgame_elo is not None]
        return max(rated, key=lambda side: side.order).postgame_elo if rated else None


def game_rules(harness: Harness) -> dict[int, tuple[str, bool]]:
    """`PARTICIPANTS_RULE` and `IS_CONF_CHAMPIONSHIP` per game id, from `GAME`."""
    rows = harness.query(
        f"SELECT GAME_ID, PARTICIPANTS_RULE, IS_CONF_CHAMPIONSHIP FROM {harness.model}.GAME"
    )
    return {int(game_id): (str(rule), bool(championship)) for game_id, rule, championship in rows}


def memberships(league: League) -> dict[TeamSeason, Membership]:
    """The membership of every team-season that a stored affiliation slice speaks for."""
    candidates: dict[TeamSeason, list[tuple[tuple[datetime, str], int, int, dict[str, Any]]]]
    candidates = defaultdict(list)
    for item in league.of("CONFERENCES_AFFILIATIONS"):
        echoes = echo_values(item.table_def(), item.slice_key)
        low = echoes["P_YEAR"] or echoes["P_MIN_YEAR"] or FIRST_SEASON
        high = echoes["P_YEAR"] or echoes["P_MAX_YEAR"] or item.fetched_at.year
        for row_no, body in enumerate(item.body, 1):
            start = body["startYear"] if body["startYear"] is not None else low
            end = body["endYear"] if body["endYear"] is not None else high
            for season in range(max(start, low), min(end, high) + 1):
                fetch = (item.fetched_at, item.slice_key)
                candidates[(body["teamId"], season)].append((fetch, start, row_no, body))
    result = {}
    for key, rows in candidates.items():
        newest = max(row[0] for row in rows)
        mine = [row for row in rows if row[0] == newest]
        winner = max(mine, key=lambda row: (row[1], row[2]))[3]
        result[key] = Membership(
            winner["conference"],
            winner["conferenceDivision"],
            winner["classification"],
            len({row[3]["conference"] for row in mine}) > 1,
        )
    return result


def sides(league: League, rules: dict[int, tuple[str, bool]]) -> dict[TeamSeason, list[Side]]:
    """Every named side of every stored game, per team-season."""
    result: dict[TeamSeason, list[Side]] = defaultdict(list)
    for season in STORED_SEASONS:
        for body in league.game_bodies(season):
            rule, championship = rules[body["id"]]
            for mine, theirs in (("home", "away"), ("away", "home")):
                team_id = body.get(f"{mine}Id")
                if team_id is None:
                    continue
                result[(team_id, season)].append(_side(body, mine, theirs, rule, championship))
    return result


def _side(body: dict[str, Any], mine: str, theirs: str, rule: str, championship: bool) -> Side:
    points_for, points_against = body.get(f"{mine}Points"), body.get(f"{theirs}Points")
    result = None
    if body.get("completed") and points_for is not None and points_against is not None:
        result = "W" if points_for > points_against else "L" if points_for < points_against else "T"
    return Side(
        game_id=body["id"],
        start=datetime.fromisoformat(body["startDate"].replace("Z", "+00:00")),
        name=body[f"{mine}Team"],
        conference=body.get(f"{mine}Conference"),
        classification=body.get(f"{mine}Classification"),
        fixed=rule == FIXED,
        championship=championship,
        conference_game=bool(body.get("conferenceGame")),
        result=result,
        points_for=points_for if result else None,
        points_against=points_against if result else None,
        pregame_elo=body.get(f"{mine}PregameElo"),
        postgame_elo=body.get(f"{mine}PostgameElo"),
    )


def _most_common_on_fixed_games(games: list[Side], attribute: str) -> str | None:
    """The value on most `FIXED` games; a tie goes to the value of the latest such game."""
    votes: Counter[str] = Counter()
    latest: dict[str, tuple[datetime, int]] = {}
    for side in games:
        value = getattr(side, attribute)
        if not side.fixed or value is None:
            continue
        votes[value] += 1
        latest[value] = max(latest.get(value, side.order), side.order)
    if not votes:
        return None
    return max(votes, key=lambda value: (votes[value], latest[value]))


@functools.cache
def _seed(file: str) -> tuple[tuple[Any, ...], ...]:
    """The rows of a reference seed, parsed once (parsing reads every model file)."""
    return tuple(seed_rows(file))


def _tiers() -> tuple[tuple[Any, ...], ...]:
    return _seed("012_conference_tier.sql")


def _tier_row(conference: str | None, season: int) -> tuple[Any, ...] | None:
    for row in _tiers():
        name, first, last = row[0], row[1], row[2]
        if name == conference and first <= season and (last is None or season <= last):
            return row
    return None


def expected_team_seasons(league: League, harness: Harness) -> dict[TeamSeason, Expected]:
    """Every team-season with a stored game or a membership, with its expected values."""
    rules = game_rules(harness)
    members = memberships(league)
    games = sides(league, rules)
    listed = {team["id"]: team["school"] for team in _team_list(league)}
    result: dict[TeamSeason, Expected] = {}
    for key in set(members) | set(games):
        team_id, season = key
        mine = sorted(games.get(key, []), key=lambda side: side.order)
        school = listed.get(team_id) or (mine[-1].name if mine else _member_name(league, team_id))
        expected = Expected(team_id, season, school, sides=mine)
        membership = members.get(key)
        if membership is not None:
            expected.conference = membership.conference
            expected.conference_source = "AFFILIATION"
            expected.division = membership.division
            expected.conflict = membership.conflict
        else:
            expected.conference = _most_common_on_fixed_games(mine, "conference")
            expected.conference_source = "GAMES" if expected.conference else "NONE"
        transition = _transition(school, season)
        from_games = _most_common_on_fixed_games(mine, "classification")
        if transition is not None:
            expected.classification, expected.classification_source = transition, "TRANSITION"
        elif from_games is not None:
            expected.classification, expected.classification_source = from_games, "GAMES"
        elif membership is not None and membership.classification is not None:
            expected.classification = membership.classification
            expected.classification_source = "AFFILIATION"
        result[key] = expected
    _assign_tiers(result)
    return result


def _team_list(league: League) -> list[dict[str, Any]]:
    return [team for item in league.of("TEAMS") for team in item.body]


def _member_name(league: League, team_id: int) -> str:
    for item in league.of("CONFERENCES_AFFILIATIONS"):
        for body in item.body:
            if body["teamId"] == team_id:
                return str(body["team"])
    raise KeyError(team_id)


def _transition(school: str, season: int) -> str | None:
    for name, first, last, classification, _ in _seed("016_classification_transition.sql"):
        if name == school and first <= season <= last:
            return str(classification)
    return None


def _assign_tiers(rows: dict[TeamSeason, Expected]) -> None:
    """Named tier rows first; the era's default (NULL-name) row for a conference whose team-seasons
    with a classification are mostly `fbs` in that season."""
    fbs: Counter[tuple[int, str]] = Counter()
    classified: Counter[tuple[int, str]] = Counter()
    for row in rows.values():
        if row.conference is not None and row.classification is not None:
            classified[(row.season, row.conference)] += 1
            fbs[(row.season, row.conference)] += row.classification == lt.FBS
    for row in rows.values():
        if row.conference is None:
            continue
        named = _tier_row(row.conference, row.season)
        group = (row.season, row.conference)
        tier = named
        if tier is None and 2 * fbs[group] > classified[group]:
            tier = _tier_row(None, row.season)
        if tier is not None:
            row.tier, row.is_power = str(tier[3]), bool(tier[4])


def division_label(season: int, classification: str | None) -> tuple[str | None, bool | None]:
    """The label of the classification in the season's division era, and the retroactive flag."""
    if classification is None:
        return None, None
    era = next(
        row[1]
        for row in _seed("010_era.sql")
        if row[0] == "DIVISION" and row[2] <= season and (row[3] is None or season <= row[3])
    )
    for division_era, code, label, retroactive in _seed("011_classification_label.sql"):
        if division_era == era and code == classification:
            return str(label), bool(retroactive)
    return None, None
