"""Season simulation engine: one numpy-vectorized batch of simulated seasons.

Pure numpy and the standard library, without relative imports: the body of the `PYTHON3` SET
script `CFB_INFRA.SIM_ENGINE` is this file followed by `engine/udf.py`, and the host tests import
it as `engine.core`. The contract is `sim-engine/engine-contract` and `sim-engine/selection-rules`.
It started as the S3 prototype (`spikes/s3/engine_proto.py`) and keeps its vectorized style, its
tiebreakers, resume and bracket; it adds fixed-shape draws from two child streams, shared team
offsets, the 2026 selection rules and a tagged stage frame that carries the per-batch tallies.

`run_batch` is the whole call: parse and validate the model rows, simulate one batch, and return
the `SIM_STAGE` columns (one DataFrame's worth) including the batch's `B` row.

BLAS is pinned to one thread before numpy loads, since every UDF instance already owns one core
slot; the leverage tally is the only matrix product.
"""

# ruff: noqa: E402
from __future__ import annotations

import os

for _blas_threads in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ.setdefault(_blas_threads, "1")

import hashlib
import math
import platform
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[Any]

RUN_COLUMNS = (
    ("RUN_ID", "DECIMAL(18,0)"),
    ("RUN_SEED", "DECIMAL(18,0)"),
    ("SIMS_PER_BATCH", "DECIMAL(9,0)"),
    ("BATCH_ID", "DECIMAL(9,0)"),
)
MODEL_COLUMNS = (
    ("ROW_KIND", "VARCHAR(5) ASCII"),
    ("GAME_ROLE", "VARCHAR(12) ASCII"),
    ("TEAM_IDX", "DECIMAL(5,0)"),
    ("TEAM_ID", "DECIMAL(18,0)"),
    ("CONFERENCE", "VARCHAR(100) UTF8"),
    ("TIER", "VARCHAR(8) ASCII"),
    ("HAS_CCG", "BOOLEAN"),
    ("IS_ELIGIBLE", "BOOLEAN"),
    ("RATING", "DECIMAL(6,2)"),
    ("SOS", "DECIMAL(6,2)"),
    ("WINS", "DECIMAL(2,0)"),
    ("LOSSES", "DECIMAL(2,0)"),
    ("CONF_WINS", "DECIMAL(2,0)"),
    ("CONF_LOSSES", "DECIMAL(2,0)"),
    ("CONF_PD", "DECIMAL(5,0)"),
    ("CAPPED_PD", "DECIMAL(5,0)"),
    ("GAME_IDX", "DECIMAL(5,0)"),
    ("HOME_IDX", "DECIMAL(5,0)"),
    ("AWAY_IDX", "DECIMAL(5,0)"),
    ("CONF_GAME", "BOOLEAN"),
    ("MU", "DECIMAL(7,2)"),
    ("TEAM_SIGMA", "DECIMAL(5,2)"),
    ("MARGIN", "DECIMAL(4,0)"),
    ("PARAM_KEY", "VARCHAR(40) ASCII"),
    ("PARAM_VALUE", "DECIMAL(12,4)"),
)
STAGE_COLUMNS = (
    ("RUN_ID", "DECIMAL(18,0)"),
    ("BATCH_ID", "DECIMAL(9,0)"),
    ("ROW_KIND", "CHAR(1) ASCII"),
    ("SIM_ID", "DECIMAL(18,0)"),
    ("TEAM_ID", "DECIMAL(18,0)"),
    ("GAME_IDX", "DECIMAL(5,0)"),
    ("SEED", "DECIMAL(2,0)"),
    ("BYE", "BOOLEAN"),
    ("REACHED_ROUND", "DECIMAL(1,0)"),
    ("CHAMPION", "BOOLEAN"),
    ("WINS", "DECIMAL(2,0)"),
    ("N", "DECIMAL(18,0)"),
    ("OUTCOMES", "VARCHAR(2000) ASCII"),
    ("INFO", "VARCHAR(2000) UTF8"),
)

KIND_TEAM, KIND_GAME, KIND_PARAM = "TEAM", "GAME", "PARAM"
ROW_KINDS = (KIND_TEAM, KIND_GAME, KIND_PARAM)
ROLE_REMAINING, ROLE_PLAYED_CONF = "REMAINING", "PLAYED_CONF"
GAME_ROLES = (ROLE_REMAINING, ROLE_PLAYED_CONF)
TIER_P4, TIER_G6, TIER_IND, TIER_NON_FBS = "P4", "G6", "IND", "NON_FBS"
TIERS = (TIER_P4, TIER_G6, TIER_IND, TIER_NON_FBS)
CONFERENCE_TIERS = (TIER_P4, TIER_G6)

STAGE_TEAM, STAGE_PATH, STAGE_WINS, STAGE_CONF_CHAMP = "T", "P", "W", "C"
STAGE_GAME, STAGE_LEVERAGE, STAGE_BATCH = "G", "L", "B"

AUTO_BID_TOP_CHAMPIONS, AUTO_BID_POWER_CHAMPIONS_PLUS_G6 = 1, 2
G6_BID_CHAMPION, G6_BID_TEAM = 1, 2
REQUIRED_PARAMS = (
    "hfa",
    "game_sigma",
    "post_team_sigma",
    "ot_margin",
    "margin_cap",
    "w_win_pct",
    "w_sos",
    "w_pd",
    "w_rating",
    "auto_bid_rule",
    "auto_bids",
    "g6_bid",
    "guarantee_team_idx",
    "guarantee_rank",
    "min_conference_teams",
)
OPTIONAL_PARAMS = ("fail_batch",)
INTEGER_PARAMS = (
    "ot_margin",
    "margin_cap",
    "auto_bid_rule",
    "auto_bids",
    "g6_bid",
    "guarantee_team_idx",
    "guarantee_rank",
    "min_conference_teams",
    "fail_batch",
)

CFP_SIZE = 12
N_BYES = 4
N_BRACKET_GAMES = 11
FIRST_ROUND_SEEDS = ((5, 12), (6, 11), (7, 10), (8, 9))
QUARTERFINAL_FEEDS = (3, 2, 1, 0)
SEMIFINAL_PAIRS = ((0, 3), (1, 2))
ROUND_FIRST, ROUND_QUARTER, ROUND_SEMI, ROUND_FINAL = 1, 2, 3, 4
FIRST_LISTED_TIE_PROB = 0.5
MAX_REMAINING_GAMES = 2000
COMPOSITE_KEY_LIMIT = 2**62
FLOAT32_EXACT_LIMIT = 2**24
SHOWN_IDS = 10
NULL_TYPE_NAMES = ("NAType", "NaTType")


class ModelError(ValueError):
    """An invalid engine input, rejected and never repaired: '<rule>: <offending rows>'."""

    def __init__(self, rule: str, detail: str = "") -> None:
        super().__init__(f"{rule}: {detail}" if detail else rule)
        self.rule = rule


class BatchFailure(RuntimeError):
    """The `fail_batch` test hook stopped this batch."""


