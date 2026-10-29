"""sim-engine/engine-contract: `engine/core.py` on crafted models (host numpy, no database)."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

import numpy as np
import pytest

from engine import core
from tests.engine.models import (
    BASE_PARAMS,
    Conference,
    Crafted,
    Game,
    Simulated,
    Team,
    league,
)

SMALL = league(
    (
        Conference("Alpha", core.TIER_P4, 10, 3),
        Conference("Beta", core.TIER_P4, 10, 3),
        Conference("Gamma", core.TIER_G6, 10, 3),
        Conference("Delta", core.TIER_G6, 10, 3),
    ),
    independents=0,
    games_per_team=3,
    seed=7,
)
LEAGUE = league(
    (
        Conference("ACC", core.TIER_P4, 8, 7),
        Conference("Big 12", core.TIER_P4, 8, 7),
        Conference("Big Ten", core.TIER_P4, 8, 7),
        Conference("SEC", core.TIER_P4, 8, 7),
        Conference("American", core.TIER_G6, 7, 6),
        Conference("MAC", core.TIER_G6, 7, 6),
        Conference("Mountain West", core.TIER_G6, 7, 6),
        Conference("Pac-12", core.TIER_G6, 7, 6, has_ccg=False),
        Conference("Sun Belt", core.TIER_G6, 7, 6),
    ),
    independents=2,
    non_fbs=6,
    played_share=0.5,
    seed=11,
)
SEC_LEADER = next(k for k, team in enumerate(LEAGUE.teams) if team.conference == "SEC")
LAST_INDEPENDENT = max(k for k, team in enumerate(LEAGUE.teams) if team.tier == core.TIER_IND)


def ineligible_with_top_resume(k: int, team: Team) -> Team:
    if team.tier == core.TIER_NON_FBS:
        return replace(team, wins=11, losses=0, sos=40.0)
    if k == LAST_INDEPENDENT:
        return replace(team, eligible=False, wins=11, losses=0, sos=40.0)
    if k == SEC_LEADER:
        return replace(team, eligible=False, rating=40.0, conf_wins=7, conf_losses=0, sos=40.0)
    return team


FULL = replace(
    LEAGUE, teams=tuple(ineligible_with_top_resume(k, t) for k, t in enumerate(LEAGUE.teams))
)
FULL_SIMS = 2000


@pytest.fixture(scope="module")
def full() -> Simulated:
    return FULL.simulate(seed=2026, sims=FULL_SIMS, batch=1)


def without_info(stage: dict[str, core.Array]) -> dict[str, list[object]]:
    return {name: values.tolist() for name, values in stage.items() if name != "INFO"}


def info_of(stage: dict[str, core.Array]) -> dict[str, str]:
    (text,) = stage["INFO"][stage["ROW_KIND"] == core.STAGE_BATCH].tolist()
    return dict(part.split("=", 1) for part in text.split(";"))


def outcomes_of(stage: dict[str, core.Array]) -> list[str]:
    return list(stage["OUTCOMES"][stage["ROW_KIND"] == core.STAGE_PATH])


def test_batch_determined_by_seed_id_and_size() -> None:
    assert (len(SMALL.teams), len(SMALL.games)) == (40, 60)
    first = core.run_batch(SMALL.columns(seed=77, sims=500, batch=3), "vm-a", "node-a")
    again = core.run_batch(SMALL.columns(seed=77, sims=500, batch=3, shuffle=9), "vm-b", "node-b")
    other = core.run_batch(SMALL.columns(seed=77, sims=500, batch=4), "vm-a", "node-a")
    assert without_info(again) == without_info(first)
    assert info_of(again)["checksum"] == info_of(first)["checksum"]
    assert info_of(again)["digest"] == info_of(first)["digest"]
    assert outcomes_of(other) != outcomes_of(first)
    for kind in (core.STAGE_TEAM, core.STAGE_PATH):
        sim_ids = first["SIM_ID"][first["ROW_KIND"] == kind]
        assert sorted(set(sim_ids.tolist())) == list(range(1500, 2000))
    shifted_game = replace(SMALL.games[0], mu=SMALL.games[0].mu + 3.0)
    shifted = replace(SMALL, games=(shifted_game, *SMALL.games[1:]))
    moved = outcomes_of(core.run_batch(shifted.columns(seed=77, sims=500, batch=3), "v", "n"))
    assert [path[1:] for path in moved] == [path[1:] for path in outcomes_of(first)]
    assert [path[0] for path in moved] != [path[0] for path in outcomes_of(first)]


def test_team_offsets_widen_win_totals() -> None:
    teams = tuple(Team() for _ in range(12))
    games = tuple(Game(home=0, away=k, mu=0.0, team_sigma=7.07) for k in range(1, 9))
    shared = Crafted(teams, games, params={**BASE_PARAMS, "game_sigma": 15.0})
    independent = Crafted(
        teams,
        tuple(replace(game, team_sigma=0.0) for game in games),
        params={**BASE_PARAMS, "game_sigma": 18.03},
    )
    for crafted, low, high in ((shared, 3.1, 3.7), (independent, 1.8, 2.2)):
        result = crafted.simulate(seed=5, sims=20_000).result
        assert low <= float(result.wins[:, 0].var()) <= high
        home_share = result.home_win.mean(axis=0)
        assert ((home_share >= 0.48) & (home_share <= 0.52)).all()


def test_every_season_complete_playoff(full: Simulated) -> None:
    model, result = full.model, full.result
    rows = full.rows(core.STAGE_TEAM)
    order = np.lexsort((rows["SEED"], rows["SIM_ID"]))
    sim_id = rows["SIM_ID"][order].reshape(FULL_SIMS, core.CFP_SIZE)
    team_id = rows["TEAM_ID"][order].reshape(FULL_SIMS, core.CFP_SIZE)
    seed = rows["SEED"][order].reshape(FULL_SIMS, core.CFP_SIZE)
    bye = rows["BYE"][order].reshape(FULL_SIMS, core.CFP_SIZE)
    reached = rows["REACHED_ROUND"][order].reshape(FULL_SIMS, core.CFP_SIZE)
    champion = rows["CHAMPION"][order].reshape(FULL_SIMS, core.CFP_SIZE)
    assert (sim_id == result.sim_ids[:, None]).all()
    assert all(len(set(teams)) == core.CFP_SIZE for teams in team_id.tolist())
    assert (seed == np.arange(1, core.CFP_SIZE + 1)).all()
    assert np.array_equal(bye, seed <= core.N_BYES)
    for minimum, count in ((1, 12), (2, 8), (3, 4), (4, 2)):
        assert ((reached >= minimum).sum(axis=1) == count).all()
    assert (champion.sum(axis=1) == 1).all()
    assert (reached[champion] == core.ROUND_FINAL).all()
    eligible_ids = set(model.team_id[model.eligible].tolist())
    assert set(team_id.ravel().tolist()) <= eligible_ids
    assert not set(team_id.ravel().tolist()) & set(
        model.team_id[model.tier == core.TIER_NON_FBS].tolist()
    )
    assert_selection_invariants(model, result)
    assert_bracket_pairs(reached)


def assert_selection_invariants(model: core.Model, result: core.BatchResult) -> None:
    sims = np.arange(result.sim_ids.size)[:, None]
    in_field = np.zeros(result.wins.shape, dtype=bool)
    in_field[sims, result.seed_teams] = True
    power = model.conf_tier[result.crown] == core.TIER_P4
    assert in_field[sims, result.conf_champion[:, power]].all()
    rank = np.empty_like(result.resume_order)
    np.put_along_axis(rank, result.resume_order, np.arange(model.n_teams)[None, :], 1)
    g6_teams = np.flatnonzero(model.eligible & (model.tier == core.TIER_G6))
    best_g6 = g6_teams[rank[:, g6_teams].argmin(axis=1)]
    assert in_field[sims[:, 0], best_g6].all()
    assert (np.diff(np.take_along_axis(rank, result.seed_teams, axis=1), axis=1) > 0).all()
    at_large = np.zeros_like(in_field)
    at_large[sims, result.seed_teams] = ~result.seed_auto
    worst_at_large = np.where(at_large, rank, -1).max(axis=1)
    left_out = ~in_field & model.eligible
    best_left_out = np.where(left_out, rank, model.n_teams).min(axis=1)
    assert (worst_at_large < best_left_out).all()


def assert_bracket_pairs(reached: core.Array) -> None:
    for high, low in core.FIRST_ROUND_SEEDS:
        advanced = (reached[:, [high - 1, low - 1]] >= core.ROUND_QUARTER).sum(axis=1)
        assert (advanced == 1).all()
    for bye_seed, (high, low) in zip(range(core.N_BYES), core.FIRST_ROUND_SEEDS[::-1], strict=True):
        quarter = reached[:, [bye_seed, high - 1, low - 1]] >= core.ROUND_SEMI
        assert (quarter.sum(axis=1) == 1).all()
    for half in ((1, 8, 9, 4, 5, 12), (2, 7, 10, 3, 6, 11)):
        finalists = reached[:, [seed - 1 for seed in half]] >= core.ROUND_FINAL
        assert (finalists.sum(axis=1) == 1).all()


def test_records_follow_the_margins(full: Simulated) -> None:
    model, result = full.model, full.result
    margins = result.margins
    assert (margins != 0).all()
    assert np.array_equal(result.home_win, margins > 0)
    capped_margins = np.clip(margins, -model.rules.margin_cap, model.rules.margin_cap)
    wins = np.tile(model.wins0, (FULL_SIMS, 1))
    conf_wins = np.tile(model.conf_wins0, (FULL_SIMS, 1))
    conf_pd = np.tile(model.conf_pd0, (FULL_SIMS, 1))
    capped_pd = np.tile(model.capped_pd0, (FULL_SIMS, 1))
    for g in range(model.n_games):
        home, away, home_won = model.home[g], model.away[g], margins[:, g] > 0
        wins[:, home] += home_won
        wins[:, away] += ~home_won
        capped_pd[:, home] += capped_margins[:, g]
        capped_pd[:, away] -= capped_margins[:, g]
        if model.conf_game[g]:
            conf_wins[:, home] += home_won
            conf_wins[:, away] += ~home_won
            conf_pd[:, home] += margins[:, g]
            conf_pd[:, away] -= margins[:, g]
    assert np.array_equal(result.wins, wins)
    assert np.array_equal(result.conf_wins, conf_wins)
    assert np.array_equal(result.conf_pd, conf_pd)
    assert np.array_equal(result.capped_pd, capped_pd)


def test_tallies_agree_with_seasons(full: Simulated) -> None:
    model, result = full.model, full.result
    paths = full.rows(core.STAGE_PATH)
    assert all(len(path) == model.n_games for path in paths["OUTCOMES"].tolist())
    by_sim = dict(zip(paths["SIM_ID"].tolist(), paths["OUTCOMES"].tolist(), strict=True))
    home_won = np.array([[c == "1" for c in by_sim[s]] for s in result.sim_ids.tolist()])
    team_rows = full.rows(core.STAGE_TEAM)
    position = {sim: k for k, sim in enumerate(result.sim_ids.tolist())}
    in_field = np.zeros((FULL_SIMS, model.n_teams), dtype=bool)
    team_index = {team: k for k, team in enumerate(model.team_id.tolist())}
    for sim, team in zip(team_rows["SIM_ID"].tolist(), team_rows["TEAM_ID"].tolist(), strict=True):
        in_field[position[sim], team_index[team]] = True

    wins = full.rows(core.STAGE_WINS)
    expected_wins = Counter(
        (int(model.team_id[t]), int(w))
        for t in np.flatnonzero(model.tier != core.TIER_NON_FBS)
        for w in result.wins[:, t]
    )
    cells = zip(wins["TEAM_ID"].tolist(), wins["WINS"].tolist(), strict=True)
    actual_wins = dict(zip(cells, wins["N"].tolist(), strict=True))
    assert actual_wins == dict(expected_wins)
    per_team = Counter[int]()
    for (team, _), n in actual_wins.items():
        per_team[team] += n
    assert set(per_team.values()) == {FULL_SIMS}

    champs = full.rows(core.STAGE_CONF_CHAMP)
    expected_champs = Counter(int(model.team_id[t]) for t in result.conf_champion.ravel())
    assert dict(zip(champs["TEAM_ID"].tolist(), champs["N"].tolist(), strict=True)) == dict(
        expected_champs
    )

    games = full.rows(core.STAGE_GAME)
    assert games["GAME_IDX"].tolist() == list(range(model.n_games))
    assert games["N"].tolist() == home_won.sum(axis=0).tolist()

    leverage = full.rows(core.STAGE_LEVERAGE)
    lev_cells = zip(leverage["TEAM_ID"].tolist(), leverage["GAME_IDX"].tolist(), strict=True)
    actual = dict(zip(lev_cells, leverage["N"].tolist(), strict=True))
    expected = {}
    for t in np.flatnonzero(in_field.any(axis=0)):
        counts = (in_field[:, t][:, None] & home_won).sum(axis=0)
        for g in np.flatnonzero(counts):
            expected[(int(model.team_id[t]), int(g))] = int(counts[g])
    assert actual == expected
    assert len(actual) > 0


def test_invalid_model_rejected() -> None:
    eleven = Crafted(tuple(Team() for _ in range(11)))
    outside = replace(SMALL, games=(replace(SMALL.games[0], home=40), *SMALL.games[1:]))
    cases = (
        (SMALL.without_param("game_sigma"), "missing parameter: game_sigma"),
        (outside, "index out of range: HOME_IDX/AWAY_IDX outside 0..39 in REMAINING rows"),
        (eleven, "fewer than 12 eligible teams: 11 eligible"),
    )
    for crafted, message in cases:
        with pytest.raises(core.ModelError, match=message):
            core.run_batch(crafted.columns(), "vm", "node")


def test_outcome_string_positions() -> None:
    games = list(SMALL.games)
    games[5] = replace(games[5], mu=500.0)
    games[17] = replace(games[17], mu=-500.0)
    stage = core.run_batch(replace(SMALL, games=tuple(games)).columns(sims=300), "vm", "node")
    paths = outcomes_of(stage)
    assert len(paths) == 300
    assert all(len(path) == 60 and set(path) <= {"0", "1"} for path in paths)
    assert {path[5] for path in paths} == {"1"}
    assert {path[17] for path in paths} == {"0"}
    home_wins = stage["N"][stage["ROW_KIND"] == core.STAGE_GAME]
    assert (home_wins[5], home_wins[17]) == (300, 0)


def test_no_remaining_game_gives_null_outcomes() -> None:
    finished = replace(SMALL, games=())
    stage = core.run_batch(finished.columns(sims=50), "vm", "node")
    assert outcomes_of(stage) == [None] * 50
    kinds = set(stage["ROW_KIND"].tolist())
    assert core.STAGE_GAME not in kinds
    assert core.STAGE_LEVERAGE not in kinds


def test_fail_batch_hook_names_the_batch() -> None:
    hooked = SMALL.with_params(fail_batch=2)
    core.run_batch(hooked.columns(sims=50, batch=1), "vm", "node")
    with pytest.raises(core.BatchFailure, match="batch 2"):
        core.run_batch(hooked.columns(sims=50, batch=2), "vm", "node")
