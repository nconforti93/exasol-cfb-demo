"""Crafted engine models for the unit tests of `engine/core.py` (no database, no CFBD calls).

`Crafted.columns` turns teams, remaining games, played conference games and parameters into the
tagged model rows of one SET call as `ctx.get_dataframe` hands them to the engine: DECIMAL values
as `Decimal`, NULL as None, rows in a shuffled order. `league` builds a seeded league of
conferences, independents and non-FBS opponents (ideas from the S3 synthetic season); `ranked`
builds a finished regular season whose resume order and champions are known in advance.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

import numpy as np

from engine import core

BASE_PARAMS: dict[str, float] = {
    "hfa": 2.5,
    "game_sigma": 15.0,
    "post_team_sigma": 2.0,
    "ot_margin": 3,
    "margin_cap": 24,
    "w_win_pct": 100,
    "w_sos": 2.0,
    "w_pd": 0.5,
    "w_rating": 0,
    "auto_bid_rule": core.AUTO_BID_POWER_CHAMPIONS_PLUS_G6,
    "auto_bids": 5,
    "g6_bid": core.G6_BID_TEAM,
    "guarantee_team_idx": -1,
    "guarantee_rank": 12,
    "min_conference_teams": 6,
}
TEAM_ID_BASE = 1000
RATING_PRIOR = {core.TIER_P4: (6.0, 9.0), core.TIER_G6: (-8.0, 7.0), core.TIER_IND: (4.0, 8.0)}
NON_FBS_RATING = (-25.0, 6.0)
PLAYED_SIGMA = 15.0
RANKED_WINS, RANKED_LOSSES = 8, 4
RANKED_CHAMPION_RECORD, RANKED_OTHER_RECORD = (5, 0), (2, 3)


@dataclass(frozen=True)
class Team:
    conference: str | None = None
    tier: str = core.TIER_IND
    rating: float = 0.0
    sos: float = 0.0
    wins: int = 0
    losses: int = 0
    conf_wins: int = 0
    conf_losses: int = 0
    conf_pd: int = 0
    capped_pd: int = 0
    has_ccg: bool = False
    eligible: bool = True


@dataclass(frozen=True)
class Game:
    home: int
    away: int
    mu: float = 0.0
    team_sigma: float = 0.0
    conf_game: bool = False


@dataclass(frozen=True)
class Played:
    home: int
    away: int
    margin: int


@dataclass(frozen=True)
class Conference:
    name: str
    tier: str
    size: int
    conf_games: int
    has_ccg: bool = True


@dataclass(frozen=True, eq=False)
class Simulated:
    model: core.Model
    result: core.BatchResult
    stage: dict[str, core.Array]

    def rows(self, kind: str) -> dict[str, core.Array]:
        """The stage columns of one row kind."""
        mask = self.stage["ROW_KIND"] == kind
        return {name: values[mask] for name, values in self.stage.items()}


@dataclass(frozen=True)
class Crafted:
    teams: tuple[Team, ...]
    games: tuple[Game, ...] = ()
    played: tuple[Played, ...] = ()
    params: Mapping[str, float] = field(default_factory=lambda: dict(BASE_PARAMS))

    def with_params(self, **changes: float) -> Crafted:
        return replace(self, params={**self.params, **changes})

    def without_param(self, key: str) -> Crafted:
        return replace(self, params={k: v for k, v in self.params.items() if k != key})

    def columns(
        self, seed: int = 1, sims: int = 100, batch: int = 0, run_id: int = 1, shuffle: int = 0
    ) -> dict[str, list[Any]]:
        """The SET group's input columns, rows in a shuffled order."""
        rows = [*self._team_rows(), *self._game_rows(), *self._param_rows()]
        order = np.random.default_rng(shuffle).permutation(len(rows)).tolist()
        run = {"RUN_ID": run_id, "RUN_SEED": seed, "SIMS_PER_BATCH": sims, "BATCH_ID": batch}
        names = [name for name, _ in core.RUN_COLUMNS + core.MODEL_COLUMNS]
        return {
            name: [run[name] if name in run else rows[i].get(name) for i in order] for name in names
        }

    def simulate(self, seed: int = 1, sims: int = 100, batch: int = 0) -> Simulated:
        columns = self.columns(seed=seed, sims=sims, batch=batch)
        run = core.parse_run(columns)
        model = core.parse_model(columns)
        result = core.simulate_batch(model, run)
        return Simulated(model, result, core.stage_columns(run, model, result, "host"))

    def _team_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "ROW_KIND": core.KIND_TEAM,
                "TEAM_IDX": idx,
                "TEAM_ID": TEAM_ID_BASE + idx,
                "CONFERENCE": team.conference,
                "TIER": team.tier,
                "HAS_CCG": team.has_ccg,
                "IS_ELIGIBLE": team.eligible,
                "RATING": _decimal(team.rating, 2),
                "SOS": _decimal(team.sos, 2),
                "WINS": team.wins,
                "LOSSES": team.losses,
                "CONF_WINS": team.conf_wins,
                "CONF_LOSSES": team.conf_losses,
                "CONF_PD": team.conf_pd,
                "CAPPED_PD": team.capped_pd,
            }
            for idx, team in enumerate(self.teams)
        ]

    def _game_rows(self) -> list[dict[str, Any]]:
        remaining = [
            {
                "ROW_KIND": core.KIND_GAME,
                "GAME_ROLE": core.ROLE_REMAINING,
                "GAME_IDX": idx,
                "HOME_IDX": game.home,
                "AWAY_IDX": game.away,
                "CONF_GAME": game.conf_game,
                "MU": _decimal(game.mu, 2),
                "TEAM_SIGMA": _decimal(game.team_sigma, 2),
            }
            for idx, game in enumerate(self.games)
        ]
        played = [
            {
                "ROW_KIND": core.KIND_GAME,
                "GAME_ROLE": core.ROLE_PLAYED_CONF,
                "GAME_IDX": idx,
                "HOME_IDX": game.home,
                "AWAY_IDX": game.away,
                "MARGIN": game.margin,
            }
            for idx, game in enumerate(self.played)
        ]
        return remaining + played

    def _param_rows(self) -> list[dict[str, Any]]:
        return [
            {"ROW_KIND": core.KIND_PARAM, "PARAM_KEY": key, "PARAM_VALUE": _decimal(value, 4)}
            for key, value in sorted(self.params.items())
        ]


