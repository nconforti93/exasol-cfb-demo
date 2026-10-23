"""Test oracles that restate model rules over mirror rows (dicts keyed by column name)."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any

Row = Mapping[str, Any]
Ranges = Mapping[str, Sequence[tuple[int, int | None]]]
PowerConferences = Callable[[int], Collection[str]]


def detect_championships(rows: Sequence[Row], seeded: Ranges, power: PowerConferences) -> set[int]:
    """Game ids found by the rule of `model/games`: inside a conference's seeded seasons, the only
    conference game of the conference's last regular week with such a game, looking only at weeks
    up to the season's power-conference championship week.

    That week is the last regular week in which a conference of `power(season)` plays a flagged
    conference game between two of its teams; a season without one has no limit.

    Fallback (decision [36]): when that finds nothing for a conference, the same rule over every
    game between two of its teams, flagged or not, up to the last week of any such game of a power
    conference; a season without one has no fallback.
    """
    found: set[int] = set()
    for season in {row["SEASON"] for row in rows}:
        same_conference = [
            row
            for row in rows
            if row["SEASON"] == season
            and row["SEASON_TYPE"] == "regular"
            and row["HOME_CONFERENCE"] is not None
            and row["HOME_CONFERENCE"] == row["AWAY_CONFERENCE"]
        ]
        powers = power(season)
        power_games = [row for row in same_conference if row["HOME_CONFERENCE"] in powers]
        limit = max((row["WEEK"] for row in power_games if row["CONFERENCE_GAME"]), default=None)
        any_limit = max((row["WEEK"] for row in power_games), default=None)
        for conference, ranges in seeded.items():
            if not any(
                first <= season and (last is None or season <= last) for first, last in ranges
            ):
                continue
            mine = [row for row in same_conference if row["HOME_CONFERENCE"] == conference]
            flagged = [
                row
                for row in mine
                if row["CONFERENCE_GAME"] and (limit is None or row["WEEK"] <= limit)
            ]
            game = _alone_in_last_week(flagged)
            if game is None and any_limit is not None:
                game = _alone_in_last_week([row for row in mine if row["WEEK"] <= any_limit])
            if game is not None:
                found.add(game["ID"])
    return found


def _alone_in_last_week(candidates: Sequence[Row]) -> Row | None:
    if not candidates:
        return None
    last_week = max(row["WEEK"] for row in candidates)
    final = [row for row in candidates if row["WEEK"] == last_week]
    return final[0] if len(final) == 1 else None
