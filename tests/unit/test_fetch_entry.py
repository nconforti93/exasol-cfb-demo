import json
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from vs.cfbd_vs import fetch, naming
from vs.cfbd_vs.catalog import Catalog
from vs.cfbd_vs.fetch import FetchEntry, FetchEntryError
from vs.cfbd_vs.http import CfbdClient, FetchResult, TransportResponse
from vs.cfbd_vs.replay import (
    DEFAULT_REPLAY_PATH,
    MAX_PART_CHARS,
    Recording,
    ReplayStore,
    assemble_parts,
    encode_recording,
    recording_path,
)

HASH = "a" * 64
ADDRESS = "https://api.example.test"
KEY = "KEY-0123456789abcdefMARKER"
CONNECTION = "CFBD_API"
REPLAY_PATH = "/buckets/test/replay"
DOH = "https://1.1.1.1/dns-query"
GAMES_SLICE = "/games?seasonType=both&week=8&year=2024"
PREVIEW_SLICE = "/games/401628374/preview"
START = datetime(2026, 10, 21, 12, 0, 0, tzinfo=UTC)
RECORDED_AT = datetime(2026, 10, 1, 8, 30, 15, 250000, tzinfo=UTC)

RAW_NAMES = [
    "SLICE_KEY",
    "PART_NO",
    "PART_COUNT",
    "BODY_PART",
    "HTTP_STATUS",
    "ATTEMPTS",
    "MS",
    "REQUESTED_AT",
]


def col(name: str, kind: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "kind": kind, **extra}


def meta_columns() -> list[dict[str, Any]]:
    return [col(name, "meta") for name in naming.META_COLUMNS]


def param(name: str, column: str, kind: str, sql_type: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "column": column,
        "in": extra.pop("location", "query"),
        "type": kind,
        "sql_type": sql_type,
        "enum": None,
        "default": extra.pop("default", None),
        "description": "d",
    }


def table_dict(
    name: str, endpoint: str, columns: list[dict[str, Any]], params: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "name": name,
        "endpoint": endpoint,
        "kind": "endpoint",
        "parent": None,
        "array_path": [],
        "hoisted": False,
        "read_through": True,
        "gated_by": None,
        "params": params,
        "columns": columns,
        "rules": [],
    }


def make_catalog() -> Catalog:
    games = table_dict(
        "GAMES",
        "/games",
        [
            col("ROW_NO", "position", path=[]),
            col("ID", "value", path=["id"], sql_type="DECIMAL(18,0)"),
            col("HOME_TEAM", "value", path=["homeTeam"], sql_type="VARCHAR(100) UTF8"),
            col("START_DATE", "value", path=["startDate"], sql_type="TIMESTAMP"),
            col("P_YEAR", "param"),
            col("P_WEEK", "param"),
            col("P_SEASON_TYPE", "param"),
            col("P_COMPLETED", "param"),
            col("P_FACTOR", "param"),
            *meta_columns(),
        ],
        [
            param("year", "P_YEAR", "integer", "DECIMAL(18,0)"),
            param("week", "P_WEEK", "integer", "DECIMAL(18,0)"),
            param("seasonType", "P_SEASON_TYPE", "string", "VARCHAR(100) UTF8", default="both"),
            param("completed", "P_COMPLETED", "boolean", "BOOLEAN"),
            param("factor", "P_FACTOR", "number", "DOUBLE"),
        ],
    )
    preview = table_dict(
        "GAMES_PREVIEW",
        "/games/{gameId}/preview",
        [
            col("ROW_NO", "position"),
            col("ID", "value", path=["id"], sql_type="DECIMAL(18,0)"),
            col("P_GAME_ID", "param"),
            *meta_columns(),
        ],
        [param("gameId", "P_GAME_ID", "integer", "DECIMAL(18,0)", location="path")],
    )
    return Catalog.from_dict(
        {
            "catalog_hash": HASH,
            "spec_version": "1",
            "generator_version": "1",
            "excluded": [],
            "tables": [games, preview],
        }
    )


GAMES_NAMES = [
    "ROW_NO",
    "ID",
    "HOME_TEAM",
    "START_DATE",
    "P_YEAR",
    "P_WEEK",
    "P_SEASON_TYPE",
    "P_COMPLETED",
    "P_FACTOR",
    *naming.META_COLUMNS,
]

GAMES_BODY = [
    {"id": 1, "homeTeam": "Ohio State", "startDate": "2024-10-05T16:00:00.000Z"},
    {"id": 2, "homeTeam": "Texas", "startDate": "2024-10-05T19:30:00.000-04:00"},
]


class FakeClock:
    def __init__(self) -> None:
        self.elapsed = 0.0

    def monotonic(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        self.elapsed += seconds

    def utcnow(self) -> datetime:
        return START + timedelta(seconds=self.elapsed)


class FakeTransport:
    """Answers each slice from a mapping of slice key to body (or response or exception)."""

    def __init__(self, answers: Mapping[str, Any] | None = None, *, latency: float = 0.0) -> None:
        self.answers = dict(answers or {})
        self.latency = latency
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.clock = FakeClock()

    def __call__(
        self, address: str, target: str, headers: Mapping[str, str], timeout: float
    ) -> TransportResponse:
        self.calls.append((address, target, dict(headers)))
        self.clock.elapsed += self.latency
        answer = self.answers[target]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, TransportResponse):
            return answer
        return TransportResponse(200, {}, json.dumps(answer).encode())


