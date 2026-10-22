import gzip
import http.server as http_server
import json
import re
import socket
import ssl
import threading
import urllib.parse
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest

from vs.cfbd_vs import http
from vs.cfbd_vs.http import (
    AccessError,
    CfbdClient,
    ClientError,
    FetchError,
    NameResolutionError,
    RequestCancelled,
    RetriesExhausted,
    TlsError,
    TransportResponse,
)

ADDRESS = "https://api.example.test"
KEY = "KEY-0123456789abcdefMARKER"
SLICE = "/games?seasonType=both&week=8&year=2019"
START = datetime(2026, 10, 21, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.sleeps: list[float] = []
        self.on_sleep: Callable[[], None] = lambda: None

    def monotonic(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.elapsed += seconds
        self.on_sleep()

    def utcnow(self) -> datetime:
        return START + timedelta(seconds=self.elapsed)


Step = TransportResponse | Exception


class ScriptedTransport:
    """Plays back one step per call and records what it was asked."""

    def __init__(self, *steps: Step, clock: FakeClock | None = None, latency: float = 0.0) -> None:
        self.steps = list(steps)
        self.calls: list[tuple[str, str, dict[str, str], float]] = []
        self.clock = clock
        self.latency = latency
        self.on_call: Callable[[], None] = lambda: None

    def __call__(
        self, address: str, target: str, headers: Mapping[str, str], timeout: float
    ) -> TransportResponse:
        self.calls.append((address, target, dict(headers), timeout))
        self.on_call()
        if self.clock is not None:
            self.clock.elapsed += self.latency
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, Exception):
            raise step
        return step


def reply(
    status: int = 200, body: Any = None, headers: Mapping[str, str] | None = None
) -> TransportResponse:
    raw = body if isinstance(body, bytes) else json.dumps([] if body is None else body).encode()
    return TransportResponse(status=status, headers=dict(headers or {}), body=raw)


def make_client(
    *steps: Step, latency: float = 0.0, key: str = KEY, address: str = ADDRESS
) -> tuple[CfbdClient, ScriptedTransport, FakeClock]:
    clock = FakeClock()
    transport = ScriptedTransport(*steps, clock=clock, latency=latency)
    client = CfbdClient(
        address, key, transport=transport, clock=clock, uniform=lambda low, high: 0.0
    )
    return client, transport, clock


def test_sends_one_authenticated_get_to_the_connection_address() -> None:
    client, transport, _ = make_client(reply(body=[{"id": 1}]))
    client.get(SLICE)
    assert len(transport.calls) == 1
    address, target, headers, timeout = transport.calls[0]
    assert (address, target) == (ADDRESS, SLICE)
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert headers["Accept"] == "application/json"
    assert timeout == 30


def test_success_reports_status_body_attempts_time_and_request_start() -> None:
    client, _, _ = make_client(reply(body=[{"id": 1}]), latency=0.25)
    result = client.get(SLICE)
    assert result.slice_key == SLICE
    assert result.status == 200
    assert json.loads(result.body) == [{"id": 1}]
    assert result.attempts == 1
    assert result.ms == 250
    assert result.requested_at == START


def test_429_with_retry_after_zero_is_retried_once() -> None:
    client, transport, clock = make_client(reply(429, headers={"retry-after": "0"}), reply())
    result = client.get(SLICE)
    assert (result.status, result.attempts) == (200, 2)
    assert clock.sleeps == [0.0]
    assert len(transport.calls) == 2


def test_two_503s_then_success_takes_three_attempts_with_growing_waits() -> None:
    client, _, clock = make_client(reply(503), reply(503), reply())
    assert client.get(SLICE).attempts == 3
    assert clock.sleeps == [1.0, 2.0]


def test_persistent_500_fails_after_exactly_four_attempts() -> None:
    client, transport, clock = make_client(reply(500, body={"message": "boom"}))
    with pytest.raises(RetriesExhausted) as error:
        client.get(SLICE)
    assert len(transport.calls) == 4
    assert clock.sleeps == [1.0, 2.0, 4.0]
    assert error.value.status == 500
    assert error.value.attempts == 4
    assert error.value.slice_key == SLICE
    message = str(error.value)
    assert SLICE in message
    assert "500" in message
    assert "4 attempts" in message
    assert "boom" in message


def test_jitter_scales_each_wait_within_twenty_percent() -> None:
    clock = FakeClock()
    transport = ScriptedTransport(reply(503), reply())
    bounds: list[tuple[float, float]] = []

    def uniform(low: float, high: float) -> float:
        bounds.append((low, high))
        return high

    CfbdClient(ADDRESS, KEY, transport=transport, clock=clock, uniform=uniform).get(SLICE)
    assert bounds == [(-0.2, 0.2)]
    assert clock.sleeps == [pytest.approx(1.2)]


def test_retry_after_seconds_replace_the_backoff() -> None:
    client, _, clock = make_client(reply(429, headers={"Retry-After": "7"}), reply())
    client.get(SLICE)
    assert clock.sleeps == [7.0]


def test_retry_after_http_date_is_honored() -> None:
    date = "Wed, 21 Oct 2026 12:00:05 GMT"
    client, _, clock = make_client(reply(429, headers={"retry-after": date}), reply())
    client.get(SLICE)
    assert clock.sleeps == [5.0]


def test_retry_after_of_exactly_thirty_seconds_is_honored() -> None:
    client, _, clock = make_client(reply(429, headers={"retry-after": "30"}), reply())
    client.get(SLICE)
    assert clock.sleeps == [30.0]


def test_retry_after_beyond_thirty_seconds_fails_at_once_without_sleeping() -> None:
    client, transport, clock = make_client(reply(429, headers={"retry-after": "120"}))
    with pytest.raises(RetriesExhausted) as error:
        client.get(SLICE)
    assert len(transport.calls) == 1
    assert clock.sleeps == []
    assert "Retry-After 120" in str(error.value)
    assert "30" in str(error.value)


def test_garbage_retry_after_falls_back_to_the_backoff() -> None:
    client, _, clock = make_client(reply(429, headers={"retry-after": "soon"}), reply())
    client.get(SLICE)
    assert clock.sleeps == [1.0]


@pytest.mark.parametrize(
    "failure",
    [TimeoutError("timed out"), ConnectionResetError("reset"), ConnectionRefusedError("refused")],
)
def test_timeouts_and_connection_errors_are_retried(failure: Exception) -> None:
    client, _, _ = make_client(failure, reply())
    assert client.get(SLICE).attempts == 2


def test_exhausted_connection_errors_name_the_error_type() -> None:
    client, transport, _ = make_client(ConnectionResetError("reset by peer"))
    with pytest.raises(RetriesExhausted) as error:
        client.get(SLICE)
    assert len(transport.calls) == 4
    assert error.value.status is None
    assert "ConnectionResetError" in str(error.value)
    assert "4 attempts" in str(error.value)


def test_ms_covers_every_attempt_and_the_waits_between_them() -> None:
    client, _, _ = make_client(reply(503), reply(), latency=0.5)
    assert client.get(SLICE).ms == 2000


def test_request_start_is_the_time_of_the_first_attempt() -> None:
    client, _, _ = make_client(reply(503), reply(), latency=0.5)
    assert client.get(SLICE).requested_at == START


def test_a_set_cancel_signal_sends_no_request() -> None:
    client, transport, _ = make_client(reply())
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RequestCancelled, match=re.escape(SLICE)):
        client.get(SLICE, cancel=cancel)
    assert transport.calls == []


