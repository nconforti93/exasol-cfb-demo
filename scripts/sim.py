"""`make sim`: the season simulator's schema and engine script.

    python -m scripts.sim deploy [--target local|aws] [--dsn HOST:PORT] [--prefix PREFIX_]
                                 [--lint-only]

    python -m scripts.sim fit [--target local|aws] [--dsn HOST:PORT] [--prefix PREFIX_]
                              [--output PATH] [--dry-run]

    python -m scripts.sim simulate --season Y --week W [--season-type regular|postseason]
                                   [--sims N] [--batch N] [--seed N] [--label TEXT] [--target ...]

    python -m scripts.sim what-if --team SCHOOL|ID [--run ID] [--win next|GAME_ID[,...]]
                                  [--lose next|GAME_ID[,...]] [--target ...]

    make sim [TARGET=local|aws] [ARGS="--prefix TEST_"]
    make sim-fit [TARGET=local|aws] [ARGS="--dry-run|--report"]
    make simulate SEASON=2026 WEEK=7 [SIMS=100000] [BATCH=5000] [SEED=..] [LABEL=..] [TARGET=..]
    make what-if TEAM=Auburn [RUN_ID=..] WIN=next [LOSE=..] [TARGET=..]

- `deploy` lints `sql/sim/` first (every problem is listed and nothing is sent), then connects as
  `sys` to the target (`vs/host/target.py`), takes the loader's run lock as kind `DEPLOY` and
  installs `CFB_SIM` and `CFB_INFRA.SIM_ENGINE` in one transaction (`engine/deploy.py`). It refuses
  while a loader run, a snapshot or a restore holds the lock, and it needs the loader deployed
  (`make deploy`). The data tables are created when missing and never replaced, so a redeploy keeps
  every run. Zero CFBD calls: the simulation never reads the virtual schema.
  `--lint-only` lints, renders and builds the script only: no target is resolved, no connection is
  opened. Exit 0 and "lint ok", or 2 with every problem listed.

- `fit` reads the completed regular-season games of 2014-2025 and the SP+ ratings of the seasons
  before them from `CFB_MODEL.GAME` and `RATING_SP` of the target (read-only `SELECT`s, no lock,
  0 CFBD calls, never the virtual schema), estimates the constants of the score model
  (`engine/fit.py`), prints them with the games behind each and the fit quality, and writes the
  seed `sql/sim/010_sim_params.sql` (`--output` another file, `--dry-run` nothing). Review the
  diff and commit it; `make sim` deploys it.

- `simulate` runs `EXECUTE SCRIPT CFB_INFRA.SIMULATE(...)` (sim-engine/sim-runs; the script
  takes the run lock as kind `SIMULATE`, snapshots the inputs, runs the engine and splits its
  output), then prints the result row, the run's step seconds and the top 25 of
  `CFB_SIM.SIM_TEAM_ODDS`. NULL arguments take the script's defaults (100,000 seasons in batches
  of 5,000, seed season x 1,000 + the point's week sequence). Zero CFBD calls.

- `what-if` answers "if TEAM wins (or loses) these games, what are its playoff odds?" from the
  stored seasons of a run (default: the newest `DONE` run): `--win next` is the team's first
  remaining game of the run, otherwise game ids. It prints the odds before (`SIM_TEAM_ODDS`) and
  after (`engine/whatif.py` over `SIM_PATH` and `SIM_TEAM`), the matching seasons and the seconds of
  the what-if query with the query cache off. Read-only, no lock, zero CFBD calls.

- `predict` freezes the current season's next-week score model rows from every as-of point through
  the given week. Earlier weeks are reconstructed and marked. Existing rows are never rewritten.

Exit codes: 0 ok, 1 refused (the run lock is held, or the run's rules refuse it), 2 error.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine import bundle, whatif
from engine import deploy as sim_deploy
from engine import fit as fit_module
from engine.deploy import SimLintError, SimOptions
from loader.runlock import RunLockError, RunLockRefused
from vs.cfbd_vs.sqlrender import qualified_name, string_literal
from vs.host import target
from vs.host.deploy import DeployError
from vs.host.namespace import Namespace, NamespaceError

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_ERROR = 2
SOCKET_TIMEOUT_SECONDS = 3600
TOP_ODDS = 25
NEXT_GAME = "next"
REFUSED = "refused"

Connector = Callable[[target.Target], Any]


def connect_sys(deployment: target.Target) -> Any:
    """A sys connection to the target; the password comes from its secrets file. Statements may
    run for up to an hour (a simulation), not pyexasol's 30 s."""
    return deployment.connect(socket_timeout=SOCKET_TIMEOUT_SECONDS)


