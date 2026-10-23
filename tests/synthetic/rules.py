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
    """
    found: set[int] = set()
    for season in {row["SEASON"] for row in rows}:
        conference_games = [
            row
            for row in rows
            if row["SEASON"] == season
            and row["SEASON_TYPE"] == "regular"
            and row["CONFERENCE_GAME"]
            and row["HOME_CONFERENCE"] is not None
            and row["HOME_CONFERENCE"] == row["AWAY_CONFERENCE"]
        ]
        powers = power(season)
        limit = max(
            (row["WEEK"] for row in conference_games if row["HOME_CONFERENCE"] in powers),
            default=None,
        )
        for conference, ranges in seeded.items():
            if not any(
                first <= season and (last is None or season <= last) for first, last in ranges
            ):
                continue
            candidates = [
                row
                for row in conference_games
                if row["HOME_CONFERENCE"] == conference and (limit is None or row["WEEK"] <= limit)
            ]
            if not candidates:
                continue
            last_week = max(row["WEEK"] for row in candidates)
            final = [row for row in candidates if row["WEEK"] == last_week]
            if len(final) == 1:
                found.add(final[0]["ID"])
    return found