@dataclass(frozen=True)
class RunParams:
    """The run columns, constant within one SET call."""

    run_id: int
    run_seed: int
    sims_per_batch: int
    batch_id: int

    @property
    def first_sim_id(self) -> int:
        return self.batch_id * self.sims_per_batch


@dataclass(frozen=True)
class Rules:
    """The `PARAM` rows: score-model constants, resume weights and the season's selection rules."""

    hfa: float
    game_sigma: float
    post_team_sigma: float
    ot_margin: int
    margin_cap: int
    w_win_pct: float
    w_sos: float
    w_pd: float
    w_rating: float
    auto_bid_rule: int
    auto_bids: int
    g6_bid: int
    guarantee_team_idx: int
    guarantee_rank: int
    min_conference_teams: int
    fail_batch: int | None


@dataclass(frozen=True, eq=False)
class Model:
    """The parsed model of one run: T teams, C conferences (sorted names), G remaining games.

    `team_conf` indexes `conf_names` (-1: no conference); `tier` holds the tier names. The `*0`
    arrays are the records so far. `played_*` are the completed conference games in `GAME_IDX`
    order (later meetings last). `params` keeps the raw values, sorted, for the digest.
    """

    team_id: Array
    tier: Array
    team_conf: Array
    conf_names: tuple[str, ...]
    conf_tier: Array
    conf_has_ccg: Array
    eligible: Array
    rating: Array
    sos: Array
    wins0: Array
    losses0: Array
    conf_wins0: Array
    conf_losses0: Array
    conf_pd0: Array
    capped_pd0: Array
    home: Array
    away: Array
    conf_game: Array
    mu: Array
    team_sigma: Array
    played_home: Array
    played_away: Array
    played_margin: Array
    rules: Rules
    params: tuple[tuple[str, float], ...]

    @property
    def n_teams(self) -> int:
        return int(self.team_id.size)

    @property
    def n_games(self) -> int:
        return int(self.home.size)

    @property
    def n_confs(self) -> int:
        return len(self.conf_names)


MODEL_ARRAY_FIELDS = (
    "team_id",
    "team_conf",
    "conf_has_ccg",
    "eligible",
    "rating",
    "sos",
    "wins0",
    "losses0",
    "conf_wins0",
    "conf_losses0",
    "conf_pd0",
    "capped_pd0",
    "home",
    "away",
    "conf_game",
    "mu",
    "team_sigma",
    "played_home",
    "played_away",
    "played_margin",
)


@dataclass(frozen=True, eq=False)
class Draws:
    """All randomness of one batch, in fixed shapes (S sims, G games, T teams, C conferences)."""

    z: Array
    u: Array
    offsets: Array
    z_post: Array
    u_post: Array


@dataclass(frozen=True, eq=False)
class Tallies:
    """Per-batch counts: `W` (team, wins, n), `C` (team, n), `G` (n per game), `L` (team, game, n).

    Teams are roster indices; the stage rows carry their `TEAM_ID`.
    """

    win_team: Array
    win_total: Array
    win_n: Array
    champ_team: Array
    champ_n: Array
    home_wins: Array
    lev_team: Array
    lev_game: Array
    lev_n: Array


@dataclass(frozen=True, eq=False)
class BatchResult:
    """One simulated batch. (S, T) arrays are indexed by roster index, (S, K) by `crown`.

    `margins` (S, G) are the remaining games' margins, home minus away, never 0. `wins` and the
    other records count regular-season games only. `crown` lists the conferences that crown a
    champion. `seed_teams` (S, 12) holds seed k + 1 in column k, with `seed_auto` (automatic
    bid) and `seed_reached` (round reached) aligned to it; `title` is the national champion.
    """

    sim_ids: Array
    margins: Array
    home_win: Array
    wins: Array
    conf_wins: Array
    conf_pd: Array
    capped_pd: Array
    crown: Array
    conf_champion: Array
    resume_order: Array
    seed_teams: Array
    seed_auto: Array
    seed_reached: Array
    title: Array
    tallies: Tallies


@dataclass(frozen=True, eq=False)
class _Plan:
    n_remaining: Array
    crown: Array
    crown_ccg: Array
    crown_members: tuple[Array, ...]
    conf_cols: Array
    swing: Array
    away_games: Array
    away_conf_games: Array
    h2h_teams: Array
    ineligible: Array
    block_size: int
    pct_scale: Array
    pct_rank: Array
    n_pct: int
    rating_rank: Array
    pair_game: Array
    pair_played_net: Array
    resume_perm: Array


@dataclass(frozen=True, eq=False)
class _Records:
    home_win: Array
    wins: Array
    losses: Array
    conf_wins: Array
    conf_pd: Array
    capped_pd: Array


def run_batch(columns: Mapping[str, Any], vm: str, node: str) -> dict[str, Array]:
    """One engine call: parse and validate, simulate the batch, return the `SIM_STAGE` columns.

    `columns` holds the SET group's run and model columns by name (any case, any row order).
    The result lists every stage column in `STAGE_COLUMNS` order; the last row is the `B` row.
    """
    started = time.perf_counter()
    run = parse_run(columns)
    model = parse_model(columns)
    result = simulate_batch(model, run)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    info = batch_info(vm, node, model_digest(model), result_checksum(result), elapsed_ms)
    return stage_columns(run, model, result, info)


def parse_run(columns: Mapping[str, Any]) -> RunParams:
    """The run columns as exact integers; each must be one constant value per call."""
    lowered = _lowered(columns, RUN_COLUMNS)
    values = []
    for name, _ in RUN_COLUMNS:
        column = lowered[name.lower()]
        if column.size == 0:
            raise ModelError("no model rows")
        ints = _ints(column, name, np.arange(column.size))
        if (ints != ints[0]).any():
            shown = sorted(set(ints.tolist()))[:SHOWN_IDS]
            raise ModelError("run columns not constant", f"{name} has {shown}")
        values.append(int(ints[0]))
    run = RunParams(*values)
    if run.run_seed < 0 or run.sims_per_batch < 1 or run.batch_id < 0:
        raise ModelError("run columns out of range", repr(run))
    return run


def parse_model(columns: Mapping[str, Any]) -> Model:
    """Build and validate the model from its tagged rows (any row order, names any case)."""
    lowered = _lowered(columns, MODEL_COLUMNS)
    kind = _strings(lowered["row_kind"], "ROW_KIND", np.arange(lowered["row_kind"].size))
    unknown = sorted(set(kind.tolist()) - set(ROW_KINDS))
    if unknown:
        raise ModelError("unknown row kind", ", ".join(unknown))
    teams = _team_fields(lowered, np.flatnonzero(kind == KIND_TEAM))
    games = _game_fields(lowered, np.flatnonzero(kind == KIND_GAME))
    n_teams = int(teams["team_id"].size)
    rules, params = _rules(lowered, np.flatnonzero(kind == KIND_PARAM), n_teams)
    model = Model(**teams, **games, rules=rules, params=params)
    _validate(model)
    return model