def test_a_cancel_during_an_attempt_stops_the_retry_before_its_wait() -> None:
    cancel = threading.Event()
    client, transport, clock = make_client(reply(503))
    transport.on_call = cancel.set
    with pytest.raises(RequestCancelled):
        client.get(SLICE, cancel=cancel)
    assert len(transport.calls) == 1
    assert clock.sleeps == []


def test_a_cancel_during_the_backoff_stops_the_next_attempt() -> None:
    cancel = threading.Event()
    client, transport, clock = make_client(reply(503))
    clock.on_sleep = cancel.set
    with pytest.raises(RequestCancelled):
        client.get(SLICE, cancel=cancel)
    assert len(transport.calls) == 1
    assert clock.sleeps == [1.0]


def test_an_unset_cancel_signal_changes_nothing() -> None:
    client, transport, _ = make_client(reply(503), reply(503), reply())
    assert client.get(SLICE, cancel=threading.Event()).attempts == 3
    assert len(transport.calls) == 3


def test_a_final_failure_wins_over_a_cancel_set_during_the_last_attempt() -> None:
    cancel = threading.Event()
    client, transport, _ = make_client(reply(400, body={"message": "bad"}))
    transport.on_call = cancel.set
    with pytest.raises(ClientError, match="HTTP 400"):
        client.get(SLICE, cancel=cancel)


