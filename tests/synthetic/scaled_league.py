"""The scaled synthetic league: about 250,000 games over 1869-2026, for performance smoke tests.

It replicates the season shapes of the base league (`tests/synthetic/league.py`) instead of its
facts: no Elo before 1890, ties until 1995, divisions as one `/games` call per season returns them
all, conference championship games inside the seasons of `013_conference_championship.sql`, bowls
and playoff rounds in postseason week 1, pregame lines and SP+ from 2013, polls from 2014, a 2026
season in progress. Teams are generated (`FBS 03-07`); only the conference names are real.

The rows go through the same flattener as the base league, so they are what a loader would store.
Everything is deterministic and lazy: `ScaledLeague.iter_slices` builds one season at a time, so
no more than about 2,500 games are in memory. Nothing here calls an API. Load it into the `PERF_`
namespace only.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tests.synthetic import league_teams as lt
from tests.synthetic.league import (
    AP,
    COACHES,
    COMMITTEE,
    CURRENT_SEASON,
    FETCHED_2026,
    FETCHED_2026_RANKINGS,
    FETCHED_EARLY,
    LAST_COMPLETED_WEEK,
    calendar_body,
    range_key,
    season_key,
    sp_row,
    static_key,
)
from tests.synthetic.league_games import (
    POSTSEASON,
    REGULAR,
    Game,
    digest,
    finish_games,
    iso,
    kickoff,
)
from tests.synthetic.seed_sql import championship_ranges
from tests.synthetic.slices import Row, Slice, load_log_row, slice_rows

FIRST_SEASON = 1869
LAST_SEASON = 2026
GAP_SEASON = 1871
FIRST_ELO_SEASON = 1890
LAST_TIES_SEASON = 1995
FIRST_LINES_SEASON = 2013
FIRST_POLLS_SEASON = 2014
ACTIVE_FIRST = 24
ACTIVE_PER_SEASON = 5
FBS_INDEPENDENTS = "FBS Independents"
FBS_CONFERENCES = (
    "SEC",
    "ACC",
    "Big Ten",
    "Big 12",
    "Pac-12",
    "Mountain West",
    "American Athletic",
    "Sun Belt",
    "Conference USA",
    "Mid-American",
)
FBS_PER_CONFERENCE = 9
FBS_INDEPENDENT_TEAMS = 6
OTHER_DIVISIONS = (("fcs", 5, 12), ("ii", 8, 8), ("iii", 5, 20))
NEUTRAL_VENUES = ("Scaled Dome", "Scaled Bowl", "Scaled Park", "Scaled Field", "Scaled Arena")
FIRST_VENUE_ID = 20001
LINE_DROP_ONE_IN = 20
RANKED_TEAMS = 25
SHAPE_DONORS = (
    (1869, 1885),
    (1890, 1950),
    (1978, 1985),
    (2000, 2000),
    (2013, 2013),
    (2014, 2014),
    (2023, 2023),
    (2024, 2024),
    (2025, 2025),
    (2026, 2026),
)


@dataclass(frozen=True)
class ScaledTeam:
    """A generated team: `conference` is the current name, `index` its place in its division."""

    id: int
    name: str
    division: str
    conference: str
    index: int
    arrival: float


@dataclass(frozen=True)
class Pair:
    """One planned game: its week, the teams and what kind of game it is."""

    week: int
    home: ScaledTeam
    away: ScaledTeam
    conference_game: bool = False
    notes: str | None = None
    season_type: str = REGULAR
    neutral: bool = False
    playoff: dict[str, Any] | None = None
    day: int = 0


@functools.cache
def teams() -> tuple[ScaledTeam, ...]:
    """Every generated team, FBS first, ids from 10001."""
    made: list[tuple[str, str, int]] = []
    for conference in FBS_CONFERENCES:
        made += [("fbs", conference, k) for k in range(FBS_PER_CONFERENCE)]
    made += [("fbs", FBS_INDEPENDENTS, k) for k in range(FBS_INDEPENDENT_TEAMS)]
    for division, conferences, size in OTHER_DIVISIONS:
        for number in range(conferences):
            made += [
                (division, f"{division.upper()} Conference {number + 1}", k) for k in range(size)
            ]
    sizes: dict[str, int] = {}
    for division, _, _ in made:
        sizes[division] = sizes.get(division, 0) + 1
    seen: dict[str, int] = {}
    result = []
    for number, (division, conference, _) in enumerate(made, 1):
        index = seen.get(division, 0)
        seen[division] = index + 1
        jitter = (digest(f"arrival:{number}") % 100) / 2000
        result.append(
            ScaledTeam(
                10000 + number,
                f"{division.upper()} {number:03d}",
                division,
                conference,
                index,
                (index + 0.5) / sizes[division] + jitter,
            )
        )
    return tuple(result)


@functools.cache
def arrival_order() -> tuple[ScaledTeam, ...]:
    """Teams in the order they join the league: divisions interleave in proportion."""
    return tuple(sorted(teams(), key=lambda team: (team.arrival, team.id)))


def active_count(season: int) -> int:
    return min(len(teams()), ACTIVE_FIRST + ACTIVE_PER_SEASON * (season - FIRST_SEASON))


def regular_weeks(season: int) -> int:
    if season < FIRST_ELO_SEASON:
        return 6
    return 10 if season < 1925 else 13


def conference_in(team: ScaledTeam, season: int) -> str:
    """The conference name used in `season` (the Pac-10 and the Big East were renamed)."""
    if team.conference == "Pac-12" and season <= 2010:
        return "Pac-10"
    if team.conference == "American Athletic" and season <= 2012:
        return "Big East"
    return team.conference


def active_teams(season: int) -> list[ScaledTeam]:
    chosen = {team.id for team in arrival_order()[: active_count(season)]}
    return [team for team in teams() if team.id in chosen]


def first_season(team: ScaledTeam) -> int:
    position = arrival_order().index(team)
    needed = max(0, position + 1 - ACTIVE_FIRST)
    return FIRST_SEASON + -(-needed // ACTIVE_PER_SEASON)


def strength(team: ScaledTeam, season: int) -> int:
    return digest(f"strength:{team.id}:{season}")


def shape_of(season: int) -> int:
    """The base-league season whose shape `season` replicates."""
    donor = SHAPE_DONORS[0][1]
    for first, base in SHAPE_DONORS:
        if season >= first:
            donor = base
    return donor


def seasons() -> tuple[int, ...]:
    return tuple(s for s in range(FIRST_SEASON, LAST_SEASON + 1) if s != GAP_SEASON)


def plan_season(season: int) -> list[Pair]:
    """Every game of a season as a plan: regular weeks, then the postseason."""
    plan = _regular_pairs(season)
    if season < CURRENT_SEASON:
        plan += _postseason_pairs(season)
    return plan


def game_count(season: int) -> int:
    """The number of games in the season's `/games` body (0 for the 1871 gap)."""
    return 0 if season == GAP_SEASON else len(plan_season(season))


