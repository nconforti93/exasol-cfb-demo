"""sim-engine/selection-rules: champions, selection, seeding and bracket on crafted models.

The `ranked` models have nothing left to play, so every simulated season selects the same field;
a team's position in the order is its resume rank (1-based in the assertions).
"""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction

import numpy as np

from engine import core
from tests.engine.models import BASE_PARAMS, Crafted, Game, Played, Team, ranked


def codes(text: str) -> list[str]:
    """Resume order codes, best first: conference, '*' for its champion, 'IND'."""
    return text.split()


TIERS = {
    "SEC": core.TIER_P4,
    "B1G": core.TIER_P4,
    "B12": core.TIER_P4,
    "ACC": core.TIER_P4,
    "SUN": core.TIER_G6,
    "MAC": core.TIER_G6,
    "IND": core.TIER_IND,
}
POWER_AND_G6 = codes(
    "B1G* SEC* SEC B1G B12* ACC B12 SEC B1G ACC B12 SEC B1G ACC ACC* B12 SEC SUN B1G B12 ACC "
    "MAC* SEC B1G B12 ACC SUN SUN SUN* SUN SUN MAC MAC MAC MAC MAC"
)
GUARANTEE = codes(
    "SEC* B1G* SEC B12* B1G SEC B12 ACC B1G SEC B12 IND ACC B1G ACC ACC* SEC B12 B1G SUN ACC "
    "SUN* SUN SUN SUN SUN SEC B1G B12 B12 ACC"
)
SEEDING = codes(
    "SEC* SEC B1G* B12* B1G ACC* B12 SEC ACC SUN* B1G B12 SEC ACC B1G B12 SEC ACC B1G B12 SEC ACC "
    "B1G B12 ACC SUN SUN SUN SUN SUN"
)
G6_ABOVE_ACC = codes(
    "SEC* B1G* B12* SEC B1G B12 SEC B1G B12 SEC B1G B12 SUN* MAC* SEC ACC* B1G B12 SEC B1G B12 "
    "ACC ACC ACC ACC ACC SUN SUN SUN SUN SUN MAC MAC MAC MAC MAC"
)
ACC_AT_LARGE = codes(
    "SEC* B1G* B12* SEC B1G B12 SEC SUN* MAC* ACC* B1G B12 SEC B1G B12 SEC B1G B12 SEC B1G B12 "
    "ACC ACC ACC ACC ACC SUN SUN SUN SUN SUN MAC MAC MAC MAC MAC"
)


def selected(crafted: Crafted) -> tuple[list[int], list[int]]:
    """The field's ranks in seed order and the ranks of its automatic bids."""
    result = crafted.simulate(sims=20).result
    assert (result.seed_teams == result.seed_teams[0]).all()
    assert (result.seed_auto == result.seed_auto[0]).all()
    field = result.seed_teams[0] + 1
    return field.tolist(), sorted(field[result.seed_auto[0]].tolist())


def test_power_champions_and_best_g6_team() -> None:
    field, auto = selected(ranked(POWER_AND_G6, TIERS))
    assert auto == [1, 2, 5, 15, 18]
    assert field == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 18]
    field, auto = selected(ranked(POWER_AND_G6, TIERS, g6_bid=core.G6_BID_CHAMPION))
    assert auto == [1, 2, 5, 15, 22]
    assert field == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 22]


def test_guarantee_within_top12() -> None:
    field, auto = selected(ranked(GUARANTEE, TIERS, guarantee_team_idx=11))
    assert auto == [1, 2, 4, 12, 16, 20]
    assert field == [1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 16, 20]
    thirteenth = [*GUARANTEE[:11], GUARANTEE[12], GUARANTEE[11], *GUARANTEE[13:]]
    field, auto = selected(ranked(thirteenth, TIERS, guarantee_team_idx=12))
    assert auto == [1, 2, 4, 16, 20]
    assert field == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 16, 20]


