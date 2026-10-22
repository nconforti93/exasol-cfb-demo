"""`make backfill`, `make refresh`, `make quota`: the guarded host wrappers of the loader scripts.

    python -m scripts.loader_cli [--target local|aws] [--dsn HOST:PORT] [--prefix PREFIX_]
                                 [--confirm yes] backfill --profile demo|full
                                 [--from-season YEAR] [--max-calls N]
    python -m scripts.loader_cli [...] [--confirm yes] refresh --season YEAR --week N
                                 [--season-type regular|postseason]
    python -m scripts.loader_cli [...] quota

    make backfill PROFILE=demo [FROM=y] [MAX_CALLS=n] [CONFIRM=yes] [TARGET=local|aws]
    make refresh SEASON=y WEEK=w [SEASON_TYPE=postseason] [CONFIRM=yes] [TARGET=local|aws]
    make quota [TARGET=local|aws]

`backfill` runs `EXECUTE SCRIPT CFB_INFRA.BACKFILL(profile, from_season, max_calls)` and `refresh`
runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(season, week, season_type)` as `sys` on the target
(`vs/host/target.py`). `quota` prints `CFB_META.QUOTA` and the ledger room. `--prefix` selects a
namespace (for example `PROBE_`), which reads the mock API, never the real key.

Order of the guards of `backfill` and `refresh`; each stops the command before the next one runs:

1. The arguments are validated, with no connection made (exit 2).
2. The target connects, with a socket timeout of `SOCKET_TIMEOUT_SECONDS`: pyexasol's 30 s default
   once cut off a response that had been paid for.
3. The Lua script exists in `CFB_INFRA`. If it does not, the error says to run `make deploy`
   (exit 2).
4. The estimate is printed, from the deployed views: `BACKFILL_QUEUE` for the profile and season, or
   the weekly slices of the season that are missing or not final, plus the tables the orphan guard
   or a missing feature blocks. In live mode `CFB_META.QUOTA` is read (free, `/info`) and printed
   too. An unreadable quota is an error.
5. Live mode only, i.e. the virtual schema's `FETCH_MODE` is `live` (the default). A replay run
   makes no call: no quota, no confirmation, no ledger.
   a. `CONFIRM=yes` (`--confirm yes`) is required; anything else prints the estimate and refuses
      (exit 1). A dry run (`--max-calls 0`) needs it too.
   b. The ledger (`vs/host/ledger.py`), for the real namespace only, and not with `LEDGER=off`:
      `Ledger.check(plan, month, estimate, /info used calls)` must pass. `MAX_CALLS` defaults to
      `Ledger.room` and is capped at it (`NULL` with `LEDGER=off`). A run that needs calls while the
      room is 0 is refused (exit 1). The verdict is also shown in the preview.
6. The run happens under the ledger lock, so two runs never spend at the same time.

After the run (live mode), whatever its outcome: the result table is printed, `/info` is read
again, and the run is appended to the ledger as `<KIND>#<run id>` with the larger of
`LOAD_RUN.CALLS` and the `/info` delta of used calls (a delta that includes other clients only
over-counts), the remaining calls before and after, and the first line of an error. When
neither can be read, the estimate is charged. The exit code is 0 for `DONE`, `STOPPED_BUDGET` and
`STOPPED_MAX_CALLS`, 3 when the run ended `FAILED` or `DONE_WITH_FAILURES`, a step failed or the
script raised.

Exit codes: 0 ok, 1 refused (nothing was sent), 2 error (arguments, target, database, ledger file),
3 the run itself failed.

`--prefix` namespaces do not touch the ledger: they read `CFBD_API_MOCK`, whose calls cost nothing.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from loader.plan import FIRST_SEASON, LAST_SEASON, PROFILES
from vs.cfbd_vs.sqlrender import quote_identifier, string_literal
from vs.host import ledger, target
from vs.host.ledger import Ledger, LedgerError, LedgerRefused
from vs.host.namespace import Namespace, NamespaceError

PLAN_NAME = "add-cfb-persistence-refresh"
CONFIRM_WORD = "yes"
SOCKET_TIMEOUT_SECONDS = 900
FETCH_MODE_PROPERTY = "FETCH_MODE"
LIVE_MODE = "live"
BACKFILL = "BACKFILL"
REFRESH_WEEK = "REFRESH_WEEK"
SEASON_TYPES = ("regular", "postseason")
TOTAL_TABLE = "*"
OK_STATUSES = frozenset({"DONE", "STOPPED_BUDGET", "STOPPED_MAX_CALLS"})
FAILED_STEP = "FAILED"
UNKNOWN_RUN = 0
MAX_WEEK = 99
DETAIL_COLUMN = "DETAIL"

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_ERROR = 2
EXIT_FAILED = 3


class Refused(Exception):
    """A guard stopped the command before anything was sent."""


class LoaderError(Exception):
    """The command cannot continue; the message is for the person at the terminal."""


class Statement(Protocol):
    def column_names(self) -> list[str]: ...

    def fetchall(self) -> list[Any]: ...


class Connection(Protocol):
    """The part of a pyexasol connection used here."""

    def execute(self, query: str) -> Statement: ...

    def close(self) -> None: ...


Connector = Callable[[target.Target], Connection]


def connect_deployment(deployment: target.Target) -> Connection:
    """A sys connection that waits `SOCKET_TIMEOUT_SECONDS` for each statement."""
    connection: Connection = deployment.connect(socket_timeout=SOCKET_TIMEOUT_SECONDS)
    return connection


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Estimate:
    """The calls a run is expected to need and where they go; blocked tables cost nothing."""

    calls: int
    per_table: tuple[tuple[str, int], ...]
    blocked: tuple[tuple[str, str], ...]

    def lines(self, headline: str) -> list[str]:
        lines = [f"{headline}: estimate: {self.calls} calls in {len(self.per_table)} tables"]
        width = max((len(name) for name, _ in self.per_table), default=0)
        lines.extend(f"  {name.ljust(width)}  {count:>5}" for name, count in self.per_table)
        if self.blocked:
            listed = ", ".join(f"{table} ({reason})" for table, reason in self.blocked)
            lines.append(f"  blocked, no calls: {listed}")
        return lines


@dataclass(frozen=True)
class Quota:
    """One row of `CFB_META.QUOTA`."""

    values: Mapping[str, Any]

    @property
    def used_calls(self) -> int:
        return int(self.values["USED_CALLS"])

    @property
    def remaining_calls(self) -> int:
        return int(self.values["REMAINING_CALLS"])

    def lines(self) -> list[str]:
        width = max(len(name) for name in self.values)
        return [f"  {name.ljust(width)}  {value}" for name, value in self.values.items()]


@dataclass(frozen=True)
class Spend:
    """What a live run may spend: `limit` is the script's `max_calls`, `room` the ledger's."""

    expected: int
    limit: int | None
    room: int | None
    wanted: int
    capped_from: int | None = None


@dataclass(frozen=True)
class Job:
    """One script call: what it is, what it costs and how to call it."""

    kind: str
    estimate: Estimate
    requested: int | None
    limit_applies: bool
    statement: Callable[[int | None], str]


@dataclass(frozen=True)
class RunRow:
    """The run's `LOAD_RUN` row."""

    run_id: int
    calls: int | None
    status: str | None