def test_cancellation_is_not_a_fetch_error() -> None:
    assert not issubclass(RequestCancelled, FetchError)


def test_client_errors_are_raised_at_once_with_the_api_message() -> None:
    client, transport, _ = make_client(reply(400, body={"message": "year is required"}))
    with pytest.raises(ClientError) as error:
        client.get(SLICE)
    assert len(transport.calls) == 1
    assert error.value.status == 400
    assert error.value.attempts == 1
    message = str(error.value)
    assert SLICE in message
    assert "400" in message
    assert "year is required" in message


def test_plain_text_error_bodies_are_used_and_truncated() -> None:
    client, _, _ = make_client(reply(404, body=b"x" * 1000))
    with pytest.raises(ClientError) as error:
        client.get(SLICE)
    assert "x" * 300 in str(error.value)
    assert "x" * 400 not in str(error.value)


def test_404_is_not_retried() -> None:
    client, transport, _ = make_client(reply(404, body=b"not found"))
    with pytest.raises(ClientError):
        client.get(SLICE)
    assert len(transport.calls) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_authorization_failures_are_access_errors(status: int) -> None:
    client, transport, _ = make_client(reply(status, body={"message": "nope"}))
    with pytest.raises(AccessError) as error:
        client.get(SLICE)
    assert len(transport.calls) == 1
    assert error.value.status == status
    assert str(status) in str(error.value)
    assert "key" in str(error.value).lower()
    assert "tier" in str(error.value).lower()
    assert "the key has no access to" in str(error.value)
    assert SLICE.partition("?")[0] in str(error.value)


def test_unexpected_redirects_are_not_followed_or_retried() -> None:
    client, transport, _ = make_client(reply(301, headers={"location": "https://evil.test/"}))
    with pytest.raises(ClientError) as error:
        client.get(SLICE)
    assert len(transport.calls) == 1
    assert "301" in str(error.value)


def test_name_resolution_errors_are_not_retried_and_point_to_dns_check() -> None:
    failure = socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    client, transport, _ = make_client(failure)
    with pytest.raises(NameResolutionError) as error:
        client.get(SLICE)
    assert len(transport.calls) == 1
    message = str(error.value)
    assert "api.example.test" in message
    assert "Name or service not known" in message
    assert "make dns-check" in message
    assert SLICE in message


def test_resolver_messages_are_passed_through_without_a_duplicate_hint() -> None:
    class Described(socket.gaierror):
        def __str__(self) -> str:
            return (
                "cannot resolve host 'api.example.test' via https://1.1.1.1/dns-query: x. "
                "Run `make dns-check`."
            )

    client, _, _ = make_client(Described(socket.EAI_NONAME, "x"))
    with pytest.raises(NameResolutionError) as error:
        client.get(SLICE)
    assert str(error.value).count("make dns-check") == 1
    assert "https://1.1.1.1/dns-query" in str(error.value)


def test_certificate_failures_are_not_retried() -> None:
    client, transport, _ = make_client(ssl.SSLCertVerificationError("certificate verify failed"))
    with pytest.raises(TlsError) as error:
        client.get(SLICE)
    assert len(transport.calls) == 1
    assert "certificate verify failed" in str(error.value)


def test_errors_are_fetch_errors() -> None:
    for kind in (AccessError, ClientError, RetriesExhausted, NameResolutionError, TlsError):
        assert issubclass(kind, FetchError)


def test_error_message_redacts_the_key_and_bearer_tokens_from_an_echoing_body() -> None:
    echo = f'{{"message": "bad header Authorization: Bearer {KEY}", "key": "{KEY}"}}'
    client, _, _ = make_client(reply(500, body=echo.encode()))
    with pytest.raises(RetriesExhausted) as error:
        client.get(SLICE)
    message = str(error.value)
    assert KEY not in message
    assert "[REDACTED]" in message


def test_other_bearer_tokens_are_redacted_too() -> None:
    client, _, _ = make_client(reply(400, body=b"Authorization: Bearer someOtherToken123"))
    with pytest.raises(ClientError) as error:
        client.get(SLICE)
    assert "someOtherToken123" not in str(error.value)
    assert "Bearer [REDACTED]" in str(error.value)


def test_success_body_is_redacted_before_it_is_returned() -> None:
    body = {"name": f"x{KEY}y", "n": 1}
    client, _, _ = make_client(reply(body=body))
    result = client.get(SLICE)
    assert KEY not in result.body
    assert json.loads(result.body) == {"name": "x[REDACTED]y", "n": 1}


