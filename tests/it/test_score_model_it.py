"""The score model over the synthetic league (sim-engine/score-model, scenarios 1-3): ratings, home
field and the horizon, read at the point 2026 week 9 through `TEAM_RATING_AS_OF` and
`GAME_SCORE_AS_OF`.

Read-only module (marker `model_readonly`) apart from the neutral-site test, which seeds a variant
and puts the base league back. Constants are set per test with `harness.set_sim_params`; the
expectations are computed here from the model's own as-of views and the constants.
"""

from __future__ import annotations

import copy
import dataclasses
import math
from collections.abc import Iterator
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pytest

from tests.it.harness import Harness
from tests.synthetic.league import League, season_key

PLAN_VARIANT = "model"
pytestmark = [pytest.mark.it, pytest.mark.model_readonly]

SEASON, POINT = 2026, 9
POINT_FILTER = f"AS_OF_SEASON = {SEASON} AND AS_OF_WEEK_SEQ = {POINT}"
TWO_PLACES = Decimal("0.01")
NEUTRAL_GAME = "ashford_2026_w13"


def number(value: Any) -> Decimal:
    """A DECIMAL cell as the driver returns it (an int, a Decimal or a string) as a Decimal."""
    return Decimal(str(value))


def rounded(value: Decimal, places: Decimal = TWO_PLACES) -> Decimal:
    return value.quantize(places, rounding=ROUND_HALF_UP)


@pytest.fixture(scope="module", autouse=True)
def sim_deployed(harness: Harness, league: League) -> None:
    harness.ensure_sim()


def at_point(harness: Harness, schema: str, view: str, columns: str = "*") -> list[dict[str, Any]]:
    return harness.query_dicts(f"SELECT {columns} FROM {schema}.{view} WHERE {POINT_FILTER}")


def expected_rating(row: dict[str, Any], params: dict[str, Decimal]) -> tuple[Decimal, str]:
    sp = None if row["SP_PRIOR_RATING"] is None else number(row["SP_PRIOR_RATING"])
    elo = None if row["ELO_AS_OF"] is None else number(row["ELO_AS_OF"])
    centered = None if elo is None else elo - params["ELO_CENTER"]
    if sp is not None and centered is not None:
        return rounded(params["B_SP"] * sp + params["B_ELO"] * centered), "SP_ELO"
    if centered is not None:
        return rounded(params["B_ELO_ONLY"] * centered), "ELO"
    if sp is not None:
        return rounded(params["B_SP_ONLY"] * sp), "SP"
    if row["CLASSIFICATION"] == "fbs":
        return rounded(params["FBS_DEFAULT_RATING"]), "DEFAULT"
    return rounded(params["NON_FBS_RATING"]), "DEFAULT"


def test_rating_blends_prior_sp_and_elo(harness: Harness) -> None:
    """Scenario score-model: A team's rating blends its prior SP+ and its current Elo."""
    params = {
        "B_SP": Decimal("0.8"),
        "B_ELO": Decimal("0.04"),
        "ELO_CENTER": Decimal("1500"),
        "B_ELO_ONLY": Decimal("0.05"),
        "B_SP_ONLY": Decimal("0.6"),
        "NON_FBS_RATING": Decimal("-20"),
        "FBS_DEFAULT_RATING": Decimal("-10"),
    }
    with harness.set_sim_params(**params):
        ratings = at_point(harness, harness.sim, "TEAM_RATING_AS_OF")
    inputs = {
        row["TEAM_ID"]: row
        for row in at_point(
            harness,
            harness.model,
            "TEAM_SEASON_AS_OF",
            "TEAM_ID, CLASSIFICATION, SP_PRIOR_RATING, ELO_AS_OF",
        )
    }
    assert len(ratings) == len(inputs) > 10
    for row in ratings:
        source = inputs[row["TEAM_ID"]]
        assert (number(row["RATING"]), row["RATING_SOURCE"]) == expected_rating(source, params), row
    ashford = next(row for row in ratings if row["SCHOOL"] == "Ashford")
    assert ashford["RATING_SOURCE"] == "SP_ELO"
    assert ashford["SP_PRIOR_RATING"] is not None and ashford["ELO_AS_OF"] is not None
    plain = [
        row
        for row in ratings
        if row["CLASSIFICATION"] != "fbs"
        and row["SP_PRIOR_RATING"] is None
        and row["ELO_AS_OF"] is None
    ]
    assert plain, "the league has non-FBS teams without an SP+ or an Elo"
    assert {(number(row["RATING"]), row["RATING_SOURCE"]) for row in plain} == {
        (Decimal("-20"), "DEFAULT")
    }
    assert {row["TIER"] for row in plain} == {"NON_FBS"}