class FakeExa:
    def __init__(
        self, names: list[str], connection_error: Exception | None = None, key: str = KEY
    ) -> None:
        self.meta = SimpleNamespace(
            output_columns=[SimpleNamespace(name=name) for name in names],
            output_column_count=len(names),
        )
        self.connection_error = connection_error
        self.key = key
        self.connection_reads: list[str] = []

    def get_connection(self, name: str) -> SimpleNamespace:
        self.connection_reads.append(name)
        if self.connection_error is not None:
            raise self.connection_error
        return SimpleNamespace(address=ADDRESS, user="", password=self.key)


class FakeCtx:
    """A SET-UDF context: attribute access reads the current row, `next()` advances."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self._index = 0
        self.emitted: list[tuple[Any, ...]] = []

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._rows[self._index][name]

    def next(self) -> bool:
        self._index += 1
        return self._index < len(self._rows)

    def emit(self, *values: Any) -> None:
        self.emitted.append(values)


class MemoryFiles:
    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = dict(files or {})
        self.reads: list[str] = []

    def read_text(self, path: str) -> str:
        self.reads.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write_text(self, path: str, text: str) -> None:
        self.files[path] = text


class RecordingExecutor:
    def __init__(self) -> None:
        self.item_counts: list[int] = []

    def map(self, function: Callable[[Any], Any], items: Iterable[Any]) -> Iterable[Any]:
        listed = list(items)
        self.item_counts.append(len(listed))
        return [function(item) for item in listed]


def args(
    slice_key: str = GAMES_SLICE,
    *,
    table: str = "GAMES",
    mode: str | None = "live",
    catalog_hash: str = HASH,
    connection: str | None = CONNECTION,
    replay_path: str | None = REPLAY_PATH,
    doh: str | None = DOH,
    parallel: int | None = 6,
) -> dict[str, Any]:
    return {
        "TABLE_NAME": table,
        "SLICE_KEY": slice_key,
        "CATALOG_HASH": catalog_hash,
        "FETCH_MODE": mode,
        "CONNECTION_NAME": connection,
        "REPLAY_PATH": replay_path,
        "DOH_URL": doh,
        "MAX_PARALLEL_FETCHES": parallel,
    }


class Rig:
    def __init__(
        self,
        names: list[str] | None = None,
        *,
        answers: Mapping[str, Any] | None = None,
        files: MemoryFiles | None = None,
        connection_error: Exception | None = None,
        executor: Any = None,
        latency: float = 0.0,
        key: str = KEY,
    ) -> None:
        self.exa = FakeExa(GAMES_NAMES if names is None else names, connection_error, key)
        self.transport = FakeTransport(answers, latency=latency)
        self.files = files or MemoryFiles()
        self.resolver_calls: list[str] = []
        self.entry = FetchEntry(
            self.exa,
            make_catalog(),
            transport=self.transport,
            clock=self.transport.clock,
            files=self.files,
            install_resolver=self.resolver_calls.append,
            executor=executor,
        )

    def run(self, *rows: dict[str, Any]) -> list[tuple[Any, ...]]:
        ctx = FakeCtx(list(rows))
        self.entry.run(ctx)
        return ctx.emitted


def games_row(emitted: tuple[Any, ...]) -> dict[str, Any]:
    return dict(zip(GAMES_NAMES, emitted, strict=True))


RECORDED_ATTEMPTS = 2


def recording(
    slice_key: str = GAMES_SLICE,
    body: Any = None,
    status: int = 200,
    ms: int = 412,
    attempts: int | None = RECORDED_ATTEMPTS,
) -> Recording:
    text = body if isinstance(body, str) else json.dumps(GAMES_BODY if body is None else body)
    return Recording(slice_key, status, ms, RECORDED_AT, text, attempts=attempts)


def older_format_files(item: Recording) -> MemoryFiles:
    """Replay files holding `item` as written before recordings stored the attempts."""
    data = json.loads(encode_recording(item))
    del data["attempts"]
    return MemoryFiles({recording_path(REPLAY_PATH, item.slice_key): json.dumps(data)})


def replay_files(*recordings: Recording) -> MemoryFiles:
    files = MemoryFiles()
    store = ReplayStore(REPLAY_PATH, files)
    for item in recordings:
        store.save(item)
    return files


def test_live_emits_flattened_rows_with_param_and_request_columns() -> None:
    rig = Rig(answers={GAMES_SLICE: GAMES_BODY}, latency=0.25)
    rows = [games_row(r) for r in rig.run(args())]
    assert [r["ID"] for r in rows] == [1, 2]
    assert [r["ROW_NO"] for r in rows] == [1, 2]
    assert rows[0]["HOME_TEAM"] == "Ohio State"
    assert rows[1]["START_DATE"] == datetime(2024, 10, 5, 23, 30)
    assert all(r["P_YEAR"] == 2024 and r["P_WEEK"] == 8 for r in rows)
    assert all(r["P_SEASON_TYPE"] == "both" for r in rows)
    assert all(r["P_COMPLETED"] is None and r["P_FACTOR"] is None for r in rows)
    first = rows[0]
    assert first["REQ_SLICE"] == GAMES_SLICE
    assert first["REQ_SOURCE"] == "LIVE"
    assert first["REQ_STATUS"] == 200
    assert first["REQ_ATTEMPTS"] == 1
    assert first["REQ_MS"] == 250
    assert first["REQ_ROWS"] == 2
    assert first["REQ_AT"] == datetime(2026, 10, 21, 12, 0, 0)
    assert first["REQ_AT"].tzinfo is None


def test_emission_follows_the_columns_exa_meta_declares() -> None:
    names = ["REQ_SLICE", "ID", "P_YEAR"]
    rig = Rig(names, answers={GAMES_SLICE: GAMES_BODY})
    assert rig.run(args()) == [(GAMES_SLICE, 1, 2024), (GAMES_SLICE, 2, 2024)]


def test_a_declared_column_the_table_lacks_is_an_error_before_any_request() -> None:
    rig = Rig([*GAMES_NAMES, "NOT_A_COLUMN"], answers={GAMES_SLICE: GAMES_BODY})
    with pytest.raises(FetchEntryError, match=r"CFBD_VS\.GAMES: .*NOT_A_COLUMN"):
        rig.run(args())
    assert rig.transport.calls == []


def test_boolean_and_number_params_are_echoed_typed() -> None:
    key = "/games?completed=true&factor=1.5&seasonType=regular&year=2024"
    rig = Rig(answers={key: GAMES_BODY})
    row = games_row(rig.run(args(key))[0])
    assert row["P_COMPLETED"] is True
    assert row["P_FACTOR"] == 1.5
    assert row["P_SEASON_TYPE"] == "regular"
    assert row["P_WEEK"] is None


def test_percent_encoded_values_are_decoded_for_the_echo_column() -> None:
    key = "/games?seasonType=a%20b%26c&year=2024"
    rig = Rig(answers={key: GAMES_BODY})
    assert games_row(rig.run(args(key))[0])["P_SEASON_TYPE"] == "a b&c"
    assert rig.transport.calls[0][1] == key


def test_path_params_are_echoed() -> None:
    rig = Rig(["ID", "P_GAME_ID", "REQ_SLICE"], answers={PREVIEW_SLICE: {"id": 401628374}})
    assert rig.run(args(PREVIEW_SLICE, table="GAMES_PREVIEW")) == [
        (401628374, 401628374, PREVIEW_SLICE)
    ]


@pytest.mark.parametrize(
    ("slice_key", "reason"),
    [
        ("/teams?year=2024", "does not match"),
        ("/games?year=2024&bogus=1", "bogus"),
        ("/games?year=2024&year=2025", "more than once"),
        ("/games?year=twenty", "year"),
        ("/games?completed=maybe", "completed"),
    ],
)
def test_slice_keys_that_do_not_fit_the_table_are_rejected(slice_key: str, reason: str) -> None:
    rig = Rig(answers={})
    with pytest.raises(FetchEntryError, match=rf"CFBD_VS\.GAMES: .*{reason}"):
        rig.run(args(slice_key))
    assert rig.transport.calls == []


def test_zero_row_response_emits_nothing() -> None:
    rig = Rig(answers={GAMES_SLICE: []})
    assert rig.run(args()) == []


def test_live_reads_the_connection_installs_the_resolver_and_sends_the_bearer_key() -> None:
    rig = Rig(answers={GAMES_SLICE: GAMES_BODY})
    rig.run(args())
    assert rig.exa.connection_reads == [CONNECTION]
    assert rig.resolver_calls == [DOH]
    address, target, headers = rig.transport.calls[0]
    assert (address, target) == (ADDRESS, GAMES_SLICE)
    assert headers["Authorization"] == f"Bearer {KEY}"


def test_null_arguments_fall_back_to_the_property_defaults() -> None:
    rig = Rig(answers={GAMES_SLICE: GAMES_BODY})
    rig.run(args(mode=None, connection=None, replay_path=None, doh=None))
    assert rig.exa.connection_reads == ["CFBD_API"]
    assert rig.resolver_calls == ["https://1.1.1.1/dns-query"]


def test_doh_off_is_passed_to_the_resolver() -> None:
    rig = Rig(answers={GAMES_SLICE: GAMES_BODY})
    rig.run(args(doh="off"))
    assert rig.resolver_calls == ["off"]


def test_an_invalid_doh_url_is_rejected_before_any_request() -> None:
    rig = Rig(answers={GAMES_SLICE: GAMES_BODY})
    with pytest.raises(FetchEntryError, match=r"CFBD_VS\.GAMES: .*DOH_URL"):
        rig.run(args(doh="http://example.com/dns"))
    assert rig.transport.calls == []


def test_catalog_hash_mismatch_is_refused_without_connection_or_request() -> None:
    rig = Rig(answers={GAMES_SLICE: GAMES_BODY})
    with pytest.raises(FetchEntryError) as caught:
        rig.run(args(catalog_hash="b" * 64))
    message = str(caught.value)
    assert message.startswith("CFBD_VS.GAMES: ")
    assert "different catalog versions" in message
    assert "redeploy" in message
    assert rig.transport.calls == []
    assert rig.exa.connection_reads == []


def test_catalog_hash_is_checked_in_replay_mode_too() -> None:
    rig = Rig(files=replay_files(recording()))
    with pytest.raises(FetchEntryError, match="different catalog versions"):
        rig.run(args(mode="replay", catalog_hash="b" * 64))


def test_unknown_table_and_mode_are_errors() -> None:
    rig = Rig(answers={})
    with pytest.raises(FetchEntryError, match=r"CFBD_VS\.NOPE: .*no table"):
        rig.run(args(table="NOPE"))
    with pytest.raises(FetchEntryError, match=r"CFBD_VS\.GAMES: .*FETCH_MODE.*'sideways'"):
        rig.run(args(mode="sideways"))


def test_replay_serves_the_recording_without_connection_resolver_or_network() -> None:
    rig = Rig(files=replay_files(recording()))
    rows = [games_row(r) for r in rig.run(args(mode="replay"))]
    assert [r["ID"] for r in rows] == [1, 2]
    first = rows[0]
    assert first["REQ_SOURCE"] == "REPLAY"
    assert first["REQ_STATUS"] == 200
    assert first["REQ_MS"] == 412
    assert first["REQ_AT"] == datetime(2026, 10, 1, 8, 30, 15, 250000)
    assert first["REQ_ATTEMPTS"] == RECORDED_ATTEMPTS
    assert first["REQ_ROWS"] == 2
    assert rig.exa.connection_reads == []
    assert rig.resolver_calls == []
    assert rig.transport.calls == []


def test_replay_of_a_recording_without_attempts_emits_null_attempts() -> None:
    rig = Rig(files=older_format_files(recording()))
    rows = [games_row(r) for r in rig.run(args(mode="replay"))]
    assert [r["REQ_ATTEMPTS"] for r in rows] == [None, None]
    assert [r["REQ_STATUS"] for r in rows] == [200, 200]


def test_replay_mode_name_is_case_insensitive_and_uses_the_replay_path() -> None:
    files = replay_files(recording())
    rig = Rig(files=files)
    rig.run(args(mode="REPLAY"))
    assert files.reads == [recording_path(REPLAY_PATH, GAMES_SLICE)]


def test_replay_without_a_path_uses_the_default_location() -> None:
    files = MemoryFiles()
    ReplayStore(DEFAULT_REPLAY_PATH, files).save(recording())
    rig = Rig(files=files)
    assert len(rig.run(args(mode="replay", replay_path=None))) == 2


def test_missing_recording_names_slice_and_replay_path() -> None:
    rig = Rig()
    with pytest.raises(FetchEntryError) as caught:
        rig.run(args(mode="replay"))
    message = str(caught.value)
    assert message.startswith("CFBD_VS.GAMES: ")
    assert GAMES_SLICE in message
    assert REPLAY_PATH in message
    assert rig.transport.calls == []


def test_a_recording_of_a_failed_response_is_refused() -> None:
    rig = Rig(files=replay_files(recording(status=500, body='{"message": "boom"}')))
    with pytest.raises(FetchEntryError, match=r"CFBD_VS\.GAMES: .*HTTP status 500"):
        rig.run(args(mode="replay"))


def test_denied_connection_gives_an_access_error_naming_the_connection() -> None:
    denied = RuntimeError("insufficient privileges for operation get_connection")
    rig = Rig(answers={GAMES_SLICE: GAMES_BODY}, connection_error=denied)
    with pytest.raises(FetchEntryError) as caught:
        rig.run(args(connection="MY_CONN"))
    message = str(caught.value)
    assert message.startswith("CFBD_VS.GAMES: ")
    assert "MY_CONN" in message
    assert "has no access to it" in message
    assert "make grants" in message
    assert rig.transport.calls == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "HTTP 401"), (403, "HTTP 403"), (400, "HTTP 400")],
)
def test_http_errors_surface_with_the_table_prefix(status: int, expected: str) -> None:
    body = json.dumps({"message": "nope"}).encode()
    rig = Rig(answers={GAMES_SLICE: TransportResponse(status, {}, body)})
    with pytest.raises(FetchEntryError) as caught:
        rig.run(args())
    message = str(caught.value)
    assert message.startswith(f"CFBD_VS.GAMES: {GAMES_SLICE}: {expected}")
    assert len(rig.transport.calls) == 1


def test_surfaced_errors_never_contain_the_key() -> None:
    body = json.dumps({"message": f"bad header Authorization: Bearer {KEY}"}).encode()
    rig = Rig(answers={GAMES_SLICE: TransportResponse(400, {}, body)})
    with pytest.raises(FetchEntryError) as caught:
        rig.run(args())
    assert KEY not in str(caught.value)
    assert "[REDACTED]" in str(caught.value)


def test_a_key_inside_a_response_value_is_redacted_before_it_is_emitted() -> None:
    body = [{"id": 1, "homeTeam": f"x{KEY}y", "startDate": None}]
    rig = Rig(answers={GAMES_SLICE: body})
    row = games_row(rig.run(args())[0])
    assert KEY not in row["HOME_TEAM"]
    assert "[REDACTED]" in row["HOME_TEAM"]


def test_bearer_words_in_a_response_value_reach_the_row_unchanged() -> None:
    body = [{"id": 1, "homeTeam": "the standard bearer Smith", "startDate": None}]
    rig = Rig(answers={GAMES_SLICE: body})
    assert games_row(rig.run(args())[0])["HOME_TEAM"] == "the standard bearer Smith"


EXOTIC_KEY = "ab+/cé=Zq9"
EXOTIC_FORMS = [
    "ab\\u002b\\/c\\u00e9=Zq9",
    "ab\\u002B\\/c\\u00E9\\u003dZq9",
    "ab%2b%2fc%c3%a9%3dZq9",
    "ab%2B%2Fc%C3%A9%3DZq9",
    "ab\\u002b/c\\u00e9\\u003d\\u005aq9",
]


def exotic_body(form: str) -> str:
    return (
        f'[{{"id": 1, "homeTeam": "x {form} y", "startDate": null, '
        f'"{form}": "as object key"}}, {{"id": 2, "homeTeam": "the standard bearer Smith", '
        '"startDate": null}]'
    )


def decoded_texts(text: str) -> list[str]:
    tree = json.loads(text)
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, dict):
            for name, item in value.items():
                found.append(name)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(tree)
    return found


@pytest.mark.parametrize("form", EXOTIC_FORMS)
def test_exotic_escapes_of_the_key_never_reach_an_emitted_row(form: str) -> None:
    assert EXOTIC_KEY not in form
    body = TransportResponse(200, {}, exotic_body(form).encode())
    rig = Rig(answers={GAMES_SLICE: body}, key=EXOTIC_KEY)
    rows = [games_row(r) for r in rig.run(args())]
    assert "[REDACTED]" in rows[0]["HOME_TEAM"]
    assert EXOTIC_KEY not in repr(rows)
    assert rows[1]["HOME_TEAM"] == "the standard bearer Smith"


@pytest.mark.parametrize("form", EXOTIC_FORMS)
def test_exotic_escapes_of_the_key_never_reach_a_raw_part(form: str) -> None:
    body = TransportResponse(200, {}, exotic_body(form).encode())
    rig = Rig(RAW_NAMES, answers={GAMES_SLICE: body}, key=EXOTIC_KEY)
    rows = raw_rows(rig.run(args(table="@RAW")))
    text = assemble_parts(rows).body
    assert EXOTIC_KEY not in text
    assert all(EXOTIC_KEY not in name for name in decoded_texts(text))
    assert "ab%2b" not in text.lower()
    assert "bearer Smith" in text


def test_a_redacted_raw_recording_replays_into_the_rows_a_live_fetch_emits() -> None:
    form = EXOTIC_FORMS[0]
    body = TransportResponse(200, {}, exotic_body(form).encode())
    live = Rig(RAW_NAMES, answers={GAMES_SLICE: body}, key=EXOTIC_KEY)
    saved = assemble_parts(raw_rows(live.run(args(table="@RAW"))))
    replayed = Rig(files=replay_files(saved), key=EXOTIC_KEY)
    replay_rows = [games_row(r) for r in replayed.run(args(mode="replay"))]
    fresh = Rig(answers={GAMES_SLICE: body}, key=EXOTIC_KEY)
    live_rows = [games_row(r) for r in fresh.run(args())]
    skipped = {"REQ_SOURCE", "REQ_MS", "REQ_AT"}
    assert [{k: v for k, v in r.items() if k not in skipped} for r in replay_rows] == [
        {k: v for k, v in r.items() if k not in skipped} for r in live_rows
    ]


def test_a_body_without_the_key_is_emitted_to_raw_byte_for_byte() -> None:
    text = '[ {"id":1,  "homeTeam":"caf\\u00e9 \\/ x"} ]'
    body = TransportResponse(200, {}, text.encode())
    rig = Rig(RAW_NAMES, answers={GAMES_SLICE: body}, key=EXOTIC_KEY)
    assert assemble_parts(raw_rows(rig.run(args(table="@RAW")))).body == text


def test_flatten_errors_name_the_table_and_the_slice() -> None:
    rig = Rig(answers={GAMES_SLICE: [{"id": "not a number", "homeTeam": "x"}]})
    with pytest.raises(FetchEntryError) as caught:
        rig.run(args())
    message = str(caught.value)
    assert message.startswith("CFBD_VS.GAMES: ")
    assert message.count("CFBD_VS.GAMES") == 1
    assert GAMES_SLICE in message
    assert "not a number" not in message


def test_a_body_that_is_not_json_is_an_error() -> None:
    rig = Rig(answers={GAMES_SLICE: TransportResponse(200, {}, b"<html>oops</html>")})
    with pytest.raises(
        FetchEntryError, match=rf"CFBD_VS\.GAMES: {re.escape(GAMES_SLICE)}: .*not valid JSON"
    ):
        rig.run(args())


def raw_rows(emitted: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    return [dict(zip(RAW_NAMES, row, strict=True)) for row in emitted]


def test_raw_emits_one_part_with_status_attempts_ms_and_naive_time() -> None:
    rig = Rig(RAW_NAMES, answers={GAMES_SLICE: GAMES_BODY}, latency=0.5)
    rows = raw_rows(rig.run(args(table="@RAW")))
    assert len(rows) == 1
    row = rows[0]
    assert row["SLICE_KEY"] == GAMES_SLICE
    assert (row["PART_NO"], row["PART_COUNT"]) == (1, 1)
    assert json.loads(row["BODY_PART"]) == GAMES_BODY
    assert (row["HTTP_STATUS"], row["ATTEMPTS"], row["MS"]) == (200, 1, 500)
    assert row["REQUESTED_AT"] == datetime(2026, 10, 21, 12, 0, 0)
    assert row["REQUESTED_AT"].tzinfo is None


def test_raw_splits_large_bodies_into_parts_that_reassemble() -> None:
    big = "x" * (MAX_PART_CHARS * 2 + 5)
    body = TransportResponse(200, {}, json.dumps([{"id": 1, "pad": big}]).encode())
    rig = Rig(RAW_NAMES, answers={GAMES_SLICE: body})
    rows = raw_rows(rig.run(args(table="@RAW")))
    assert [r["PART_NO"] for r in rows] == [1, 2, 3]
    assert all(r["PART_COUNT"] == 3 for r in rows)
    assert all(len(r["BODY_PART"]) <= MAX_PART_CHARS for r in rows)
    rebuilt = assemble_parts(rows)
    assert json.loads(rebuilt.body)[0]["pad"] == big


def test_raw_output_is_redacted_and_needs_no_catalog_table() -> None:
    body = TransportResponse(200, {}, json.dumps({"echo": KEY}).encode())
    rig = Rig(RAW_NAMES, answers={"/anything?x=1": body})
    rows = raw_rows(rig.run(args("/anything?x=1", table="@RAW")))
    assert KEY not in rows[0]["BODY_PART"]


def test_raw_output_keeps_bearer_words_and_redacts_the_bearer_key() -> None:
    payload = {"text": "standard bearer Smith", "echo": f"Bearer {KEY}"}
    body = TransportResponse(200, {}, json.dumps(payload).encode())
    rig = Rig(RAW_NAMES, answers={"/anything?x=1": body})
    part = raw_rows(rig.run(args("/anything?x=1", table="@RAW")))[0]["BODY_PART"]
    assert json.loads(part) == {"text": "standard bearer Smith", "echo": "Bearer [REDACTED]"}


def test_raw_from_a_recording_round_trips_into_identical_rows() -> None:
    live = Rig(RAW_NAMES, answers={GAMES_SLICE: GAMES_BODY})
    saved = assemble_parts(raw_rows(live.run(args(table="@RAW"))))
    files = replay_files(saved)
    replayed = Rig(files=files)
    replay_rows = [games_row(r) for r in replayed.run(args(mode="replay"))]
    fresh = Rig(answers={GAMES_SLICE: GAMES_BODY}, latency=0.75)
    live_rows = [games_row(r) for r in fresh.run(args())]
    source_and_timing = {"REQ_SOURCE", "REQ_MS", "REQ_AT"}
    strip = [{k: v for k, v in r.items() if k not in source_and_timing} for r in replay_rows]
    assert strip == [{k: v for k, v in r.items() if k not in source_and_timing} for r in live_rows]
    assert {r["REQ_ATTEMPTS"] for r in replay_rows} == {1}


def test_raw_in_replay_mode_re_emits_the_recording() -> None:
    rig = Rig(RAW_NAMES, files=replay_files(recording()))
    rows = raw_rows(rig.run(args(table="@RAW", mode="replay")))
    assert rows[0]["MS"] == 412
    assert rows[0]["ATTEMPTS"] == RECORDED_ATTEMPTS
    assert rows[0]["REQUESTED_AT"] == datetime(2026, 10, 1, 8, 30, 15, 250000)


SLICES = [f"/games?seasonType=both&year={year}" for year in (2019, 2020, 2021)]


def slice_answers() -> dict[str, Any]:
    return {key: [{"id": number, "homeTeam": key}] for number, key in enumerate(SLICES, start=1)}


@pytest.mark.parametrize("width", [1, 2, 8])
def test_every_input_row_of_the_group_is_processed_in_input_order(width: int) -> None:
    rig = Rig(answers=slice_answers(), executor=fetch.executor_for(width))
    emitted = rig.run(*(args(key) for key in SLICES))
    assert [games_row(r)["REQ_SLICE"] for r in emitted] == SLICES
    assert [games_row(r)["ID"] for r in emitted] == [1, 2, 3]


def test_output_order_ignores_completion_order() -> None:
    answers = slice_answers()
    first_done = threading.Event()

    def slow_first(
        address: str, target: str, headers: Mapping[str, str], timeout: float
    ) -> TransportResponse:
        if target == SLICES[0]:
            first_done.wait(timeout=0.3)
        else:
            first_done.set()
        return TransportResponse(200, {}, json.dumps(answers[target]).encode())

    rig = Rig(answers=answers, executor=fetch.executor_for(3))
    rig.entry = FetchEntry(
        rig.exa,
        make_catalog(),
        transport=slow_first,
        files=rig.files,
        install_resolver=rig.resolver_calls.append,
        executor=fetch.executor_for(3),
    )
    emitted = rig.run(*(args(key) for key in SLICES))
    assert [games_row(r)["REQ_SLICE"] for r in emitted] == SLICES


def test_width_bounds_the_number_of_concurrent_requests() -> None:
    answers = slice_answers()
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}

    def counting(
        address: str, target: str, headers: Mapping[str, str], timeout: float
    ) -> TransportResponse:
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return TransportResponse(200, {}, json.dumps(answers[target]).encode())

    exa = FakeExa(GAMES_NAMES)
    entry = FetchEntry(
        exa,
        make_catalog(),
        transport=counting,
        install_resolver=lambda _doh: None,
        executor=fetch.executor_for(2),
    )
    entry.run(FakeCtx([args(key) for key in SLICES]))
    assert state["peak"] == 2


def test_width_one_is_strictly_serial_and_stops_after_the_first_failure() -> None:
    answers = slice_answers()
    answers[SLICES[0]] = TransportResponse(400, {}, b'{"message":"bad"}')
    rig = Rig(answers=answers, executor=fetch.executor_for(1))
    with pytest.raises(FetchEntryError):
        rig.run(*(args(key) for key in SLICES))
    assert [call[1] for call in rig.transport.calls] == [SLICES[0]]


def test_the_connection_is_read_once_per_group_and_dispatch_uses_the_executor() -> None:
    executor = RecordingExecutor()
    rig = Rig(answers=slice_answers(), executor=executor)
    rig.run(*(args(key) for key in SLICES))
    assert rig.exa.connection_reads == [CONNECTION]
    assert executor.item_counts == [3]


def test_a_group_with_a_bad_row_sends_no_request_at_all() -> None:
    rig = Rig(answers=slice_answers())
    with pytest.raises(FetchEntryError, match="different catalog versions"):
        rig.run(args(SLICES[0]), args(SLICES[1], catalog_hash="b" * 64))
    assert rig.transport.calls == []


def test_executor_for_picks_serial_or_threaded_by_width() -> None:
    assert list(fetch.executor_for(1).map(lambda n: n * 2, [1, 2, 3])) == [2, 4, 6]
    assert list(fetch.executor_for(4).map(lambda n: n * 2, [1, 2, 3])) == [2, 4, 6]
    assert list(fetch.executor_for(4).map(lambda n: n, [])) == []
    with pytest.raises(ValueError, match="width"):
        fetch.executor_for(0)


def test_module_run_uses_the_embedded_catalog_and_the_local_file_system(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fetch, "CATALOG_JSON", make_catalog().to_json(indent=None))
    monkeypatch.setattr(fetch, "_embedded_catalog", None)
    ReplayStore(str(tmp_path)).save(recording())
    ctx = FakeCtx([args(mode="replay", replay_path=str(tmp_path))])
    fetch.run(ctx, FakeExa(["ID", "REQ_SOURCE"]))
    assert ctx.emitted == [(1, "REPLAY"), (2, "REPLAY")]


def test_module_run_without_an_embedded_catalog_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch, "CATALOG_JSON", None)
    monkeypatch.setattr(fetch, "_embedded_catalog", None)
    with pytest.raises(FetchEntryError, match="no embedded catalog"):
        fetch.run(FakeCtx([args()]), FakeExa(GAMES_NAMES))


def peak_concurrency(slice_count: int, parallel: int | None) -> int:
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}
    keys = [f"/games?seasonType=both&year={2000 + n}" for n in range(slice_count)]

    def counting(
        address: str, target: str, headers: Mapping[str, str], timeout: float
    ) -> TransportResponse:
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return TransportResponse(200, {}, b"[]")

    entry = FetchEntry(
        FakeExa(GAMES_NAMES),
        make_catalog(),
        transport=counting,
        install_resolver=lambda _doh: None,
    )
    entry.run(FakeCtx([args(key, parallel=parallel) for key in keys]))
    return state["peak"]


def test_pool_width_comes_from_the_max_parallel_fetches_argument() -> None:
    assert peak_concurrency(5, 2) == 2
    assert peak_concurrency(3, 1) == 1


def test_a_null_width_means_six_and_the_pool_never_exceeds_the_slice_count() -> None:
    assert peak_concurrency(8, None) == 6
    assert peak_concurrency(2, 16) == 2


@pytest.mark.parametrize("width", [0, 17, -1, 2.5])
def test_an_out_of_range_width_is_rejected_before_any_request(width: float) -> None:
    rig = Rig(answers={GAMES_SLICE: GAMES_BODY})
    with pytest.raises(FetchEntryError, match=r"CFBD_VS\.GAMES: MAX_PARALLEL_FETCHES must be"):
        rig.run(args(parallel=width))  # type: ignore[arg-type]
    assert rig.transport.calls == []


def test_one_failing_slice_fails_the_group_and_cancels_queued_slices() -> None:
    answers = slice_answers()
    answers[SLICES[0]] = TransportResponse(400, {}, b'{"message":"bad"}')
    rig = Rig(answers=answers)
    ctx = FakeCtx([args(key, parallel=1) for key in SLICES])
    with pytest.raises(FetchEntryError, match="HTTP 400"):
        rig.entry.run(ctx)
    assert ctx.emitted == []
    assert [call[1] for call in rig.transport.calls] == [SLICES[0]]


FAN_OUT = [f"/games?seasonType=both&year={year}" for year in range(2019, 2025)]
SIGNAL_WAIT_SECONDS = 5.0


class StopSignalSpy:
    """Captures the group's stop signal from `CfbdClient.get`, so a transport can wait on it.

    A correct fetch sets the signal at the first failure; a broken one never does, and the
    bounded wait then returns False instead of hanging the test.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._captured = threading.Event()
        self._signal: threading.Event | None = None
        original = CfbdClient.get

        def spying_get(
            client: CfbdClient, slice_key: str, *, cancel: threading.Event | None = None
        ) -> FetchResult:
            if cancel is not None:
                self._signal = cancel
                self._captured.set()
            return original(client, slice_key, cancel=cancel)

        monkeypatch.setattr(CfbdClient, "get", spying_get)

    def wait_for_stop(self) -> bool:
        if not self._captured.wait(SIGNAL_WAIT_SECONDS):
            return False
        assert self._signal is not None
        return self._signal.wait(SIGNAL_WAIT_SECONDS)