def model_digest(model: Model) -> str:
    """SHA-256 over the parsed model: equal on the host and in the database for equal inputs."""
    digest = hashlib.sha256()
    digest.update("\x1f".join(model.conf_names).encode())
    digest.update("\x1f".join(model.tier.tolist()).encode())
    for name in MODEL_ARRAY_FIELDS:
        values = np.ascontiguousarray(getattr(model, name))
        digest.update(f"{name}:{values.dtype.str}:{values.shape}".encode())
        digest.update(values.tobytes())
    digest.update(repr(model.params).encode())
    return digest.hexdigest()


def simulate_batch(model: Model, run: RunParams) -> BatchResult:
    """Simulate `sims_per_batch` seasons of batch `batch_id`, fully determined by the run columns.

    Season, standings, championship games, resume, selection, bracket and tallies, vectorized
    across the batch. `SIM_ID` = `batch_id` x `sims_per_batch` + k.
    """
    if model.rules.fail_batch is not None and model.rules.fail_batch == run.batch_id:
        raise BatchFailure(f"fail_batch: batch {run.batch_id} failed by the test hook")
    plan = _plan(model)
    draws = sample_draws(model, run)
    margins = _season_margins(model, draws)
    records = _records(model, plan, margins)
    top_two = _standings_top_two(model, plan, records, margins)
    champion, runner_up, ccg_margin = _championships(model, plan, draws, top_two)
    order, rank = _resume_order(model, plan, records, champion, runner_up, ccg_margin)
    seed_teams, seed_auto = _select_and_seed(model, plan, champion, order, rank)
    seed_reached, title = _play_bracket(model, draws, seed_teams)
    return BatchResult(
        sim_ids=run.first_sim_id + np.arange(run.sims_per_batch, dtype=np.int64),
        margins=margins,
        home_win=records.home_win,
        wins=records.wins,
        conf_wins=records.conf_wins,
        conf_pd=records.conf_pd,
        capped_pd=records.capped_pd,
        crown=plan.crown,
        conf_champion=champion,
        resume_order=order,
        seed_teams=seed_teams,
        seed_auto=seed_auto,
        seed_reached=seed_reached,
        title=title,
        tallies=_tallies(model, records, champion, seed_teams),
    )


def sample_draws(model: Model, run: RunParams) -> Draws:
    """Fixed-shape draws: `SeedSequence([seed, batch]).spawn(2)` feeds a season and a postseason
    `Generator(Philox)`. Season: normals (S, G), uniforms (S, G), team offsets (S, T).
    Postseason: normals (S, C + 11), uniforms (S, C + 11); columns 0..C-1 are the conferences'
    championship games, C..C+10 the bracket games."""
    season_seq, post_seq = np.random.SeedSequence([run.run_seed, run.batch_id]).spawn(2)
    season = np.random.Generator(np.random.Philox(season_seq))
    post = np.random.Generator(np.random.Philox(post_seq))
    shape = (run.sims_per_batch, model.n_games)
    z = season.standard_normal(shape)
    u = season.random(shape)
    offsets = season.standard_normal((run.sims_per_batch, model.n_teams))
    post_shape = (run.sims_per_batch, model.n_confs + N_BRACKET_GAMES)
    z_post = post.standard_normal(post_shape)
    u_post = post.random(post_shape)
    return Draws(z, u, offsets, z_post, u_post)


def result_checksum(result: BatchResult) -> str:
    """SHA-256 over the batch's outcomes: sim ids, outcome strings, wins, champions, playoffs."""
    digest = hashlib.sha256()
    fields = (
        (result.sim_ids, np.int64),
        (result.home_win, np.uint8),
        (result.wins, np.int16),
        (result.conf_champion, np.int64),
        (result.seed_teams, np.int64),
        (result.seed_reached, np.int8),
        (result.title, np.int64),
    )
    for values, dtype in fields:
        array = np.ascontiguousarray(values, dtype=dtype)
        digest.update(f"{array.shape}".encode())
        digest.update(array.tobytes())
    return digest.hexdigest()


def batch_info(vm: str, node: str, digest: str, checksum: str, elapsed_ms: int) -> str:
    """The `INFO` text of the batch's `B` row."""
    return (
        f"vm={vm};node={node};digest={digest};checksum={checksum};"
        f"numpy={np.__version__};python={platform.python_version()};ms={elapsed_ms}"
    )


def outcome_strings(home_win: Array) -> Array:
    """One ASCII string per season, '1' = home win, in `GAME_IDX` order; None when G = 0."""
    n_sims, n_games = home_win.shape
    if n_games == 0:
        return np.full(n_sims, None, dtype=object)
    codes = np.ascontiguousarray(home_win, dtype=np.uint8) + np.uint8(ord("0"))
    packed = codes.view(f"S{n_games}").ravel()
    return np.array([value.decode("ascii") for value in packed.tolist()], dtype=object)


def stage_columns(run: RunParams, model: Model, result: BatchResult, info: str) -> dict[str, Array]:
    """The tagged `SIM_STAGE` rows of one batch: T, P, W, C, G, L, then the B row.

    Columns that do not apply to a kind carry 0 or FALSE; only `OUTCOMES` and `INFO` carry None.
    """
    tallies = result.tallies
    team_id = model.team_id
    n_sims = result.sim_ids.size
    seeds = np.tile(np.arange(1, CFP_SIZE + 1, dtype=np.int64), n_sims)
    blocks = (
        (
            STAGE_TEAM,
            {
                "SIM_ID": np.repeat(result.sim_ids, CFP_SIZE),
                "TEAM_ID": team_id[result.seed_teams].ravel(),
                "SEED": seeds,
                "BYE": seeds <= N_BYES,
                "REACHED_ROUND": result.seed_reached.ravel(),
                "CHAMPION": (result.seed_teams == result.title[:, None]).ravel(),
            },
        ),
        (STAGE_PATH, {"SIM_ID": result.sim_ids, "OUTCOMES": outcome_strings(result.home_win)}),
        (
            STAGE_WINS,
            {
                "TEAM_ID": team_id[tallies.win_team],
                "WINS": tallies.win_total,
                "N": tallies.win_n,
            },
        ),
        (STAGE_CONF_CHAMP, {"TEAM_ID": team_id[tallies.champ_team], "N": tallies.champ_n}),
        (
            STAGE_GAME,
            {"GAME_IDX": np.arange(model.n_games, dtype=np.int64), "N": tallies.home_wins},
        ),
        (
            STAGE_LEVERAGE,
            {
                "TEAM_ID": team_id[tallies.lev_team],
                "GAME_IDX": tallies.lev_game,
                "N": tallies.lev_n,
            },
        ),
        (STAGE_BATCH, {"INFO": np.array([info], dtype=object)}),
    )
    return _assemble(run, blocks)