@dataclass(frozen=True)
class Outcome:
    """What the script returned or raised."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    error: str | None = None


def first_line(error: BaseException | str) -> str:
    message = str(getattr(error, "message", error)).strip()
    return message.splitlines()[0] if message else type(error).__name__


class _Session:
    def __init__(
        self,
        connection: Connection,
        namespace: Namespace,
        deployment: target.Target,
        book: Ledger,
        confirm: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._connection = connection
        self._namespace = namespace
        self._deployment = deployment
        self._book = book
        self._confirm = confirm
        self._clock = clock

    def quota(self) -> int:
        quota = self._read_quota()
        if quota is None:
            raise LoaderError(f"{self._meta('QUOTA')} has no row: /info could not be read")
        print("\n".join(["quota (CFB_META.QUOTA):", *quota.lines()]))
        print("\n".join(self._ledger_lines(quota)))
        return EXIT_OK

    def backfill(self, profile: str, from_season: int | None, max_calls: int | None) -> int:
        self._require_script(BACKFILL)
        estimate = self._backfill_estimate(profile, from_season)
        script = quote_identifier(self._namespace.infra_schema) + "." + quote_identifier(BACKFILL)

        def statement(limit: int | None) -> str:
            arguments = [string_literal(profile), _literal(from_season), _literal(limit)]
            return f"EXECUTE SCRIPT {script}({', '.join(arguments)})"

        scope = f"profile {profile}" + (f", seasons up to {from_season}" if from_season else "")
        return self._launch(
            Job(BACKFILL, estimate, max_calls, True, statement), f"backfill, {scope}"
        )

    def refresh(self, season: int, week: int, season_type: str) -> int:
        self._require_script(REFRESH_WEEK)
        estimate = self._refresh_estimate(season)
        script = (
            quote_identifier(self._namespace.infra_schema) + "." + quote_identifier(REFRESH_WEEK)
        )

        def statement(_: int | None) -> str:
            return f"EXECUTE SCRIPT {script}({season}, {week}, {string_literal(season_type)})"

        job = Job(REFRESH_WEEK, estimate, None, False, statement)
        return self._launch(job, f"refresh, season {season} week {week} {season_type}")

    def _launch(self, job: Job, headline: str) -> int:
        print("\n".join(job.estimate.lines(headline)))
        live = self._is_live()
        quota = self._preview_quota(live)
        spend = self._spend(job, quota)
        verdict = self._verdict(job, spend, quota)
        print("\n".join(self._spend_lines(live, spend, verdict, quota)))
        if live:
            if self._confirm != CONFIRM_WORD:
                raise Refused(f"nothing was sent; rerun with CONFIRM={CONFIRM_WORD}")
            if verdict:
                raise Refused(verdict)
        with self._exclusive():
            return self._run(job, spend, quota)

    def _run(self, job: Job, spend: Spend, before: Quota | None) -> int:
        outcome = self._execute(job.statement(spend.limit))
        print("\n".join(_outcome_lines(job.kind, outcome)))
        code = EXIT_FAILED if _failed(job.kind, outcome) else EXIT_OK
        if before is not None and self._uses_ledger():
            self._record(job, spend, before, outcome)
        return code

    def _record(self, job: Job, spend: Spend, before: Quota, outcome: Outcome) -> None:
        run_id = _run_id(outcome)
        row = self._run_row(run_id)
        after = self._try_quota()
        delta = None if after is None else max(0, after.used_calls - before.used_calls)
        known = [n for n in (row.calls if row else None, delta) if n is not None]
        calls = max(known) if known else spend.expected
        note = (
            f"script reports {row.calls if row and row.calls is not None else '?'}, "
            f"/info delta {'?' if delta is None else delta}"
        )
        if not known:
            note += "; charged the estimate"
        self._book.record_run(
            PLAN_NAME,
            self._deployment.name,
            kind=job.kind,
            run_id=row.run_id if row else run_id,
            calls=calls,
            remaining_before=before.remaining_calls,
            remaining_after=None if after is None else after.remaining_calls,
            error=outcome.error,
            clock=self._clock,
        )
        print(
            f"ledger: recorded {calls} calls as {job.kind}#{row.run_id if row else run_id} ({note})"
        )

    def _spend(self, job: Job, quota: Quota | None) -> Spend:
        pending = job.estimate.calls
        wanted = pending if job.requested is None else min(pending, job.requested)
        room = self._room(quota)
        if room is None or not job.limit_applies:
            return Spend(wanted, job.requested, room, wanted)
        limit = room if job.requested is None else min(job.requested, room)
        capped = job.requested if job.requested is not None and job.requested > room else None
        return Spend(min(pending, limit), limit, room, wanted, capped)

    def _verdict(self, job: Job, spend: Spend, quota: Quota | None) -> str | None:
        if quota is None or not self._uses_ledger():
            return None
        try:
            self._book.check(
                PLAN_NAME, ledger.month_of(self._clock()), spend.expected, quota.used_calls
            )
        except LedgerRefused as error:
            return str(error)
        if job.limit_applies and spend.room == 0 and spend.wanted > 0:
            return (
                f"ledger room is 0 for plan {PLAN_NAME} in {ledger.month_of(self._clock())}, "
                f"and the run needs {spend.wanted} calls"
            )
        return None

    def _room(self, quota: Quota | None) -> int | None:
        if quota is None or not self._uses_ledger():
            return None
        return self._book.room(PLAN_NAME, ledger.month_of(self._clock()), quota.used_calls)

    def _spend_lines(
        self, live: bool, spend: Spend, verdict: str | None, quota: Quota | None
    ) -> list[str]:
        if not live or quota is None:
            return ["replay mode: no API calls, no quota read, no ledger"]
        lines = self._ledger_lines(quota)
        if spend.capped_from is not None:
            lines.append(f"MAX_CALLS {spend.capped_from} capped to the ledger room {spend.room}")
        if spend.room is not None and spend.limit is not None:
            lines.append(f"max_calls passed to the script: {spend.limit}")
        if verdict:
            lines.append(f"ledger: would refuse: {verdict}")
        return lines

    def _ledger_state(self) -> list[str]:
        if not self._book.enabled:
            return [f"ledger: off ({ledger.OFF_VARIABLE}=off); no checks, no records"]
        if not self._namespace.is_real:
            return [
                f"ledger: not used for namespace {self._namespace.prefix} (it reads the mock API)"
            ]
        return []

    def _ledger_lines(self, quota: Quota) -> list[str]:
        state = self._ledger_state()
        if state:
            return state
        month = ledger.month_of(self._clock())
        room = self._book.room(PLAN_NAME, month, quota.used_calls)
        config = self._book.config
        caps = f"{PLAN_NAME} cap {config.plans.get(PLAN_NAME, 'none')}, month {month} cap "
        return [f"ledger room now: {room} ({caps}{config.months.get(month, 'none')})"]

    def _uses_ledger(self) -> bool:
        return self._book.enabled and self._namespace.is_real

    def _exclusive(self) -> AbstractContextManager[None]:
        return self._book.exclusive() if self._uses_ledger() else nullcontext()

    def _preview_quota(self, live: bool) -> Quota | None:
        if not live:
            return None
        quota = self._read_quota()
        if quota is None:
            raise LoaderError(
                f"cannot read the quota: {self._meta('QUOTA')} has no row (/info unreadable); "
                "no live run without it"
            )
        print("\n".join(["quota:", *quota.lines()]))
        return quota

    def _is_live(self) -> bool:
        rows = self._query(
            "SELECT PROPERTY_VALUE FROM EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES "
            f"WHERE SCHEMA_NAME = {string_literal(self._namespace.vs_name)} "
            f"AND PROPERTY_NAME = {string_literal(FETCH_MODE_PROPERTY)}"
        )
        mode = str(rows[0][0]).lower() if rows and rows[0][0] else LIVE_MODE
        return mode == LIVE_MODE

    def _require_script(self, name: str) -> None:
        rows = self._query(
            "SELECT COUNT(*) FROM EXA_ALL_SCRIPTS "
            f"WHERE SCRIPT_SCHEMA = {string_literal(self._namespace.infra_schema)} "
            f"AND SCRIPT_NAME = {string_literal(name)}"
        )
        if int(rows[0][0]) == 0:
            where = f"{quote_identifier(self._namespace.infra_schema)}.{quote_identifier(name)}"
            raise LoaderError(
                f"the Lua script {where} is not installed: run `make deploy` "
                "(the loader deploy skips a script whose source does not exist yet)"
            )

    def _backfill_estimate(self, profile: str, from_season: int | None) -> Estimate:
        rank = PROFILES.index(profile) + 1
        season = (
            "" if from_season is None else f' AND ("SEASON" IS NULL OR "SEASON" <= {from_season})'
        )
        queue = self._query(
            f'SELECT "TABLE_NAME", COUNT(*) FROM {self._meta("BACKFILL_QUEUE")} '
            f'WHERE "PROFILE_RANK" <= {rank}{season} GROUP BY "TABLE_NAME" ORDER BY "TABLE_NAME"'
        )
        blocked = self._query(
            f'SELECT "TABLE_NAME", "BLOCKED_BY" FROM {self._meta("LOAD_STATUS")} '
            f'WHERE "BLOCKED_BY" IS NOT NULL AND "PROFILE_RANK" <= {rank} '
            'GROUP BY "TABLE_NAME", "BLOCKED_BY" ORDER BY "TABLE_NAME"'
        )
        return _estimate(queue, blocked)

    def _refresh_estimate(self, season: int) -> Estimate:
        slices = self._meta("LOAD_SLICES")
        settings = self._meta("LOADER_SETTINGS")
        orphans = self._meta("LOAD_ORPHANS")
        candidates = self._query(
            f'SELECT S."TABLE_NAME", COUNT(*) FROM {slices} S CROSS JOIN {settings} CFG '
            f'WHERE S."REFRESH_CLASS" = \'weekly\' AND S."SEASON" = {season} '
            'AND NOT S."IS_GAP" AND NOT S."IS_FINAL" '
            "AND INSTR(',' || CFG.\"REFRESH_PROFILES\" || ',', "
            "',' || S.\"PROFILE_NAME\" || ',') > 0 "
            f'AND NOT EXISTS (SELECT 1 FROM {orphans} O WHERE O."TABLE_NAME" = S."TABLE_NAME") '
            'GROUP BY S."TABLE_NAME" ORDER BY S."TABLE_NAME"'
        )
        blocked = self._query(
            f'SELECT "TABLE_NAME", "BLOCKED_BY" FROM {self._meta("LOAD_STATUS")} '
            'WHERE "BLOCKED_BY" IS NOT NULL AND "REFRESH_CLASS" = \'weekly\' '
            'GROUP BY "TABLE_NAME", "BLOCKED_BY" ORDER BY "TABLE_NAME"'
        )
        return _estimate(candidates, blocked)

    def _read_quota(self) -> Quota | None:
        statement = self._execute_sql(f"SELECT * FROM {self._meta('QUOTA')}")
        rows = statement.fetchall()
        if not rows:
            return None
        return Quota(dict(zip(statement.column_names(), rows[0], strict=True)))

    def _try_quota(self) -> Quota | None:
        try:
            return self._read_quota()
        except LoaderError:
            return None

    def _run_row(self, run_id: int) -> RunRow | None:
        where = (
            f'"RUN_ID" = {run_id}'
            if run_id != UNKNOWN_RUN
            else '"SESSION_ID" = CURRENT_SESSION ORDER BY "RUN_ID" DESC LIMIT 1'
        )
        try:
            rows = self._query(
                f'SELECT "RUN_ID", "CALLS", "RUN_STATUS" FROM {self._meta("LOAD_RUN")} '
                f"WHERE {where}"
            )
        except LoaderError:
            return None
        if not rows:
            return None
        found, calls, status = rows[0]
        return RunRow(int(found), None if calls is None else int(calls), status)

    def _execute(self, sql: str) -> Outcome:
        try:
            statement = self._connection.execute(sql)
            columns = tuple(statement.column_names())
            rows = tuple(tuple(row) for row in statement.fetchall())
        except Exception as error:
            message = first_line(error)
            print(f"script failed: {message}", file=sys.stderr)
            return Outcome((), (), message)
        return Outcome(columns, rows)

    def _query(self, sql: str) -> list[tuple[Any, ...]]:
        return [tuple(row) for row in self._execute_sql(sql).fetchall()]

    def _execute_sql(self, sql: str) -> Statement:
        try:
            return self._connection.execute(sql)
        except Exception as error:
            raise LoaderError(f"Exasol rejected a query: {first_line(error)}") from None

    def _meta(self, name: str) -> str:
        return f"{quote_identifier(self._namespace.meta_schema)}.{quote_identifier(name)}"


def _estimate(counts: Sequence[Sequence[Any]], blocked: Sequence[Sequence[Any]]) -> Estimate:
    per_table = tuple((str(name), int(count)) for name, count in counts)
    return Estimate(
        sum(count for _, count in per_table),
        per_table,
        tuple((str(table), str(reason)) for table, reason in blocked),
    )


def _literal(value: int | None) -> str:
    return "NULL" if value is None else str(value)


def _run_id(outcome: Outcome) -> int:
    if not outcome.rows or "RUN_ID" not in outcome.columns:
        return UNKNOWN_RUN
    return int(outcome.rows[0][outcome.columns.index("RUN_ID")])


def _failed(kind: str, outcome: Outcome) -> bool:
    if outcome.error is not None or not outcome.rows:
        return True
    if kind == REFRESH_WEEK:
        position = outcome.columns.index("STEP_STATUS")
        return any(row[position] == FAILED_STEP for row in outcome.rows)
    position = outcome.columns.index("RUN_STATUS")
    totals = [
        row for row in outcome.rows if row[outcome.columns.index("TABLE_NAME")] == TOTAL_TABLE
    ]
    return not totals or totals[-1][position] not in OK_STATUSES


def _outcome_lines(kind: str, outcome: Outcome) -> list[str]:
    if outcome.error is not None:
        return ["result: the script raised an error, see above"]
    shown = [name for name in outcome.columns if name != DETAIL_COLUMN]
    keep = [outcome.columns.index(name) for name in shown]
    cells = [[_cell(row[i]) for i in keep] for row in outcome.rows]
    widths = [max(len(name), *(len(line[n]) for line in cells)) for n, name in enumerate(shown)]
    lines = [
        "result:",
        "  " + "  ".join(name.ljust(w) for name, w in zip(shown, widths, strict=True)),
    ]
    lines.extend(
        "  " + "  ".join(c.ljust(w) for c, w in zip(line, widths, strict=True)) for line in cells
    )
    label = "STEP_NAME" if kind == REFRESH_WEEK else "TABLE_NAME"
    if DETAIL_COLUMN in outcome.columns and label in outcome.columns:
        detail, name = outcome.columns.index(DETAIL_COLUMN), outcome.columns.index(label)
        lines.extend(f"  {row[name]}: {row[detail]}" for row in outcome.rows if row[detail])
    return lines


def _cell(value: Any) -> str:
    return "" if value is None else str(value)


def _count(text: str) -> int:
    try:
        number = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if number < 0:
        raise argparse.ArgumentTypeError(f"{number} must not be negative")
    return number


def _season(text: str) -> int:
    number = _count(text)
    if not FIRST_SEASON <= number <= LAST_SEASON:
        raise argparse.ArgumentTypeError(f"season {number} is outside {FIRST_SEASON}-{LAST_SEASON}")
    return number


def _week(text: str) -> int:
    number = _count(text)
    if not 1 <= number <= MAX_WEEK:
        raise argparse.ArgumentTypeError(f"week {number} is outside 1-{MAX_WEEK}")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.loader_cli",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--target", default=None, help="local or aws (default: $TARGET, else local)"
    )
    parser.add_argument("--dsn", default=None, help="overrides host and port of the target")
    parser.add_argument("--prefix", default="", help="namespace prefix, for example PROBE_")
    parser.add_argument(
        "--confirm", default="", help=f"must be '{CONFIRM_WORD}' to run in live mode"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    backfill = commands.add_parser("backfill", help="run CFB_INFRA.BACKFILL")
    backfill.add_argument("--profile", choices=PROFILES, default=PROFILES[0])
    backfill.add_argument("--from-season", type=_season, default=None, help="newest season to load")
    backfill.add_argument("--max-calls", type=_count, default=None, help="most calls; 0 = dry run")
    refresh = commands.add_parser("refresh", help="run CFB_INFRA.REFRESH_WEEK")
    refresh.add_argument("--season", type=_season, required=True)
    refresh.add_argument("--week", type=_week, required=True)
    refresh.add_argument("--season-type", choices=SEASON_TYPES, default=SEASON_TYPES[0])
    commands.add_parser("quota", help="print CFB_META.QUOTA and the ledger room")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    connector: Connector = connect_deployment,
    resolve: Callable[..., target.Target] = target.resolve,
    book: Ledger | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> int:
    args = _parser().parse_args(argv)
    try:
        namespace = Namespace(args.prefix)
        deployment = resolve(args.target, dsn=args.dsn)
        connection = connector(deployment)
    except (NamespaceError, target.TargetError) as error:
        print(f"loader failed: {error}", file=sys.stderr)
        return EXIT_ERROR
    try:
        session = _Session(
            connection,
            namespace,
            deployment,
            Ledger.from_environment() if book is None else book,
            args.confirm,
            clock,
        )
        return _dispatch(session, args)
    except Refused as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except LedgerRefused as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except (LoaderError, LedgerError) as error:
        print(f"loader failed: {error}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        connection.close()


def _dispatch(session: _Session, args: argparse.Namespace) -> int:
    if args.command == "backfill":
        return session.backfill(args.profile, args.from_season, args.max_calls)
    if args.command == "refresh":
        return session.refresh(args.season, args.week, args.season_type)
    return session.quota()


if __name__ == "__main__":
    sys.exit(main())
