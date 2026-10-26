"""The synthetic league: a fictional CFBD data set with exactly the facts the model specs use.

`base_league()` returns a `League`: API-shaped response bodies per stored slice, the mirror rows
the foundation's flattener makes of them (`League.rows`), the `LOAD_LOG` registrations and the
replay recordings of the 2026 weekly slices. Teams, schools and stadiums are made up; only the
conference names are real. Everything is deterministic and costs no API call.

Fetch times (UTC): seasons up to 2025 at 2026-10-20 10:00, 2026 at 2026-10-25 12:00, except the
2026 rankings at Tuesday 2026-10-27 23:00. Week 9 of 2026 opens at 2026-10-22 23:30 and week 10 at
Thursday 2026-10-29 23:30. `NOW_OVERRIDE` is 2026-10-28 12:00.

Variants (each takes a league and returns a changed copy): `mutate_after`,
`with_unaffiliated_fbs_team`, `with_incomplete_week9_game`, `with_renamed_conference`,
`with_duplicate_rows`, `with_thursday_result`, `with_nonfinal_prior`. The base league has no
natural key stored twice and no failing ERROR check.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cache
from typing import Any

from loader.plan import default_catalog, load_plan
from loader.templates import SliceTemplate, compile_plan
from tests.synthetic import fake
from tests.synthetic import league_teams as lt
from tests.synthetic.league_games import (
    POSTSEASON,
    REGULAR,
    Game,
    SeasonBuilder,
    fbs_pair,
    game_week_seq,
    iso,
    kickoff,
    week_seq,
)
from tests.synthetic.slices import Slice, SliceSet
from vs.cfbd_vs.plan import build_slice_key
from vs.cfbd_vs.replay import Recording

STORED_SEASONS = (1885, 1950, 1985, 2000, 2013, 2014, 2023, 2024, 2025, 2026)
CALENDAR_SEASONS = (2013, 2014, 2023, 2024, 2025, 2026)
LINE_SEASONS = (2013, 2014, 2024, 2025, 2026)
SP_SEASONS = (2013, 2014, 2023, 2024, 2025, 2026)
RANKING_SEASONS = (2024, 2025, 2026)
LAST_FINAL_SEASON = 2025
CURRENT_SEASON = 2026
LAST_COMPLETED_WEEK = 9
FETCHED_EARLY = datetime(2026, 10, 20, 10, 0)
FETCHED_2026 = datetime(2026, 10, 25, 12, 0)
FETCHED_2026_RANKINGS = datetime(2026, 10, 27, 23, 0)
FETCHED_LATER = datetime(2026, 10, 26, 8, 0)
FETCHED_LATER_RANKINGS = datetime(2026, 10, 28, 8, 0)
FETCHED_THURSDAY_RESULT = datetime(2026, 10, 30, 12, 0)
NOW_OVERRIDE = datetime(2026, 10, 28, 12, 0)

AP = "AP Top 25"
COACHES = "Coaches Poll"
COMMITTEE = "Playoff Committee Rankings"
MYSTERY = "Mystery Poll"
LEAD_WEEKS = {AP: 1, COACHES: 1, COMMITTEE: 0}
RANK_POOL = (
    "Ashford",
    "Brookvale",
    "Kingsley",
    "Granton",
    "Fillmore",
    "Hollis",
    "Carrow",
    "Quinton",
    "Eastport",
    "Ivel",
)
SP_OVERRIDES: dict[tuple[int, str], float] = {
    (2023, "Carrow"): 20.0,
    (2023, "Ashford"): 22.3,
    (2024, "Ashford"): 25.1,
    (2025, "Ashford"): 24.4,
    (2026, "Ashford"): 24.9,
}
SP_UNMATCHED = "Ashford St."
SP_NATIONAL_AVERAGES = "nationalAverages"
FIXED_LINES: dict[str, tuple[float, float]] = {"ashford_brookvale_2024": (-7.5, 0.70)}

Body = list[dict[str, Any]]


@dataclass(frozen=True)
class League(SliceSet):
    """The league's slices and the ids of the games that tests look up by name."""

    names: Mapping[str, int] = field(default_factory=dict)

    def game_id(self, name: str) -> int:
        """The game id behind a name such as `ashford_brookvale_2024`."""
        return self.names[name]

    def game_bodies(self, season: int) -> list[Game]:
        """The `/games` elements of a season, in response order (do not change them)."""
        return list(self.get("GAMES", season_key("GAMES", season)).body)

    def replay_recordings(self) -> list[Recording]:
        """The recordings of the 2026 weekly slices (games, lines, SP+, rankings)."""
        weekly = {"GAMES", "METRICS_WP_PREGAME", "RATINGS_SP", "RANKINGS_POLLS_RANKS"}
        return self.recordings(
            lambda item: (
                item.table in weekly and item.slice_key == season_key(item.table, CURRENT_SEASON)
            )
        )


@cache
def _templates() -> dict[tuple[str, str], SliceTemplate]:
    compiled = compile_plan(load_plan(), default_catalog())
    return {(item.table, item.slicing): item for item in compiled}


def season_key(table: str, season: int) -> str:
    """The slice key of the load plan's season entry of `table`."""
    return _templates()[(table, "season")].slice_key(season)


def static_key(table: str) -> str:
    return _templates()[(table, "none")].slice_key()


