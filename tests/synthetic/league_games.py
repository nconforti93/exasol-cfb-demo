"""Game bodies of the synthetic league: a season builder that writes `/games` elements.

Every game is a dict in the shape of a CFBD `/games` element (camelCase keys, UTC offsets), so
the foundation's flattener turns it into exactly the row a loader would store. `SeasonBuilder`
adds named and filler games, keeps track of who plays when, and `finish` assigns a deterministic
Elo chain, postgame win probabilities and attendance, then orders the body by a hash of the
game id: CFBD does not keep a stable row order (S1), so nothing may rely on one.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any

from tests.synthetic import league_teams as lt

FIRST_ELO_SEASON = 1890
ELO_K = 20
REGULAR = "regular"
POSTSEASON = "postseason"
POSTSEASON_FIRST_DAY = (12, 21)
WEEK_DAYS = 7
SATURDAY = 5
ELO_START_OVERRIDES: dict[tuple[int, str], int] = {(2024, "Ashford"): 1650}
ELO_POST_OVERRIDES: dict[tuple[int, str, int], int] = {(2024, "Ashford", 8): 1702}

Game = dict[str, Any]


def week_seq(season_type: str, week: int) -> int:
    """0 is the preseason, a regular week keeps its number, postseason week n is 100 + n."""
    return week if season_type == REGULAR else 100 + week


def game_week_seq(game: Game) -> int:
    return week_seq(game["seasonType"], game["week"])


def week1_saturday(season: int) -> date:
    """The first Saturday on or after 28 August."""
    start = date(season, 8, 28)
    return start + timedelta(days=(SATURDAY - start.weekday()) % WEEK_DAYS)


def kickoff(season: int, season_type: str, week: int, day: int, clock: str) -> datetime:
    """Naive UTC kickoff: `day` days after the week's Saturday (postseason: after 21 December)."""
    if season_type == REGULAR:
        base = week1_saturday(season) + timedelta(days=WEEK_DAYS * (week - 1) + day)
    else:
        base = date(season, *POSTSEASON_FIRST_DAY) + timedelta(days=day)
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime.combine(base, time(hour, minute))


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _digest(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def quarters(total: int, seed: int) -> list[float]:
    """Four non-negative quarter scores that add up to `total` (CFBD reports them as numbers)."""
    first = min(total, seed % 10)
    second = min(total - first, (seed // 10) % 10)
    third = min(total - first - second, (seed // 100) % 10)
    return [float(first), float(second), float(third), float(total - first - second - third)]


class SeasonBuilder:
    """Games of one season, plus the names tests use to find them."""

    def __init__(self, season: int) -> None:
        self.season = season
        self.games: list[Game] = []
        self.names: dict[str, int] = {}
        self._busy: dict[tuple[str, int], set[str]] = defaultdict(set)
        self._pairs: set[frozenset[str]] = set()
        self._count = 0

    def add(
        self,
        home: str,
        away: str,
        week: int,
        *,
        key: str | None = None,
        season_type: str = REGULAR,
        day: int = 0,
        clock: str = "17:00",
        points: tuple[int, int] | None = None,
        neutral: bool = False,
        conference_game: bool | None = None,
        venue: str | None = None,
        notes: str | None = None,
        playoff: dict[str, Any] | None = None,
    ) -> Game:
        """A game between two named teams; `points` None makes it a scheduled game."""
        home_team, away_team = lt.team(home), lt.team(away)
        home_conference = home_team.conference_in(self.season)
        away_conference = away_team.conference_in(self.season)
        if conference_game is None:
            conference_game = home_conference is not None and home_conference == away_conference
        venue_name = venue or (lt.NEUTRAL_DOME if neutral else lt.home_venue_name(home))
        game = self._base(week, season_type, day, clock, key)
        game.update(
            {
                "completed": points is not None,
                "neutralSite": neutral,
                "conferenceGame": conference_game,
                "venueId": lt.VENUE_BY_NAME[venue_name].id,
                "venue": venue_name,
                "homeId": home_team.id,
                "homeTeam": home,
                "homeConference": home_conference,
                "homeClassification": lt.recorded_classification(home, self.season),
                "homePoints": None if points is None else points[0],
                "awayId": away_team.id,
                "awayTeam": away,
                "awayConference": away_conference,
                "awayClassification": lt.recorded_classification(away, self.season),
                "awayPoints": None if points is None else points[1],
                "notes": notes,
                "playoff": playoff,
            }
        )
        self._register(game, home, away, season_type, week)
        return game

    def add_placeholder(
        self, week: int, *, key: str, venue: str, notes: str, day: int = 0, clock: str = "17:00"
    ) -> Game:
        """A game that CFBD lists before it names the teams."""
        game = self._base(week, REGULAR, day, clock, key)
        game.update(
            {
                "startTimeTBD": True,
                "completed": False,
                "neutralSite": True,
                "conferenceGame": True,
                "venueId": lt.VENUE_BY_NAME[venue].id,
                "venue": venue,
                "notes": notes,
            }
        )
        self.games.append(game)
        return game

    def busy_in(self, week: int, season_type: str = REGULAR) -> set[str]:
        return self._busy[(season_type, week)]

    def fill(
        self,
        count: int,
        weeks: Sequence[int],
        pool: Sequence[str],
        *,
        completed_through: int | None = None,
        season_type: str = REGULAR,
    ) -> list[Game]:
        """`count` games between teams of different conferences that are free in their week.

        Game k goes to week `weeks[k % len(weeks)]`. Deterministic: the opponents rotate through
        `pool`. Games after `completed_through` (a regular week) are scheduled, not played.
        """
        made: list[Game] = []
        for index in range(count):
            week = weeks[index % len(weeks)]
            home, away = self._free_pair(pool, week, season_type, index)
            seed = _digest(f"{self.season}:{index}:{home}:{away}")
            played = completed_through is None or week <= completed_through
            points = (10 + seed % 35, 7 + (seed // 64) % 31) if played else None
            if points is not None and points[0] == points[1]:
                points = (points[0] + 3, points[1])
            made.append(
                self.add(
                    home,
                    away,
                    week,
                    season_type=season_type,
                    day=(seed // 4096) % 2,
                    clock=("17:00", "20:00", "23:30")[(seed // 8192) % 3],
                    points=points,
                    conference_game=False,
                )
            )
        return made

    def finish(self) -> list[Game]:
        """Assign Elo, postgame win probability, attendance and excitement; fix the row order."""
        return finish_games(self.season, self.games)

    def _base(self, week: int, season_type: str, day: int, clock: str, key: str | None) -> Game:
        self._count += 1
        identifier = self.season * 1000 + self._count
        if key:
            if key in self.names:
                raise ValueError(f"game name {key} is used twice")
            self.names[key] = identifier
        return {
            "id": identifier,
            "season": self.season,
            "week": week,
            "seasonType": season_type,
            "startDate": iso(kickoff(self.season, season_type, week, day, clock)),
            "startTimeTBD": False,
        }

    def _register(self, game: Game, home: str, away: str, season_type: str, week: int) -> None:
        for team in (home, away):
            if season_type == REGULAR and team in self._busy[(season_type, week)]:
                raise ValueError(f"{team} plays twice in {season_type} week {week}")
            self._busy[(season_type, week)].add(team)
        self._pairs.add(frozenset((home, away)))
        self.games.append(game)

    def _free_pair(
        self, pool: Sequence[str], week: int, season_type: str, index: int
    ) -> tuple[str, str]:
        busy = self._busy[(season_type, week)]
        size = len(pool)
        start = index * 3
        for first in range(size):
            for gap in range(1, size):
                home = pool[(start + first) % size]
                away = pool[(start + first + gap) % size]
                if home in busy or away in busy:
                    continue
                if frozenset((home, away)) in self._pairs:
                    continue
                if self._conference(home) == self._conference(away):
                    continue
                return home, away
        raise ValueError(f"no free pair in week {week}")

    def _conference(self, name: str) -> str | None:
        return lt.team(name).conference_in(self.season)


def finish_games(season: int, games: list[Game]) -> list[Game]:
    """Assign Elo, postgame win probability, attendance and excitement, and order the games by a
    hash of their id (CFBD's order is not stable). Changes and returns `games`."""
    _assign_elo(season, games)
    for game in games:
        _assign_extras(game)
    games.sort(key=lambda game: _digest(f"{season}:{game['id']}"))
    return games


def digest(text: str) -> int:
    """A stable 64-bit number for `text`."""
    return _digest(text)


def _start_elo(season: int, name: str) -> int:
    fixed = ELO_START_OVERRIDES.get((season, name))
    return fixed if fixed is not None else 1500 + (sum(map(ord, name)) * 7 + season) % 250


def _assign_elo(season: int, games: list[Game]) -> None:
    if season < FIRST_ELO_SEASON:
        return
    ratings: dict[int, int] = {}
    for game in sorted(games, key=lambda g: (g["startDate"], g["id"])):
        if game.get("homeId") is None or game.get("awayId") is None:
            continue
        home, away = game["homeId"], game["awayId"]
        for side in ("home", "away"):
            ratings.setdefault(game[f"{side}Id"], _start_elo(season, game[f"{side}Team"]))
        before_home, before_away = ratings[home], ratings[away]
        game["homePregameElo"], game["awayPregameElo"] = before_home, before_away
        if not game["completed"]:
            continue
        expected = 1 / (1 + 10 ** ((before_away - before_home) / 400))
        result = _result(game["homePoints"], game["awayPoints"])
        change = round(ELO_K * (result - expected))
        after_home, after_away = before_home + change, before_away - change
        after_home = _override(season, game, "home", after_home)
        after_away = _override(season, game, "away", after_away)
        ratings[home], ratings[away] = after_home, after_away
        game["homePostgameElo"], game["awayPostgameElo"] = after_home, after_away


def _override(season: int, game: Game, side: str, value: int) -> int:
    if game["seasonType"] != REGULAR:
        return value
    return ELO_POST_OVERRIDES.get((season, game[f"{side}Team"], game["week"]), value)


def _result(home_points: int, away_points: int) -> float:
    if home_points == away_points:
        return 0.5
    return 1.0 if home_points > away_points else 0.0


def _assign_extras(game: Game) -> None:
    seed = _digest(f"extras:{game['id']}")
    if game["completed"]:
        home, away = game["homePoints"], game["awayPoints"]
        probability = {1.0: 0.8 + (seed % 15) / 100, 0.0: 0.2 - (seed % 15) / 100, 0.5: 0.5}[
            _result(home, away)
        ]
        game["homePostgameWinProbability"] = round(probability, 3)
        game["awayPostgameWinProbability"] = round(1 - probability, 3)
        game["attendance"] = 20000 + seed % 60000
        game["excitementIndex"] = round(2.0 + (seed // 7) % 70 / 10, 1)
        if game["season"] >= 2013:
            game["homeLineScores"] = quarters(home, seed)
            game["awayLineScores"] = quarters(away, seed // 3)


def fbs_pair(game: Game) -> bool:
    """Both teams are recorded as `fbs` on the game."""
    return game.get("homeClassification") == lt.FBS and game.get("awayClassification") == lt.FBS


def games_where(games: Sequence[Game], test: Callable[[Game], bool]) -> list[Game]:
    return [game for game in games if test(game)]