class SimRefused(Exception):
    """The database refused the run: the run lock is held, or the rules or batch sizes refuse it."""


@dataclass(frozen=True)
class GameRow:
    """A remaining game of a run, as the what-if needs it."""

    game_id: int
    path_pos: int
    week_seq: int
    home_team_id: int
    away_team_id: int
    home_school: str
    away_school: str


def simulate_call(namespace: Namespace, args: argparse.Namespace) -> str:
    """`EXECUTE SCRIPT <infra>.SIMULATE(...)`: whole numbers as text, NULL for a default."""

    def number(value: int | None) -> str:
        return "NULL" if value is None else str(int(value))

    def text(value: str | None) -> str:
        return "NULL" if value is None else string_literal(value)

    values = [
        number(args.season),
        number(args.week),
        text(args.season_type),
        number(args.sims),
        number(args.batch),
        number(args.seed),
        text(args.label),
    ]
    script = qualified_name(namespace.infra_schema, sim_deploy.SIMULATE_NAME)
    return f"EXECUTE SCRIPT {script}({', '.join(values)})"


def predict_call(namespace: Namespace, args: argparse.Namespace) -> str:
    """`EXECUTE SCRIPT <infra>.PREDICT(season, week, season_type)`."""
    season_type = string_literal(args.season_type or "regular")
    script = qualified_name(namespace.infra_schema, sim_deploy.PREDICT_NAME)
    return f"EXECUTE SCRIPT {script}({int(args.season)}, {int(args.week)}, {season_type})"


def game_specs(value: str | None) -> list[str]:
    """`next` or comma-separated game ids, as given (empty when the option is absent)."""
    if value is None:
        return []
    specs = [part.strip() for part in value.split(",") if part.strip()]
    for spec in specs:
        if spec != NEXT_GAME and not spec.isdigit():
            raise DeployError(f"a game is '{NEXT_GAME}' or a game id, got {spec!r}")
    return specs


def pick_game(spec: str, games: Sequence[GameRow], school: str) -> GameRow:
    """The remaining game of the team that `spec` names: its first one for `next`."""
    if not games:
        raise DeployError(f"{school} has no remaining game in this run")
    if spec == NEXT_GAME:
        return games[0]
    for game in games:
        if game.game_id == int(spec):
            return game
    raise DeployError(f"{school} plays no remaining game {spec} in this run")


def _percent(value: Any) -> str:
    return "     n/a" if value is None else f"{100 * float(value):7.2f}%"


def _message(error: BaseException) -> str:
    return str(getattr(error, "message", error)).strip()


def _print(line: str) -> None:
    print(line, flush=True)


