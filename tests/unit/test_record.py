"""Unit tests of the live recorder `scripts/record.py`, with fakes only: zero CFBD calls."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scripts import record
from tests.synthetic import fake
from vs.cfbd_vs.http import MAX_ATTEMPTS
from vs.cfbd_vs.replay import ReplayStore, assemble_parts, split_parts
from vs.host import ledger, target
from vs.host.ledger import Entry, Ledger

PLAN = record.DEFAULT_PLAN
GAMES = "/games?seasonType=both&year=2024"
GAMES_WEEK = "/games?seasonType=both&week=8&year=2024"
RANKINGS = "/rankings?seasonType=both&year=2024"
NOW = datetime(2026, 10, 22, 15, 0, 0, tzinfo=UTC)


def raw_rows(
    slice_key: str, body: str, *, status: int = 200, attempts: int | None = 1, limit: int = 0
) -> list[dict[str, Any]]:
    parts = split_parts(body, limit) if limit else split_parts(body)
    return [
        {
            "SLICE_KEY": slice_key,
            "PART_NO": number,
            "PART_COUNT": len(parts),
            "BODY_PART": part,
            "HTTP_STATUS": status,
            "ATTEMPTS": attempts,
            "MS": 250,
            "REQUESTED_AT": "2026-10-22 15:00:01.000000",
        }
        for number, part in enumerate(parts, start=1)
    ]


def synthetic_body(slice_key: str) -> str:
    path, _, query = slice_key.partition("?")
    pairs = dict(item.split("=", 1) for item in query.split("&") if item)
    return fake.response_for(path, pairs).decode("utf-8")


class FakeApi:
    """The fetch UDF's raw output over a CFBD that charges one call per attempt (by default)."""

    def __init__(
        self,
        *,
        remaining: int = 3000,
        failures: Mapping[str, str] | None = None,
        attempts: Mapping[str, int] | None = None,
        charges: Mapping[str, int] | None = None,
        bodies: Mapping[str, str] | None = None,
        part_limit: int = 0,
        info_fails_after: int | None = None,
    ) -> None:
        self.remaining = remaining
        self.failures = dict(failures or {})
        self.attempts = dict(attempts or {})
        self.charges = dict(charges or {})
        self.bodies = dict(bodies or {})
        self.part_limit = part_limit
        self.info_fails_after = info_fails_after
        self.requests: list[str] = []

    @property
    def data_requests(self) -> list[str]:
        return [key for key in self.requests if key != record.INFO_SLICE]

    def fetch(self, slice_key: str) -> list[dict[str, Any]]:
        self.requests.append(slice_key)
        if slice_key == record.INFO_SLICE:
            return self._info()
        attempts = self.attempts.get(slice_key, 1)
        self.remaining -= self.charges.get(slice_key, attempts)
        if slice_key in self.failures:
            raise record.RawFetchFailed(self.failures[slice_key])
        body = self.bodies.get(slice_key) or synthetic_body(slice_key)
        return raw_rows(slice_key, body, attempts=attempts, limit=self.part_limit)

    def _info(self) -> list[dict[str, Any]]:
        if self.info_fails_after is not None and len(self.data_requests) >= self.info_fails_after:
            raise record.RawFetchFailed("CFBD_VS.@RAW: /info: request failed after 4 attempts")
        body = json.dumps(
            {
                "patronLevel": 0,
                "tierName": "Academic",
                "monthlyLimit": 3000,
                "remainingCalls": self.remaining,
                "usedCalls": 3000 - self.remaining,
                "resetAt": "2026-11-01T00:00:00.000Z",
            }
        )
        return raw_rows(record.INFO_SLICE, body)


def make_paths(tmp_path: Path) -> record.Paths:
    return record.Paths(
        ledger=tmp_path / "cache" / "live-calls.jsonl",
        replay_cache=tmp_path / "cache" / "replay",
        measurements=tmp_path / "cache" / "measurements",
        local_bucketfs_replay=tmp_path / "bucketfs" / "bfsdefault" / "default" / "cfbd" / "replay",
    )


def clock() -> datetime:
    return NOW


def make_recorder(
    tmp_path: Path,
    api: FakeApi,
    *,
    target_name: str = "local",
    cap: int = record.DEFAULT_PLAN_CAP,
    lines: list[str] | None = None,
    now: datetime = NOW,
) -> record.Recorder:
    paths = make_paths(tmp_path)
    sinks, _ = record.replay_sinks(target_name, paths)
    return record.Recorder(
        api,
        catalog=fake.catalog(),
        book=Ledger(paths.ledger),
        budget=record.Budget(PLAN, cap),
        target_name=target_name,
        sinks=sinks,
        measurements=paths.measurements,
        clock=lambda: now,
        out=(lines if lines is not None else []).append,
    )