def _assemble(run: RunParams, blocks: tuple[tuple[str, dict[str, Array]], ...]) -> dict[str, Array]:
    lengths = [len(next(iter(values.values()))) for _, values in blocks]
    total = sum(lengths)
    columns: dict[str, Array] = {
        "RUN_ID": np.full(total, run.run_id, dtype=np.int64),
        "BATCH_ID": np.full(total, run.batch_id, dtype=np.int64),
        "ROW_KIND": np.repeat(np.array([kind for kind, _ in blocks], dtype=object), lengths),
    }
    for name, sql_type in STAGE_COLUMNS[3:]:
        parts = [
            values[name] if name in values else _sentinel(sql_type, length)
            for (_, values), length in zip(blocks, lengths, strict=True)
        ]
        columns[name] = np.concatenate([_typed(part, sql_type) for part in parts])
    return columns


def _sentinel(sql_type: str, length: int) -> Array:
    if sql_type == "BOOLEAN":
        return np.zeros(length, dtype=bool)
    if sql_type.startswith(("VARCHAR", "CHAR")):
        return np.full(length, None, dtype=object)
    return np.zeros(length, dtype=np.int64)


def _typed(values: Array, sql_type: str) -> Array:
    if sql_type == "BOOLEAN":
        return np.asarray(values, dtype=bool)
    if sql_type.startswith(("VARCHAR", "CHAR")):
        return np.asarray(values, dtype=object)
    return np.asarray(values, dtype=np.int64)


def _lowered(columns: Mapping[str, Any], expected: tuple[tuple[str, str], ...]) -> dict[str, Array]:
    lowered = {str(name).lower(): np.asarray(values) for name, values in columns.items()}
    missing = [name for name, _ in expected if name.lower() not in lowered]
    if missing:
        raise ModelError("missing column", ", ".join(missing))
    return lowered


def _is_null(value: Any) -> bool:
    if value is None or type(value).__name__ in NULL_TYPE_NAMES:
        return True
    return isinstance(value, float) and math.isnan(value)


def _shown(ids: Array) -> str:
    values = [int(value) for value in ids[:SHOWN_IDS]]
    more = f" and {ids.size - SHOWN_IDS} more" if ids.size > SHOWN_IDS else ""
    return f"{values}{more}"


def _check_nulls(values: Array, what: str, ids: Array) -> None:
    nulls = np.array([_is_null(value) for value in values], dtype=bool)
    if nulls.any():
        raise ModelError("missing value", f"{what} at {_shown(ids[nulls])}")


def _floats(values: Array, what: str, ids: Array) -> Array:
    _check_nulls(values, what, ids)
    floats: Array = np.array([float(value) for value in values], dtype=np.float64)
    return floats + 0.0


def _ints(values: Array, what: str, ids: Array) -> Array:
    _check_nulls(values, what, ids)
    parsed = [_exact_int(value) for value in values]
    bad = np.array([value is None for value in parsed], dtype=bool)
    if bad.any():
        raise ModelError("not an integer", f"{what} at {_shown(ids[bad])}")
    return np.array(parsed, dtype=np.int64)


def _exact_int(value: Any) -> int | None:
    if isinstance(value, bool | np.bool_):
        return None
    if isinstance(value, int | np.integer):
        return int(value)
    number = value if isinstance(value, Decimal) else Decimal(float(value))
    if not number.is_finite() or number != number.to_integral_value():
        return None
    return int(number)


def _flags(values: Array, what: str, ids: Array) -> Array:
    _check_nulls(values, what, ids)
    return np.array([bool(value) for value in values], dtype=bool)


def _strings(values: Array, what: str, ids: Array) -> Array:
    _check_nulls(values, what, ids)
    return np.array([str(value) for value in values], dtype=object)


def _optional_strings(values: Array) -> Array:
    return np.array([None if _is_null(value) else str(value) for value in values], dtype=object)


def _dense_order(index: Array, what: str) -> Array:
    order: Array = np.argsort(index, kind="stable")
    if not np.array_equal(index[order], np.arange(index.size)):
        raise ModelError("index not dense", f"{what} must be 0..{index.size - 1} once each")
    return order


def _team_fields(columns: dict[str, Array], rows: Array) -> dict[str, Any]:
    raw_idx = _ints(columns["team_idx"][rows], "TEAM_IDX of TEAM rows", rows)
    order = _dense_order(raw_idx, "TEAM_IDX of TEAM rows")
    ids = np.arange(rows.size)

    def pick(name: str) -> Array:
        picked: Array = columns[name][rows][order]
        return picked

    def label(name: str) -> str:
        return f"{name} of TEAM rows with TEAM_IDX"

    team_id = _ints(pick("team_id"), label("TEAM_ID"), ids)
    unique_ids, counts = np.unique(team_id, return_counts=True)
    if (counts > 1).any():
        raise ModelError("duplicate TEAM_ID", _shown(unique_ids[counts > 1]))
    tier = _strings(pick("tier"), label("TIER"), ids)
    bad_tier = ~np.isin(tier, TIERS)
    if bad_tier.any():
        raise ModelError("unknown tier", f"TEAM_IDX {_shown(ids[bad_tier])}")
    conference = _optional_strings(pick("conference"))
    has_ccg = _flags(pick("has_ccg"), label("HAS_CCG"), ids)
    eligible = _flags(pick("is_eligible"), label("IS_ELIGIBLE"), ids)
    non_fbs_eligible = eligible & (tier == TIER_NON_FBS)
    if non_fbs_eligible.any():
        raise ModelError(
            "non-FBS team marked eligible", f"TEAM_IDX {_shown(ids[non_fbs_eligible])}"
        )
    conf_names, team_conf, conf_tier, conf_has_ccg = _conferences(conference, tier, has_ccg)
    return {
        "team_id": team_id,
        "tier": tier,
        "team_conf": team_conf,
        "conf_names": conf_names,
        "conf_tier": conf_tier,
        "conf_has_ccg": conf_has_ccg,
        "eligible": eligible,
        "rating": _floats(pick("rating"), label("RATING"), ids),
        "sos": _floats(pick("sos"), label("SOS"), ids),
        "wins0": _ints(pick("wins"), label("WINS"), ids),
        "losses0": _ints(pick("losses"), label("LOSSES"), ids),
        "conf_wins0": _ints(pick("conf_wins"), label("CONF_WINS"), ids),
        "conf_losses0": _ints(pick("conf_losses"), label("CONF_LOSSES"), ids),
        "conf_pd0": _ints(pick("conf_pd"), label("CONF_PD"), ids),
        "capped_pd0": _ints(pick("capped_pd"), label("CAPPED_PD"), ids),
    }


