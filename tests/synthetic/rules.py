"""Test oracles that restate model rules over mirror rows (dicts keyed by column name)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

Row = Mapping[str, Any]
Ranges = Mapping[str, Sequence[tuple[int, int | None]]]


def detect_championships(rows: Sequence[Row], seeded: Ranges) -> set[int]:
    """Game ids found by the rule of `model/games`: inside a conference's seeded seasons, the only
    conference game of the conference's last regular week with such a game."""
    found: set[int] = set()
    for season in {row["SEASON"] for row in rows}:
        for conference, ranges in seeded.items():
            if not any(
                first <= season and (last is None or season <= last) for first, last in ranges
            ):
                continue
            candidates = [
                row
                for row in rows
                if row["SEASON"] == season
                and row["SEASON_TYPE"] == "regular"
                and row["CONFERENCE_GAME"]
                and row["HOME_CONFERENCE"] == row["AWAY_CONFERENCE"] == conference
            ]
            if not candidates:
                continue
            last_week = max(row["WEEK"] for row in candidates)
            final = [row for row in candidates if row["WEEK"] == last_week]
            if len(final) == 1:
                found.add(final[0]["ID"])
    return found
