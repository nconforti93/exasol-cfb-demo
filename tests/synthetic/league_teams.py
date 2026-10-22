"""The fictional teams, conference spells and venues of the synthetic league.

Only the conference names are real (they are CFBD's names and the keys of the model's reference
tables); every team, school and stadium is made up. Spells are what the affiliations endpoint
would return: a team, a conference, a division and `startYear` to `endYear` (open while None).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FBS = "fbs"
FCS = "fcs"
INDEPENDENTS = "FBS Independents"
BIG_SKY = "Big Sky"
RANGE_FIRST_SEASON = 1869
RANGE_LAST_SEASON = 2025
SLICE_SEASON = 2026

CONFERENCE_NAMES = (
    "SEC",
    "ACC",
    "Big Ten",
    "Big 12",
    "Pac-10",
    "Pac-12",
    "Big East",
    "American Athletic",
    "Mountain West",
    "Sun Belt",
    "Conference USA",
    "Mid-American",
    "Western Athletic",
    INDEPENDENTS,
    BIG_SKY,
)
FCS_CONFERENCES = frozenset({BIG_SKY})


@dataclass(frozen=True)
class Spell:
    """One conference membership as the affiliations endpoint reports it."""

    conference: str
    start: int
    end: int | None = None
    division: str | None = None

    def covers(self, season: int) -> bool:
        return self.start <= season and (self.end is None or season <= self.end)


@dataclass(frozen=True)
class TeamDef:
    """A team: CFBD id, school name, the classification it is recorded with, its spells."""

    id: int
    name: str
    classification: str
    spells: tuple[Spell, ...] = ()
    recorded_conference: str | None = None
    in_team_list: bool = True

    def conference_in(self, season: int) -> str | None:
        """The conference of the latest spell that covers `season`, else the recorded one."""
        covering = [spell for spell in self.spells if spell.covers(season)]
        if covering:
            return max(covering, key=lambda spell: spell.start).conference
        return self.recorded_conference

    def current_spell(self, season: int) -> Spell | None:
        covering = [spell for spell in self.spells if spell.covers(season)]
        return max(covering, key=lambda spell: spell.start) if covering else None


def _team(
    number: int,
    name: str,
    *spells: Spell,
    classification: str = FBS,
    recorded: str | None = None,
    listed: bool = True,
) -> TeamDef:
    return TeamDef(1000 + number, name, classification, spells, recorded, listed)


_SEC = (Spell("SEC", 1933),)

TEAMS: tuple[TeamDef, ...] = (
    _team(1, "Ashford", *_SEC),
    _team(2, "Brookvale", *_SEC),
    _team(3, "Quinton", *_SEC),
    _team(4, "Selby", *_SEC),
    _team(5, "Tolland", *_SEC),
    _team(6, "Uxbridge", *_SEC),
    _team(7, "Vance", *_SEC),
    _team(8, "Wexford", *_SEC),
    _team(9, "Yarrow", *_SEC),
    _team(
        10,
        "Carrow",
        Spell("Pac-10", 1978, 2010),
        Spell("Pac-12", 2011, 2023),
        Spell("SEC", 2024),
    ),
    _team(
        11,
        "Dunmore",
        Spell("Pac-10", 1978, 2010),
        Spell("Pac-12", 2011),
        Spell("Mountain West", SLICE_SEASON),
    ),
    _team(12, "Ruskin", Spell("Pac-12", 2011)),
    _team(13, "Sutton", Spell("Pac-12", 2011)),
    _team(14, "Thorne", Spell("Pac-12", 2011)),
    _team(15, "Fillmore", Spell("ACC", 1953)),
    _team(16, "Granton", Spell("Big Ten", 1896)),
    _team(17, "Kingsley", Spell("Big Ten", 1990)),
    _team(18, "Hollis", Spell("Big 12", 1996)),
    _team(19, "Ivel", Spell("Big East", 1991, 2012), Spell("American Athletic", 2013)),
    _team(20, "Jessup", Spell("American Athletic", 2013)),
    _team(21, "Kenning", Spell("American Athletic", 2013)),
    _team(22, "Marlow", Spell("American Athletic", 2013)),
    _team(
        23,
        "Eastport",
        Spell("Mountain West", 1999, 2022, "Mountain"),
        Spell("Mountain West", 2023),
    ),
    _team(24, "Fairhaven", Spell("Mountain West", 2013, None, "West")),
    _team(25, "Gilmore", Spell("Mountain West", 2013, None, "Mountain")),
    _team(26, "Halsey", Spell("Mountain West", 2013, None, "West")),
    _team(27, "Northam", Spell("Mountain West", 2010, 2014), Spell("Sun Belt", 2014)),
    _team(28, "Ludlow", Spell(INDEPENDENTS, 1990)),
    _team(29, "Marden", Spell(INDEPENDENTS, 1990)),
    _team(30, "Lowell", classification=FCS, recorded=BIG_SKY),
    _team(31, "Pemberton", classification=FCS, recorded=BIG_SKY),
    _team(32, "Marston", listed=False),
)
QUARRY = TeamDef(1099, "Quarry", FBS)

BY_NAME: dict[str, TeamDef] = {team.name: team for team in TEAMS}

CLASSIFICATION_OVERRIDES: dict[tuple[str, int], str] = {("Fairhaven", 2013): FCS}


def team(name: str) -> TeamDef:
    return QUARRY if name == QUARRY.name else BY_NAME[name]


def recorded_classification(name: str, season: int) -> str:
    """The code CFBD records on the team's games in `season`."""
    return CLASSIFICATION_OVERRIDES.get((name, season), team(name).classification)