def spend(
    tmp_path: Path, cost: int, *, plan: str = PLAN, ts: str = "2026-10-22T10:00:00.000Z"
) -> None:
    Ledger(make_paths(tmp_path).ledger).append(
        Entry(ts=ts, plan=plan, target="aws", run="earlier", slice_key="/teams", cost=cost)
    )


def entries(tmp_path: Path) -> list[Entry]:
    return Ledger(make_paths(tmp_path).ledger).entries()


def patterns(*slice_keys: str) -> list[record.Pattern]:
    return [record.Pattern(key) for key in slice_keys]


# --- patterns ------------------------------------------------------------------------------


def test_patterns_accept_plain_slices_and_mappings_with_a_note() -> None:
    text = f"patterns:\n  - {GAMES}\n  - slice: {RANKINGS}\n    note: polls\n"
    loaded = record.load_patterns(text, fake.catalog())
    assert loaded == [record.Pattern(GAMES), record.Pattern(RANKINGS, "polls")]


def test_patterns_accept_path_parameters_and_encoded_strings() -> None:
    text = (
        "patterns:\n"
        "  - /games/401628374/preview\n"
        "  - /teams/matchup?team1=Michigan&team2=Ohio%20State\n"
    )
    assert [p.slice_key for p in record.load_patterns(text, fake.catalog())] == [
        "/games/401628374/preview",
        "/teams/matchup?team1=Michigan&team2=Ohio%20State",
    ]


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("patterns: []\n", "no patterns"),
        ("calls: [/teams]\n", "patterns"),
        ("patterns:\n  - slice: /teams\n    why: x\n", "unknown key"),
        ("patterns:\n  - /teams\n  - /teams\n", "more than once"),
        ("patterns:\n  - /games?year=2024&seasonType=both\n", "canonical"),
        ("patterns:\n  - /games?seasonType=both&year=02024\n", "canonical"),
        ("patterns:\n  - /teams/matchup?team1=Michigan&team2=Ohio+State\n", "canonical"),
        ("patterns:\n  - /games?seasonType=both&year=twenty\n", "integer"),
        ("patterns:\n  - /games?season=2024\n", "unknown query parameter"),
        ("patterns:\n  - /nowhere\n", "no catalog endpoint"),
        ("patterns:\n  - '/games/{id}/preview'\n", "placeholder"),
        ("patterns:\n  - /plays/stats?gameId=…\n", "printable ASCII"),
        ("patterns:\n  - games?year=2024\n", "printable ASCII"),
        ("patterns:\n  - /games?year\n", "no '='"),
    ],
)
def test_patterns_that_would_waste_or_misfile_a_call_are_rejected(text: str, reason: str) -> None:
    with pytest.raises(record.PatternError, match=reason):
        record.load_patterns(text, fake.catalog())


def test_tables_for_a_path_are_every_table_of_its_endpoint() -> None:
    names = {table.name for table in record.tables_for(fake.catalog(), "/rankings")}
    assert names == {"RANKINGS", "RANKINGS_POLLS", "RANKINGS_POLLS_RANKS"}
    preview = record.tables_for(fake.catalog(), "/games/401628374/preview")
    assert preview
    assert {table.endpoint for table in preview} == {"/games/{gameId}/preview"}
    assert record.tables_for(fake.catalog(), "/games/teams") != []
    assert {t.endpoint for t in record.tables_for(fake.catalog(), "/games/teams")} == {
        "/games/teams"
    }


def test_already_recorded_slices_are_skipped_unless_rerecording(tmp_path: Path) -> None:
    store = ReplayStore(str(make_paths(tmp_path).replay_cache))
    store.save(assemble_parts(raw_rows(GAMES, synthetic_body(GAMES))))
    wanted = patterns(GAMES, RANKINGS)
    assert record.select_patterns(wanted, store, rerecord=False) == (
        patterns(RANKINGS),
        patterns(GAMES),
    )
    assert record.select_patterns(wanted, store, rerecord=True) == (wanted, [])


# --- budget --------------------------------------------------------------------------------