class _Command:
    def __init__(
        self,
        args: argparse.Namespace,
        connector: Connector,
        resolve: Callable[..., target.Target],
        options: SimOptions,
    ) -> None:
        self._args = args
        self._connector = connector
        self._resolve = resolve
        self._options = options

    def deploy(self) -> int:
        namespace = Namespace(self._args.prefix)
        sim_deploy.refresh_settings(self._options.config_path)
        objects = sim_deploy.prepare(
            namespace, self._options.directory, self._options.data_directory
        )
        script = bundle.build(namespace, self._options.engine_directory)
        if self._args.lint_only:
            _print(
                f"lint ok: {len(objects)} objects, engine script {len(script.create):,} "
                f"characters, ENGINE_VERSION {script.version}; nothing sent, 0 CFBD calls"
            )
            return EXIT_OK
        deployment = self._resolve(self._args.target, dsn=self._args.dsn)
        connection = self._connector(deployment)
        try:
            report = sim_deploy.deploy_sim(
                connection, namespace, self._options, target_name=deployment.name
            )
        finally:
            connection.close()
        for line in report.lines:
            _print(line)
        return EXIT_OK

    def simulate(self) -> int:
        namespace = Namespace(self._args.prefix)
        connection = self._connector(self._resolve(self._args.target, dsn=self._args.dsn))
        try:
            started = time.perf_counter()
            try:
                statement = connection.execute(simulate_call(namespace, self._args))
            except Exception as error:
                message = _message(error)
                if REFUSED in message.splitlines()[0]:
                    raise SimRefused(message.splitlines()[0]) from None
                raise
            names = list(statement.columns())
            result = dict(zip(names, statement.fetchone(), strict=True))
            wall = time.perf_counter() - started
            self._print_run(connection, namespace, int(result["RUN_ID"]), wall)
        finally:
            connection.close()
        return EXIT_OK

    def predict(self) -> int:
        namespace = Namespace(self._args.prefix)
        connection = self._connector(self._resolve(self._args.target, dsn=self._args.dsn))
        try:
            row = connection.execute(predict_call(namespace, self._args)).fetchone()
        finally:
            connection.close()
        _print(f"predictions frozen: {int(row[0])}; 0 CFBD calls")
        return EXIT_OK

    def _print_run(self, connection: Any, namespace: Namespace, run_id: int, wall: float) -> None:
        sim = namespace.sim_schema
        run = connection.execute(
            "SELECT RUN_STATUS, AS_OF_LABEL, N_SIMS, N_BATCHES, SIMS_PER_BATCH, RUN_SEED, N_TEAMS, "
            "N_GAMES, N_LEFT_OUT_GAMES, INSTANCES, NODES, ENGINE_VERSION, NUMPY_VERSION, "
            "SECONDS_INPUTS, SECONDS_ENGINE, SECONDS_OUTPUTS, SECONDS_TOTAL, SIMS_PER_SECOND, "
            "DETAIL "
            f"FROM {qualified_name(sim, 'SIM_RUN')} WHERE RUN_ID = {run_id}"
        ).fetchone()
        (status, label, n_sims, n_batches, per_batch, seed, teams, games, left_out) = run[:9]
        (instances, nodes, version, numpy, inputs, engine, outputs, total, rate, detail) = run[9:]
        _print(
            f"run {run_id} {status}: {label}, {int(n_sims):,} seasons in {int(n_batches)} "
            f"batches of {int(per_batch):,}, seed {seed}; {teams} teams, {games} games "
            f"({left_out} left out)"
        )
        _print(
            f"seconds: inputs {inputs}, engine {engine}, outputs {outputs}, total {total} "
            f"({float(rate):,.0f} seasons/s); client wall {wall:.2f} s"
        )
        _print(f"engine {version}, numpy {numpy}, {instances} instances on {nodes} node(s)")
        _print(f"steps: {detail}")
        rows = connection.execute(
            "SELECT o.ODDS_RANK, o.SCHOOL, o.CONFERENCE, t.WINS, t.LOSSES, t.RATING, o.P_IN_CFP, "
            "o.P_BYE, o.P_CHAMPION, o.P_CONF_CHAMPION, o.EXPECTED_WINS, o.MEAN_SEED "
            f"FROM {qualified_name(sim, 'SIM_TEAM_ODDS')} o "
            f"JOIN {qualified_name(sim, 'SIM_RUN_TEAM')} t "
            "ON t.RUN_ID = o.RUN_ID AND t.TEAM_ID = o.TEAM_ID "
            f"WHERE o.RUN_ID = {run_id} ORDER BY o.ODDS_RANK LIMIT {TOP_ODDS}"
        ).fetchall()
        _print(
            f"{'#':>3} {'school':<22} {'conference':<18} {'W-L':>5} {'rating':>7} {'P(CFP)':>8} "
            f"{'P(bye)':>8} {'P(title)':>8} {'P(conf)':>8} {'E(W)':>6} {'seed':>5}"
        )
        for (
            rank,
            school,
            conference,
            wins,
            losses,
            rating,
            cfp,
            bye,
            title,
            conf,
            exp,
            seed_,
        ) in rows:
            _print(
                f"{rank:>3} {str(school)[:22]:<22} {str(conference or 'Independent')[:18]:<18} "
                f"{f'{wins}-{losses}':>5} {float(rating):7.2f} {_percent(cfp)} {_percent(bye)} "
                f"{_percent(title)} {_percent(conf)} {exp!s:>6} {seed_ or '-'!s:>5}"
            )

    def what_if(self) -> int:
        namespace = Namespace(self._args.prefix)
        wins = game_specs(self._args.win)
        losses = game_specs(self._args.lose)
        if not wins and not losses:
            raise DeployError("give at least one game: --win next or --lose <game id>")
        connection = self._connector(self._resolve(self._args.target, dsn=self._args.dsn))
        try:
            connection.execute("ALTER SESSION SET QUERY_CACHE = 'OFF'")
            self._answer(connection, namespace, wins, losses)
        finally:
            connection.close()
        return EXIT_OK

    def _answer(
        self, connection: Any, namespace: Namespace, wins: list[str], losses: list[str]
    ) -> None:
        sim = namespace.sim_schema
        runs = qualified_name(sim, "SIM_RUN")
        run_id = self._args.run
        if run_id is None:
            row = connection.execute(
                f"SELECT MAX(RUN_ID) FROM {runs} WHERE RUN_STATUS = 'DONE'"
            ).fetchone()
            if row is None or row[0] is None:
                raise DeployError(f"{sim}.SIM_RUN has no DONE run: make simulate first")
            run_id = int(row[0])
        found = connection.execute(
            f"SELECT RUN_STATUS, AS_OF_LABEL, N_SIMS FROM {runs} WHERE RUN_ID = {int(run_id)}"
        ).fetchone()
        if found is None or found[0] != "DONE":
            raise DeployError(f"run {run_id} is not a DONE run of {sim}.SIM_RUN")
        status, label, n_sims = found
        team_id, school = self._team(connection, sim, int(run_id))
        games = self._games(connection, sim, int(run_id), team_id)
        picked = [(pick_game(s, games, school), True) for s in wins]
        picked += [(pick_game(s, games, school), False) for s in losses]
        conditions = [
            whatif.team_condition(
                team_id,
                game.path_pos,
                home_team_id=game.home_team_id,
                away_team_id=game.away_team_id,
                team_wins=team_wins,
            )
            for game, team_wins in picked
        ]
        base = connection.execute(
            "SELECT P_IN_CFP, P_BYE, P_CHAMPION FROM "
            f"{qualified_name(sim, 'SIM_TEAM_ODDS')} WHERE RUN_ID = {int(run_id)} "
            f"AND TEAM_ID = {team_id}"
        ).fetchone() or (None, None, None)
        sql = whatif.team_sql(int(run_id), team_id, conditions, whatif.Names(schema=sim))
        started = time.perf_counter()
        matching, in_cfp, bye, title = connection.execute(sql).fetchone()
        seconds = time.perf_counter() - started
        _print(f"run {run_id} ({label}, {int(n_sims):,} seasons, {status})")
        for game, team_wins in picked:
            verb = "beats" if team_wins else "loses to"
            home = game.home_team_id == team_id
            opponent = game.away_school if home else game.home_school
            where = "home" if home else "away"
            _print(
                f"  if {school} {verb} {opponent} (game {game.game_id}, week {game.week_seq}, "
                f"{where}, outcome position {game.path_pos})"
            )
        share = int(matching) / int(n_sims) if int(n_sims) else 0.0
        _print(f"matching seasons: {int(matching):,} of {int(n_sims):,} ({100 * share:.1f} %)")
        after = [
            None if not int(matching) else int(n) / int(matching) for n in (in_cfp, bye, title)
        ]
        _print(f"{'':<10} {'now':>8} {'if':>8}")
        for name, before, value in zip(("P(CFP)", "P(bye)", "P(title)"), base, after, strict=True):
            _print(f"{name:<10} {_percent(before)} {_percent(value)}")
        _print(f"what-if query: {seconds:.3f} s (query cache off)")

    def _team(self, connection: Any, sim: str, run_id: int) -> tuple[int, str]:
        given = str(self._args.team).strip()
        roster = qualified_name(sim, "SIM_RUN_TEAM")
        if given.isdigit():
            condition = f"TEAM_ID = {int(given)}"
        else:
            condition = f"UPPER(SCHOOL) = UPPER({string_literal(given)})"
        rows = connection.execute(
            f"SELECT TEAM_ID, SCHOOL FROM {roster} WHERE RUN_ID = {run_id} AND IS_ELIGIBLE "
            f"AND {condition}"
        ).fetchall()
        if len(rows) != 1:
            raise DeployError(f"run {run_id} has {len(rows)} eligible teams matching {given!r}")
        return int(rows[0][0]), str(rows[0][1])

    def _games(self, connection: Any, sim: str, run_id: int, team_id: int) -> list[GameRow]:
        roster = qualified_name(sim, "SIM_RUN_TEAM")
        rows = connection.execute(
            "SELECT g.GAME_ID, g.PATH_POS, g.WEEK_SEQ, g.HOME_TEAM_ID, g.AWAY_TEAM_ID, "
            f"h.SCHOOL AS HOME_SCHOOL, a.SCHOOL AS AWAY_SCHOOL "
            f"FROM {qualified_name(sim, 'SIM_RUN_GAME')} g "
            f"JOIN {roster} h ON h.RUN_ID = g.RUN_ID AND h.TEAM_ID = g.HOME_TEAM_ID "
            f"JOIN {roster} a ON a.RUN_ID = g.RUN_ID AND a.TEAM_ID = g.AWAY_TEAM_ID "
            f"WHERE g.RUN_ID = {run_id} AND g.GAME_ROLE = 'REMAINING' "
            f"AND {team_id} IN (g.HOME_TEAM_ID, g.AWAY_TEAM_ID) ORDER BY g.GAME_IDX"
        ).fetchall()
        return [
            GameRow(int(a), int(b), int(c), int(d), int(e), str(f), str(g))
            for a, b, c, d, e, f, g in rows
        ]

    def fit(self) -> int:
        namespace = Namespace(self._args.prefix)
        connection = self._connector(self._resolve(self._args.target, dsn=self._args.dsn))
        try:
            games, ratings = fit_module.read_inputs(connection, namespace.model_schema)
            current_lines = (
                _current_scorecard_lines(connection, namespace) if self._args.report else []
            )
        finally:
            connection.close()
        table = fit_module.build_table(games, ratings)
        if self._args.report:
            for line in (*fit_module.report_lines(table), *current_lines):
                _print(line)
            _print("report only: seed unchanged; 0 CFBD calls")
            return EXIT_OK
        result = fit_module.fit_constants(table)
        for line in fit_module.summary_lines(result, table):
            _print(line)
        if self._args.dry_run:
            _print("dry run: nothing written")
            return EXIT_OK
        output = Path(self._args.output)
        output.write_text(fit_module.render_seed(result), encoding="utf-8")
        _print(f"seed written: {output}; review the diff, then make sim to deploy it")
        return EXIT_OK