def _conferences(
    conference: Array, tier: Array, has_ccg: Array
) -> tuple[tuple[str, ...], Array, Array, Array]:
    member = np.isin(tier, CONFERENCE_TIERS)
    has_conf = np.array([value is not None for value in conference], dtype=bool)
    mismatch = member != has_conf
    if mismatch.any():
        raise ModelError(
            "conference does not match tier",
            f"TEAM_IDX {_shown(np.flatnonzero(mismatch))}: P4 and G6 teams need a conference, "
            "IND and NON_FBS teams have none",
        )
    conf_names = tuple(sorted({str(value) for value in conference[member]}))
    position = {name: index for index, name in enumerate(conf_names)}
    team_conf = np.array(
        [position[str(value)] if value is not None else -1 for value in conference], dtype=np.int64
    )
    conf_tier = np.empty(len(conf_names), dtype=object)
    conf_has_ccg = np.zeros(len(conf_names), dtype=bool)
    for index, name in enumerate(conf_names):
        members = team_conf == index
        tiers = sorted(set(tier[members].tolist()))
        if len(tiers) != 1:
            raise ModelError("conference does not match tier", f"{name} mixes {tiers}")
        flags = set(has_ccg[members].tolist())
        if len(flags) != 1:
            raise ModelError("HAS_CCG not constant within a conference", name)
        conf_tier[index] = tiers[0]
        conf_has_ccg[index] = flags.pop()
    return conf_names, team_conf, conf_tier, conf_has_ccg


def _game_fields(columns: dict[str, Array], rows: Array) -> dict[str, Any]:
    role = _strings(columns["game_role"][rows], "GAME_ROLE of GAME rows", rows)
    unknown = sorted(set(role.tolist()) - set(GAME_ROLES))
    if unknown:
        raise ModelError("unknown game role", ", ".join(unknown))
    remaining = rows[role == ROLE_REMAINING]
    played = rows[role == ROLE_PLAYED_CONF]
    remaining_idx = _ints(columns["game_idx"][remaining], "GAME_IDX of REMAINING rows", remaining)
    remaining = remaining[_dense_order(remaining_idx, "GAME_IDX of REMAINING rows")]
    played_idx = _ints(columns["game_idx"][played], "GAME_IDX of PLAYED_CONF rows", played)
    unique_idx, counts = np.unique(played_idx, return_counts=True)
    if (counts > 1).any():
        raise ModelError("duplicate GAME_IDX of PLAYED_CONF rows", _shown(unique_idx[counts > 1]))
    played_order = np.argsort(played_idx, kind="stable")
    played = played[played_order]
    remaining_ids = np.arange(remaining.size)
    played_ids = played_idx[played_order]

    def remaining_values(name: str, parse: Any) -> Array:
        parsed: Array = parse(
            columns[name][remaining],
            f"{name.upper()} of REMAINING rows with GAME_IDX",
            remaining_ids,
        )
        return parsed

    def played_values(name: str) -> Array:
        what = f"{name.upper()} of PLAYED_CONF rows with GAME_IDX"
        return _ints(columns[name][played], what, played_ids)

    return {
        "home": remaining_values("home_idx", _ints),
        "away": remaining_values("away_idx", _ints),
        "conf_game": remaining_values("conf_game", _flags),
        "mu": remaining_values("mu", _floats),
        "team_sigma": remaining_values("team_sigma", _floats),
        "played_home": played_values("home_idx"),
        "played_away": played_values("away_idx"),
        "played_margin": played_values("margin"),
    }


def _rules(
    columns: dict[str, Array], rows: Array, n_teams: int
) -> tuple[Rules, tuple[tuple[str, float], ...]]:
    keys = _strings(columns["param_key"][rows], "PARAM_KEY of PARAM rows", rows).tolist()
    values = _floats(columns["param_value"][rows], "PARAM_VALUE of PARAM rows", rows)
    unknown = sorted(set(keys) - set(REQUIRED_PARAMS) - set(OPTIONAL_PARAMS))
    if unknown:
        raise ModelError("unknown parameter", ", ".join(unknown))
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ModelError("duplicate parameter", ", ".join(duplicates))
    missing = [key for key in REQUIRED_PARAMS if key not in keys]
    if missing:
        raise ModelError("missing parameter", ", ".join(missing))
    raw = {key: float(value) for key, value in zip(keys, values, strict=True)}
    for key in INTEGER_PARAMS:
        if key in raw and not raw[key].is_integer():
            raise ModelError("parameter not an integer", f"{key}={raw[key]}")
    typed: dict[str, Any] = {
        key: int(raw[key]) if key in INTEGER_PARAMS else raw[key] for key in REQUIRED_PARAMS
    }
    fail_batch = int(raw["fail_batch"]) if "fail_batch" in raw else None
    rules = Rules(**typed, fail_batch=fail_batch)
    _validate_rules(rules, n_teams)
    return rules, tuple(sorted(raw.items()))


def _validate_rules(rules: Rules, n_teams: int) -> None:
    if rules.game_sigma <= 0:
        raise ModelError("game_sigma must be positive", f"game_sigma={rules.game_sigma}")
    if rules.post_team_sigma < 0:
        raise ModelError("negative post_team_sigma", f"post_team_sigma={rules.post_team_sigma}")
    for key in ("ot_margin", "margin_cap"):
        if getattr(rules, key) <= 0:
            raise ModelError("parameter must be positive", f"{key}={getattr(rules, key)}")
    codes = (
        ("auto_bid_rule", (AUTO_BID_TOP_CHAMPIONS, AUTO_BID_POWER_CHAMPIONS_PLUS_G6)),
        ("g6_bid", (G6_BID_CHAMPION, G6_BID_TEAM)),
        ("auto_bids", tuple(range(1, CFP_SIZE + 1))),
        ("guarantee_rank", tuple(range(1, CFP_SIZE + 1))),
    )
    for key, allowed in codes:
        if getattr(rules, key) not in allowed:
            raise ModelError("rule code out of range", f"{key}={getattr(rules, key)}")
    if rules.min_conference_teams < 2:
        raise ModelError(
            "rule code out of range", f"min_conference_teams={rules.min_conference_teams}"
        )
    if not -1 <= rules.guarantee_team_idx < n_teams:
        raise ModelError("index out of range", f"guarantee_team_idx={rules.guarantee_team_idx}")


def _validate(model: Model) -> None:
    n_teams = model.n_teams
    remaining_ids = np.arange(model.n_games)
    for role, home, away, ids in (
        (ROLE_REMAINING, model.home, model.away, remaining_ids),
        (ROLE_PLAYED_CONF, model.played_home, model.played_away, None),
    ):
        game_ids = np.arange(home.size) if ids is None else ids
        outside = (home < 0) | (home >= n_teams) | (away < 0) | (away >= n_teams)
        if outside.any():
            raise ModelError(
                "index out of range",
                f"HOME_IDX/AWAY_IDX outside 0..{n_teams - 1} in {role} rows "
                f"(positions {_shown(game_ids[outside])})",
            )
        same = home == away
        if same.any():
            raise ModelError(
                "home team is away team", f"{role} rows (positions {_shown(game_ids[same])})"
            )
    if model.n_games > MAX_REMAINING_GAMES:
        raise ModelError("too many remaining games", f"{model.n_games} > {MAX_REMAINING_GAMES}")
    negative = model.team_sigma < 0
    if negative.any():
        raise ModelError("negative TEAM_SIGMA", f"GAME_IDX {_shown(remaining_ids[negative])}")
    _validate_conference_games(model)
    n_eligible = int(model.eligible.sum())
    if n_eligible < CFP_SIZE:
        raise ModelError("fewer than 12 eligible teams", f"{n_eligible} eligible")
    most_auto = _most_automatic_bids(model)
    if most_auto > CFP_SIZE:
        raise ModelError("more automatic bids than places", f"up to {most_auto}")