class GatedTransport:
    """Answers `FAN_OUT` slices: `failing` ones with HTTP 400, `gated` ones only after the
    group's stop signal is set (then with `gated_status`), all others at once with rows.

    When there are failing slices, every gated and failing request first meets the others at a
    barrier, so the gated ones are on the wire before any failure comes back.
    """

    def __init__(
        self,
        spy: StopSignalSpy,
        *,
        failing: Iterable[str] = (),
        gated: Iterable[str] = (),
        gated_status: int = 200,
    ) -> None:
        self._spy = spy
        self._failing = set(failing)
        self._gated = set(gated)
        self._gated_status = gated_status
        parties = len(self._failing) + len(self._gated)
        self._barrier = (
            threading.Barrier(parties, timeout=SIGNAL_WAIT_SECONDS) if self._failing else None
        )
        self._lock = threading.Lock()
        self.calls: list[str] = []
        self.waits: list[bool] = []

    def __call__(
        self, address: str, target: str, headers: Mapping[str, str], timeout: float
    ) -> TransportResponse:
        with self._lock:
            self.calls.append(target)
        if self._barrier is not None and target in self._failing | self._gated:
            self._barrier.wait()
        if target in self._failing:
            return TransportResponse(400, {}, json.dumps({"message": f"bad {target}"}).encode())
        if target in self._gated:
            stopped = self._spy.wait_for_stop()
            with self._lock:
                self.waits.append(stopped)
            if self._gated_status not in range(200, 300):
                return TransportResponse(self._gated_status, {}, b'{"message":"busy"}')
        body = [{"id": FAN_OUT.index(target) + 1, "homeTeam": target}]
        return TransportResponse(200, {}, json.dumps(body).encode())