def _current_scorecard_lines(connection: Any, namespace: Namespace) -> list[str]:
    """Stored 2026 prediction evidence next to the historic holdout report."""
    scorecard = qualified_name(namespace.sim_schema, "PREDICTION_SCORECARD")
    calibration = qualified_name(namespace.sim_schema, "PREDICTION_CALIBRATION")
    season_row = connection.execute(
        f"SELECT MAX(SEASON) FROM {scorecard} WHERE WEEK_SEQ IS NULL "
        "AND POPULATION = 'FBS_INVOLVED'"
    ).fetchone()
    if season_row is None or season_row[0] is None:
        return ["Current prediction scorecard: no graded season yet"]
    season = int(str(season_row[0]))
    lines = [
        f"{season} stored prediction scorecard (FBS involved; advance and reconstructed separate):"
    ]
    rows = connection.execute(
        f"SELECT FREEZE_KIND, GAMES_GRADED, CORRECT, WRONG, MAE_MARGIN, BRIER, LINE_BRIER "
        f"FROM {scorecard} WHERE SEASON = {season} AND WEEK_SEQ IS NULL "
        "AND POPULATION = 'FBS_INVOLVED' "
        "AND FREEZE_KIND IN ('IN_ADVANCE', 'RECONSTRUCTED') ORDER BY FREEZE_KIND"
    ).fetchall()
    for kind, games, correct, wrong, mae, brier, line_brier in rows:
        line_text = "—" if line_brier is None else f"{float(line_brier):.5f}"
        lines.append(
            f"  {kind}: {int(games):,} graded, {int(correct):,}-{int(wrong):,}, "
            f"margin MAE {float(mae):.3f}, Brier {float(brier):.5f}, "
            f"stored-line Brier {line_text}"
        )
    buckets = connection.execute(
        f"SELECT FREEZE_KIND, BUCKET, GAMES_GRADED, MEAN_PICK_WIN_PROB, PICK_WIN_SHARE "
        f"FROM {calibration} WHERE SEASON = {season} AND POPULATION = 'FBS_INVOLVED' "
        "AND FREEZE_KIND IN ('IN_ADVANCE', 'RECONSTRUCTED') "
        "ORDER BY FREEZE_KIND, BUCKET"
    ).fetchall()
    lines.append(f"{season} stored pick-calibration rows:")
    for kind, bucket, games, predicted, observed in buckets:
        lines.append(
            f"  {kind} {int(bucket) / 10:.1f}-{(int(bucket) + 1) / 10:.1f}: "
            f"n={int(games):,}, predicted={float(predicted):.3f}, "
            f"observed={float(observed):.3f}"
        )
    return lines