def _validate_conference_games(model: Model) -> None:
    conf = model.team_conf
    for role, home, away in (
        (ROLE_REMAINING, model.home[model.conf_game], model.away[model.conf_game]),
        (ROLE_PLAYED_CONF, model.played_home, model.played_away),
    ):
        apart = (conf[home] < 0) | (conf[home] != conf[away])
        if apart.any():
            positions = (
                np.flatnonzero(model.conf_game)[apart]
                if role == ROLE_REMAINING
                else np.flatnonzero(apart)
            )
            raise ModelError(
                "conference game between different conferences",
                f"{role} rows (positions {_shown(positions)})",
            )


def _crowning_conferences(model: Model) -> Array:
    members = model.team_conf[(model.team_conf >= 0) & model.eligible]
    sizes = np.bincount(members, minlength=model.n_confs)
    crown: Array = np.flatnonzero(sizes >= model.rules.min_conference_teams)
    return crown


def _most_automatic_bids(model: Model) -> int:
    rules = model.rules
    crown = _crowning_conferences(model)
    guarantee = int(rules.guarantee_team_idx >= 0)
    if rules.auto_bid_rule == AUTO_BID_TOP_CHAMPIONS:
        return min(rules.auto_bids, int(crown.size)) + guarantee
    power = int((model.conf_tier[crown] == TIER_P4).sum())
    if rules.g6_bid == G6_BID_TEAM:
        g6_bid = int((model.eligible & (model.tier == TIER_G6)).any())
    else:
        g6_bid = int((model.conf_tier[crown] == TIER_G6).any())
    return power + g6_bid + guarantee


