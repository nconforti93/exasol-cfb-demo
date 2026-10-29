"""The host fit behind `make sim-fit` (sim-engine/score-model): constants from past games.

One least-squares fit on the completed regular-season games of 2014-2025, with the information a
prediction has at the point before the game: each team's Elo as of that point and the SP+ rating
of the previous season. It estimates the constants of `CFB_SIM.SIM_PARAMS` that the score model
reads and renders the committed seed `sql/sim/010_sim_params.sql`. Pure numpy; `read_inputs` is
the only function that touches a database (read-only `SELECT`s on `CFB_MODEL.GAME` and
`RATING_SP`).

- A game's Elo for a team is what `TEAM_SEASON_AS_OF.ELO_AS_OF` shows at the point `week - 1 - k`:
  the postgame Elo of the team's last game up to that week that has one, else the pregame Elo of
  its first game of the season when that game is no later than the point's next week, else
  missing. k = 0 is the point right before the game; `elo_series` and `elo_at` replay the rule.
- `HFA`, `B_SP`, `B_ELO` and `SIGMA_1`: least squares of `MARGIN` on the home flag (not neutral),
  the prior SP+ difference and the Elo difference over FBS-vs-FBS games with both features;
  `SIGMA_1` is the residual standard deviation, `ELO_CENTER` the mean pregame Elo of those teams.
- `B_ELO_ONLY`, `B_SP_ONLY`: the one-feature fits. `NON_FBS_RATING`: the mean rating that margins
  of FBS-vs-non-FBS games imply for a non-FBS opponent without an Elo.
- `TAU`: the residual variance at k = 0..6 weeks of extra Elo lag, with the k = 0 coefficients,
  grows by `TAU`^2 per week. When it does not grow, `TAU` is `ASSUMED_TAU` (origin `ASSUMED`).
- `TOTAL_*`: least squares of the total points on the four prior offense and defense ratings.
- `ELO_PROB_SCALE`, `ELO_PROB_HFA`: grid search on the log-likelihood of the Elo win probability.

A sample of fewer than `MIN_SAMPLE` games keeps the S3 default of its constants, origin `ASSUMED`.
The same games always give the same seed text: no timestamp, fixed order, fixed rounding.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[Any]

ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "sql" / "sim" / "010_sim_params.sql"
FIRST_SEASON = 2014
LAST_SEASON = 2025
HOLDOUT_FIRST_SEASON = 2024
LAGS = 6
MIN_SAMPLE = 30
MIN_GROWTH = 1e-6
ASSUMED_TAU = 1.5
DEFAULT_FBS_RATING = -10.0
CALIBRATION_BUCKETS = 10
FBS = "fbs"

ELO_SCALE_GRID = range(200, 801, 10)
ELO_HFA_GRID = range(0, 151, 5)
ELO_SCALE_REFINE = 10
ELO_HFA_REFINE = 5

FITTED = "FITTED"
ASSUMED = "ASSUMED"

DEFAULTS: dict[str, float] = {
    "NON_FBS_RATING": -25.0,
    "TOTAL_INTERCEPT": 55.0,
    "TOTAL_SLOPE": 0.0,
    "TOTAL_DEFAULT": 55.0,
    "ELO_PROB_SCALE": 400.0,
    "ELO_PROB_HFA": 55.0,
}
FIT_KEYS = (
    "HFA",
    "B_SP",
    "B_ELO",
    "ELO_CENTER",
    "B_ELO_ONLY",
    "B_SP_ONLY",
    "NON_FBS_RATING",
    "SIGMA_1",
    "TAU",
    "TOTAL_INTERCEPT",
    "TOTAL_SLOPE",
    "TOTAL_DEFAULT",
    "ELO_PROB_SCALE",
    "ELO_PROB_HFA",
)
FIT_NOTES = {
    "HFA": "Home-field advantage in points, applied off neutral sites",
    "B_SP": "Weight of the previous season SP+ rating in a team rating, points per SP+ point",
    "B_ELO": "Weight of the current Elo above ELO_CENTER in a team rating, points per Elo point",
    "ELO_CENTER": "Mean Elo of the FBS teams in the fit, a whole number",
    "B_ELO_ONLY": "Points per Elo point when a team has an Elo but no previous SP+",
    "B_SP_ONLY": "Points per SP+ point when a team has a previous SP+ but no Elo",
    "NON_FBS_RATING": "Rating in points of a non-FBS opponent without an Elo",
    "SIGMA_1": "Standard deviation of a game margin one week ahead, in points",
    "TAU": "Growth of the margin standard deviation per week ahead, in points; "
    "it sets the shared team-strength offsets of the engine",
    "TOTAL_INTERCEPT": "Intercept of the predicted game total in points",
    "TOTAL_SLOPE": "Points of predicted game total per point of the four prior offense "
    "and defense ratings",
    "TOTAL_DEFAULT": "Predicted game total in points when a team has no previous SP+",
    "ELO_PROB_SCALE": "Elo points that separate a 10:1 win-odds ratio in the pregame "
    "probability from Elo",
    "ELO_PROB_HFA": "Elo points of home-field advantage in the pregame probability from Elo",
}
ASSUMED_ROWS = (
    (
        "FBS_DEFAULT_RATING",
        "-10",
        "Rating in points of an FBS team with neither a previous SP+ nor an Elo",
    ),
    ("OT_MARGIN", "3", "Margin in points of a game that went to overtime, a whole number"),
    (
        "MARGIN_CAP",
        "24",
        "Cap in points on one game margin in the resume point differential, a whole number",
    ),
    ("W_WIN_PCT", "100", "Resume weight of the win percentage"),
    (
        "W_SOS",
        "2.0",
        "Resume weight of the strength of schedule, in points of mean opponent rating",
    ),
    ("W_PD", "0.5", "Resume weight of the capped point differential per game"),
    ("W_RATING", "0", "Resume weight of the team rating"),
    (
        "MOVE_THRESHOLD",
        "0.0070",
        "Change in the probability of making the playoff between two points that counts as a "
        "move: three standard errors of a difference of two 100,000-season runs",
    ),
    (
        "MIN_BRANCH_SAMPLE",
        "100",
        "Fewest simulated seasons in a what-if branch for the rooting guide to trust it",
    ),
)
SEED_HEADER = (
    "-- Reference seed: the constants of the score model, the resume ranking and the odds views\n"
    "-- (sim-engine/score-model). Replaced on every deploy; change it in a pull request, never in "
    "the\n"
    "-- database. FITTED rows are written by `make sim-fit` (engine/fit.py) from the completed\n"
    "-- regular-season games of {first}-{last}; {games} FBS games have a prior SP+ and an Elo.\n"
    "-- ASSUMED rows are stated choices (published rules, S3 prototype values, or a fit that fell "
    "back).\n"
    "CREATE OR REPLACE TABLE {{SIM_SCHEMA}}.SIM_PARAMS (\n"
    "  PARAM_KEY    VARCHAR(40) ASCII NOT NULL COMMENT IS 'Name of the constant, upper case, "
    "unique; SCORE_PARAMS turns each key into a column',\n"
    "  PARAM_VALUE  DECIMAL(12,4) NOT NULL COMMENT IS 'Value of the constant, four decimals',\n"
    "  PARAM_ORIGIN VARCHAR(10) ASCII NOT NULL COMMENT IS 'Where the value comes from: FITTED "
    "(written by make sim-fit), ASSUMED (a stated choice, published or inherited from the S3 "
    "prototype) or UNFITTED (a placeholder until the fit has run)',\n"
    "  PARAM_NOTE   VARCHAR(300) UTF8 NOT NULL COMMENT IS 'One sentence on what the constant means "
    "and its unit'\n"
    ") COMMENT IS 'One row per constant of the score model, the resume ranking and the odds "
    "movement views. Replaced from git on every deploy; the fit and the owner change it through "
    "a reviewed seed file.';\n"
    "\n"
    'INSERT INTO {{SIM_SCHEMA}}."SIM_PARAMS" VALUES\n'
)


class GameRow(NamedTuple):
    """One game of `CFB_MODEL.GAME`, the columns the fit reads (Elo and SP+ as numbers)."""

    season: int
    week_seq: int
    start_at: datetime | None
    game_id: int
    home_id: int | None
    away_id: int | None
    home_class: str | None
    away_class: str | None
    is_neutral: bool
    home_points: int | None
    away_points: int | None
    home_pre: Decimal | float | None
    away_pre: Decimal | float | None
    home_post: Decimal | float | None
    away_post: Decimal | float | None
    spread: Decimal | float | None
    line_home_prob: Decimal | float | None = None


class SpRow(NamedTuple):
    """One row of `CFB_MODEL.RATING_SP` with a team id."""

    season: int
    team_id: int
    sp: float
    offense: float | None
    defense: float | None


@dataclass(frozen=True)
class FitTable:
    """The games of the fit as arrays, one row per game with an FBS team.

    `home_elo` and `away_elo` have `LAGS + 1` columns: column k is the Elo as of the point k weeks
    before the point that precedes the game (NaN when missing); SP+ columns are NaN when missing.
    `spread` is CFBD's pregame spread from the home side (NaN without a line).
    """

    season: Array
    home_flag: Array
    margin: Array
    total: Array
    spread: Array
    home_is_fbs: Array
    away_is_fbs: Array
    home_elo: Array
    away_elo: Array
    home_sp: Array
    away_sp: Array
    home_off: Array
    home_def: Array
    away_off: Array
    away_def: Array
    line_probability: Array = field(default_factory=lambda: np.array([], dtype=float))

    def __post_init__(self) -> None:
        if self.line_probability.size == 0:
            object.__setattr__(self, "line_probability", np.full(len(self), np.nan))
        elif self.line_probability.shape != self.margin.shape:
            raise ValueError("line probability must have one value per game")

    def where(self, mask: Array) -> FitTable:
        return FitTable(**{name: values[mask] for name, values in self.__dict__.items()})

    def __len__(self) -> int:
        return int(self.margin.shape[0])


@dataclass(frozen=True)
class Fit:
    """The fitted constants (rounded as the seed stores them) and what stands behind them."""

    values: dict[str, float]
    origins: dict[str, str]
    counts: dict[str, int]
    lag_variance: tuple[float, ...]
    lag_count: int
    first_season: int
    last_season: int
    notes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Evaluation:
    """How the constants predict a set of games one week ahead."""

    games: int
    margin_rmse: float
    margin_mae: float
    margin_bias: float
    margin_bias_se: float
    brier: float
    line_games: int
    line_rmse: float
    line_mae: float
    model_rmse_on_line_games: float
    model_mae_on_line_games: float
    line_prob_games: int
    line_brier: float
    model_brier_on_line_games: float
    buckets: tuple[tuple[float, int, float, float], ...]


class FitError(RuntimeError):
    """The inputs cannot be fitted (no games at all)."""


@dataclass(frozen=True)
class EloSeries:
    """One team-season's Elo facts: the first game's week and pregame Elo, then the postgame Elo
    after each game that has one, by week."""

    first_week: int
    first_pre: float | None
    weeks: tuple[int, ...]
    values: tuple[float, ...]


def number(value: object) -> float | None:
    """A database number (int, Decimal or string) as a float; `None` stays `None`."""
    return None if value is None else float(str(value))


def elo_series(games: Sequence[GameRow]) -> dict[tuple[int, int], EloSeries]:
    """The Elo series of every `(season, team id)` in `games` (every game of the team's season)."""
    per_team: dict[tuple[int, int], list[tuple[GameRow, bool]]] = {}
    for game in games:
        for team_id, is_home in ((game.home_id, True), (game.away_id, False)):
            if team_id is not None:
                per_team.setdefault((game.season, team_id), []).append((game, is_home))
    series: dict[tuple[int, int], EloSeries] = {}
    for key, entries in per_team.items():
        entries.sort(key=lambda entry: _order(entry[0]))
        first, first_is_home = entries[0]
        first_pre = number(first.home_pre if first_is_home else first.away_pre)
        by_week = sorted(
            (
                (game.week_seq, _order(game), post)
                for game, is_home in entries
                if (post := number(game.home_post if is_home else game.away_post)) is not None
            ),
            key=lambda item: item[:2],
        )
        series[key] = EloSeries(
            first.week_seq,
            first_pre,
            tuple(week for week, _, _ in by_week),
            tuple(post for _, _, post in by_week),
        )
    return series


def elo_at(series: EloSeries | None, point: int) -> float | None:
    """The team's Elo at the as-of point with week sequence `point`, as `ELO_AS_OF` shows it."""
    if series is None:
        return None
    index = bisect.bisect_right(series.weeks, point) - 1
    if index >= 0:
        return series.values[index]
    if series.first_pre is not None and series.first_week <= point + 1:
        return series.first_pre
    return None


def build_table(games: Sequence[GameRow], ratings: Sequence[SpRow]) -> FitTable:
    """The `FitTable` of the completed games of `games`, with the previous season's SP+."""
    series = elo_series(games)
    prior = {(row.season + 1, row.team_id): row for row in ratings}
    played = [
        g
        for g in games
        if g.home_id is not None
        and g.away_id is not None
        and g.home_points is not None
        and g.away_points is not None
    ]
    n = len(played)
    home_elo = np.full((n, LAGS + 1), np.nan)
    away_elo = np.full((n, LAGS + 1), np.nan)
    sp = {name: np.full(n, np.nan) for name in ("hs", "as", "ho", "hd", "ao", "ad")}
    for i, game in enumerate(played):
        for k in range(LAGS + 1):
            point = game.week_seq - 1 - k
            if point < 0:
                continue
            for target, team in ((home_elo, game.home_id), (away_elo, game.away_id)):
                value = elo_at(series.get((game.season, int(team or 0))), point)
                if value is not None:
                    target[i, k] = value
        for team, columns in (
            (game.home_id, ("hs", "ho", "hd")),
            (game.away_id, ("as", "ao", "ad")),
        ):
            row = prior.get((game.season, int(team or 0)))
            if row is not None:
                sp[columns[0]][i] = row.sp
                sp[columns[1]][i] = np.nan if row.offense is None else row.offense
                sp[columns[2]][i] = np.nan if row.defense is None else row.defense
    points_home = np.array([g.home_points for g in played], dtype=float)
    points_away = np.array([g.away_points for g in played], dtype=float)
    spreads = [number(g.spread) for g in played]
    line_probabilities = [number(g.line_home_prob) for g in played]
    return FitTable(
        season=np.array([g.season for g in played], dtype=int),
        home_flag=np.array([0.0 if g.is_neutral else 1.0 for g in played]),
        margin=points_home - points_away,
        total=points_home + points_away,
        spread=np.array([np.nan if s is None else s for s in spreads]),
        line_probability=np.array([np.nan if p is None else p for p in line_probabilities]),
        home_is_fbs=np.array([g.home_class == FBS for g in played], dtype=bool),
        away_is_fbs=np.array([g.away_class == FBS for g in played], dtype=bool),
        home_elo=home_elo,
        away_elo=away_elo,
        home_sp=sp["hs"],
        away_sp=sp["as"],
        home_off=sp["ho"],
        home_def=sp["hd"],
        away_off=sp["ao"],
        away_def=sp["ad"],
    )


def fit_constants(table: FitTable) -> Fit:
    """Estimate every fitted constant of `SIM_PARAMS` from `table`."""
    if len(table) == 0:
        raise FitError("no completed games to fit")
    values: dict[str, float] = {}
    origins: dict[str, str] = {}
    counts: dict[str, int] = {}

    def fitted(key: str, value: float, n: int) -> None:
        values[key], origins[key], counts[key] = value, FITTED, n

    def fallback(key: str, n: int) -> None:
        values[key], origins[key], counts[key] = DEFAULTS[key], ASSUMED, n

    both_fbs = table.home_is_fbs & table.away_is_fbs
    d_sp = table.home_sp - table.away_sp
    d_elo = table.home_elo[:, 0] - table.away_elo[:, 0]
    full = both_fbs & np.isfinite(d_sp) & np.isfinite(d_elo)
    if int(full.sum()) < MIN_SAMPLE:
        raise FitError(f"only {int(full.sum())} FBS games have both an SP+ and an Elo")
    x_full = np.column_stack([table.home_flag, d_sp, d_elo])[full]
    beta, residual = _least_squares(x_full, table.margin[full])
    n_full = int(full.sum())
    hfa, b_sp, b_elo = (float(b) for b in beta)
    sigma_1 = math.sqrt(float(residual @ residual) / (n_full - 3))
    centers = np.concatenate([table.home_elo[full, 0], table.away_elo[full, 0]])
    center = float(round(float(centers.mean())))
    fitted("HFA", hfa, n_full)
    fitted("B_SP", b_sp, n_full)
    fitted("B_ELO", b_elo, n_full)
    fitted("ELO_CENTER", center, n_full)
    fitted("SIGMA_1", sigma_1, n_full)

    elo_only = both_fbs & np.isfinite(d_elo)
    beta_elo, _ = _least_squares(
        np.column_stack([table.home_flag, d_elo])[elo_only], table.margin[elo_only]
    )
    fitted("B_ELO_ONLY", float(beta_elo[1]), int(elo_only.sum()))
    sp_only = both_fbs & np.isfinite(d_sp)
    beta_sp, _ = _least_squares(
        np.column_stack([table.home_flag, d_sp])[sp_only], table.margin[sp_only]
    )
    fitted("B_SP_ONLY", float(beta_sp[1]), int(sp_only.sum()))

    _fit_non_fbs(table, (hfa, b_sp, b_elo, center), fitted, fallback)
    lag_variance, lag_count = _fit_tau(
        table, full, np.array([hfa, b_sp, b_elo]), fitted, values, origins, counts
    )
    _fit_total(table, both_fbs, fitted, fallback)
    _fit_elo_probability(table, both_fbs, fitted, fallback)

    rounded = {key: _rounded(key, value) for key, value in values.items()}
    return Fit(
        values=rounded,
        origins=origins,
        counts=counts,
        lag_variance=lag_variance,
        lag_count=lag_count,
        first_season=int(table.season.min()),
        last_season=int(table.season.max()),
    )


def _fit_non_fbs(
    table: FitTable,
    constants: tuple[float, float, float, float],
    fitted: Any,
    fallback: Any,
) -> None:
    hfa, b_sp, b_elo, center = constants
    fbs_home = table.home_is_fbs & ~table.away_is_fbs
    fbs_away = table.away_is_fbs & ~table.home_is_fbs
    home_rating = b_sp * table.home_sp + b_elo * (table.home_elo[:, 0] - center)
    away_rating = b_sp * table.away_sp + b_elo * (table.away_elo[:, 0] - center)
    implied_home = home_rating + hfa * table.home_flag - table.margin
    implied_away = table.margin - hfa * table.home_flag + away_rating
    keep_home = fbs_home & np.isfinite(home_rating) & ~np.isfinite(table.away_elo[:, 0])
    keep_away = fbs_away & np.isfinite(away_rating) & ~np.isfinite(table.home_elo[:, 0])
    implied = np.concatenate([implied_home[keep_home], implied_away[keep_away]])
    if implied.size < MIN_SAMPLE:
        fallback("NON_FBS_RATING", int(implied.size))
    else:
        fitted("NON_FBS_RATING", float(implied.mean()), int(implied.size))


def _fit_tau_growth(
    table: FitTable, sample: Array, beta: Array
) -> tuple[tuple[float, ...], int, float]:
    lagged = (
        sample
        & np.all(np.isfinite(table.home_elo), axis=1)
        & np.all(np.isfinite(table.away_elo), axis=1)
    )
    n = int(lagged.sum())
    if n < MIN_SAMPLE:
        return (), n, 0.0
    d_sp = (table.home_sp - table.away_sp)[lagged]
    flag = table.home_flag[lagged]
    margin = table.margin[lagged]
    variances = []
    for k in range(LAGS + 1):
        d_elo = table.home_elo[lagged, k] - table.away_elo[lagged, k]
        residual = margin - (beta[0] * flag + beta[1] * d_sp + beta[2] * d_elo)
        variances.append(float(np.mean(residual * residual)))
    slope = float(np.polyfit(np.arange(LAGS + 1), np.array(variances), 1)[0])
    return tuple(variances), n, slope


def _fit_tau(
    table: FitTable,
    full: Array,
    beta: Array,
    fitted: Any,
    values: dict[str, float],
    origins: dict[str, str],
    counts: dict[str, int],
) -> tuple[tuple[float, ...], int]:
    variances, n, slope = _fit_tau_growth(table, full, beta)
    if slope > MIN_GROWTH:
        fitted("TAU", math.sqrt(slope), n)
    else:
        values["TAU"], origins["TAU"], counts["TAU"] = ASSUMED_TAU, ASSUMED, n
    return variances, n


def _fit_total(table: FitTable, both_fbs: Array, fitted: Any, fallback: Any) -> None:
    scoring = table.home_off + table.home_def + table.away_off + table.away_def
    usable = both_fbs & np.isfinite(scoring) & np.isfinite(table.total)
    n = int(usable.sum())
    if n < MIN_SAMPLE:
        for key in ("TOTAL_INTERCEPT", "TOTAL_SLOPE", "TOTAL_DEFAULT"):
            fallback(key, n)
        return
    x = np.column_stack([np.ones(n), scoring[usable]])
    beta, _ = _least_squares(x, table.total[usable])
    fitted("TOTAL_INTERCEPT", float(beta[0]), n)
    fitted("TOTAL_SLOPE", float(beta[1]), n)
    totals = table.total[both_fbs & np.isfinite(table.total)]
    fitted("TOTAL_DEFAULT", float(totals.mean()), int(totals.size))


def _fit_elo_probability(table: FitTable, both_fbs: Array, fitted: Any, fallback: Any) -> None:
    d_elo = table.home_elo[:, 0] - table.away_elo[:, 0]
    usable = both_fbs & np.isfinite(d_elo) & (table.margin != 0)
    n = int(usable.sum())
    if n < MIN_SAMPLE:
        fallback("ELO_PROB_SCALE", n)
        fallback("ELO_PROB_HFA", n)
        return
    keys = np.column_stack([np.rint(d_elo[usable]), table.home_flag[usable]])
    cells, inverse = np.unique(keys, axis=0, return_inverse=True)
    games = np.bincount(inverse).astype(float)
    wins = np.bincount(inverse, weights=(table.margin[usable] > 0).astype(float))
    gap, flag = cells[:, 0], cells[:, 1]

    def log_likelihood(scale: int, edge: int) -> float:
        p = np.clip(1.0 / (1.0 + 10.0 ** (-(gap + edge * flag) / scale)), 1e-12, 1 - 1e-12)
        return float(np.sum(wins * np.log(p) + (games - wins) * np.log(1 - p)))

    best = max(
        ((s, h) for s in ELO_SCALE_GRID for h in ELO_HFA_GRID),
        key=lambda pair: (log_likelihood(*pair), -pair[0], -pair[1]),
    )
    refined = max(
        (
            (s, h)
            for s in range(best[0] - ELO_SCALE_REFINE, best[0] + ELO_SCALE_REFINE + 1)
            for h in range(max(0, best[1] - ELO_HFA_REFINE), best[1] + ELO_HFA_REFINE + 1)
        ),
        key=lambda pair: (log_likelihood(*pair), -pair[0], -pair[1]),
    )
    fitted("ELO_PROB_SCALE", float(refined[0]), n)
    fitted("ELO_PROB_HFA", float(refined[1]), n)


def _least_squares(x: Array, y: Array) -> tuple[Array, Array]:
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    return beta, y - x @ beta


def _order(game: GameRow) -> tuple[int, datetime, int]:
    return (game.start_at is not None, game.start_at or datetime.min, game.game_id)


def _rounded(key: str, value: float) -> float:
    if key in ("ELO_CENTER", "ELO_PROB_SCALE", "ELO_PROB_HFA"):
        return float(round(value))
    return float(Decimal(repr(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN))


def _literal(value: float) -> str:
    text = format(Decimal(repr(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN), "f")
    text = text.rstrip("0").rstrip(".") if "." in text else text
    return "0" if text in ("", "-0") else text


def _sql_text(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def render_seed(fit: Fit) -> str:
    """The text of `sql/sim/010_sim_params.sql` for `fit`; the same fit gives the same text."""
    rows = []
    for key in FIT_KEYS:
        note = FIT_NOTES[key]
        rows.append(
            f"  ({_sql_text(key)}, {_literal(fit.values[key])}, {_sql_text(fit.origins[key])}, "
            f"{_sql_text(note)})"
        )
    for key, literal, note in ASSUMED_ROWS:
        rows.append(f"  ({_sql_text(key)}, {literal}, 'ASSUMED', {_sql_text(note)})")
    return (
        SEED_HEADER.format(
            first=fit.first_season, last=fit.last_season, games=f"{fit.counts['B_SP']:,}"
        )
        + ",\n".join(rows)
        + ";\n"
    )


def evaluate(table: FitTable, values: dict[str, float]) -> Evaluation:
    """Predict every game of `table` with FBS involvement one week ahead from `values`."""
    ratings = {
        side: _team_ratings(
            values,
            getattr(table, f"{side}_sp"),
            getattr(table, f"{side}_elo")[:, 0],
            getattr(table, f"{side}_is_fbs"),
        )
        for side in ("home", "away")
    }
    mu = values["HFA"] * table.home_flag + ratings["home"] - ratings["away"]
    sigma = values["SIGMA_1"]
    p_home = np.array([_win_probability(float(m), sigma) for m in mu])
    won = (table.margin > 0).astype(float)
    decided = table.margin != 0
    error = table.margin - mu
    on_line = np.isfinite(table.spread)
    on_line_probability = np.isfinite(table.line_probability) & decided
    line_error = table.margin[on_line] + table.spread[on_line]
    edges = np.linspace(0.0, 1.0, CALIBRATION_BUCKETS + 1)
    index = np.minimum(np.digitize(p_home, edges[1:-1]), CALIBRATION_BUCKETS - 1)
    buckets = tuple(
        (
            float(edges[b]),
            int(((index == b) & decided).sum()),
            float(p_home[(index == b) & decided].mean()) if ((index == b) & decided).any() else 0.0,
            float(won[(index == b) & decided].mean()) if ((index == b) & decided).any() else 0.0,
        )
        for b in range(CALIBRATION_BUCKETS)
    )
    return Evaluation(
        games=len(table),
        margin_rmse=float(np.sqrt(np.mean(error**2))),
        margin_mae=float(np.mean(np.abs(error))),
        margin_bias=float(error.mean()),
        margin_bias_se=float(np.std(error, ddof=1) / math.sqrt(len(table)))
        if len(table) > 1
        else float("nan"),
        brier=float(np.mean((p_home[decided] - won[decided]) ** 2))
        if decided.any()
        else float("nan"),
        line_games=int(on_line.sum()),
        line_rmse=float(np.sqrt(np.mean(line_error**2))) if on_line.any() else float("nan"),
        line_mae=float(np.mean(np.abs(line_error))) if on_line.any() else float("nan"),
        model_rmse_on_line_games=float(np.sqrt(np.mean(error[on_line] ** 2)))
        if on_line.any()
        else float("nan"),
        model_mae_on_line_games=float(np.mean(np.abs(error[on_line])))
        if on_line.any()
        else float("nan"),
        line_prob_games=int(on_line_probability.sum()),
        line_brier=float(
            np.mean((table.line_probability[on_line_probability] - won[on_line_probability]) ** 2)
        )
        if on_line_probability.any()
        else float("nan"),
        model_brier_on_line_games=float(
            np.mean((p_home[on_line_probability] - won[on_line_probability]) ** 2)
        )
        if on_line_probability.any()
        else float("nan"),
        buckets=buckets,
    )


def _team_ratings(values: dict[str, float], sp: Array, elo: Array, is_fbs: Array) -> Array:
    has_sp, has_elo = np.isfinite(sp), np.isfinite(elo)
    centered = np.where(has_elo, elo - values["ELO_CENTER"], 0.0)
    blended = values["B_SP"] * np.where(has_sp, sp, 0.0) + values["B_ELO"] * centered
    default = np.where(is_fbs, DEFAULT_FBS_RATING, values["NON_FBS_RATING"])
    return np.where(
        has_sp & has_elo,
        blended,
        np.where(
            has_elo,
            values["B_ELO_ONLY"] * centered,
            np.where(has_sp, values["B_SP_ONLY"] * np.where(has_sp, sp, 0.0), default),
        ),
    )


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _win_probability(mu: float, sigma: float) -> float:
    return 0.5 * (_normal_cdf((mu - 0.5) / sigma) + _normal_cdf((mu + 0.5) / sigma))


def summary_lines(fit: Fit, table: FitTable) -> list[str]:
    """The printout of `make sim-fit`: constants with games behind them, lag variances, quality."""
    lines = [
        f"fit on {len(table):,} completed regular-season games of "
        f"{fit.first_season}-{fit.last_season}"
    ]
    for key in FIT_KEYS:
        lines.append(
            f"  {key:<16} {_literal(fit.values[key]):>10}  {fit.origins[key]:<8} "
            f"games {fit.counts[key]:,}"
        )
    if fit.lag_variance:
        lag = ", ".join(f"k={k}: {v:.1f}" for k, v in enumerate(fit.lag_variance))
        lines.append(
            f"  residual variance by weeks of extra Elo lag ({fit.lag_count:,} games): {lag}"
        )
    lines.extend(
        _quality_lines("in-sample, all games with an FBS team", evaluate(table, fit.values))
    )
    cutoff = HOLDOUT_FIRST_SEASON
    train, test = table.where(table.season < cutoff), table.where(table.season >= cutoff)
    if len(train) and len(test):
        try:
            holdout = fit_constants(train)
        except FitError:
            return lines
        lines.append(
            f"holdout: constants refit on {train.season.min()}-{cutoff - 1}, scored on "
            f"{cutoff}-{test.season.max()} (HFA {_literal(holdout.values['HFA'])}, B_SP "
            f"{_literal(holdout.values['B_SP'])}, B_ELO {_literal(holdout.values['B_ELO'])}, "
            f"SIGMA_1 {_literal(holdout.values['SIGMA_1'])})"
        )
        lines.extend(_quality_lines("out-of-sample", evaluate(test, holdout.values)))
    return lines


def _quality_lines(title: str, result: Evaluation) -> list[str]:
    lines = [
        f"{title}: {result.games:,} games, margin RMSE {result.margin_rmse:.2f}, bias "
        f"{result.margin_bias:+.2f}, Brier {result.brier:.4f}"
    ]
    if result.line_games:
        lines.append(
            f"  against CFBD's pregame line on {result.line_games:,} games: line RMSE "
            f"{result.line_rmse:.2f}, model RMSE on the same games "
            f"{result.model_rmse_on_line_games:.2f}"
        )
    lines.append("  calibration (home win probability bucket: games, mean predicted, observed):")
    for low, n, predicted, observed in result.buckets:
        lines.append(
            f"    {low:.1f}-{low + 1 / CALIBRATION_BUCKETS:.1f}: {n:>5,}  "
            f"{predicted:.3f}  {observed:.3f}"
        )
    return lines


def bucket_tolerance(predicted: float, games: int) -> float:
    """Three binomial standard errors around one bucket's mean prediction."""
    return 3 * math.sqrt(predicted * (1 - predicted) / games) if games else float("nan")


def report_lines(table: FitTable) -> list[str]:
    """Out-of-sample 2024-25 score-model review; observations never block deployment."""
    train = table.where((table.season >= FIRST_SEASON) & (table.season < HOLDOUT_FIRST_SEASON))
    held_out = table.where(
        (table.season >= HOLDOUT_FIRST_SEASON)
        & (table.season <= LAST_SEASON)
        & table.home_is_fbs
        & table.away_is_fbs
    )
    if len(train) == 0 or len(held_out) == 0:
        raise FitError("report needs 2014-2023 training games and 2024-2025 FBS holdout games")
    trained = fit_constants(train)
    scored = evaluate(held_out, trained.values)
    lines = [
        "Calibration report (stored data only, 0 CFBD calls; observations are not gates)",
        f"2014-2023 fit: {len(train):,} completed games; "
        f"2024-2025 holdout: {scored.games:,} FBS-vs-FBS regular-season games, one week ahead",
        f"holdout margin: MAE {scored.margin_mae:.3f} points, RMSE {scored.margin_rmse:.3f}, "
        f"bias {scored.margin_bias:+.3f} ± {3 * scored.margin_bias_se:.3f} (3 SE)",
        f"holdout home-win Brier: {scored.brier:.5f} (ties excluded)",
        f"stored spread comparison ({scored.line_games:,} same games): model MAE "
        f"{scored.model_mae_on_line_games:.3f}, line MAE {scored.line_mae:.3f} points",
        f"stored line-probability comparison ({scored.line_prob_games:,} same games): "
        f"model Brier {scored.model_brier_on_line_games:.5f}, "
        f"line Brier {scored.line_brier:.5f}",
        "ten home-win probability buckets (predicted, observed, 3-SE tolerance):",
    ]
    outside: list[str] = []
    for low, games, predicted, observed in scored.buckets:
        tolerance = bucket_tolerance(predicted, games)
        flag = games > 0 and abs(observed - predicted) > tolerance
        if flag:
            outside.append(f"{low:.1f}-{low + 0.1:.1f}")
        lines.append(
            f"  {low:.1f}-{low + 0.1:.1f}: n={games:,}, predicted={predicted:.3f}, "
            f"observed={observed:.3f}, ±{tolerance:.3f}" + (" REVIEW" if flag else "")
        )
    lines.append(
        f"Elo-lag residual variance on {trained.lag_count:,} training games "
        f"(TAU={trained.values['TAU']:.4f}, {trained.origins['TAU']}):"
    )
    for k, variance in enumerate(trained.lag_variance):
        lines.append(
            f"  k={k}: observed {variance:.2f}, "
            f"TAU line {trained.lag_variance[0] + trained.values['TAU'] ** 2 * k:.2f}"
        )
    triggers = []
    if outside:
        triggers.append("bucket(s) outside 3 SE: " + ", ".join(outside))
    if abs(scored.margin_bias) > 1.5:
        triggers.append(f"|bias| > 1.5 points ({scored.margin_bias:+.3f})")
    if scored.line_prob_games and (scored.model_brier_on_line_games - scored.line_brier) > 0.03:
        triggers.append("model Brier exceeds stored line by > 0.03")
    lines.append("review: " + ("; ".join(triggers) if triggers else "no threshold triggered"))
    return lines


GAME_QUERY = """
SELECT "SEASON", "WEEK_SEQ", "START_AT", "GAME_ID", "HOME_TEAM_ID", "AWAY_TEAM_ID",
       "HOME_CLASSIFICATION", "AWAY_CLASSIFICATION", "IS_NEUTRAL_SITE", "HOME_POINTS",
       "AWAY_POINTS",
       "HOME_PREGAME_ELO", "AWAY_PREGAME_ELO", "HOME_POSTGAME_ELO", "AWAY_POSTGAME_ELO",
       "PREGAME_SPREAD", "HOME_PREGAME_WIN_PROB"
  FROM "{model}"."GAME"
 WHERE "SEASON" BETWEEN {first} AND {last}
   AND "SEASON_TYPE" = 'regular'
   AND "HAS_TEAMS"
   AND "INVOLVES_FBS"
"""
RATING_QUERY = """
SELECT "SEASON", "TEAM_ID", "SP_RATING", "OFFENSE_RATING", "DEFENSE_RATING"
  FROM "{model}"."RATING_SP"
 WHERE "SEASON" BETWEEN {first} AND {last} AND "TEAM_ID" IS NOT NULL
"""


def read_inputs(
    connection: Any, model_schema: str, first: int = FIRST_SEASON, last: int = LAST_SEASON
) -> tuple[list[GameRow], list[SpRow]]:
    """The games of `first`..`last` and the SP+ ratings of the seasons before them, read-only."""
    game_rows = connection.execute(
        GAME_QUERY.format(model=model_schema, first=first, last=last)
    ).fetchall()
    rating_rows = connection.execute(
        RATING_QUERY.format(model=model_schema, first=first - 1, last=last - 1)
    ).fetchall()
    games = [
        GameRow(
            int(str(r[0])),
            int(str(r[1])),
            _moment(r[2]),
            int(str(r[3])),
            None if r[4] is None else int(str(r[4])),
            None if r[5] is None else int(str(r[5])),
            r[6],
            r[7],
            bool(r[8]),
            None if r[9] is None else int(str(r[9])),
            None if r[10] is None else int(str(r[10])),
            r[11],
            r[12],
            r[13],
            r[14],
            r[15],
            r[16],
        )
        for r in game_rows
    ]
    ratings = [
        SpRow(int(str(r[0])), int(str(r[1])), float(str(r[2])), number(r[3]), number(r[4]))
        for r in rating_rows
        if r[2] is not None
    ]
    return games, ratings


def _moment(value: object) -> datetime | None:
    if value is None:
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