def test_straight_seeding_and_byes() -> None:
    field, auto = selected(ranked(SEEDING, TIERS))
    assert (field, auto) == (list(range(1, 13)), [1, 3, 4, 6, 10])
    seasons = ranked(SEEDING, TIERS, hfa=1000.0).simulate(sims=400)
    rows = seasons.rows(core.STAGE_TEAM)
    order = np.lexsort((rows["SEED"], rows["SIM_ID"]))
    team = rows["TEAM_ID"][order].reshape(-1, core.CFP_SIZE) - seasons.model.team_id[0]
    bye = rows["BYE"][order].reshape(-1, core.CFP_SIZE)
    reached = rows["REACHED_ROUND"][order].reshape(-1, core.CFP_SIZE)
    assert (team == np.arange(core.CFP_SIZE)).all()
    assert bye[:, 1].all() and not bye[:, 5].any()
    assert (reached[:, 4:8] >= core.ROUND_QUARTER).all()
    assert (reached[:, 8:] == core.ROUND_FIRST).all()
    seed_4_or_5 = reached[:, [3, 4]] >= core.ROUND_SEMI
    assert (seed_4_or_5.sum(axis=1) == 1).all()
    assert seed_4_or_5[:, 0].any() and seed_4_or_5[:, 1].any()


def test_top_champions_rule() -> None:
    top_champions = {"auto_bid_rule": core.AUTO_BID_TOP_CHAMPIONS, "auto_bids": 5}
    field, auto = selected(ranked(G6_ABOVE_ACC, TIERS, **top_champions))
    assert auto == [1, 2, 3, 13, 14]
    assert field == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 13, 14]
    field, auto = selected(ranked(G6_ABOVE_ACC, TIERS))
    assert (auto, 16 in field) == ([1, 2, 3, 13, 16], True)
    field, auto = selected(ranked(ACC_AT_LARGE, TIERS, **top_champions))
    assert auto == [1, 2, 3, 8, 9]
    assert 10 in field


def small_conferences() -> Crafted:
    """Tiny: 4 teams with a title game; Seven: 7 teams without one, whose two best teams met
    already (4 beat 5) and meet again; two independents. Everything left is a conference game."""
    tiny = [Team("Tiny", core.TIER_G6, rating, has_ccg=True) for rating in (5.0, 0.0, -3.0, -6.0)]
    seven_ratings = (10.0, 9.5, 3.0, 2.0, 0.0, -2.0, -4.0)
    seven = [Team("Seven", core.TIER_G6, rating) for rating in seven_ratings]
    seven[0] = Team("Seven", core.TIER_G6, 10.0, wins=1, conf_wins=1, conf_pd=7, capped_pd=7)
    seven[1] = Team("Seven", core.TIER_G6, 9.5, losses=1, conf_losses=1, conf_pd=-7, capped_pd=-7)
    teams = (*tiny, *seven, Team(rating=4.0), Team(rating=1.0))
    games = [
        *round_robin(range(0, 4), teams),
        *round_robin(range(4, 11), teams),
    ]
    return Crafted(teams, tuple(games), (Played(4, 5, 7),), dict(BASE_PARAMS))


def round_robin(members: Sequence[int], teams: Sequence[Team]) -> list[Game]:
    games = []
    for k, a in enumerate(members):
        for b in members[k + 1 :]:
            home, away = (b, a) if (a + b) % 2 else (a, b)
            mu = teams[home].rating - teams[away].rating + BASE_PARAMS["hfa"]
            games.append(Game(home, away, mu=mu, team_sigma=1.0, conf_game=True))
    return games


def test_small_and_no_ccg_conferences() -> None:
    crafted = small_conferences()
    seasons = crafted.simulate(seed=3, sims=1000)
    model, result = seasons.model, seasons.result
    assert model.conf_names == ("Seven", "Tiny")
    assert result.crown.tolist() == [0]
    champs = seasons.rows(core.STAGE_CONF_CHAMP)
    tiny_ids = set(model.team_id[:4].tolist())
    assert not set(champs["TEAM_ID"].tolist()) & tiny_ids
    assert int(champs["N"].sum()) == 1000

    seven = range(4, 11)
    conf_games = {
        t: int(model.conf_wins0[t] + model.conf_losses0[t])
        + sum(t in (game.home, game.away) for game in crafted.games)
        for t in seven
    }
    rematch_overruled_first_meeting = 0
    for s in range(1000):
        pct = {t: Fraction(int(result.conf_wins[s, t]), conf_games[t]) for t in seven}
        top = [t for t in seven if pct[t] == max(pct.values())]
        if len(top) == 2:
            g = next(
                k for k, game in enumerate(crafted.games) if {game.home, game.away} == set(top)
            )
            game = crafted.games[g]
            leader = game.home if result.home_win[s, g] else game.away
            rematch_overruled_first_meeting += set(top) == {4, 5} and leader == 5
        else:
            leader = min(top, key=lambda t: (-int(result.conf_pd[s, t]), -model.rating[t], t))
        assert int(result.conf_champion[s, 0]) == leader, s
    assert rematch_overruled_first_meeting > 0