def range_key(table: str) -> str:
    return _templates()[(table, "season_range")].slice_key()


def request_key(table: str, **values: str | int) -> str:
    """The canonical slice key of any request of `table` (used for slices outside the plan)."""
    return build_slice_key(fake.catalog().table(table), values)


def fetched_at(season: int) -> datetime:
    return FETCHED_2026 if season == CURRENT_SEASON else FETCHED_EARLY


def is_final(season: int) -> bool:
    return season <= LAST_FINAL_SEASON


def fbs_pool(season: int, exclude: tuple[str, ...] = ()) -> list[str]:
    """FBS teams with a membership in `season`, recorded as `fbs` that year."""
    return [
        t.name
        for t in lt.TEAMS
        if t.spells
        and t.name not in exclude
        and t.current_spell(season) is not None
        and lt.recorded_classification(t.name, season) == lt.FBS
    ]


def base_league() -> League:
    """The league as described in the module docstring."""
    builders = {season: _build_season(season) for season in STORED_SEASONS}
    names: dict[str, int] = {}
    slices = _static_slices()
    for season, builder in builders.items():
        names.update(builder.names)
        slices.extend(_season_slices(season, builder))
    return League(slices=tuple(slices), names=names)


def _static_slices() -> list[Slice]:
    teams = [lt.team_body(t) for t in lt.TEAMS if t.in_team_list]
    affiliations = [
        lt.affiliation_body(t, spell)
        for t in lt.TEAMS
        for spell in t.spells
        if spell.start <= lt.RANGE_LAST_SEASON
    ]
    current = [
        lt.affiliation_body(t, spell)
        for t in lt.TEAMS
        if (spell := t.current_spell(CURRENT_SEASON)) is not None
    ]
    return [
        Slice("TEAMS", static_key("TEAMS"), teams, FETCHED_EARLY, True),
        Slice(
            "VENUES",
            static_key("VENUES"),
            [lt.venue_body(v) for v in lt.venues()],
            FETCHED_EARLY,
            True,
        ),
        Slice(
            "CONFERENCES", static_key("CONFERENCES"), lt.conference_bodies(), FETCHED_EARLY, True
        ),
        Slice(
            "CONFERENCES_AFFILIATIONS",
            range_key("CONFERENCES_AFFILIATIONS"),
            affiliations,
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


def _season_slices(season: int, builder: SeasonBuilder) -> list[Slice]:
    games = builder.finish()
    when, final = fetched_at(season), is_final(season)
    slices = [Slice("GAMES", season_key("GAMES", season), games, when, final)]
    if season in CALENDAR_SEASONS:
        slices.append(
            Slice(
                "CALENDAR",
                season_key("CALENDAR", season),
                calendar_body(season, games),
                when,
                final,
            )
        )
    if season in LINE_SEASONS:
        lines = _lines(season, games, builder.names)
        key = season_key("METRICS_WP_PREGAME", season)
        slices.append(Slice("METRICS_WP_PREGAME", key, lines, when, final))
    if season in SP_SEASONS:
        slices.append(
            Slice("RATINGS_SP", season_key("RATINGS_SP", season), _sp_rows(season), when, final)
        )
    if season in RANKING_SEASONS:
        slices.append(
            Slice(
                "RANKINGS_POLLS_RANKS",
                season_key("RANKINGS_POLLS_RANKS", season),
                _rankings(season),
                FETCHED_2026_RANKINGS if season == CURRENT_SEASON else when,
                final,
            )
        )
    return slices


def _build_season(season: int) -> SeasonBuilder:
    builder = SeasonBuilder(season)
    {
        1885: _season_1885,
        1950: _season_1950,
        1985: _season_1985,
        2000: _season_2000,
        2013: _season_2013,
        2014: _season_2014,
        2023: _season_2023,
        2024: _season_2024,
        2025: _season_2025,
        2026: _season_2026,
    }[season](builder)
    return builder


def _season_1885(b: SeasonBuilder) -> None:
    b.add("Ashford", "Marston", 1, points=(12, 0), venue=lt.OLD_GROUNDS, key="old_grounds_1885")
    b.add("Marston", "Ashford", 2, points=(0, 8), venue=lt.OLD_GROUNDS)


def _season_1950(b: SeasonBuilder) -> None:
    b.add("Ashford", "Lowell", 1, points=(21, 0))
    b.add("Brookvale", "Quinton", 2, points=(14, 3))
    b.add("Ashford", "Brookvale", 4, points=(7, 7), key="tie_1950")
    b.add("Brookvale", "Lowell", 5, key="unplayed_1950")


def _season_1985(b: SeasonBuilder) -> None:
    b.add("Ashford", "Lowell", 1, points=(35, 3), key="fbs_fcs_1985")
    b.add("Lowell", "Pemberton", 2, points=(17, 10))
    b.add("Ashford", "Quinton", 13, points=(24, 17))
    b.add("Brookvale", "Selby", 13, points=(21, 20))
    b.add("Ashford", "Brookvale", 14, points=(28, 14), key="sec_final_1985")


def _season_2000(b: SeasonBuilder) -> None:
    b.add("Ashford", "Hollis", 5, points=(28, 21), key="bcs_game_2000")


def _season_2013(b: SeasonBuilder) -> None:
    b.add("Eastport", "Fairhaven", 6, points=(31, 10), conference_game=False, key="fairhaven_2013")
    b.fill(25, list(range(1, 13)), fbs_pool(2013, ("Fairhaven",)))


def _season_2014(b: SeasonBuilder) -> None:
    b.add(
        "Brookvale",
        "Ashford",
        14,
        points=(27, 24),
        neutral=True,
        notes="SEC Championship",
        key="sec_championship_2014",
    )
    b.add(
        "Eastport",
        "Fairhaven",
        15,
        points=(35, 28),
        notes="Mountain West Championship",
        key="mw_championship_2014",
    )
    b.add("Gilmore", "Halsey", 15, points=(24, 21), key="mw_makeup_2014")
    b.add("Northam", "Marlow", 4, points=(20, 17), key="northam_2014_w4")
    b.add("Northam", "Ludlow", 8, points=(30, 13))
    b.fill(20, list(range(1, 14)), fbs_pool(2014))


def _season_2023(b: SeasonBuilder) -> None:
    b.add("Ivel", "Kenning", 13, points=(28, 14))
    b.add("Jessup", "Marlow", 13, points=(21, 17))
    b.add(
        "Ivel",
        "Jessup",
        14,
        points=(31, 28),
        neutral=True,
        notes="American Athletic Championship",
        key="aac_championship_2023",
    )
    b.add("Jessup", "Kenning", 15, points=(24, 20), conference_game=False, key="aac_week15_2023")
    b.add(
        "Carrow",
        "Eastport",
        1,
        season_type=POSTSEASON,
        day=6,
        points=(27, 24),
        neutral=True,
        notes="Desert Bowl",
        key="desert_bowl_2023",
    )


ASHFORD_2024 = (
    # week, opponent, home?, Ashford points, opponent points, conference game
    (1, "Lowell", True, 45, 3, False),
    (2, "Fillmore", False, 31, 17, False),
    (3, "Hollis", True, 38, 24, False),
    (4, "Granton", True, 20, 27, False),
    (5, "Quinton", True, 35, 14, True),
    (6, "Selby", False, 28, 21, True),
    (7, "Tolland", True, 17, 24, True),
    (8, "Uxbridge", False, 41, 10, True),
    (10, "Vance", True, 30, 13, True),
    (12, "Wexford", False, 27, 20, True),
    (13, "Yarrow", True, 44, 7, True),
    (14, "Brookvale", True, 31, 24, True),
)


def _season_2024(b: SeasonBuilder) -> None:
    for week, opponent, home, mine, theirs, conference in ASHFORD_2024:
        key = "ashford_brookvale_2024" if opponent == "Brookvale" else f"ashford_2024_w{week}"
        teams = ("Ashford", opponent) if home else (opponent, "Ashford")
        points = (mine, theirs) if home else (theirs, mine)
        b.add(*teams, week, points=points, conference_game=conference, key=key)
    b.add("Carrow", "Quinton", 14, points=(26, 23), key="sec_week14_other_2024")
    b.add(
        "Ashford",
        "Carrow",
        15,
        points=(28, 21),
        neutral=True,
        notes="SEC Championship",
        key="sec_championship_2024",
    )
    b.add("Dunmore", "Ruskin", 13, points=(24, 17))
    b.add("Sutton", "Thorne", 13, points=(20, 14))
    b.add("Dunmore", "Sutton", 14, points=(27, 24), key="pac12_final_2024")
    b.add(
        "Ludlow",
        "Marden",
        15,
        points=(17, 13),
        conference_game=False,
        key="independents_week15_2024",
    )
    _cfp_2024(b)
    b.fill(45, list(range(1, 16)), fbs_pool(2024, ("Ashford",)))


def _cfp_2024(b: SeasonBuilder) -> None:
    first_round = {
        "competition": "cfp",
        "format": "12-team",
        "round": "firstRound",
        "roundName": "First Round",
        "bracketSlot": "FR1",
        "homeSeed": 8,
        "awaySeed": 9,
    }
    quarterfinal = {
        "competition": "cfp",
        "format": "12-team",
        "round": "quarterfinal",
        "roundName": "Quarterfinal",
        "bracketSlot": "QF1",
        "homeSeed": 1,
        "awaySeed": 8,
        "bowlName": "Sugar Bowl",
    }
    b.add(
        "Kingsley",
        "Northam",
        1,
        season_type=POSTSEASON,
        points=(38, 17),
        playoff=first_round,
        key="cfp_first_round_2024",
    )
    b.add(
        "Brookvale",
        "Eastport",
        1,
        season_type=POSTSEASON,
        day=5,
        points=(30, 27),
        neutral=True,
        venue=lt.HARBOR_PARK,
        notes="Harbor Bowl",
        key="harbor_bowl_2024",
    )
    b.add(
        "Lowell",
        "Pemberton",
        1,
        season_type=POSTSEASON,
        day=1,
        points=(21, 14),
        notes="FCS Playoffs First Round",
        key="fcs_playoff_2024",
    )
    b.add(
        "Ashford",
        "Kingsley",
        1,
        season_type=POSTSEASON,
        day=11,
        points=(24, 31),
        neutral=True,
        notes="CFP Quarterfinal at the Sugar Bowl",
        playoff=quarterfinal,
        key="cfp_quarterfinal_2024",
    )


def _season_2025(b: SeasonBuilder) -> None:
    b.add(
        "Kingsley",
        "Eastport",
        1,
        season_type=POSTSEASON,
        day=5,
        points=(27, 24),
        neutral=True,
        venue=lt.HARBOR_PARK,
        notes="Harbor Bowl",
        key="harbor_bowl_2025",
    )
    b.fill(30, list(range(1, 16)), fbs_pool(2025))
    _championship_week_2025(b)


def _championship_week_2025(b: SeasonBuilder) -> None:
    """The power conferences' championship week 14, then an American Athletic game flagged as a
    conference game alone in week 15: the stored 2026 Army-Navy shape (cross-plan X1). Added after
    the filler games, so their pairs, ids and Elo stay as they were."""
    b.add("Quinton", "Selby", 13, points=(24, 20))
    b.add("Uxbridge", "Vance", 13, points=(17, 27))
    b.add(
        "Quinton",
        "Vance",
        14,
        points=(31, 27),
        neutral=True,
        notes="SEC Championship",
        key="sec_championship_2025",
    )
    b.add("Ivel", "Jessup", 13, points=(28, 21))
    b.add("Kenning", "Marlow", 13, points=(20, 17))
    b.add(
        "Ivel",
        "Kenning",
        14,
        points=(35, 31),
        neutral=True,
        notes="American Athletic Championship",
        key="aac_championship_2025",
    )
    b.add("Marlow", "Jessup", 15, points=(17, 13), key="aac_week15_2025")


ASHFORD_2026: tuple[tuple[int, str, bool, int | None, int | None, bool], ...] = (
    (1, "Lowell", True, 42, 7, False),
    (2, "Fillmore", False, 34, 20, False),
    (3, "Hollis", True, 17, 24, False),
    (4, "Quinton", True, 31, 17, True),
    (5, "Selby", False, 27, 20, True),
    (6, "Tolland", True, 38, 14, True),
    (7, "Uxbridge", False, 13, 21, True),
    (8, "Vance", True, 35, 10, True),
    (9, "Wexford", False, 24, 17, True),
    (10, "Yarrow", True, None, None, True),
    (12, "Brookvale", False, None, None, True),
    (13, "Pemberton", True, None, None, False),
)


def _season_2026(b: SeasonBuilder) -> None:
    for week, opponent, home, mine, theirs, conference in ASHFORD_2026:
        teams = ("Ashford", opponent) if home else (opponent, "Ashford")
        points: tuple[int, int] | None = None
        if mine is not None and theirs is not None:
            points = (mine, theirs) if home else (theirs, mine)
        key = "brookvale_ashford_2026_w12" if week == 12 else f"ashford_2026_w{week}"
        b.add(*teams, week, points=points, conference_game=conference, key=key)
    b.add("Quinton", "Selby", 12, conference_game=True)
    b.add("Carrow", "Dunmore", 3, points=(21, 14), key="carrow_dunmore_2026_w3")
    b.add("Granton", "Fillmore", 9, day=-2, clock="23:30", points=(27, 24), key="thursday_2026_w9")
    b.add("Kingsley", "Hollis", 10, day=-2, clock="23:30", key="thursday_2026_w10")
    b.add("Ivel", "Ruskin", 9, points=(31, 20), key="week9_game_2026")
    b.add_placeholder(
        15, key="sec_placeholder_2026", venue=lt.NEUTRAL_DOME, notes="SEC Championship"
    )
    b.fill(
        26, list(range(1, 16)), fbs_pool(2026, ("Ashford",)), completed_through=LAST_COMPLETED_WEEK
    )


def _lines(season: int, games: list[Game], names: Mapping[str, int]) -> Body:
    wanted = _line_game_ids(season, games, names)
    by_id = {game["id"]: game for game in games}
    fixed = {names[key]: value for key, value in FIXED_LINES.items() if key in names}
    rows = []
    for identifier in sorted(wanted):
        game = by_id[identifier]
        spread, probability = fixed.get(identifier) or _line_values(game)
        rows.append(
            {
                "season": season,
                "seasonType": game["seasonType"],
                "week": game["week"],
                "gameId": identifier,
                "homeTeam": game["homeTeam"],
                "awayTeam": game["awayTeam"],
                "spread": spread,
                "homeWinProbability": probability,
            }
        )
    return rows


def _line_values(game: Game) -> tuple[float, float]:
    home, away = game["homePregameElo"], game["awayPregameElo"]
    probability = round(1 / (1 + 10 ** ((away - home) / 400)), 2)
    spread = -round((probability - 0.5) * 80) / 2 + 0.0
    return spread, probability


def _line_game_ids(season: int, games: list[Game], names: Mapping[str, int]) -> set[int]:
    pairs = sorted((g for g in games if fbs_pair(g)), key=lambda g: g["id"])
    if season == 2013:
        completed = [g for g in pairs if g["completed"]]
        return {g["id"] for g in completed} - {completed[12]["id"]}
    if season == 2014:
        named = set(names.values())
        fillers = [g for g in pairs if g["id"] not in named]
        dropped = {g["id"] for index, g in enumerate(fillers) if index % 4 == 3}
        return {g["id"] for g in pairs} - dropped
    if season == 2024:
        return {g["id"] for g in pairs if g["week"] in (9, 10, 14) and g["seasonType"] == REGULAR}
    if season == 2025:
        return {g["id"] for g in pairs if g["week"] in (5, 6, 7, 8) and g["seasonType"] == REGULAR}
    return {g["id"] for g in pairs if g["week"] in (10, 11) and g["seasonType"] == REGULAR}


def calendar_body(season: int, games: list[Game]) -> Body:
    """The `/calendar` body derived from a season's games: one row per season type and week."""
    rows: Body = []
    weeks = sorted({(g["seasonType"], g["week"]) for g in games}, key=lambda w: week_seq(*w))
    for season_type, week in weeks:
        starts = sorted(
            g["startDate"] for g in games if (g["seasonType"], g["week"]) == (season_type, week)
        )
        if season_type == REGULAR:
            first = kickoff(season, REGULAR, week, -4, "00:00")
            last = first + timedelta(days=6, hours=23, minutes=59)
        else:
            first = datetime(season, 12, 15)
            last = datetime(season + 1, 1, 15, 23, 59)
        rows.append(
            {
                "season": season,
                "week": week,
                "seasonType": season_type,
                "startDate": iso(first),
                "endDate": iso(last),
                "firstGameStart": starts[0],
                "lastGameStart": starts[-1],
            }
        )
    return rows


def _sp_rows(season: int) -> Body:
    teams = [
        t for t in lt.TEAMS if t.spells and t.current_spell(season) and t.classification == lt.FBS
    ]
    rated = [(t, _sp_rating(season, t)) for t in teams]
    ordered = sorted(rated, key=lambda item: -item[1])
    rows = [
        sp_row(season, t.name, t.conference_in(season), rating, rank)
        for rank, (t, rating) in enumerate(ordered, 1)
    ]
    if season == 2024:
        rows.append(sp_row(season, SP_UNMATCHED, "SEC", 3.5, len(rows) + 1))
    rows.append(sp_row(season, SP_NATIONAL_AVERAGES, None, 0.0, None))
    return rows


def _sp_rating(season: int, definition: lt.TeamDef) -> float:
    fixed = SP_OVERRIDES.get((season, definition.name))
    return fixed if fixed is not None else ((definition.id * 37 + season) % 400) / 10 - 10


def sp_row(
    season: int, team: str, conference: str | None, rating: float, rank: int | None
) -> dict[str, Any]:
    seed = sum(map(ord, team)) + season
    return {
        "year": season,
        "team": team,
        "conference": conference,
        "rating": rating,
        "ranking": rank,
        "secondOrderWins": 6.0 + seed % 40 / 10,
        "sos": -1.0 + seed % 30 / 10,
        "offense": {
            "pace": 70.0 + seed % 10,
            "runRate": 0.5,
            "passingDowns": 1.0,
            "standardDowns": 1.1,
            "passing": 1.2,
            "rushing": 1.3,
            "explosiveness": 1.4,
            "success": 0.45,
            "rating": 28.0 + seed % 8,
            "ranking": 1 + seed % 100,
        },
        "defense": {
            "havoc": {"db": 0.05, "frontSeven": 0.08, "total": 0.13},
            "passingDowns": 1.0,
            "standardDowns": 1.1,
            "passing": 1.2,
            "rushing": 1.3,
            "explosiveness": 1.4,
            "success": 0.4,
            "rating": 25.0 + seed % 6,
            "ranking": 1 + seed % 90,
        },
        "specialTeams": {"rating": 0.5},
    }


def _poll_weeks(season: int) -> list[tuple[str, str, int]]:
    if season == 2024:
        return [
            *[(AP, REGULAR, w) for w in range(1, 17)],
            (AP, POSTSEASON, 1),
            *[(COMMITTEE, REGULAR, w) for w in range(10, 16)],
            (MYSTERY, REGULAR, 5),
        ]
    if season == 2025:
        return [
            *[(AP, REGULAR, w) for w in range(1, 16)],
            *[(COMMITTEE, REGULAR, w) for w in range(10, 15)],
            (COMMITTEE, POSTSEASON, 1),
        ]
    return [*[(AP, REGULAR, w) for w in range(1, 11)], (COMMITTEE, REGULAR, 10)]


def _rankings(season: int) -> Body:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for poll, season_type, week in _poll_weeks(season):
        grouped.setdefault((season_type, week), []).append(
            _poll_body(season, poll, season_type, week)
        )
    return [
        {"season": season, "seasonType": season_type, "week": week, "polls": polls}
        for (season_type, week), polls in sorted(
            grouped.items(), key=lambda item: week_seq(*item[0])
        )
    ]


def _poll_body(season: int, poll: str, season_type: str, week: int) -> dict[str, Any]:
    order = list(RANK_POOL)
    shift = (week + len(poll)) % len(order)
    order = order[shift:] + order[:shift]
    if poll == MYSTERY:
        order = order[:3]
    elif poll == COMMITTEE:
        order = order[:8]
    pinned = {(AP, 2024, POSTSEASON, 1): 5, (COMMITTEE, 2024, REGULAR, 15): 1}.get(
        (poll, season, season_type, week)
    )
    if pinned is not None:
        order = [name for name in order if name != "Ashford"]
        order.insert(pinned - 1, "Ashford")
    ranks = [
        {
            "rank": position,
            "teamId": lt.team(name).id,
            "school": name,
            "conference": lt.team(name).conference_in(season),
            "firstPlaceVotes": max(0, 10 - position) if poll == AP else None,
            "points": 1500 - 50 * position,
        }
        for position, name in enumerate(order, 1)
    ]
    return {"poll": poll, "isFinal": season_type == POSTSEASON, "ranks": ranks}


def _games_slice(league: League, season: int) -> Slice:
    return league.get("GAMES", season_key("GAMES", season))


def _replace(league: League, old: Slice, new: Slice) -> League:
    return league.with_slices(new if item is old else item for item in league.slices)


def with_nonfinal_prior(league: League) -> League:
    """The 2025 SP+ slice is registered as not final."""
    old = league.get("RATINGS_SP", season_key("RATINGS_SP", LAST_FINAL_SEASON))
    return _replace(league, old, dataclasses.replace(old, is_final=False))


def with_incomplete_week9_game(league: League) -> League:
    """The 2026 week-9 game `week9_game_2026` was never completed."""
    old = _games_slice(league, CURRENT_SEASON)
    body = copy.deepcopy(old.body)
    target = league.game_id("week9_game_2026")
    for game in body:
        if game["id"] == target:
            game.update(
                {
                    "completed": False,
                    "homePoints": None,
                    "awayPoints": None,
                    "homePostgameElo": None,
                    "awayPostgameElo": None,
                    "homePostgameWinProbability": None,
                    "awayPostgameWinProbability": None,
                    "attendance": None,
                    "excitementIndex": None,
                    "homeLineScores": None,
                    "awayLineScores": None,
                }
            )
    return _replace(league, old, dataclasses.replace(old, body=body))


def with_thursday_result(league: League) -> League:
    """The 2026 games re-fetched at 2026-10-30 12:00 UTC with week 10's Thursday game completed."""
    old = _games_slice(league, CURRENT_SEASON)
    body = copy.deepcopy(old.body)
    target = league.game_id("thursday_2026_w10")
    for game in body:
        if game["id"] == target:
            game.update(
                {
                    "completed": True,
                    "homePoints": 24,
                    "awayPoints": 17,
                    "homePostgameWinProbability": 0.85,
                    "awayPostgameWinProbability": 0.15,
                    "homePostgameElo": game["homePregameElo"] + 9,
                    "awayPostgameElo": game["awayPregameElo"] - 9,
                }
            )
    return _replace(
        league, old, dataclasses.replace(old, body=body, fetched_at=FETCHED_THURSDAY_RESULT)
    )


def with_unaffiliated_fbs_team(league: League) -> League:
    """Quarry, an FBS team without any conference, plays a completed 2026 game in week 8."""
    old = _games_slice(league, CURRENT_SEASON)
    body = copy.deepcopy(old.body)
    busy = {g["homeTeam"] for g in body if g["week"] == 8} | {
        g["awayTeam"] for g in body if g["week"] == 8
    }
    opponent = next(name for name in fbs_pool(CURRENT_SEASON) if name not in busy)
    rival = lt.team(opponent)
    identifier = CURRENT_SEASON * 1000 + 900
    body.append(
        {
            "id": identifier,
            "season": CURRENT_SEASON,
            "week": 8,
            "seasonType": REGULAR,
            "startDate": iso(kickoff(CURRENT_SEASON, REGULAR, 8, 0, "17:00")),
            "startTimeTBD": False,
            "completed": True,
            "neutralSite": False,
            "conferenceGame": False,
            "venueId": lt.VENUE_BY_NAME[lt.home_venue_name("Quarry")].id,
            "venue": lt.home_venue_name("Quarry"),
            "homeId": lt.QUARRY.id,
            "homeTeam": "Quarry",
            "homeConference": None,
            "homeClassification": lt.FBS,
            "homePoints": 21,
            "awayId": rival.id,
            "awayTeam": opponent,
            "awayConference": rival.conference_in(CURRENT_SEASON),
            "awayClassification": lt.FBS,
            "awayPoints": 14,
        }
    )
    teams = copy.deepcopy(league.get("TEAMS", static_key("TEAMS")).body)
    teams.append(lt.team_body(lt.QUARRY))
    changed = _replace(league, old, dataclasses.replace(old, body=body))
    old_teams = changed.get("TEAMS", static_key("TEAMS"))
    return _replace(changed, old_teams, dataclasses.replace(old_teams, body=teams))


def with_renamed_conference(league: League) -> League:
    """From 2024 on, the SEC is stored as "Southeastern" in memberships and on 2024 games."""
    renamed = "Southeastern"
    old = league.get("CONFERENCES_AFFILIATIONS", range_key("CONFERENCES_AFFILIATIONS"))
    body: list[dict[str, Any]] = []
    for row in old.body:
        if row["conference"] != "SEC" or (row["endYear"] is not None and row["endYear"] < 2024):
            body.append(row)
        elif row["startYear"] >= 2024:
            body.append({**row, "conference": renamed})
        else:
            body.append({**row, "endYear": 2023})
            body.append({**row, "conference": renamed, "startYear": 2024})
    changed = _replace(league, old, dataclasses.replace(old, body=body))
    games = _games_slice(changed, 2024)
    games_body = copy.deepcopy(games.body)
    for game in games_body:
        for side in ("home", "away"):
            if game[f"{side}Conference"] == "SEC":
                game[f"{side}Conference"] = renamed
    return _replace(changed, games, dataclasses.replace(games, body=games_body))


def with_duplicate_rows(league: League) -> League:
    """Later slices outside the load plan that store a 2026 game, line, SP+ rating and poll twice.

    The week-3 game Carrow vs Dunmore is fetched again as 21-17 with the line -3.5 (home win
    probability 0.60); its first line, -3.0 (0.58), is in the season's line slice. Ashford's 2026
    SP+ is 24.0 in a team slice; the AP poll of week 9 comes again in a week slice. Only view-level
    tests use this variant.
    """
    target = league.game_id("carrow_dunmore_2026_w3")
    game = copy.deepcopy(next(g for g in league.game_bodies(CURRENT_SEASON) if g["id"] == target))
    game.update({"homePoints": 21, "awayPoints": 17})
    line = {
        "season": CURRENT_SEASON,
        "seasonType": REGULAR,
        "week": 3,
        "gameId": target,
        "homeTeam": "Carrow",
        "awayTeam": "Dunmore",
        "spread": -3.5,
        "homeWinProbability": 0.6,
    }
    season_lines = league.get(
        "METRICS_WP_PREGAME", season_key("METRICS_WP_PREGAME", CURRENT_SEASON)
    )
    first_line = {**line, "spread": -3.0, "homeWinProbability": 0.58}
    league = _replace(
        league,
        season_lines,
        dataclasses.replace(season_lines, body=[*season_lines.body, first_line]),
    )
    ashford = next(r for r in _sp_rows(CURRENT_SEASON) if r["team"] == "Ashford")
    ashford = {**ashford, "rating": 24.0}
    rankings = league.get(
        "RANKINGS_POLLS_RANKS", season_key("RANKINGS_POLLS_RANKS", CURRENT_SEASON)
    )
    week9 = next(
        {**entry, "polls": [p for p in entry["polls"] if p["poll"] == AP]}
        for entry in rankings.body
        if entry["week"] == 9 and entry["seasonType"] == REGULAR
    )
    extra = [
        Slice(
            "GAMES",
            request_key("GAMES", year=CURRENT_SEASON, week=3, seasonType=REGULAR),
            [game],
            FETCHED_LATER,
            False,
        ),
        Slice(
            "METRICS_WP_PREGAME",
            request_key("METRICS_WP_PREGAME", year=CURRENT_SEASON, week=3, seasonType=REGULAR),
            [line],
            FETCHED_LATER,
            False,
        ),
        Slice(
            "RATINGS_SP",
            request_key("RATINGS_SP", year=CURRENT_SEASON, team="Ashford"),
            [ashford],
            FETCHED_LATER,
            False,
        ),
        Slice(
            "RANKINGS_POLLS_RANKS",
            request_key(
                "RANKINGS_POLLS_RANKS", year=CURRENT_SEASON, week=9, seasonType=REGULAR, poll=AP
            ),
            [week9],
            FETCHED_LATER_RANKINGS,
            False,
        ),
    ]
    return league.with_slices([*league.slices, *extra])


def mutate_after(league: League, season: int, point: int) -> League:
    """Change every fact of `season` that is not visible at the as-of point (`point` is the week
    sequence: 0 preseason, week n, 100 + n). Visible facts, and the first kickoff of the next
    week, stay as they are; see `model/as-of` ("No fact after the point changes an as-of answer").
    """
    games_slice = _games_slice(league, season)
    games: list[Game] = copy.deepcopy(games_slice.body)
    sequences = sorted({game_week_seq(g) for g in games})
    following = next((s for s in sequences if s > point), None)
    cutoff = None if following is None else _first_kickoff(games, following)
    last_regular = max((s for s in sequences if s < 100), default=0)
    visible_games = following if following is not None else point
    for game in games:
        _hide_results(game, point, following)
        _swap_participants(game, season, visible_games, point)
    if point < last_regular:
        games.append(_added_bowl(season))
    result = _replace(league, games_slice, dataclasses.replace(games_slice, body=games))
    lookup = {g["id"]: game_week_seq(g) for g in games_slice.body}
    result = _mutate_lines(result, season, point, following, cutoff, lookup)
    result = _mutate_rankings(result, season, point, cutoff, last_regular)
    result = _mutate_sp(result, season, cutoff)
    result = _mutate_calendar(result, season, following)
    return result


def _first_kickoff(games: list[Game], sequence: int) -> datetime:
    starts = [g["startDate"] for g in games if game_week_seq(g) == sequence]
    return datetime.strptime(min(starts), "%Y-%m-%dT%H:%M:%S.000Z")


def _hide_results(game: Game, point: int, following: int | None) -> None:
    sequence = game_week_seq(game)
    if sequence > point:
        game["completed"] = not game["completed"]
        game["homePoints"] = (game.get("homePoints") or 0) + 7
        game["awayPoints"] = (game.get("awayPoints") or 0) + 3
        for column in ("homePostgameElo", "awayPostgameElo"):
            game[column] = (game.get(column) or 1500) + 13
        for column in ("homePostgameWinProbability", "awayPostgameWinProbability"):
            game[column] = 0.5
        game["attendance"] = (game.get("attendance") or 0) + 1
        game["excitementIndex"] = (game.get("excitementIndex") or 0.0) + 1.0
    if following is None or sequence > following:
        for column in ("homePregameElo", "awayPregameElo"):
            if game.get(column) is not None:
                game[column] += 11


def _swap_participants(game: Game, season: int, visible_through: int, point: int) -> None:
    sequence = game_week_seq(game)
    championship = (
        game.get("conferenceGame")
        and game.get("homeId") is not None
        and "Championship" in (game.get("notes") or "")
        and game["seasonType"] == REGULAR
        and sequence > visible_through
    )
    quarterfinal = (game.get("playoff") or {}).get("round") == "quarterfinal" and sequence > point
    if championship:
        conference = game["homeConference"]
        others = [
            t.name
            for t in lt.TEAMS
            if t.conference_in(season) == conference
            and t.name not in (game["homeTeam"], game["awayTeam"])
        ]
        if len(others) >= 2:
            _set_team(game, "home", others[0], season)
            _set_team(game, "away", others[1], season)
    elif quarterfinal:
        _set_team(game, "home", "Hollis", season)


def _set_team(game: Game, side: str, name: str, season: int) -> None:
    definition = lt.team(name)
    game[f"{side}Id"] = definition.id
    game[f"{side}Team"] = name
    game[f"{side}Conference"] = definition.conference_in(season)
    game[f"{side}Classification"] = lt.recorded_classification(name, season)


def _added_bowl(season: int) -> Game:
    return {
        "id": season * 1000 + 950,
        "season": season,
        "week": 1,
        "seasonType": POSTSEASON,
        "startDate": iso(kickoff(season, POSTSEASON, 1, 3, "20:00")),
        "startTimeTBD": False,
        "completed": False,
        "neutralSite": True,
        "conferenceGame": False,
        "venueId": lt.VENUE_BY_NAME[lt.NEUTRAL_DOME].id,
        "venue": lt.NEUTRAL_DOME,
        "homeId": lt.team("Gilmore").id,
        "homeTeam": "Gilmore",
        "homeConference": lt.team("Gilmore").conference_in(season),
        "homeClassification": lt.FBS,
        "awayId": lt.team("Marlow").id,
        "awayTeam": "Marlow",
        "awayConference": lt.team("Marlow").conference_in(season),
        "awayClassification": lt.FBS,
        "notes": "Added Bowl",
    }


def _mutate_slices(
    league: League, table: str, season: int, change: Callable[[Slice, Any], Any]
) -> League:
    result = league
    for item in league.of(table):
        if f"year={season}" in item.slice_key:
            body = change(item, copy.deepcopy(item.body))
            result = _replace(result, item, dataclasses.replace(item, body=body))
    return result


def _mutate_lines(
    league: League,
    season: int,
    point: int,
    following: int | None,
    cutoff: datetime | None,
    sequences: Mapping[int, int],
) -> League:
    def change(item: Slice, body: Body) -> Body:
        for row in body:
            sequence = sequences.get(row["gameId"], 0)
            known_in_time = (
                sequence == following and cutoff is not None and item.fetched_at < cutoff
            )
            if sequence > point and not known_in_time:
                row["spread"] += 1.5
                row["homeWinProbability"] = min(0.99, row["homeWinProbability"] + 0.05)
        return body

    return _mutate_slices(league, "METRICS_WP_PREGAME", season, change)


def _mutate_rankings(
    league: League, season: int, point: int, cutoff: datetime | None, last_regular: int
) -> League:
    def change(item: Slice, body: Body) -> Body:
        in_time = cutoff is not None and item.fetched_at < cutoff
        for entry in body:
            sequence = week_seq(entry["seasonType"], entry["week"])
            for poll in entry["polls"]:
                if in_time or _poll_visible_at(poll["poll"], sequence, last_regular) <= point:
                    continue
                poll["ranks"].reverse()
                for rank in poll["ranks"]:
                    rank["points"] += 1
        return body

    return _mutate_slices(league, "RANKINGS_POLLS_RANKS", season, change)


def _poll_visible_at(poll: str, sequence: int, last_regular: int) -> int:
    if poll == COMMITTEE and sequence >= 100:
        return last_regular
    if sequence >= 100:
        return sequence
    return sequence - LEAD_WEEKS.get(poll, 0)


def _mutate_sp(league: League, season: int, cutoff: datetime | None) -> League:
    result = league
    for item in league.of("RATINGS_SP"):
        if item.slice_key != season_key("RATINGS_SP", season):
            continue
        body = copy.deepcopy(item.body)
        if cutoff is not None and item.fetched_at >= cutoff:
            for row in body:
                row["rating"] += 1.0
        result = _replace(result, item, dataclasses.replace(item, body=body))
    previous = season_key("RATINGS_SP", season - 1)
    for item in result.of("RATINGS_SP"):
        if item.slice_key == previous:
            result = _replace(result, item, dataclasses.replace(item, is_final=not item.is_final))
    return result


def _mutate_calendar(league: League, season: int, following: int | None) -> League:
    key = season_key("CALENDAR", season)
    if not any(item.slice_key == key for item in league.slices):
        return league
    old = league.get("CALENDAR", key)
    body = copy.deepcopy(old.body)
    for row in body:
        if following is not None and week_seq(row["seasonType"], row["week"]) > following:
            for column in ("startDate", "endDate", "firstGameStart", "lastGameStart"):
                row[column] = iso(
                    datetime.strptime(row[column], "%Y-%m-%dT%H:%M:%S.000Z") + timedelta(days=1)
                )
    return _replace(league, old, dataclasses.replace(old, body=body))