def _decimal(value: float, places: int) -> Decimal:
    return Decimal(f"{value:.{places}f}")


def league(
    conferences: Sequence[Conference],
    independents: int = 2,
    non_fbs: int = 0,
    games_per_team: int = 12,
    played_share: float = 0.0,
    weeks: int = 6,
    tau: float = 1.5,
    seed: int = 1,
) -> Crafted:
    """A seeded league: circulant conference schedules, random non-conference pairings, one game
    against a non-FBS opponent per FBS team when `non_fbs` > 0, and a `played_share` of the games
    already played (records so far, `PLAYED_CONF` rows). Remaining games are ordered by week,
    with `TEAM_SIGMA` growing with it."""
    rng = np.random.default_rng(seed)
    hfa = BASE_PARAMS["hfa"]
    teams = _league_teams(rng, conferences, independents, non_fbs)
    pairs = _conference_pairs(rng, teams, conferences) + _nonconference_pairs(
        rng, teams, conferences, games_per_team - int(non_fbs > 0)
    )
    fbs = [i for i, team in enumerate(teams) if team.tier != core.TIER_NON_FBS]
    opponents = [i for i, team in enumerate(teams) if team.tier == core.TIER_NON_FBS]
    if opponents:
        pairs += [(home, int(rng.choice(opponents)), False) for home in fbs]
    records = {
        i: {"wins": 0, "losses": 0, "cw": 0, "cl": 0, "cpd": 0, "capped": 0}
        for i in range(len(teams))
    }
    remaining: list[tuple[int, Game]] = []
    played: list[Played] = []
    for home, away, conf_game in pairs:
        mu = round(teams[home].rating - teams[away].rating + hfa, 2)
        if rng.random() < played_share:
            margin = _played_margin(rng, mu)
            _record(records, home, away, margin, conf_game)
            if conf_game:
                played.append(Played(home, away, margin))
        else:
            week = int(rng.integers(1, weeks + 1))
            team_sigma = round(tau * math.sqrt(week - 1) / math.sqrt(2), 2)
            remaining.append((week, Game(home, away, mu, team_sigma, conf_game)))
    remaining.sort(key=lambda item: item[0])
    teams = _with_records_and_sos(teams, pairs, records)
    return Crafted(tuple(teams), tuple(game for _, game in remaining), tuple(played))


def _league_teams(
    rng: np.random.Generator, conferences: Sequence[Conference], independents: int, non_fbs: int
) -> list[Team]:
    teams = []
    for conference in conferences:
        mean, sd = RATING_PRIOR[conference.tier]
        for _ in range(conference.size):
            rating = round(float(rng.normal(mean, sd)), 2)
            teams.append(Team(conference.name, conference.tier, rating, has_ccg=conference.has_ccg))
    mean, sd = RATING_PRIOR[core.TIER_IND]
    for _ in range(independents):
        teams.append(Team(None, core.TIER_IND, round(float(rng.normal(mean, sd)), 2)))
    for _ in range(non_fbs):
        rating = round(float(rng.normal(*NON_FBS_RATING)), 2)
        teams.append(Team(None, core.TIER_NON_FBS, rating, eligible=False))
    return teams


