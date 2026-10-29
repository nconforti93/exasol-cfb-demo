"""`engine/fit.py`: the host fit behind `make sim-fit` (sim-engine/score-model, scenario 4)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from engine import fit
from scripts import sim as sim_cli
from vs.host import target

SEED_FILE = Path(fit.SEED_PATH)
N_GAMES = 20_000
TRUE_HFA = 2.5
TRUE_B_SP = 0.8
TRUE_B_ELO = 0.04
TRUE_SIGMA = 15.0
TRUE_TAU = 2.0


def generated_table(n: int = N_GAMES, seed: int = 11) -> fit.FitTable:
    """FBS-vs-FBS games whose margins follow the known constants; the Elo seen k weeks earlier
    is the true Elo difference plus noise, so the residual variance grows by TAU² per week."""
    rng = np.random.default_rng(seed)
    home_flag = (rng.random(n) < 0.9).astype(float)
    d_sp = rng.normal(0.0, 12.0, n)
    d_elo = rng.normal(0.0, 150.0, n)
    margin = TRUE_HFA * home_flag + TRUE_B_SP * d_sp + TRUE_B_ELO * d_elo
    margin += rng.normal(0.0, TRUE_SIGMA, n)
    lag_noise_sd = TRUE_TAU / TRUE_B_ELO
    elo_gap = np.stack(
        [d_elo + lag_noise_sd * np.sqrt(k) * rng.normal(size=n) for k in range(fit.LAGS + 1)],
        axis=1,
    )
    elo_gap[:, 0] = d_elo
    home_elo = 1500.0 + elo_gap / 2.0
    away_elo = 1500.0 - elo_gap / 2.0
    home_sp = 5.0 + d_sp / 2.0
    away_sp = 5.0 - d_sp / 2.0
    nan = np.full(n, np.nan)
    return fit.FitTable(
        season=np.full(n, 2020),
        home_flag=home_flag,
        margin=np.rint(margin),
        total=nan,
        spread=nan,
        home_is_fbs=np.ones(n, dtype=bool),
        away_is_fbs=np.ones(n, dtype=bool),
        home_elo=home_elo,
        away_elo=away_elo,
        home_sp=home_sp,
        away_sp=away_sp,
        home_off=nan,
        home_def=nan,
        away_off=nan,
        away_def=nan,
    )


def test_fit_recovers_known_constants() -> None:
    table = generated_table()
    result = fit.fit_constants(table)
    values = result.values
    assert abs(values["HFA"] - TRUE_HFA) < 0.3
    assert abs(values["B_SP"] - TRUE_B_SP) < 0.05
    assert abs(values["B_ELO"] - TRUE_B_ELO) < 0.005
    assert abs(values["SIGMA_1"] / TRUE_SIGMA - 1) < 0.03
    assert abs(values["TAU"] / TRUE_TAU - 1) < 0.25
    assert result.origins["TAU"] == "FITTED"
    assert fit.render_seed(result) == fit.render_seed(fit.fit_constants(table))


def test_the_seed_keeps_the_keys_of_the_committed_file() -> None:
    result = fit.fit_constants(generated_table(4_000))
    rendered = fit.render_seed(result)
    keys = re.findall(r"^  \('([A-Z0-9_]+)', ", rendered, flags=re.MULTILINE)
    committed = re.findall(
        r"^  \('([A-Z0-9_]+)', ", SEED_FILE.read_text(encoding="utf-8"), flags=re.MULTILINE
    )
    assert keys == committed
    assert "'UNFITTED'" not in rendered


def test_tau_falls_back_to_an_assumed_value_when_variance_does_not_grow() -> None:
    table = generated_table(6_000, seed=5)
    flat = fit.FitTable(
        **{
            **table.__dict__,
            "home_elo": np.repeat(table.home_elo[:, :1], fit.LAGS + 1, axis=1),
            "away_elo": np.repeat(table.away_elo[:, :1], fit.LAGS + 1, axis=1),
        }
    )
    result = fit.fit_constants(flat)
    assert result.values["TAU"] == fit.ASSUMED_TAU
    assert result.origins["TAU"] == "ASSUMED"


def test_report_metrics_match_hand_computed_margin_line_and_probability_scores() -> None:
    n = 4
    zeros = np.zeros(n)
    nan = np.full(n, np.nan)
    elo = np.full((n, fit.LAGS + 1), 1500.0)
    table = fit.FitTable(
        season=np.full(n, 2024),
        home_flag=zeros,
        margin=np.array([1.0, -1.0, 3.0, -3.0]),
        total=zeros,
        spread=np.array([-1.0, 1.0, -2.0, 2.0]),
        home_is_fbs=np.ones(n, dtype=bool),
        away_is_fbs=np.ones(n, dtype=bool),
        home_elo=elo,
        away_elo=elo,
        home_sp=zeros,
        away_sp=zeros,
        home_off=nan,
        home_def=nan,
        away_off=nan,
        away_def=nan,
        line_probability=np.array([0.6, 0.4, 0.7, 0.3]),
    )
    values = {
        "HFA": 0.0,
        "B_SP": 0.0,
        "B_ELO": 0.0,
        "ELO_CENTER": 1500.0,
        "B_ELO_ONLY": 0.0,
        "B_SP_ONLY": 0.0,
        "NON_FBS_RATING": -25.0,
        "SIGMA_1": 15.0,
    }
    scored = fit.evaluate(table, values)
    assert scored.margin_mae == 2.0 and scored.margin_bias == 0.0
    assert scored.line_mae == 0.5 and scored.model_mae_on_line_games == 2.0
    assert scored.brier == 0.25
    assert abs(scored.line_brier - 0.125) < 1e-12
    assert scored.model_brier_on_line_games == 0.25
    assert scored.buckets[5] == (0.5, 4, 0.5, 0.5)
    assert fit.bucket_tolerance(0.5, 4) == 0.75


def test_report_mode_prints_without_writing_the_seed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    class Connection:
        closed = False

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(sim_cli.fit_module, "read_inputs", lambda *_: ([], []))
    monkeypatch.setattr(sim_cli.fit_module, "build_table", lambda *_: generated_table(4))
    monkeypatch.setattr(sim_cli.fit_module, "report_lines", lambda *_: ["holdout report"])
    monkeypatch.setattr(sim_cli, "_current_scorecard_lines", lambda *_: ["current scorecard"])
    output = tmp_path / "seed.sql"

    def resolve(name: str | None, *, dsn: str | None = None) -> target.Target:
        return target.Target("local", "localhost", 8563, "sys", tmp_path, target.TLS_UNVERIFIED_LOCAL)

    code = sim_cli.main(
        ["fit", "--report", "--output", str(output)],
        connector=lambda _: connection,
        resolve=resolve,
    )
    assert code == sim_cli.EXIT_OK and connection.closed and not output.exists()
    assert "holdout report\ncurrent scorecard\nreport only: seed unchanged" in capsys.readouterr().out


def test_elo_series_uses_the_postgame_elo_of_the_last_game_and_hides_the_first_pregame_early() -> (
    None
):
    kickoff = datetime(2024, 9, 1, 18, 0)

    def game(
        game_id: int, week: int, home: int, away: int, pre: tuple[int, int] | None
    ) -> fit.GameRow:
        post = (None, None) if pre is None else (pre[0] + 10, pre[1] - 10)
        return fit.GameRow(
            season=2024,
            week_seq=week,
            start_at=kickoff + timedelta(days=7 * week),
            game_id=game_id,
            home_id=home,
            away_id=away,
            home_class="fbs",
            away_class="fbs",
            is_neutral=False,
            home_points=30,
            away_points=20,
            home_pre=None if pre is None else Decimal(pre[0]),
            away_pre=None if pre is None else Decimal(pre[1]),
            home_post=None if post[0] is None else Decimal(post[0]),
            away_post=None if post[1] is None else Decimal(post[1]),
            spread=None,
        )

    games = [game(1, 3, 1, 2, (1600, 1400)), game(2, 5, 1, 3, (1610, 1500))]
    series = fit.elo_series(games)
    assert fit.elo_at(series[(2024, 1)], 2) == 1600.0
    assert fit.elo_at(series[(2024, 1)], 1) is None
    assert fit.elo_at(series[(2024, 1)], 3) == 1610.0
    assert fit.elo_at(series[(2024, 1)], 4) == 1610.0
    assert fit.elo_at(series[(2024, 2)], 2) == 1400.0
    assert fit.elo_at(series[(2024, 3)], 4) == 1500.0
    assert fit.elo_at(series[(2024, 3)], 5) == 1490.0