def test_the_budget_reserves_retry_headroom_for_the_last_planned_call(tmp_path: Path) -> None:
    budget = record.Budget(PLAN, 50)
    spend(tmp_path, 40)
    book = entries(tmp_path)
    record.check_budget(budget, book, "2026-10", 50 - 40 - (MAX_ATTEMPTS - 1))
    with pytest.raises(record.Refused, match="50"):
        record.check_budget(budget, book, "2026-10", 50 - 40 - (MAX_ATTEMPTS - 1) + 1)


def test_the_month_cap_counts_every_plan_of_the_month_only(tmp_path: Path) -> None:
    budget = record.Budget(PLAN, 50)
    spend(tmp_path, 390, plan="add-cfb-persistence-refresh")
    spend(tmp_path, 500, plan="older-plan", ts="2026-09-30T12:00:00.000Z")
    book = entries(tmp_path)
    assert budget.headroom(book, "2026-10") == 10
    with pytest.raises(record.Refused, match="400"):
        record.check_budget(budget, book, "2026-10", 8)


# --- recording run -------------------------------------------------------------------------


def test_a_run_records_every_slice_and_logs_each_call_with_its_quota_reading(
    tmp_path: Path,
) -> None:
    api = FakeApi(remaining=3000)
    exit_code = make_recorder(tmp_path, api).record(patterns(GAMES, RANKINGS))
    assert exit_code == record.EXIT_OK
    assert api.data_requests == [GAMES, RANKINGS]
    logged = entries(tmp_path)
    assert [e.slice_key for e in logged] == ["/info", GAMES, RANKINGS, "/info"]
    first, games, rankings, last = logged
    assert (first.cost, first.remaining_before, first.remaining_after) == (0, 3000, 3000)
    assert (games.cost, games.remaining_before, games.remaining_after) == (1, 3000, 2999)
    assert (rankings.cost, rankings.remaining_before, rankings.remaining_after) == (1, 2999, 2998)
    assert (last.cost, last.remaining_after) == (0, 2998)
    assert games.status == 200 and games.attempts == 1 and games.ms == 250
    assert games.bytes == len(synthetic_body(GAMES).encode("utf-8"))
    assert {e.plan for e in logged} == {PLAN}
    assert {e.target for e in logged} == {"local"}
    assert len({e.run for e in logged}) == 1
    assert ledger.plan_total(logged, PLAN) == 2


def test_recordings_land_in_the_cache_and_the_local_bucketfs_and_replay(tmp_path: Path) -> None:
    api = FakeApi(part_limit=1000)
    make_recorder(tmp_path, api).record(patterns(GAMES))
    paths = make_paths(tmp_path)
    for base in (paths.replay_cache, paths.local_bucketfs_replay):
        recording = ReplayStore(str(base)).load(GAMES)
        assert recording.body == synthetic_body(GAMES)
        assert (recording.status, recording.attempts, recording.ms) == (200, 1, 250)


def test_on_aws_recordings_go_to_the_cache_only_and_the_run_says_so(tmp_path: Path) -> None:
    lines: list[str] = []
    paths = make_paths(tmp_path)
    sinks, note = record.replay_sinks("aws", paths)
    assert sinks == [paths.replay_cache]
    assert "cache/replay" in note and "--publish-local" in note
    make_recorder(tmp_path, FakeApi(), target_name="aws", lines=lines).record(patterns(GAMES))
    assert ReplayStore(str(paths.replay_cache)).exists(GAMES)
    assert not paths.local_bucketfs_replay.exists()


def test_measurements_hold_sizes_and_rows_per_catalog_table(tmp_path: Path) -> None:
    make_recorder(tmp_path, FakeApi()).record(patterns(RANKINGS))
    files = list(make_paths(tmp_path).measurements.glob("*.jsonl"))
    assert len(files) == 1
    (line,) = files[0].read_text(encoding="utf-8").splitlines()
    measured = json.loads(line)
    assert measured["slice_key"] == RANKINGS
    assert (measured["status"], measured["attempts"], measured["ms"]) == (200, 1, 250)
    assert measured["bytes"] == len(synthetic_body(RANKINGS).encode("utf-8"))
    assert set(measured["rows"]) == {"RANKINGS", "RANKINGS_POLLS", "RANKINGS_POLLS_RANKS"}
    assert all(count > 0 for count in measured["rows"].values())
    assert measured["flatten_errors"] == {}


def test_a_flatten_failure_is_measured_not_raised(tmp_path: Path) -> None:
    api = FakeApi(bodies={GAMES: json.dumps([{"id": "not a number"}])})
    assert make_recorder(tmp_path, api).record(patterns(GAMES)) == record.EXIT_OK
    (line,) = next(make_paths(tmp_path).measurements.glob("*.jsonl")).read_text().splitlines()
    assert "GAMES" in json.loads(line)["flatten_errors"]