def test_bearer_words_in_a_success_body_are_returned_unchanged() -> None:
    body = {"playText": "the standard bearer Smith ran 5 yds", "note": "BEARER of bad news"}
    client, _, _ = make_client(reply(body=body))
    assert json.loads(client.get(SLICE).body) == body


def test_a_bearer_key_in_a_success_body_is_still_redacted() -> None:
    body = {"echo": f"Authorization: Bearer {KEY}", "plain": KEY}
    client, _, _ = make_client(reply(body=body))
    result = client.get(SLICE)
    assert KEY not in result.body
    assert json.loads(result.body) == {
        "echo": "Authorization: Bearer [REDACTED]",
        "plain": "[REDACTED]",
    }


@pytest.mark.parametrize(
    "key",
    ['we"ird\\key/é+1', "plain key&with=chars", "ünï-" + "k" * 12],
    ids=["json", "url", "uni"],
)
def test_escaped_forms_of_the_key_are_redacted_from_success_bodies(key: str) -> None:
    forms = [
        json.dumps(key)[1:-1],
        json.dumps(key, ensure_ascii=False)[1:-1],
        urllib.parse.quote(key, safe=""),
        key,
    ]
    client, _, _ = make_client(reply(body=" | ".join(forms).encode()), key=key)
    body = client.get(SLICE).body
    for form in forms:
        assert form not in body


def test_redact_key_replaces_only_the_forms_of_the_key() -> None:
    text = f"standard bearer Smith; Bearer {KEY}; {urllib.parse.quote(KEY, safe='')}"
    assert http.redact_key(text, KEY) == "standard bearer Smith; Bearer [REDACTED]; [REDACTED]"
    assert http.redact_key("standard bearer Smith", "") == "standard bearer Smith"


def test_percent_encoded_forms_match_in_either_hex_case() -> None:
    key = "ab+/c=Z"
    text = "ab%2b%2fc%3dZ | ab%2B%2Fc%3DZ | ab+%2fc%3DZ | AB%2B%2FC%3DZ"
    assert http.redact_key(text, key) == "[REDACTED] | [REDACTED] | ab+%2fc%3DZ | AB%2B%2FC%3DZ"


def test_redact_tree_replaces_the_key_in_every_string_value_and_object_key() -> None:
    key = "ab+/c"
    tree = {"ab+/c": [1, None, True, "x ab+/c y", {"deep ab+/c": ["ab+/c"]}], "n": 1.5}
    assert http.redact_tree(tree, key) == {
        "[REDACTED]": [1, None, True, "x [REDACTED] y", {"deep [REDACTED]": ["[REDACTED]"]}],
        "n": 1.5,
    }


def test_redact_tree_leaves_bearer_words_and_empty_keys_alone() -> None:
    tree = {"text": "standard bearer Smith"}
    assert http.redact_tree(tree, "k") == tree
    assert http.redact_tree({"k": "k"}, "") == {"k": "k"}


def test_redact_body_catches_escapes_that_only_json_parsing_reveals() -> None:
    key = "ab+/cé=Z"
    text = r'{"a": "x ab\u002b\/c\u00e9=Z y", "ab\u002b\/c\u00e9=Z": 1}'
    redacted = http.redact_body(text, key)
    assert json.loads(redacted) == {"a": "x [REDACTED] y", "[REDACTED]": 1}


def test_redact_body_returns_a_body_without_the_key_unchanged() -> None:
    text = '[ {"a":  "standard bearer Smith \\/ caf\\u00e9"} ]'
    assert http.redact_body(text, "ab+/c") == text
    assert http.redact_body("not json ab+/c", "ab+/c") == "not json [REDACTED]"


def test_redact_for_error_text_also_replaces_any_bearer_token() -> None:
    text = f"Authorization: Bearer abc.def-123 and {KEY}"
    assert http.redact(text, KEY) == "Authorization: Bearer [REDACTED] and [REDACTED]"


def test_transport_error_messages_are_redacted() -> None:
    client, _, _ = make_client(ConnectionResetError(f"reset while sending {KEY}"))
    with pytest.raises(RetriesExhausted) as error:
        client.get(SLICE)
    assert KEY not in str(error.value)