class RefusingCtx(FakeCtx):
    def emit(self, *values: Any) -> None:
        raise RuntimeError("emit refused")


def fan_out_entry(transport: GatedTransport, width: int) -> FetchEntry:
    return FetchEntry(
        FakeExa(GAMES_NAMES),
        make_catalog(),
        transport=transport,
        clock=FakeClock(),
        uniform=lambda low, high: 0.0,
        install_resolver=lambda _doh: None,
        executor=fetch.executor_for(width),
    )


def test_a_failure_at_width_two_stops_every_later_slice_from_being_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = StopSignalSpy(monkeypatch)
    transport = GatedTransport(spy, gated=[FAN_OUT[0]], failing=[FAN_OUT[1]])
    with pytest.raises(FetchEntryError, match=re.escape(f"{FAN_OUT[1]}: HTTP 400")):
        fan_out_entry(transport, 2).run(FakeCtx([args(key) for key in FAN_OUT]))
    assert sorted(transport.calls) == sorted(FAN_OUT[:2])
    assert transport.waits == [True]


def test_an_in_flight_slice_does_not_retry_after_a_sibling_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = StopSignalSpy(monkeypatch)
    transport = GatedTransport(spy, gated=[FAN_OUT[0]], failing=[FAN_OUT[1]], gated_status=503)
    with pytest.raises(FetchEntryError) as caught:
        fan_out_entry(transport, 2).run(FakeCtx([args(key) for key in FAN_OUT]))
    assert transport.calls.count(FAN_OUT[0]) == 1
    assert sorted(transport.calls) == sorted(FAN_OUT[:2])
    assert transport.waits == [True]
    message = str(caught.value)
    assert message.startswith("CFBD_VS.GAMES: ")
    assert f"{FAN_OUT[1]}: HTTP 400" in message