def test_a_failed_call_is_charged_and_stops_the_run(tmp_path: Path) -> None:
    failure = f"CFBD_VS.@RAW: {GAMES}: HTTP 429 after 4 attempts: rate limited"
    api = FakeApi(failures={GAMES: failure}, attempts={GAMES: 4})
    lines: list[str] = []
    exit_code = make_recorder(tmp_path, api, lines=lines).record(patterns(GAMES, RANKINGS))
    assert exit_code == record.EXIT_STOPPED
    assert api.data_requests == [GAMES]
    games = next(e for e in entries(tmp_path) if e.slice_key == GAMES)
    assert (games.status, games.attempts, games.cost) == (429, 4, 4)
    assert games.error is not None and "HTTP 429" in games.error
    assert games.bytes is None
    assert not ReplayStore(str(make_paths(tmp_path).replay_cache)).exists(GAMES)
    assert any("stopped" in line for line in lines)
    closing = entries(tmp_path)[-1]
    assert (closing.slice_key, closing.cost, closing.remaining_after) == ("/info", 0, 2996)


class RecordingDeployment:
    def __init__(self) -> None:
        self.connect_kwargs: dict[str, Any] = {}
        self.closed = False

    def connect(self, **kwargs: Any) -> Any:
        self.connect_kwargs = kwargs
        deployment = self

        class Closable(FakeConnection):
            def close(self) -> None:
                deployment.closed = True

        return Closable()


def test_the_recorder_connection_waits_long_enough_for_large_responses() -> None:
    deployment = RecordingDeployment()
    options = record._parser().parse_args([])
    with record.open_database_source(deployment, options, fake.catalog()):  # type: ignore[arg-type]
        pass
    assert deployment.connect_kwargs == {"socket_timeout": record.SOCKET_TIMEOUT_SECONDS}
    assert record.SOCKET_TIMEOUT_SECONDS >= 600
    assert deployment.closed


@pytest.mark.parametrize(
    ("message", "attempts", "status"),
    [
        ("CFBD_VS.@RAW: /games?year=1: HTTP 400: bad year", 1, 400),
        ("CFBD_VS.@RAW: /games?year=1: HTTP 401: the key has no access", 1, 401),
        ("CFBD_VS.@RAW: /games?year=1: request failed after 3 attempts: timeout", 3, None),
        ("CFBD_VS.@RAW: /games?year=1: HTTP 503 after 1 attempt: down", 1, 503),
        ("CFBD_VS.@RAW: /games?year=1: name resolution failed for host 'x'", 0, None),
        ("CFBD_VS.@RAW: /games?year=1: TLS certificate verification failed: x", 0, None),
        (
            "CFBD_VS.@RAW: the adapter and the fetch UDF are from different catalog versions",
            0,
            None,
        ),
        ("CFBD_VS.@RAW: connection 'CFBD_API' cannot be read: no access", 0, None),
        ("websocket closed unexpectedly", None, None),
    ],
)
def test_failure_text_gives_attempts_and_status(
    message: str, attempts: int | None, status: int | None
) -> None:
    assert record.failure_attempts(message) == attempts
    assert record.failure_status(message) == status


def test_an_unknown_failure_is_charged_all_attempts(tmp_path: Path) -> None:
    api = FakeApi(failures={GAMES: "websocket closed"}, charges={GAMES: 0})
    make_recorder(tmp_path, api).record(patterns(GAMES))
    games = next(e for e in entries(tmp_path) if e.slice_key == GAMES)
    assert (games.attempts, games.cost) == (None, MAX_ATTEMPTS)


def test_a_call_that_cost_more_than_its_attempts_is_charged_and_stops_the_run(
    tmp_path: Path,
) -> None:
    api = FakeApi(charges={GAMES: 3})
    lines: list[str] = []
    exit_code = make_recorder(tmp_path, api, lines=lines).record(patterns(GAMES, RANKINGS))
    assert exit_code == record.EXIT_STOPPED
    assert api.data_requests == [GAMES]
    games = next(e for e in entries(tmp_path) if e.slice_key == GAMES)
    assert (games.attempts, games.cost) == (1, 3)
    assert ReplayStore(str(make_paths(tmp_path).replay_cache)).exists(GAMES)
    assert any("remainingCalls" in line for line in lines)