def _circulant_offsets(size: int, degree: int) -> list[int]:
    if degree >= size or (degree % 2 and size % 2):
        raise ValueError(f"no {degree}-regular schedule for {size} teams")
    return list(range(1, degree // 2 + 1)) + ([size // 2] if degree % 2 else [])


def _conference_pairs(
    rng: np.random.Generator, teams: list[Team], conferences: Sequence[Conference]
) -> list[tuple[int, int, bool]]:
    pairs = []
    for conference in conferences:
        members = [i for i, team in enumerate(teams) if team.conference == conference.name]
        members = [members[i] for i in rng.permutation(len(members))]
        edges = {
            tuple(sorted((members[i], members[(i + d) % conference.size])))
            for i in range(conference.size)
            for d in _circulant_offsets(conference.size, conference.conf_games)
        }
        for a, b in sorted(edges):
            home, away = (a, b) if rng.random() < 0.5 else (b, a)
            pairs.append((home, away, True))
    return pairs


def _nonconference_pairs(
    rng: np.random.Generator,
    teams: list[Team],
    conferences: Sequence[Conference],
    fbs_games: int,
) -> list[tuple[int, int, bool]]:
    conf_games = {conference.name: conference.conf_games for conference in conferences}
    slots = [
        i
        for i, team in enumerate(teams)
        if team.tier != core.TIER_NON_FBS
        for _ in range(fbs_games - conf_games.get(team.conference or "", 0))
    ]
    slots = [slots[i] for i in rng.permutation(len(slots))]
    used: set[frozenset[int]] = set()
    pairs = []
    for a, b in zip(slots[0::2], slots[1::2], strict=False):
        same_conf = teams[a].conference is not None and teams[a].conference == teams[b].conference
        if a == b or same_conf or frozenset((a, b)) in used:
            continue
        used.add(frozenset((a, b)))
        home, away = (a, b) if rng.random() < 0.5 else (b, a)
        pairs.append((home, away, False))
    return pairs


def _played_margin(rng: np.random.Generator, mu: float) -> int:
    margin = int(np.rint(mu + PLAYED_SIGMA * rng.standard_normal()))
    return margin if margin else (3 if rng.random() < 0.5 else -3)


def _record(
    records: dict[int, dict[str, int]], home: int, away: int, margin: int, conf_game: bool
) -> None:
    winner, loser = (home, away) if margin > 0 else (away, home)
    capped = max(-24, min(24, margin))
    records[winner]["wins"] += 1
    records[loser]["losses"] += 1
    records[home]["capped"] += capped
    records[away]["capped"] -= capped
    if conf_game:
        records[winner]["cw"] += 1
        records[loser]["cl"] += 1
        records[home]["cpd"] += margin
        records[away]["cpd"] -= margin


def _with_records_and_sos(
    teams: list[Team], pairs: list[tuple[int, int, bool]], records: dict[int, dict[str, int]]
) -> list[Team]:
    total = np.zeros(len(teams))
    count = np.zeros(len(teams))
    for home, away, _ in pairs:
        total[home] += teams[away].rating
        total[away] += teams[home].rating
        count[home] += 1
        count[away] += 1
    updated = []
    for i, team in enumerate(teams):
        record = records[i]
        sos = round(float(total[i] / count[i]), 2) if count[i] else 0.0
        updated.append(
            replace(
                team,
                sos=sos if team.tier != core.TIER_NON_FBS else 0.0,
                wins=record["wins"],
                losses=record["losses"],
                conf_wins=record["cw"],
                conf_losses=record["cl"],
                conf_pd=record["cpd"],
                capped_pd=record["capped"],
            )
        )
    return updated


def ranked(order: Sequence[str], tiers: Mapping[str, str], **params: float) -> Crafted:
    """A finished regular season (no remaining games, no title games) in resume order: the team at
    position k is ranked k + 1. Codes name the conference ('IND' for an independent); '*' marks
    the conference champion, who leads its conference 5-0 while the others stand 2-3. Every team
    is 8-4 with no point differential, so the strength of schedule alone sets the resume order."""
    n_teams = len(order)
    teams = []
    for position, code in enumerate(order):
        name = code.rstrip("*")
        tier = tiers[name]
        conference = name if tier in core.CONFERENCE_TIERS else None
        conf_record = RANKED_CHAMPION_RECORD if code.endswith("*") else RANKED_OTHER_RECORD
        conf_wins, conf_losses = conf_record if conference else (0, 0)
        teams.append(
            Team(
                conference,
                tier,
                rating=(n_teams - position) / 2,
                sos=float(n_teams - position),
                wins=RANKED_WINS,
                losses=RANKED_LOSSES,
                conf_wins=conf_wins,
                conf_losses=conf_losses,
            )
        )
    return Crafted(tuple(teams), params={**BASE_PARAMS, **params})