def _plan(model: Model) -> _Plan:
    n_teams, n_confs = model.n_teams, model.n_confs
    member = model.team_conf >= 0
    block = np.where(member, model.team_conf, n_confs)
    sizes = np.bincount(block, minlength=n_confs + 1)
    starts = np.concatenate([[0], np.cumsum(sizes)])
    conf_games = _conference_game_counts(model)
    counted = member & (conf_games > 0)
    game_counts = sorted(set(conf_games[counted].tolist()))
    lcm = math.lcm(*game_counts) if game_counts else 1
    codes = sorted({w * (lcm // g) for g in game_counts for w in range(g + 1)} | {0})
    pct_rank = np.full(lcm + 1, -1, dtype=np.int64)
    pct_rank[codes] = np.arange(len(codes))
    by_block = np.lexsort((np.arange(n_teams), -model.rating, block))
    rating_rank = np.empty(n_teams, dtype=np.int64)
    rating_rank[by_block] = np.arange(n_teams) - starts[block[by_block]]
    crown = _crowning_conferences(model)
    games = np.arange(model.n_games)
    swing = np.zeros((model.n_games, n_teams), dtype=np.float32)
    swing[games, model.home] = 1.0
    swing[games, model.away] = -1.0
    conf_cols = np.flatnonzero(model.conf_game)
    return _Plan(
        n_remaining=_team_counts(model.home, n_teams) + _team_counts(model.away, n_teams),
        crown=crown,
        crown_ccg=model.conf_has_ccg[crown],
        crown_members=tuple(np.flatnonzero(model.team_conf == c) for c in crown.tolist()),
        conf_cols=conf_cols,
        swing=swing,
        away_games=_team_counts(model.away, n_teams),
        away_conf_games=_team_counts(model.away[conf_cols], n_teams),
        h2h_teams=np.flatnonzero(member & model.eligible),
        ineligible=(~model.eligible).astype(np.int64),
        block_size=int(sizes.max()),
        pct_scale=np.where(counted, lcm // np.maximum(conf_games, 1), 0),
        pct_rank=pct_rank,
        n_pct=len(codes),
        rating_rank=rating_rank,
        pair_game=_pair_games(model),
        pair_played_net=_pair_played_net(model),
        resume_perm=np.lexsort((np.arange(n_teams), -model.rating)),
    )


def _conference_game_counts(model: Model) -> Array:
    counts: Array = (
        model.conf_wins0
        + model.conf_losses0
        + _team_counts(model.home[model.conf_game], model.n_teams)
        + _team_counts(model.away[model.conf_game], model.n_teams)
    )
    return counts


def _team_counts(teams: Array, n_teams: int) -> Array:
    return np.bincount(teams, minlength=n_teams).astype(np.int64)


def _pair_games(model: Model) -> Array:
    pair_game = np.full((model.n_teams, model.n_teams), -1, dtype=np.int64)
    games = np.flatnonzero(model.conf_game)
    np.maximum.at(pair_game, (model.home[games], model.away[games]), games)
    np.maximum.at(pair_game, (model.away[games], model.home[games]), games)
    return pair_game


def _pair_played_net(model: Model) -> Array:
    net = np.zeros((model.n_teams, model.n_teams), dtype=np.int64)
    games = zip(
        model.played_home.tolist(),
        model.played_away.tolist(),
        model.played_margin.tolist(),
        strict=True,
    )
    for home, away, margin in games:
        result = int(np.sign(margin))
        net[home, away] = result
        net[away, home] = -result
    return net


def _season_margins(model: Model, draws: Draws) -> Array:
    latent = draws.offsets[:, model.home]
    latent -= draws.offsets[:, model.away]
    latent *= model.team_sigma
    latent += model.mu
    latent += model.rules.game_sigma * draws.z
    margins: Array = np.rint(latent, out=latent).astype(np.int32)
    tied = margins == 0
    overtime = model.rules.ot_margin
    margins[tied] = np.where(draws.u[tied] < FIRST_LISTED_TIE_PROB, overtime, -overtime)
    return margins


def _records(model: Model, plan: _Plan, margins: Array) -> _Records:
    """Regular-season records (S, T): so far plus the simulated remaining games, each sum one
    float32 product with the game-by-team swing matrix (+1 home, -1 away)."""
    most_games = int(plan.n_remaining.max(initial=0))
    if int(np.abs(margins).max(initial=0)) * most_games >= FLOAT32_EXACT_LIMIT:
        raise OverflowError("simulated margins too large for exact float32 sums")
    home_win = margins > 0
    won = home_win.astype(np.float32)
    cols = plan.conf_cols
    conf_swing = plan.swing[cols]
    wins_added = _integer_product(won, plan.swing) + plan.away_games
    conf_wins_added = _integer_product(won[:, cols], conf_swing) + plan.away_conf_games
    cap = model.rules.margin_cap
    return _Records(
        home_win=home_win,
        wins=model.wins0 + wins_added,
        losses=model.losses0 + plan.n_remaining - wins_added,
        conf_wins=model.conf_wins0 + conf_wins_added,
        conf_pd=model.conf_pd0 + _integer_product(margins[:, cols], conf_swing),
        capped_pd=model.capped_pd0 + _integer_product(np.clip(margins, -cap, cap), plan.swing),
    )


def _integer_product(left: Array, right: Array) -> Array:
    product = np.asarray(left, dtype=np.float32) @ right
    counts: Array = np.rint(product).astype(np.int64)
    return counts


def _standings_top_two(model: Model, plan: _Plan, records: _Records, margins: Array) -> Array:
    """Top two (S, K, 2) of each crowning conference, best first: eligible teams first, then
    conference win percentage; head-to-head only for a tie of exactly two teams; conference
    point differential; rating; team index."""
    pct_rank = plan.pct_rank[records.conf_wins * plan.pct_scale]
    bonus = _head_to_head_bonus(model, plan, margins, pct_rank)
    conf_pd = records.conf_pd
    pd_low, pd_high = int(conf_pd.min()), int(conf_pd.max())
    pd_span = pd_high - pd_low + 1
    n_pct = plan.n_pct
    if 2 * n_pct * 2 * pd_span * plan.block_size >= COMPOSITE_KEY_LIMIT:
        raise OverflowError("standings key does not fit in int64")
    key = plan.ineligible * n_pct + (n_pct - 1 - pct_rank)
    key = key * 2 + (1 - bonus)
    key = key * pd_span + (pd_high - conf_pd)
    key = key * plan.block_size + plan.rating_rank
    rows = np.arange(key.shape[0])
    top_two = np.empty((key.shape[0], plan.crown.size, 2), dtype=np.int64)
    for k, members in enumerate(plan.crown_members):
        block = key[:, members]
        first = block.argmin(axis=1)
        block[rows, first] = np.iinfo(np.int64).max
        top_two[:, k, 0] = members[first]
        top_two[:, k, 1] = members[block.argmin(axis=1)]
    return top_two


def _head_to_head_bonus(model: Model, plan: _Plan, margins: Array, pct_rank: Array) -> Array:
    n_sims, n_teams = pct_rank.shape
    bonus = np.zeros((n_sims, n_teams), dtype=np.int64)
    teams = plan.h2h_teams
    if teams.size == 0:
        return bonus
    n_pct = plan.n_pct
    n_buckets = n_sims * model.n_confs * n_pct
    sim_base = (np.arange(n_sims, dtype=np.int64) * (model.n_confs * n_pct))[:, None]
    bucket = (sim_base + model.team_conf[teams] * n_pct + pct_rank[:, teams]).ravel()
    counts = np.bincount(bucket, minlength=n_buckets)
    team_sums = np.bincount(
        bucket, weights=np.broadcast_to(teams, (n_sims, teams.size)).ravel(), minlength=n_buckets
    )
    two_way = (counts[bucket] == 2).reshape(n_sims, teams.size)
    partner_sum = np.rint(team_sums[bucket]).astype(np.int64).reshape(n_sims, teams.size)
    partner = np.where(two_way, partner_sum - teams, teams)
    net = plan.pair_played_net[teams, partner]
    game = plan.pair_game[teams, partner]
    met = game >= 0
    if met.any():
        game = np.where(met, game, 0)
        home_won = np.take_along_axis(margins, game, axis=1) > 0
        won = home_won == (model.home[game] == teams)
        net = np.where(met, np.where(won, 1, -1), net)
    bonus[:, teams] = two_way & (net > 0)
    return bonus


def _post_margins(
    model: Model, draws: Draws, first: Array, second: Array, edge: float, columns: Array
) -> Array:
    rules = model.rules
    rows = np.arange(first.shape[0])[:, None]
    spread = draws.offsets[rows, first] - draws.offsets[rows, second]
    mu = model.rating[first] - model.rating[second] + edge
    margins = np.rint(
        mu + rules.post_team_sigma * spread + rules.game_sigma * draws.z_post[:, columns]
    ).astype(np.int64)
    overtime = np.where(
        draws.u_post[:, columns] < FIRST_LISTED_TIE_PROB, rules.ot_margin, -rules.ot_margin
    )
    resolved: Array = np.where(margins == 0, overtime, margins)
    return resolved


def _championships(
    model: Model, plan: _Plan, draws: Draws, top_two: Array
) -> tuple[Array, Array, Array]:
    """Champion, runner-up (-1 without a title game) and title-game margin (S, K). Title games
    are neutral, between the top two; other conferences crown their leader."""
    first, second = top_two[..., 0], top_two[..., 1]
    margin = _post_margins(model, draws, first, second, 0.0, plan.crown)
    margin = np.where(plan.crown_ccg, margin, 0)
    first_won = ~plan.crown_ccg | (margin > 0)
    champion = np.where(first_won, first, second)
    runner_up = np.where(plan.crown_ccg, np.where(first_won, second, first), -1)
    return champion, runner_up, margin


def _resume_order(
    model: Model,
    plan: _Plan,
    records: _Records,
    champion: Array,
    runner_up: Array,
    ccg_margin: Array,
) -> tuple[Array, Array]:
    """Eligible teams by resume score desc, rating desc, team index asc; others after them.

    Score = w_win_pct * W / (W + L) + w_sos * SOS + w_pd * CAPPED_PD / (W + L) + w_rating *
    RATING, with W and L over the games so far, the remaining games and the title game.
    """
    rules = model.rules
    n_sims = champion.shape[0]
    rows = np.arange(n_sims)[:, None]
    ccg = np.flatnonzero(plan.crown_ccg)
    ccg_wins = np.zeros_like(records.wins)
    ccg_losses = np.zeros_like(records.wins)
    ccg_pd = np.zeros(records.wins.shape, dtype=np.float64)
    if ccg.size:
        capped = np.minimum(np.abs(ccg_margin[:, ccg]), rules.margin_cap).astype(np.float64)
        ccg_wins[rows, champion[:, ccg]] = 1
        ccg_losses[rows, runner_up[:, ccg]] = 1
        ccg_pd[rows, champion[:, ccg]] = capped
        ccg_pd[rows, runner_up[:, ccg]] = -capped
    wins = records.wins + ccg_wins
    games = np.maximum(wins + records.losses + ccg_losses, 1)
    capped_pd = records.capped_pd + ccg_pd
    score = (
        rules.w_win_pct * (wins / games)
        + rules.w_sos * model.sos
        + rules.w_pd * (capped_pd / games)
        + rules.w_rating * model.rating
    )
    score = np.where(model.eligible, score, -np.inf)
    perm = plan.resume_perm
    order = perm[np.argsort(-score[:, perm], axis=1, kind="stable")]
    rank = np.empty_like(order)
    np.put_along_axis(rank, order, np.broadcast_to(np.arange(order.shape[1]), order.shape), 1)
    return order, rank


def _select_and_seed(
    model: Model, plan: _Plan, champion: Array, order: Array, rank: Array
) -> tuple[Array, Array]:
    """The 12 teams in seed order (S, 12) and their automatic-bid flags.

    Automatic bids first, even outside the top 12: with `POWER_CHAMPIONS_PLUS_G6` the `P4`
    champions plus the best-ranked `G6` team (`TEAM`) or `G6` champion (`CHAMPION`); with
    `TOP_CHAMPIONS` the `auto_bids` best-ranked champions; and the guaranteed team when ranked
    within `guarantee_rank`. At-large places go to the best-ranked others; seeding is straight.
    """
    rules = model.rules
    n_sims, n_teams = order.shape
    sims = np.arange(n_sims)
    auto = np.zeros((n_sims, n_teams), dtype=bool)
    crown_tier = model.conf_tier[plan.crown]
    if rules.auto_bid_rule == AUTO_BID_TOP_CHAMPIONS:
        n_take = min(rules.auto_bids, int(plan.crown.size))
        if n_take:
            champion_rank = np.take_along_axis(rank, champion, axis=1)
            best = np.argsort(champion_rank, axis=1)[:, :n_take]
            auto[sims[:, None], np.take_along_axis(champion, best, axis=1)] = True
    else:
        auto[sims[:, None], champion[:, crown_tier == TIER_P4]] = True
        if rules.g6_bid == G6_BID_TEAM:
            g6_teams = np.flatnonzero(model.eligible & (model.tier == TIER_G6))
            candidates = np.broadcast_to(g6_teams, (n_sims, g6_teams.size))
        else:
            candidates = champion[:, crown_tier == TIER_G6]
        if candidates.shape[1]:
            best_rank = np.take_along_axis(rank, candidates, axis=1).argmin(axis=1)
            auto[sims, candidates[sims, best_rank]] = True
    if rules.guarantee_team_idx >= 0:
        guaranteed = rules.guarantee_team_idx
        auto[:, guaranteed] |= rank[:, guaranteed] < rules.guarantee_rank
    auto_ranked = np.take_along_axis(auto, order, axis=1)
    free = ~auto_ranked
    places = CFP_SIZE - auto_ranked.sum(axis=1, keepdims=True)
    chosen = auto_ranked | (free & (np.cumsum(free, axis=1) <= places))
    positions = np.argsort(~chosen, axis=1, kind="stable")[:, :CFP_SIZE]
    seed_teams = np.take_along_axis(order, positions, axis=1)
    seed_auto = np.take_along_axis(auto_ranked, positions, axis=1)
    return seed_teams, seed_auto


def _play_bracket(model: Model, draws: Draws, seed_teams: Array) -> tuple[Array, Array]:
    """Fixed bracket: 5-12, 6-11, 7-10, 8-9 at the better seed (with `hfa`); quarterfinals 1 vs
    8/9, 2 vs 7/10, 3 vs 6/11, 4 vs 5/12; semifinals 1-8-9-4-5-12 against 2-7-10-3-6-11; final.
    From the quarterfinals on, neutral with the better seed listed first. Returns the round each
    seed reached (S, 12) and the national champion (S,)."""
    n_sims = seed_teams.shape[0]
    first_column = model.n_confs
    seeds = np.broadcast_to(np.arange(1, CFP_SIZE + 1), seed_teams.shape)

    def play(
        first: Array, first_seed: Array, second: Array, second_seed: Array, slot: int, edge: float
    ) -> tuple[Array, Array]:
        columns = np.arange(first_column + slot, first_column + slot + first.shape[1])
        first_won = _post_margins(model, draws, first, second, edge, columns) > 0
        return np.where(first_won, first, second), np.where(first_won, first_seed, second_seed)

    high = [h - 1 for h, _ in FIRST_ROUND_SEEDS]
    low = [lo - 1 for _, lo in FIRST_ROUND_SEEDS]
    first_team, first_seed = play(
        seed_teams[:, high], seeds[:, high], seed_teams[:, low], seeds[:, low], 0, model.rules.hfa
    )
    feeds = list(QUARTERFINAL_FEEDS)
    quarter_team, quarter_seed = play(
        seed_teams[:, :N_BYES],
        seeds[:, :N_BYES],
        first_team[:, feeds],
        first_seed[:, feeds],
        4,
        0.0,
    )
    semi_team, semi_seed = play(
        *_better_seed_first(quarter_team, quarter_seed, SEMIFINAL_PAIRS), 8, 0.0
    )
    final_team, _ = play(*_better_seed_first(semi_team, semi_seed, ((0, 1),)), 10, 0.0)
    rows = np.arange(n_sims)[:, None]
    reached = np.full((n_sims, CFP_SIZE), ROUND_FIRST, dtype=np.int64)
    reached[:, :N_BYES] = ROUND_QUARTER
    reached[rows, first_seed - 1] = ROUND_QUARTER
    reached[rows, quarter_seed - 1] = ROUND_SEMI
    reached[rows, semi_seed - 1] = ROUND_FINAL
    title: Array = final_team[:, 0]
    return reached, title


def _better_seed_first(
    teams: Array, seeds: Array, pairs: tuple[tuple[int, int], ...]
) -> tuple[Array, Array, Array, Array]:
    a = [p for p, _ in pairs]
    b = [q for _, q in pairs]
    a_first = seeds[:, a] < seeds[:, b]
    return (
        np.where(a_first, teams[:, a], teams[:, b]),
        np.where(a_first, seeds[:, a], seeds[:, b]),
        np.where(a_first, teams[:, b], teams[:, a]),
        np.where(a_first, seeds[:, b], seeds[:, a]),
    )


def _tallies(model: Model, records: _Records, champion: Array, seed_teams: Array) -> Tallies:
    """W per FBS team and win total, C per champion, G per game, and L as one float32 product
    of the in-field indicator (teams that made the field in this batch) with the home wins."""
    n_sims, n_teams = records.wins.shape
    fbs = np.flatnonzero(model.tier != TIER_NON_FBS)
    fbs_wins = records.wins[:, fbs]
    span = int(fbs_wins.max()) + 1 if fbs_wins.size else 1
    win_keys = (np.arange(fbs.size, dtype=np.int64) * span + fbs_wins).ravel()
    win_counts = np.bincount(win_keys, minlength=fbs.size * span)
    win_cells = np.flatnonzero(win_counts)
    champ_counts = np.bincount(champion.ravel(), minlength=n_teams)
    champ_team = np.flatnonzero(champ_counts)
    in_field = np.zeros((n_sims, n_teams), dtype=bool)
    in_field[np.arange(n_sims)[:, None], seed_teams] = True
    field_teams = np.flatnonzero(in_field.any(axis=0))
    product = in_field[:, field_teams].T.astype(np.float32) @ records.home_win.astype(np.float32)
    lev_counts = np.rint(product).astype(np.int64)
    lev_pos, lev_game = np.nonzero(lev_counts)
    return Tallies(
        win_team=fbs[win_cells // span],
        win_total=(win_cells % span).astype(np.int64),
        win_n=win_counts[win_cells].astype(np.int64),
        champ_team=champ_team,
        champ_n=champ_counts[champ_team].astype(np.int64),
        home_wins=records.home_win.sum(axis=0).astype(np.int64),
        lev_team=field_teams[lev_pos],
        lev_game=lev_game.astype(np.int64),
        lev_n=lev_counts[lev_pos, lev_game],
    )