def _common() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--target", default=None, help="local or aws (default: $TARGET, else local)"
    )
    common.add_argument("--dsn", default=None, help="overrides host and port of the target")
    common.add_argument("--prefix", default="", help="namespace prefix, for example TEST_")
    return common


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.sim",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    deploy = commands.add_parser(
        "deploy", parents=[_common()], help="lint and deploy CFB_SIM and CFB_INFRA.SIM_ENGINE"
    )
    deploy.add_argument(
        "--lint-only",
        action="store_true",
        help="lint, render and build only: no connection, exit 0 or 2",
    )
    simulate = commands.add_parser(
        "simulate", parents=[_common()], help="run CFB_INFRA.SIMULATE and print the top odds"
    )
    simulate.add_argument("--season", type=int, required=True)
    simulate.add_argument("--week", type=int, required=True)
    simulate.add_argument("--season-type", default=None, help="regular (default) or postseason")
    simulate.add_argument("--sims", type=int, default=None, help="seasons (default 100,000)")
    simulate.add_argument("--batch", type=int, default=None, help="seasons per batch (5,000)")
    simulate.add_argument("--seed", type=int, default=None, help="run seed (season x 1000 + week)")
    simulate.add_argument("--label", default=None, help="free label of the run")
    predict = commands.add_parser(
        "predict", parents=[_common()], help="freeze next-week predictions through an as-of point"
    )
    predict.add_argument("--season", type=int, required=True)
    predict.add_argument("--week", type=int, required=True)
    predict.add_argument("--season-type", default=None, help="regular (default) or postseason")
    what = commands.add_parser(
        "what-if", parents=[_common()], help="a team's playoff odds if it wins or loses games"
    )
    what.add_argument("--team", required=True, help="school name or team id")
    what.add_argument("--run", type=int, default=None, help="run id (newest DONE run)")
    what.add_argument("--win", default=None, help="next, or game ids the team wins")
    what.add_argument("--lose", default=None, help="next, or game ids the team loses")
    fit_command = commands.add_parser(
        "fit", parents=[_common()], help="fit the score model's constants and write the seed"
    )
    fit_command.add_argument(
        "--output", default=str(fit_module.SEED_PATH), help="the seed file to write"
    )
    fit_command.add_argument(
        "--dry-run", action="store_true", help="print the fit and write nothing"
    )
    fit_command.add_argument(
        "--report",
        action="store_true",
        help="print the 2024-2025 holdout and current scorecard; write nothing",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    connector: Connector = connect_sys,
    resolve: Callable[..., target.Target] = target.resolve,
    options: SimOptions | None = None,
) -> int:
    args = _parser().parse_args(argv)
    command = _Command(args, connector, resolve, options or SimOptions())
    try:
        run: Callable[[], int] = getattr(command, args.command.replace("-", "_"))
        return run()
    except SimRefused as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except RunLockRefused as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except SimLintError as error:
        print(f"{args.command} failed: {error}", file=sys.stderr)
        return EXIT_ERROR
    except (
        DeployError,
        bundle.BundleError,
        RunLockError,
        target.TargetError,
        NamespaceError,
        OSError,
    ) as error:
        print(f"{args.command} failed: {error}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as error:
        message = str(getattr(error, "message", error)).strip().splitlines()
        print(
            f"{args.command} failed: {message[0] if message else type(error).__name__}",
            file=sys.stderr,
        )
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