def test_retries_that_eat_the_headroom_stop_the_run_before_the_cap(tmp_path: Path) -> None:
    spend(tmp_path, 42)
    api = FakeApi(attempts={GAMES: 4})
    exit_code = make_recorder(tmp_path, api, cap=50).record(patterns(GAMES, GAMES_WEEK, RANKINGS))
    assert exit_code == record.EXIT_STOPPED
    assert api.data_requests == [GAMES, GAMES_WEEK]
    assert ledger.plan_total(entries(tmp_path), PLAN) == 42 + 4 + 1
    assert ledger.plan_total(entries(tmp_path), PLAN) + MAX_ATTEMPTS > 50


def test_a_run_beyond_the_cap_is_refused_before_any_request(tmp_path: Path) -> None:
    spend(tmp_path, 48)
    api = FakeApi()
    with pytest.raises(record.Refused, match="50"):
        make_recorder(tmp_path, api, cap=50).record(patterns(GAMES))
    assert api.requests == []
    assert len(entries(tmp_path)) == 1


def test_no_data_call_without_a_quota_reading_first(tmp_path: Path) -> None:
    api = FakeApi(info_fails_after=0)
    with pytest.raises(record.RecordError, match="/info"):
        make_recorder(tmp_path, api).record(patterns(GAMES))
    assert api.data_requests == []
    assert entries(tmp_path) == []


def test_a_missing_quota_reading_after_a_call_is_logged_and_stops_the_run(tmp_path: Path) -> None:
    api = FakeApi(info_fails_after=1)
    exit_code = make_recorder(tmp_path, api).record(patterns(GAMES, RANKINGS))
    assert exit_code == record.EXIT_STOPPED
    assert api.data_requests == [GAMES]
    games = next(e for e in entries(tmp_path) if e.slice_key == GAMES)
    assert (games.remaining_before, games.remaining_after, games.cost) == (3000, None, 1)
    assert [e.slice_key for e in entries(tmp_path)] == ["/info", GAMES]


def test_a_run_is_refused_when_the_api_quota_cannot_cover_it(tmp_path: Path) -> None:
    api = FakeApi(remaining=1)
    with pytest.raises(record.Refused, match="remainingCalls"):
        make_recorder(tmp_path, api).record(patterns(GAMES, RANKINGS))
    assert api.data_requests == []
    assert [e.slice_key for e in entries(tmp_path)] == ["/info"]


def test_a_second_run_cannot_spend_while_one_holds_the_ledger(tmp_path: Path) -> None:
    api = FakeApi()
    held = Ledger(make_paths(tmp_path).ledger)
    with held.exclusive(), pytest.raises(ledger.LedgerError, match="another run"):
        make_recorder(tmp_path, api).record(patterns(GAMES))
    assert api.requests == []


def test_info_logs_a_free_quota_reading(tmp_path: Path) -> None:
    lines: list[str] = []
    api = FakeApi(remaining=2990)
    assert make_recorder(tmp_path, api, lines=lines).info() == record.EXIT_OK
    assert api.requests == [record.INFO_SLICE]
    (entry,) = entries(tmp_path)
    assert (entry.slice_key, entry.cost, entry.remaining_before, entry.remaining_after) == (
        "/info",
        0,
        2990,
        2990,
    )
    assert any("Academic" in line and "2990" in line for line in lines)


# --- database source -----------------------------------------------------------------------


class FakeStatement:
    def __init__(self, rows: Sequence[tuple[Any, ...]]) -> None:
        self._rows = list(rows)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakeConnection:
    def __init__(self, rows: Sequence[tuple[Any, ...]] = (), error: Exception | None = None):
        self.rows = rows
        self.error = error
        self.statements: list[str] = []

    def execute(self, sql: str) -> FakeStatement:
        self.statements.append(sql)
        if self.error is not None:
            raise self.error
        return FakeStatement(self.rows)


class FakeExaError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def database_source(connection: FakeConnection) -> record.DatabaseRawSource:
    return record.DatabaseRawSource(
        connection,
        fetch_script="CFB_INFRA.CFBD_FETCH",
        catalog_hash="abc123",
        connection_name="CFBD_API",
        doh_url="https://1.1.1.1/dns-query",
    )