@pytest.mark.parametrize(
    "key",
    ['we"ird\\key/é+1', "plain key&with=chars", "ünï-" + "k" * 12],
    ids=["json", "url", "uni"],
)
def test_escaped_forms_of_the_key_are_redacted(key: str) -> None:
    forms = [
        json.dumps(key)[1:-1],
        json.dumps(key, ensure_ascii=False)[1:-1],
        urllib.parse.quote(key, safe=""),
        key,
    ]
    body = " | ".join(forms).encode()
    client, _, _ = make_client(reply(400, body=body), key=key)
    with pytest.raises(ClientError) as error:
        client.get(SLICE)
    message = str(error.value)
    for form in forms:
        assert form not in message


def test_redact_leaves_text_alone_for_an_empty_key() -> None:
    assert http.redact("plain text", "") == "plain text"


def test_repr_does_not_contain_the_key() -> None:
    client, _, _ = make_client(reply())
    assert KEY not in repr(client)
    assert KEY not in str(client)


@pytest.mark.parametrize(
    "bad",
    ["games", "/games with space", "/games\r\nHost: evil", "/games\x00", "http://evil/x", ""],
)
def test_malformed_slice_keys_are_rejected_before_any_request(bad: str) -> None:
    client, transport, _ = make_client(reply())
    with pytest.raises(ValueError, match="slice key"):
        client.get(bad)
    assert transport.calls == []


def test_the_key_only_ever_goes_to_the_connection_address() -> None:
    client, transport, _ = make_client(reply())
    client.get("/teams?team=http%3A%2F%2Fother-host%2F")
    assert {call[0] for call in transport.calls} == {ADDRESS}
    assert transport.calls[0][1].startswith("/teams?")


@pytest.mark.parametrize("address", ["", "api.example.test", "ftp://x/", "https://"])
def test_invalid_addresses_are_rejected(address: str) -> None:
    with pytest.raises(ValueError, match="address"):
        CfbdClient(address, KEY)


def test_a_key_is_required() -> None:
    with pytest.raises(ValueError, match="key"):
        CfbdClient(ADDRESS, "")


def test_system_clock_provides_the_clock_interface() -> None:
    clock = http.SystemClock()
    assert clock.monotonic() <= clock.monotonic()
    assert clock.utcnow().tzinfo is UTC
    clock.sleep(0)


class Recorder(http_server.BaseHTTPRequestHandler):
    seen: ClassVar[list[dict[str, str]]] = []
    reply_body = b"[]"
    reply_status = 200
    gzipped = False

    def do_GET(self) -> None:
        type(self).seen.append(
            {"path": self.path, **{k.lower(): v for k, v in self.headers.items()}}
        )
        body = gzip.compress(self.reply_body) if self.gzipped else self.reply_body
        self.send_response(self.reply_status)
        if self.gzipped:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def server() -> Iterator[Callable[..., str]]:
    Recorder.seen = []
    Recorder.reply_body = b"[]"
    Recorder.reply_status = 200
    Recorder.gzipped = False
    httpd = http_server.ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def address(prefix: str = "") -> str:
        return f"http://127.0.0.1:{httpd.server_address[1]}{prefix}"

    yield address
    httpd.shutdown()
    httpd.server_close()


def test_default_transport_sends_the_request_to_the_address_by_ip_literal(
    server: Callable[..., str],
) -> None:
    Recorder.reply_body = b'[{"id": 1}]'
    result = CfbdClient(server(), KEY).get("/games?year=2019")
    assert json.loads(result.body) == [{"id": 1}]
    seen = Recorder.seen[0]
    assert seen["path"] == "/games?year=2019"
    assert seen["authorization"] == f"Bearer {KEY}"
    assert seen["accept"] == "application/json"
    assert seen["host"].startswith("127.0.0.1:")


def test_default_transport_keeps_a_base_path_of_the_address(server: Callable[..., str]) -> None:
    CfbdClient(server("/v1/"), KEY).get("/games")
    assert Recorder.seen[0]["path"] == "/v1/games"


def test_default_transport_decodes_gzip_bodies(server: Callable[..., str]) -> None:
    Recorder.reply_body = b'{"big": "payload"}'
    Recorder.gzipped = True
    result = CfbdClient(server(), KEY).get("/info")
    assert json.loads(result.body) == {"big": "payload"}
    assert "gzip" in Recorder.seen[0]["accept-encoding"]


def test_default_transport_returns_error_statuses_without_raising(
    server: Callable[..., str],
) -> None:
    Recorder.reply_status = 400
    Recorder.reply_body = b'{"message": "bad"}'
    with pytest.raises(ClientError, match="bad"):
        CfbdClient(server(), KEY).get("/games")