def with_neutral_site(league: League) -> League:
    """The 2026 game `NEUTRAL_GAME` is played at a neutral site."""
    old = league.get("GAMES", season_key("GAMES", SEASON))
    body = copy.deepcopy(old.body)
    target = league.game_id(NEUTRAL_GAME)
    for game in body:
        if game["id"] == target:
            game["neutralSite"] = True
    return league.with_slices(
        dataclasses.replace(old, body=body) if item is old else item for item in league.slices
    )


@pytest.fixture
def neutral_league(harness: Harness, league: League) -> Iterator[League]:
    changed = harness.seed_league(with_neutral_site)
    try:
        yield changed
    finally:
        harness.seed_league()


def test_home_field_only_off_neutral_sites(harness: Harness, neutral_league: League) -> None:
    """Scenario score-model: Home field counts only off neutral sites."""
    neutral_id = neutral_league.game_id(NEUTRAL_GAME)
    with harness.set_sim_params(HFA="2.5"):
        games = at_point(harness, harness.sim, "GAME_SCORE_AS_OF")
    assert len(games) > 10
    for game in games:
        edge = number(game["MU"]) - (number(game["HOME_RATING"]) - number(game["AWAY_RATING"]))
        expected = Decimal(0) if game["GAME_ID"] == neutral_id else Decimal("2.5")
        assert edge == expected, game
    neutral = [game for game in games if game["IS_NEUTRAL_SITE"]]
    assert [game["GAME_ID"] for game in neutral] == [neutral_id]


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def test_sigma_by_horizon_and_win_probability(harness: Harness) -> None:
    """Scenario score-model: Uncertainty grows with the horizon, and the win probability follows
    the margin."""
    with harness.set_sim_params(SIGMA_1="15", TAU="2"):
        games = at_point(harness, harness.sim, "GAME_SCORE_AS_OF")
    by_week: dict[int, list[dict[str, Any]]] = {}
    for game in games:
        by_week.setdefault(int(game["WEEK_SEQ"]), []).append(game)
    next_week, later_week = by_week[10], by_week[13]
    assert {
        (g["HORIZON_WEEKS"], number(g["SIGMA"]), number(g["TEAM_SIGMA"])) for g in next_week
    } == {(1, Decimal("15.00"), Decimal("0.00"))}
    assert {
        (g["HORIZON_WEEKS"], number(g["SIGMA"]), number(g["TEAM_SIGMA"])) for g in later_week
    } == {(4, Decimal("15.39"), Decimal("2.45"))}
    assert {g["GAME_STATUS_AS_OF"] for g in next_week} == {"NEXT"}
    for game in games:
        mu, sigma = float(number(game["MU"])), float(number(game["SIGMA"]))
        expected = 0.5 * (normal_cdf((mu - 0.5) / sigma) + normal_cdf((mu + 0.5) / sigma))
        assert abs(float(number(game["HOME_WIN_PROB"])) - expected) < 0.0001, game
        home, away = number(game["HOME_POINTS_PRED"]), number(game["AWAY_POINTS_PRED"])
        if home > 0 and away > 0:
            assert abs((home - away) - number(game["MU"])) <= Decimal("0.1"), game