@dataclass(frozen=True)
class VenueDef:
    id: int
    name: str
    latitude: float | None
    longitude: float | None
    dome: bool = False


FIRST_VENUE_ID = 5001
OLD_GROUNDS = "Old Grounds"
NEUTRAL_DOME = "Neutral Dome"
HARBOR_PARK = "Harbor Park"
HOME_FIELDS = {"Ashford": "Ashford Field", "Brookvale": "Brookvale Bowl"}
FIXED_COORDINATES: dict[str, tuple[float | None, float | None]] = {
    "Ashford Field": (33.0, -87.0),
    "Brookvale Bowl": (34.0, -87.0),
    NEUTRAL_DOME: (29.95, -90.08),
    HARBOR_PARK: (40.0, -74.0),
    OLD_GROUNDS: (None, None),
}


def home_venue_name(name: str) -> str:
    return HOME_FIELDS.get(name, f"{name} Stadium")


def venues() -> tuple[VenueDef, ...]:
    """Every venue: one per team and the special ones. Only Ashford Field and Brookvale Bowl lie
    between 32 and 35 degrees north and 88 and 85 degrees west."""
    names = [home_venue_name(t.name) for t in (*TEAMS, QUARRY) if t.name != "Marston"]
    names += [NEUTRAL_DOME, HARBOR_PARK, OLD_GROUNDS]
    result = []
    for index, name in enumerate(names):
        latitude, longitude = FIXED_COORDINATES.get(
            name, (36.0 + (index * 7) % 13, -120.0 + (index * 11) % 45)
        )
        result.append(
            VenueDef(FIRST_VENUE_ID + index, name, latitude, longitude, dome=name == NEUTRAL_DOME)
        )
    return tuple(result)


VENUE_BY_NAME: dict[str, VenueDef] = {venue.name: venue for venue in venues()}


def venue_body(venue: VenueDef) -> dict[str, Any]:
    """A `/venues` element."""
    return {
        "id": venue.id,
        "name": venue.name,
        "city": venue.name.split()[0],
        "state": "AL",
        "zip": "35000",
        "countryCode": "US",
        "timezone": "America/Chicago",
        "latitude": venue.latitude,
        "longitude": venue.longitude,
        "elevation": None if venue.latitude is None else "100.0",
        "capacity": 40000 + venue.id % 30000,
        "constructionYear": 1900 + venue.id % 100,
        "grass": not venue.dome,
        "dome": venue.dome,
    }


def conference_id(name: str) -> int:
    return 100 + CONFERENCE_NAMES.index(name)


def conference_bodies() -> list[dict[str, Any]]:
    """The `/conferences` body."""
    return [
        {
            "id": conference_id(name),
            "name": name,
            "shortName": name,
            "abbreviation": "".join(word[0] for word in name.split()).upper(),
            "classification": FCS if name in FCS_CONFERENCES else FBS,
        }
        for name in CONFERENCE_NAMES
    ]


def team_body(definition: TeamDef) -> dict[str, Any]:
    """A `/teams` element: the team as of the last load of the team list."""
    venue = VENUE_BY_NAME[home_venue_name(definition.name)]
    spell = definition.current_spell(SLICE_SEASON)
    return {
        "id": definition.id,
        "school": definition.name,
        "mascot": f"{definition.name} Foxes",
        "abbreviation": definition.name[:4].upper(),
        "alternateNames": [definition.name[:3]],
        "conference": spell.conference if spell else definition.recorded_conference,
        "division": spell.division if spell else None,
        "classification": definition.classification,
        "color": "#336699",
        "alternateColor": "#ffffff",
        "logos": [f"https://example.invalid/logos/{definition.id}.png"],
        "twitter": f"@{definition.name}",
        "location": {
            "id": venue.id,
            "name": venue.name,
            "city": definition.name,
            "state": "AL",
            "zip": "35000",
            "countryCode": "US",
            "timezone": "America/Chicago",
            "latitude": venue.latitude,
            "longitude": venue.longitude,
            "elevation": "100.0",
            "capacity": 40000 + venue.id % 30000,
            "constructionYear": 1900 + venue.id % 100,
            "grass": not venue.dome,
            "dome": venue.dome,
        },
    }


def affiliation_body(definition: TeamDef, spell: Spell) -> dict[str, Any]:
    """An `/conferences/affiliations` element."""
    return {
        "teamId": definition.id,
        "team": definition.name,
        "conferenceId": conference_id(spell.conference),
        "conference": spell.conference,
        "conferenceAbbreviation": spell.conference[:4].upper(),
        "classification": definition.classification,
        "conferenceDivision": spell.division,
        "startYear": spell.start,
        "endYear": spell.end,
    }