def test_the_raw_call_goes_through_the_real_fetch_udf_in_live_raw_mode() -> None:
    connection = FakeConnection()
    database_source(connection).fetch(GAMES)
    (sql,) = connection.statements
    assert sql.startswith(
        f"SELECT CFB_INFRA.CFBD_FETCH('@RAW', '{GAMES}', 'abc123', 'live', 'CFBD_API', NULL, "
        "'https://1.1.1.1/dns-query', 1) EMITS ("
    )
    emits = sql.split("EMITS (", 1)[1]
    names = [name for name, _ in record.RAW_COLUMNS]
    assert [part.split()[0] for part in emits.removesuffix(") FROM DUAL").split(", ")] == [
        f'"{name}"' for name in names
    ]
    assert sql.endswith(") FROM DUAL")


def test_raw_rows_become_typed_dicts() -> None:
    row = (GAMES, "1", 1, "[]", "200", 2, "812", "2026-10-22 15:00:01.000000")
    (part,) = database_source(FakeConnection([row])).fetch(GAMES)
    assert part["PART_NO"] == 1 and part["HTTP_STATUS"] == 200 and part["MS"] == 812
    assert part["ATTEMPTS"] == 2 and part["BODY_PART"] == "[]"
    assert assemble_parts([part]).attempts == 2


def test_a_database_error_becomes_a_raw_fetch_failure_with_its_cause_line() -> None:
    error = FakeExaError(
        "VM error: F-UDF-CL-LIB-1127: Exception during run\n"
        "fetch.py:300 _rows\n"
        f"FetchEntryError: CFBD_VS.@RAW: {GAMES}: HTTP 400: bad request\n"
    )
    with pytest.raises(record.RawFetchFailed) as raised:
        database_source(FakeConnection(error=error)).fetch(GAMES)
    assert str(raised.value) == f"CFBD_VS.@RAW: {GAMES}: HTTP 400: bad request"


# --- command line --------------------------------------------------------------------------


@contextmanager
def opened(api: FakeApi) -> Iterator[FakeApi]:
    yield api


def run_main(
    tmp_path: Path, argv: list[str], api: FakeApi | None = None, *, target_name: str = "local"
) -> int:
    api = api or FakeApi()
    deployment = target.Target(target_name, "127.0.0.1", 8563, "sys", tmp_path, "unverified")
    return record.main(
        argv,
        paths=make_paths(tmp_path),
        clock=clock,
        resolve=lambda name, dsn=None: deployment,
        open_source=lambda deployment, options, catalog: opened(api),
    )


def write_patterns(tmp_path: Path, *slice_keys: str) -> str:
    path = tmp_path / "patterns.yaml"
    path.write_text("patterns:\n" + "".join(f"  - {key}\n" for key in slice_keys))
    return str(path)