def total_games() -> int:
    return sum(game_count(season) for season in seasons())


def _circle(group: Sequence[ScaledTeam], step: int) -> list[tuple[ScaledTeam, ScaledTeam]]:
    """Round-robin pairs of the circle method; every step pairs different teams."""
    if len(group) < 2:
        return []
    items: list[ScaledTeam | None] = list(group)
    if len(items) % 2:
        items.append(None)
    count = len(items)
    shift = step % (count - 1)
    rest = items[1:]
    rotated = [items[0], *rest[shift:], *rest[:shift]]
    pairs = []
    for position in range(count // 2):
        first, second = rotated[position], rotated[count - 1 - position]
        if first is None or second is None:
            continue
        pairs.append((first, second) if (step + position) % 2 == 0 else (second, first))
    return pairs


def _by_division(season: int) -> dict[str, list[ScaledTeam]]:
    result: dict[str, list[ScaledTeam]] = {}
    for team in active_teams(season):
        result.setdefault(team.division, []).append(team)
    return result


def _conference_groups(members: Sequence[ScaledTeam], season: int) -> list[list[ScaledTeam]]:
    groups: dict[str, list[ScaledTeam]] = {}
    for team in members:
        groups.setdefault(conference_in(team, season), []).append(team)
    return list(groups.values())


def _champions(
    season: int, members: Sequence[ScaledTeam]
) -> list[tuple[str, ScaledTeam, ScaledTeam]]:
    seeded = championship_ranges()
    result = []
    for group in _conference_groups(members, season):
        name = conference_in(group[0], season)
        covered = any(a <= season and (b is None or season <= b) for a, b in seeded.get(name, ()))
        if covered and len(group) >= 2:
            best = sorted(group, key=lambda team: -strength(team, season))[:2]
            result.append((name, best[0], best[1]))
    return result


def _regular_pairs(season: int) -> list[Pair]:
    divisions = _by_division(season)
    last = regular_weeks(season)
    champions = _champions(season, divisions.get("fbs", []))
    reserved = {team.id for _, first, second in champions for team in (first, second)}
    pairs: list[Pair] = []
    for week in range(1, last + 1):
        for division, members in divisions.items():
            conference_week = 4 <= week < last
            if conference_week:
                for group in _conference_groups(members, season):
                    independent = conference_in(group[0], season) == FBS_INDEPENDENTS
                    pairs += _pairs(group, week, conference_game=not independent)
            elif week == last and division == "fbs":
                pairs += _pairs([t for t in members if t.id not in reserved], week)
                pairs += _championship_pairs(champions, week)
            else:
                pairs += _pairs(members, week)
    return pairs


def _pairs(group: Sequence[ScaledTeam], week: int, *, conference_game: bool = False) -> list[Pair]:
    return [Pair(week, home, away, conference_game) for home, away in _circle(group, week)]


def _championship_pairs(
    champions: list[tuple[str, ScaledTeam, ScaledTeam]], week: int
) -> list[Pair]:
    return [
        Pair(week, first, second, True, f"{name} Championship", neutral=True, day=1)
        for name, first, second in champions
    ]


def _postseason_pairs(season: int) -> list[Pair]:
    divisions = _by_division(season)
    fbs = sorted(divisions.get("fbs", []), key=lambda team: -strength(team, season))
    pairs: list[Pair] = []
    playoff = _playoff_pairs(season, fbs)
    taken = {team.id for pair in playoff for team in (pair.home, pair.away)}
    rest = [team for team in fbs if team.id not in taken]
    for number in range(min(_bowl_count(season), len(rest) // 2)):
        pairs.append(
            Pair(
                1,
                rest[2 * number],
                rest[2 * number + 1],
                notes=f"Bowl {number + 1}",
                season_type=POSTSEASON,
                neutral=True,
                day=1 + number % 14,
            )
        )
    pairs += playoff
    for division, size in (("fcs", _fcs_playoff_games(season)), ("ii", 10), ("iii", 10)):
        if season < (1978 if division == "fcs" else 1973):
            continue
        top = sorted(divisions.get(division, []), key=lambda team: -strength(team, season))
        for number in range(min(size, len(top) // 2)):
            pairs.append(
                Pair(
                    1,
                    top[2 * number],
                    top[2 * number + 1],
                    notes=f"{division.upper()} Playoffs",
                    season_type=POSTSEASON,
                    day=1 + number % 10,
                )
            )
    return pairs


def _bowl_count(season: int) -> int:
    if season < 1902:
        return 0
    if season < 1990:
        return 4 + (season - 1902) // 8
    return min(38, 16 + season - 1990)


def _fcs_playoff_games(season: int) -> int:
    return min(24, 4 + (season - 1978) // 2) if season >= 1978 else 0


def _playoff_pairs(season: int, fbs: Sequence[ScaledTeam]) -> list[Pair]:
    if season < 2014 or len(fbs) < 12:
        return []
    slots = (
        [("semifinal", 1, 4), ("semifinal", 2, 3), ("championship", 1, 2)]
        if season < 2024
        else [
            *[("firstRound", seed, 17 - seed) for seed in (5, 6, 7, 8)],
            *[("quarterfinal", seed, 9 - seed) for seed in (1, 2, 3, 4)],
            ("semifinal", 1, 4),
            ("semifinal", 2, 3),
            ("championship", 1, 2),
        ]
    )
    pairs = []
    for number, (kind, home_seed, away_seed) in enumerate(slots):
        playoff = {
            "competition": "cfp",
            "format": "4-team" if season < 2024 else "12-team",
            "round": kind,
            "roundName": kind,
            "bracketSlot": f"{kind[:2].upper()}{number}",
            "homeSeed": home_seed,
            "awaySeed": away_seed,
        }
        pairs.append(
            Pair(
                1,
                fbs[home_seed - 1],
                fbs[away_seed - 1],
                notes=f"CFP {kind}",
                season_type=POSTSEASON,
                neutral=kind != "firstRound",
                playoff=playoff,
                day=12 + number,
            )
        )
    return pairs


def _game_body(season: int, number: int, pair: Pair) -> Game:
    seed = digest(f"{season}:{number}")
    played = season < CURRENT_SEASON or (
        pair.season_type == REGULAR and pair.week <= LAST_COMPLETED_WEEK
    )
    home, away = (3 + seed % 50, seed // 64 % 46)
    if home == away and season > LAST_TIES_SEASON:
        home += 3
    venue = NEUTRAL_VENUES[seed % len(NEUTRAL_VENUES)] if pair.neutral else _venue_name(pair.home)
    clock = ("17:00", "20:00", "23:30")[seed // 4096 % 3]
    start = kickoff(season, pair.season_type, pair.week, pair.day + (seed // 8192) % 2, clock)
    return {
        "id": season * 10_000 + number,
        "season": season,
        "week": pair.week,
        "seasonType": pair.season_type,
        "startDate": iso(start),
        "startTimeTBD": False,
        "completed": played,
        "neutralSite": pair.neutral,
        "conferenceGame": pair.conference_game,
        "venueId": _venue_id(venue),
        "venue": venue,
        "homeId": pair.home.id,
        "homeTeam": pair.home.name,
        "homeConference": conference_in(pair.home, season),
        "homeClassification": pair.home.division,
        "homePoints": home if played else None,
        "awayId": pair.away.id,
        "awayTeam": pair.away.name,
        "awayConference": conference_in(pair.away, season),
        "awayClassification": pair.away.division,
        "awayPoints": away if played else None,
        "notes": pair.notes if pair.season_type == POSTSEASON or pair.conference_game else None,
        "playoff": pair.playoff,
    }


def _venue_name(team: ScaledTeam) -> str:
    return f"{team.name} Stadium"


def _venue_id(name: str) -> int:
    if name in NEUTRAL_VENUES:
        return FIRST_VENUE_ID + len(teams()) + NEUTRAL_VENUES.index(name)
    return FIRST_VENUE_ID + int(name.split()[1]) - 1


def season_games(season: int) -> list[Game]:
    """The season's `/games` elements, with Elo and the rest, in a hash order."""
    games = [_game_body(season, number, pair) for number, pair in enumerate(plan_season(season), 1)]
    return finish_games(season, games)


def _lines(season: int, games: list[Game]) -> list[dict[str, Any]]:
    rows = []
    for game in sorted(games, key=lambda g: g["id"]):
        both_fbs = game["homeClassification"] == game["awayClassification"] == "fbs"
        if (
            not both_fbs
            or not game["completed"]
            or digest(f"line:{game['id']}") % LINE_DROP_ONE_IN == 0
        ):
            continue
        home, away = game["homePregameElo"], game["awayPregameElo"]
        probability = round(1 / (1 + 10 ** ((away - home) / 400)), 2)
        rows.append(
            {
                "season": season,
                "seasonType": game["seasonType"],
                "week": game["week"],
                "gameId": game["id"],
                "homeTeam": game["homeTeam"],
                "awayTeam": game["awayTeam"],
                "spread": -round((probability - 0.5) * 80) / 2 + 0.0,
                "homeWinProbability": probability,
            }
        )
    return rows


def _sp_rows(season: int) -> list[dict[str, Any]]:
    fbs = [t for t in active_teams(season) if t.division == "fbs"]
    rated = sorted(fbs, key=lambda team: -strength(team, season))
    rows = [
        sp_row(season, team.name, conference_in(team, season), 30.0 - rank * 0.4, rank)
        for rank, team in enumerate(rated, 1)
    ]
    rows.append(sp_row(season, "nationalAverages", None, 0.0, None))
    return rows


def _polls(season: int) -> list[dict[str, Any]]:
    fbs = sorted(
        (t for t in active_teams(season) if t.division == "fbs"),
        key=lambda team: -strength(team, season),
    )
    last = LAST_COMPLETED_WEEK + 1 if season == CURRENT_SEASON else regular_weeks(season) + 2
    weeks: list[tuple[str, str, int]] = []
    for poll in (AP, COACHES):
        weeks += [(poll, REGULAR, week) for week in range(1, last + 1)]
    weeks += [(COMMITTEE, REGULAR, week) for week in range(10, last + 1)]
    if season < CURRENT_SEASON:
        weeks += [(poll, POSTSEASON, 1) for poll in (AP, COACHES, COMMITTEE)]
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for poll, season_type, week in weeks:
        shift = (week + len(poll)) % 5
        order = (fbs[shift:] + fbs[:shift])[:RANKED_TEAMS]
        ranks = [
            {
                "rank": position,
                "teamId": team.id,
                "school": team.name,
                "conference": conference_in(team, season),
                "firstPlaceVotes": max(0, 10 - position) if poll == AP else None,
                "points": 1500 - 20 * position,
            }
            for position, team in enumerate(order, 1)
        ]
        grouped.setdefault((season_type, week), []).append(
            {"poll": poll, "isFinal": season_type == POSTSEASON, "ranks": ranks}
        )
    ordered = sorted(grouped.items(), key=lambda item: (item[0][0] == POSTSEASON, item[0][1]))
    return [
        {"season": season, "seasonType": kind, "week": week, "polls": polls}
        for (kind, week), polls in ordered
    ]


def slice_keys(season: int) -> list[tuple[str, str]]:
    """The (table, slice key) pairs of a season's slices, without building them."""
    if season == GAP_SEASON:
        return []
    keys = [("GAMES", season_key("GAMES", season))]
    if season >= FIRST_LINES_SEASON:
        keys += [
            ("CALENDAR", season_key("CALENDAR", season)),
            ("METRICS_WP_PREGAME", season_key("METRICS_WP_PREGAME", season)),
            ("RATINGS_SP", season_key("RATINGS_SP", season)),
        ]
    if season >= FIRST_POLLS_SEASON:
        keys.append(("RANKINGS_POLLS_RANKS", season_key("RANKINGS_POLLS_RANKS", season)))
    return keys


def season_slices(season: int) -> list[Slice]:
    """The slices of one season, built now (about 2,500 games for a modern season)."""
    if season == GAP_SEASON:
        return []
    when: datetime = FETCHED_2026 if season == CURRENT_SEASON else FETCHED_EARLY
    final = season < CURRENT_SEASON
    games = season_games(season)
    bodies: dict[str, Any] = {"GAMES": games}
    if season >= FIRST_LINES_SEASON:
        bodies["CALENDAR"] = calendar_body(season, games)
        bodies["METRICS_WP_PREGAME"] = _lines(season, games)
        bodies["RATINGS_SP"] = _sp_rows(season)
    if season >= FIRST_POLLS_SEASON:
        bodies["RANKINGS_POLLS_RANKS"] = _polls(season)
    result = []
    for table, key in slice_keys(season):
        moment = FETCHED_2026_RANKINGS if (table == "RANKINGS_POLLS_RANKS" and not final) else when
        result.append(Slice(table, key, bodies[table], moment, final))
    return result


def _spells(team: ScaledTeam) -> list[lt.Spell]:
    first = first_season(team)
    if team.conference == "Pac-12" and first <= 2010:
        return [lt.Spell("Pac-10", first, 2010), lt.Spell("Pac-12", 2011)]
    if team.conference == "American Athletic" and first <= 2012:
        return [lt.Spell("Big East", first, 2012), lt.Spell("American Athletic", 2013)]
    return [lt.Spell(team.conference, first)]


def _team_body(team: ScaledTeam) -> dict[str, Any]:
    venue = _venue_name(team)
    return {
        "id": team.id,
        "school": team.name,
        "mascot": f"{team.name} Foxes",
        "abbreviation": f"S{team.id % 1000:03d}",
        "alternateNames": [],
        "conference": team.conference,
        "division": None,
        "classification": team.division,
        "color": "#336699",
        "alternateColor": "#ffffff",
        "logos": [],
        "twitter": None,
        "location": {
            "id": _venue_id(venue),
            "name": venue,
            "city": team.name,
            "state": "AL",
            "zip": "35000",
            "countryCode": "US",
            "timezone": "America/Chicago",
            "latitude": 25.0 + team.id % 23,
            "longitude": -120.0 + team.id % 50,
            "elevation": "100.0",
            "capacity": 20000 + team.id * 13 % 60000,
            "constructionYear": 1900 + team.id % 100,
            "grass": True,
            "dome": False,
        },
    }


def _venues() -> list[dict[str, Any]]:
    names = [_venue_name(team) for team in teams()] + list(NEUTRAL_VENUES)
    return [
        lt.venue_body(lt.VenueDef(_venue_id(name), name, 25.0 + index % 23, -120.0 + index % 50))
        for index, name in enumerate(names)
    ]


def _conference_names() -> list[str]:
    names = [*FBS_CONFERENCES, "Pac-10", "Big East", FBS_INDEPENDENTS]
    names += sorted({t.conference for t in teams() if t.division != "fbs"})
    return names


def _affiliation(team: ScaledTeam, spell: lt.Spell) -> dict[str, Any]:
    return {
        "teamId": team.id,
        "team": team.name,
        "conferenceId": 200 + _conference_names().index(spell.conference),
        "conference": spell.conference,
        "conferenceAbbreviation": spell.conference[:4].upper(),
        "classification": team.division,
        "conferenceDivision": None,
        "startYear": spell.start,
        "endYear": spell.end,
    }


def static_slices() -> list[Slice]:
    """Teams, venues, conferences and the two affiliation slices (range 1869-2025, season 2026)."""
    ranges = [
        _affiliation(team, spell)
        for team in teams()
        for spell in _spells(team)
        if spell.start <= lt.RANGE_LAST_SEASON
    ]
    current = [
        _affiliation(team, _spells(team)[-1])
        for team in teams()
        if first_season(team) <= CURRENT_SEASON
    ]
    conferences = [
        {
            "id": 200 + number,
            "name": name,
            "shortName": name,
            "abbreviation": name[:4].upper(),
            "classification": "fbs"
            if name in (*FBS_CONFERENCES, "Pac-10", "Big East", FBS_INDEPENDENTS)
            else "fcs",
        }
        for number, name in enumerate(_conference_names())
    ]
    return [
        Slice("TEAMS", static_key("TEAMS"), [_team_body(t) for t in teams()], FETCHED_EARLY, True),
        Slice("VENUES", static_key("VENUES"), _venues(), FETCHED_EARLY, True),
        Slice("CONFERENCES", static_key("CONFERENCES"), conferences, FETCHED_EARLY, True),
        Slice(
            "CONFERENCES_AFFILIATIONS",
            range_key("CONFERENCES_AFFILIATIONS"),
            ranges,
            FETCHED_EARLY,
            True,
        ),
        Slice(
            "CONFERENCES_AFFILIATIONS",
            season_key("CONFERENCES_AFFILIATIONS", CURRENT_SEASON),
            current,
            FETCHED_2026,
            False,
        ),
    ]


class ScaledLeague:
    """The whole scaled league, produced one season at a time."""

    def seasons(self) -> tuple[int, ...]:
        return seasons()

    def game_count(self, season: int) -> int:
        return game_count(season)

    def total_games(self) -> int:
        return total_games()

    def iter_slices(self) -> Iterator[Slice]:
        """The static slices, then every season's slices, one season in memory at a time."""
        yield from static_slices()
        for season in seasons():
            yield from season_slices(season)

    def iter_rows(self, table: str) -> Iterator[Row]:
        for item in self.iter_slices():
            if item.table == table:
                yield from slice_rows(item)

    def iter_load_log_rows(self) -> Iterator[Row]:
        for item in self.iter_slices():
            yield load_log_row(item, len(slice_rows(item)))