def test_the_first_failure_in_time_is_raised_even_when_an_earlier_slice_fails_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = StopSignalSpy(monkeypatch)
    transport = GatedTransport(spy, gated=[FAN_OUT[0]], failing=[FAN_OUT[1]], gated_status=404)
    with pytest.raises(FetchEntryError) as caught:
        fan_out_entry(transport, 2).run(FakeCtx([args(key) for key in FAN_OUT]))
    assert f"{FAN_OUT[1]}: HTTP 400" in str(caught.value)
    assert FAN_OUT[0] not in str(caught.value)
    assert sorted(transport.calls) == sorted(FAN_OUT[:2])


def test_a_failing_emit_stops_the_pool_before_it_waits_for_running_slices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = StopSignalSpy(monkeypatch)
    transport = GatedTransport(spy, gated=FAN_OUT[1:3])
    with pytest.raises(RuntimeError, match="emit refused"):
        fan_out_entry(transport, 2).run(RefusingCtx([args(key) for key in FAN_OUT]))
    assert FAN_OUT[0] in transport.calls
    assert set(transport.calls) <= set(FAN_OUT[:3])
    assert all(transport.waits)


def test_a_failure_on_the_serial_path_is_raised_unchanged() -> None:
    answers = slice_answers()
    answers[SLICES[1]] = TransportResponse(404, {}, b'{"message":"gone"}')
    rig = Rig(answers=answers, executor=fetch.executor_for(1))
    with pytest.raises(FetchEntryError, match=re.escape(f"{SLICES[1]}: HTTP 404: gone")):
        rig.run(*(args(key) for key in SLICES))
    assert [call[1] for call in rig.transport.calls] == SLICES[:2]


class ChainedRefusingCtx(FakeCtx):
    def emit(self, *values: Any) -> None:
        raise RuntimeError("emit refused") from ValueError("value too long")


def test_a_first_failure_propagates_with_its_own_cause() -> None:
    rig = Rig(answers=slice_answers(), executor=fetch.executor_for(2))
    ctx = ChainedRefusingCtx([args(key) for key in SLICES])
    with pytest.raises(RuntimeError, match="emit refused") as caught:
        rig.entry.run(ctx)
    assert isinstance(caught.value.__cause__, ValueError)