def test_without_confirm_the_plan_is_printed_and_nothing_is_called(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spend(tmp_path, 7)
    api = FakeApi()
    file = write_patterns(tmp_path, GAMES, RANKINGS)
    assert run_main(tmp_path, ["--patterns", file, "--confirm", ""], api) == record.EXIT_REFUSED
    captured = capsys.readouterr()
    assert GAMES in captured.out and RANKINGS in captured.out
    assert "7 of 50" in captured.out
    assert "CONFIRM=yes" in captured.err
    assert api.requests == []
    assert len(entries(tmp_path)) == 1


def test_with_confirm_the_run_records(tmp_path: Path) -> None:
    api = FakeApi()
    file = write_patterns(tmp_path, GAMES)
    assert run_main(tmp_path, ["--patterns", file, "--confirm", "yes"], api) == record.EXIT_OK
    assert api.data_requests == [GAMES]


def test_a_refused_run_exits_one_and_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spend(tmp_path, 49)
    api = FakeApi()
    file = write_patterns(tmp_path, GAMES)
    assert run_main(tmp_path, ["--patterns", file, "--confirm", "yes"], api) == record.EXIT_REFUSED
    assert "50" in capsys.readouterr().err
    assert api.requests == []


def test_invalid_patterns_exit_two_without_calls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = FakeApi()
    file = write_patterns(tmp_path, "/nowhere")
    assert run_main(tmp_path, ["--patterns", file, "--confirm", "yes"], api) == record.EXIT_ERROR
    assert "/nowhere" in capsys.readouterr().err
    assert api.requests == []


def test_patterns_are_required_for_a_recording_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_main(tmp_path, ["--patterns", "", "--confirm", "yes"]) == record.EXIT_ERROR
    assert "PATTERNS" in capsys.readouterr().err


def test_everything_already_recorded_makes_no_call(tmp_path: Path) -> None:
    ReplayStore(str(make_paths(tmp_path).replay_cache)).save(
        assemble_parts(raw_rows(GAMES, synthetic_body(GAMES)))
    )
    api = FakeApi()
    file = write_patterns(tmp_path, GAMES)
    assert run_main(tmp_path, ["--patterns", file, "--confirm", "yes"], api) == record.EXIT_OK
    assert api.requests == []


def test_ledger_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spend(tmp_path, 7)
    spend(tmp_path, 5, plan="add-cfb-persistence-refresh")
    assert run_main(tmp_path, ["--ledger"]) == record.EXIT_OK
    out = capsys.readouterr().out
    assert f"{PLAN}: 7 of 50" in out
    assert "add-cfb-persistence-refresh: 5" in out
    assert "2026-10, all plans: 12 of 400" in out


def test_info_needs_no_confirm(tmp_path: Path) -> None:
    api = FakeApi()
    assert run_main(tmp_path, ["--info"], api) == record.EXIT_OK
    assert api.requests == [record.INFO_SLICE]


def test_publish_local_copies_cached_recordings_into_the_local_bucketfs(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    recording = assemble_parts(raw_rows(GAMES, synthetic_body(GAMES)))
    ReplayStore(str(paths.replay_cache)).save(recording)
    assert run_main(tmp_path, ["--publish-local"]) == record.EXIT_OK
    assert ReplayStore(str(paths.local_bucketfs_replay)).load(GAMES) == recording


def test_the_local_bucketfs_replay_directory_maps_the_default_replay_path() -> None:
    expected = (
        target.DEPLOYMENTS_ROOT
        / "local"
        / "local"
        / "runtime"
        / "exa"
        / "bucketfs"
        / "bfsdefault"
        / "default"
        / "cfbd"
        / "replay"
    )
    assert record.Paths().local_bucketfs_replay == expected


def test_ledger_timestamps_follow_the_clock(tmp_path: Path) -> None:
    make_recorder(tmp_path, FakeApi(), now=NOW + timedelta(days=10)).info()
    assert entries(tmp_path)[0].ts == "2026-11-01T15:00:00.000Z"


# --- documented virtual-schema queries (--query) -------------------------------------------

WEEK8 = "/games?seasonType=both&week=8&year=2019"
WEEK9 = "/games?seasonType=both&week=9&year=2019"
META = ("REQ_SLICE", "REQ_SOURCE", "REQ_STATUS", "REQ_ATTEMPTS", "REQ_MS", "REQ_ROWS", "REQ_AT")


def pushdown(slice_keys: Sequence[str], sources: Sequence[str] = ("LIVE",)) -> str:
    manifest = {
        "table": "GAMES",
        "fetch_mode": "live",
        "read_through": True,
        "budget": 10,
        "planned_requests": len(slice_keys),
        "slice_keys": list(slice_keys),
        "sources": list(sources),
    }
    return f"/*CFBD {json.dumps(manifest)}*/ SELECT 1"


class FakeVs(FakeApi):
    """FakeApi plus a virtual schema: EXPLAIN gives the manifest, running charges live slices."""

    def __init__(
        self,
        slice_keys: Sequence[str],
        result: tuple[list[str], list[tuple[Any, ...]]],
        *,
        sources: Sequence[str] = ("LIVE",),
        charge: int | None = None,
        query_error: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.slice_keys = list(slice_keys)
        self.result = result
        self.sources = list(sources)
        self.charge = charge
        self.query_error = query_error
        self.ran: list[str] = []

    def explain(self, sql: str) -> str:
        return pushdown(self.slice_keys, self.sources)

    def run(self, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        self.ran.append(sql)
        names, rows = self.result
        live = {
            row[names.index("REQ_SLICE")]
            for row in rows
            if "REQ_SOURCE" in names and row[names.index("REQ_SOURCE")] == "LIVE"
        }
        self.remaining -= self.charge if self.charge is not None else len(live)
        if self.query_error:
            raise record.RawFetchFailed(self.query_error)
        return names, rows


def games_rows(*specs: tuple[str, str, int]) -> tuple[list[str], list[tuple[Any, ...]]]:
    names = ["ID", *META]
    rows = []
    for number, (slice_key, source, count) in enumerate(specs):
        for index in range(count):
            status, attempts, ms = (200, 1, 700 + number) if source == "LIVE" else (None, 0, None)
            at = f"2026-10-22 15:00:0{number}.000000"
            rows.append((index, slice_key, source, status, attempts, ms, count, at))
    return names, rows


def test_a_query_logs_each_live_slice_with_its_request_metadata(tmp_path: Path) -> None:
    vs = FakeVs([WEEK8, WEEK9], games_rows((WEEK8, "LIVE", 3), (WEEK9, "LIVE", 2)))
    assert make_recorder(tmp_path, vs).query("SELECT * FROM CFBD_VS.GAMES") == record.EXIT_OK
    logged = entries(tmp_path)
    assert [e.slice_key for e in logged] == ["/info", WEEK8, WEEK9, "/info"]
    week8 = logged[1]
    assert (week8.status, week8.attempts, week8.ms, week8.cost) == (200, 1, 700, 1)
    assert (week8.remaining_before, week8.remaining_after) == (3000, 2998)
    assert logged[-1].remaining_after == 2998
    (line,) = next(make_paths(tmp_path).measurements.glob("*.jsonl")).read_text().splitlines()
    measured = json.loads(line)
    assert measured["query"] == "SELECT * FROM CFBD_VS.GAMES"
    assert measured["wall_ms"] >= 0 and measured["rows"] == 5
    assert [s["slice_key"] for s in measured["slices"]] == [WEEK8, WEEK9]
    assert measured["slices"][0]["requested_at"] == "2026-10-22 15:00:00.000000"


def test_a_stored_slice_costs_nothing_and_logs_no_call(tmp_path: Path) -> None:
    vs = FakeVs([WEEK8], games_rows((WEEK8, "STORED", 3)), sources=("STORED", "LIVE"))
    lines: list[str] = []
    assert make_recorder(tmp_path, vs, lines=lines).query("SELECT *") == record.EXIT_OK
    assert [e.slice_key for e in entries(tmp_path)] == ["/info", "/info"]
    assert entries(tmp_path)[-1].remaining_after == 3000
    assert any("0 live call" in line for line in lines)


def test_a_query_is_charged_at_least_the_observed_drop(tmp_path: Path) -> None:
    vs = FakeVs([WEEK8, WEEK9], games_rows((WEEK8, "LIVE", 1), (WEEK9, "LIVE", 1)), charge=3)
    lines: list[str] = []
    make_recorder(tmp_path, vs, lines=lines).query("SELECT *")
    costs = [e.cost for e in entries(tmp_path) if e.slice_key != "/info"]
    assert costs == [1, 2]
    assert any("remainingCalls" in line and "3" in line for line in lines)


def test_a_query_beyond_the_cap_is_refused_before_it_runs(tmp_path: Path) -> None:
    spend(tmp_path, 46)
    vs = FakeVs([WEEK8, WEEK9], games_rows((WEEK8, "LIVE", 1), (WEEK9, "LIVE", 1)))
    with pytest.raises(record.Refused, match="50"):
        make_recorder(tmp_path, vs, cap=50).query("SELECT *")
    assert vs.ran == [] and vs.requests == []


def test_a_query_without_request_columns_is_charged_per_planned_slice(tmp_path: Path) -> None:
    vs = FakeVs([WEEK8, WEEK9], (["COUNT(*)"], [(5,)]), charge=2)
    make_recorder(tmp_path, vs).query("SELECT COUNT(*) FROM CFBD_VS.GAMES")
    data = [e for e in entries(tmp_path) if e.slice_key != "/info"]
    assert [(e.slice_key, e.attempts, e.cost) for e in data] == [(WEEK8, None, 1), (WEEK9, None, 1)]


def test_a_failed_query_charges_every_planned_slice_and_stops(tmp_path: Path) -> None:
    vs = FakeVs(
        [WEEK8, WEEK9], ([], []), charge=1, query_error="CFBD_VS.GAMES: HTTP 429 after 4 attempts"
    )
    assert make_recorder(tmp_path, vs).query("SELECT *") == record.EXIT_STOPPED
    data = [e for e in entries(tmp_path) if e.slice_key != "/info"]
    assert [e.slice_key for e in data] == [WEEK8, WEEK9]
    assert all(e.error and "HTTP 429" in e.error for e in data)
    assert sum(e.cost for e in data) == 2


def test_the_query_option_prints_the_plan_and_needs_confirm(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vs = FakeVs([WEEK8], games_rows((WEEK8, "LIVE", 1)))
    assert run_main(tmp_path, ["--query", "SELECT * FROM CFBD_VS.GAMES"], vs) == record.EXIT_REFUSED
    assert WEEK8 in capsys.readouterr().out
    assert vs.ran == [] and vs.requests == []
    assert run_main(tmp_path, ["--query", "SELECT *", "--confirm", "yes"], vs) == record.EXIT_OK
    assert len(vs.ran) == 1
