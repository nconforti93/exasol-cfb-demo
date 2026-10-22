import http.client
import json
import socket
import ssl
import time
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.mock_cfbd import server as mock
from tests.mock_cfbd.certs import generate_self_signed
from vs.cfbd_vs.resolver import ResolutionError, Resolver, https_get

MARKER_KEY = "marker-key-123"
BIG_BODY = 2 * 1024 * 1024
HOST = "api.collegefootballdata.com"


class StubProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, list[str]]]] = []

    def __call__(self, path: str, query: dict[str, list[str]]) -> Any:
        self.calls.append((path, query))
        if path == "/games":
            return [{"id": 1, "season": int(query["year"][0])}] if "year" in query else None
        if path == "/raw":
            return b'{"raw": true}'
        if path == "/teapot":
            return 418, {"short": "stout"}
        if path == "/missing":
            raise KeyError(path)
        if path == "/broken":
            raise RuntimeError("boom")
        return None


@pytest.fixture
def provider() -> StubProvider:
    return StubProvider()


@pytest.fixture
def certs(tmp_path: Path) -> tuple[Path, Path]:
    return generate_self_signed(tmp_path)


@pytest.fixture
def running(provider: StubProvider, certs: tuple[Path, Path]) -> Iterator[mock.MockCfbd]:
    cert, key = certs
    server = mock.MockCfbd(provider=provider, port=0, doh_port=0, admin_port=0, cert=cert, key=key)
    server.start()
    yield server
    server.stop()


def get(
    server: mock.MockCfbd, target: str, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    try:
        connection.request("GET", target, headers=headers or {})
        response = connection.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        connection.close()


def admin(server: mock.MockCfbd, method: str, path: str, payload: Any = None) -> Any:
    return mock.admin_request(server.admin_port, method, path, payload)


def test_provider_response_is_served_with_parsed_query(
    running: mock.MockCfbd, provider: StubProvider
) -> None:
    status, headers, body = get(running, "/games?year=2024&team=Ohio%20State")
    assert status == 200
    assert headers["content-type"] == "application/json"
    assert json.loads(body) == [{"id": 1, "season": 2024}]
    assert provider.calls == [("/games", {"year": ["2024"], "team": ["Ohio State"]})]


def test_provider_result_shapes_are_normalised(running: mock.MockCfbd) -> None:
    assert get(running, "/raw")[2] == b'{"raw": true}'
    status, _, body = get(running, "/teapot")
    assert (status, json.loads(body)) == (418, {"short": "stout"})


def test_provider_exceptions_become_404_and_500(running: mock.MockCfbd) -> None:
    missing = get(running, "/missing")
    broken = get(running, "/broken")
    assert missing[0] == 404
    assert broken[0] == 500
    assert "RuntimeError: boom" in json.loads(broken[2])["error"]


def test_unknown_path_is_404_json(running: mock.MockCfbd) -> None:
    status, _, body = get(running, "/nope?x=1")
    assert status == 404
    assert "/nope" in json.loads(body)["error"]


def test_info_has_a_builtin_answer_when_the_provider_has_none(running: mock.MockCfbd) -> None:
    status, _, body = get(running, "/info")
    assert status == 200
    assert "features" in json.loads(body)


def test_non_get_is_405_and_logged(running: mock.MockCfbd) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", running.port, timeout=10)
    connection.request("POST", "/games", body=b"{}")
    assert connection.getresponse().status == 405
    connection.close()
    assert running.state.log()[-1]["method"] == "POST"


def test_429_with_retry_after_then_falls_through(running: mock.MockCfbd) -> None:
    admin(
        running,
        "POST",
        "/_admin/script",
        {"path": "/games", "times": 2, "status": 429, "retry_after": 3},
    )
    first = get(running, "/games?year=2024")
    second = get(running, "/games?year=2024")
    third = get(running, "/games?year=2024")
    assert [first[0], second[0], third[0]] == [429, 429, 200]
    assert first[1]["retry-after"] == "3"
    assert running.state.hits("/games") == 3


@pytest.mark.parametrize("status", [400, 401, 403, 500, 503])
def test_scripted_error_statuses(running: mock.MockCfbd, status: int) -> None:
    running.state.add_rule({"path": "/games", "status": status})
    got, _, body = get(running, "/games?year=2024")
    assert got == status
    assert "error" in json.loads(body)


def test_scripted_body_and_headers(running: mock.MockCfbd) -> None:
    running.state.add_rule(
        {"path": "/games", "status": 400, "body": '{"error": "bad year"}', "headers": {"X-A": "b"}}
    )
    status, headers, body = get(running, "/games?year=abc")
    assert (status, json.loads(body), headers["x-a"]) == (400, {"error": "bad year"}, "b")


def test_latency_injection_delays_then_serves_normally(running: mock.MockCfbd) -> None:
    running.state.add_rule({"path": "/games", "delay_ms": 300})
    started = time.monotonic()
    status = get(running, "/games?year=2024")[0]
    assert status == 200
    assert time.monotonic() - started >= 0.3


def test_oversized_body_is_valid_json_above_two_megabytes(running: mock.MockCfbd) -> None:
    running.state.add_rule({"path": "/games", "oversize_bytes": BIG_BODY + 1})
    status, headers, body = get(running, "/games?year=2024")
    assert status == 200
    assert len(body) > BIG_BODY
    assert int(headers["content-length"]) == len(body)
    assert isinstance(json.loads(body), list)


def test_rule_matches_on_query_subset_and_prefix(running: mock.MockCfbd) -> None:
    running.state.add_rule({"path": "/ga*", "query": {"year": "1999"}, "status": 500})
    assert get(running, "/games?year=2024")[0] == 200
    assert get(running, "/games?year=1999&team=x")[0] == 500


def test_rules_apply_in_order_and_expire(running: mock.MockCfbd) -> None:
    running.state.add_rule({"path": "/games", "times": 1, "status": 500})
    running.state.add_rule({"path": "/games", "times": 1, "status": 429})
    codes = [get(running, "/games?year=1")[0] for _ in range(3)]
    assert codes == [500, 429, 200]


def test_request_log_records_headers_and_outcome(running: mock.MockCfbd) -> None:
    running.state.add_rule({"path": "/games", "status": 401})
    get(running, "/games?year=2024", {"Authorization": f"Bearer {MARKER_KEY}", "X-Probe": "1"})
    entry = admin(running, "GET", "/_admin/log")["entries"][-1]
    assert entry["method"] == "GET"
    assert entry["path"] == "/games"
    assert entry["query"] == {"year": ["2024"]}
    assert entry["headers"]["authorization"] == f"Bearer {MARKER_KEY}"
    assert entry["headers"]["x-probe"] == "1"
    assert entry["status"] == 401
    assert entry["scripted"] is True


def test_hits_by_path_prefix_and_query(running: mock.MockCfbd) -> None:
    for target in ["/games?year=1", "/games?year=2", "/rankings?year=1"]:
        get(running, target)
    count = admin(running, "GET", "/_admin/hits?" + urllib.parse.urlencode({"path": "/games"}))
    assert count["count"] == 2
    year_one = urllib.parse.urlencode({"path": "/games", "query": "year=1"})
    assert admin(running, "GET", "/_admin/hits?" + year_one)["count"] == 1
    assert admin(running, "GET", "/_admin/hits")["count"] == 3


def test_key_echo_mode_reflects_the_key(running: mock.MockCfbd) -> None:
    auth = {"Authorization": f"Bearer {MARKER_KEY}"}
    assert "x-echo-key" not in get(running, "/games?year=1", auth)[1]
    admin(running, "POST", "/_admin/config", {"echo_key": True})
    running.state.add_rule({"path": "/games", "status": 401})
    _, headers, body = get(running, "/games?year=1", auth)
    assert headers["x-echo-key"] == MARKER_KEY
    assert MARKER_KEY in body.decode()


def test_reset_clears_log_rules_and_modes(running: mock.MockCfbd) -> None:
    admin(running, "POST", "/_admin/config", {"echo_key": True})
    running.state.add_rule({"path": "/games", "status": 500})
    get(running, "/games?year=1")
    admin(running, "POST", "/_admin/reset")
    assert running.state.hits() == 0
    status, headers, _ = get(running, "/games?year=1")
    assert status == 200
    assert "x-echo-key" not in headers


def test_admin_rejects_bad_requests(running: mock.MockCfbd) -> None:
    with pytest.raises(mock.AdminError) as unknown:
        admin(running, "GET", "/_admin/nope")
    assert unknown.value.status == 404
    with pytest.raises(mock.AdminError) as invalid:
        admin(running, "POST", "/_admin/script", {"status": 500})
    assert invalid.value.status == 400
    with pytest.raises(mock.AdminError):
        admin(running, "POST", "/_admin/script", {"path": "/x", "status": "boom"})


def doh_context(trust: Path | None) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if trust is not None:
        context.load_verify_locations(cafile=str(trust))
    return context


def doh_get(server: mock.MockCfbd, trust: Path | None, name: str = HOST) -> dict[str, Any]:
    connection = http.client.HTTPSConnection(
        "127.0.0.1", server.doh_port, timeout=10, context=doh_context(trust)
    )
    try:
        connection.request(
            "GET", f"/dns-query?name={name}&type=A", headers={"Accept": "application/dns-json"}
        )
        return dict(json.loads(connection.getresponse().read()))
    finally:
        connection.close()


def test_doh_answers_configured_hosts_and_logs_the_query(
    running: mock.MockCfbd, certs: tuple[Path, Path]
) -> None:
    admin(running, "POST", "/_admin/doh-answers", {HOST: ["203.0.113.7"]})
    answer = doh_get(running, certs[0])
    assert answer["Answer"][0]["data"] == "203.0.113.7"
    kinds = [entry["kind"] for entry in admin(running, "GET", "/_admin/doh-log")["entries"]]
    assert kinds == ["connect", "tls", "query"]
    assert running.state.doh_connections() == 1


def test_doh_unknown_host_is_nxdomain(running: mock.MockCfbd, certs: tuple[Path, Path]) -> None:
    assert doh_get(running, certs[0], "unknown.example")["Status"] == 3


def test_untrusted_certificate_fails_verification_and_is_still_logged(
    running: mock.MockCfbd,
) -> None:
    with pytest.raises(ssl.SSLCertVerificationError):
        doh_get(running, None)
    deadline = time.monotonic() + 5
    while running.state.doh_connections() == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert running.state.doh_connections() == 1


def test_resolver_with_a_working_system_resolver_never_connects_to_doh(
    running: mock.MockCfbd,
) -> None:
    def system(host: Any, port: Any, *_: Any) -> list[tuple[Any, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.1", port))]

    doh_url = f"https://127.0.0.1:{running.doh_port}/dns-query"
    assert Resolver(system, doh_url, https_get).resolve(HOST, 443) == ["198.51.100.1"]
    time.sleep(0.2)
    assert running.state.doh_connections() == 0


def test_resolver_reports_a_clear_doh_error_for_an_unverifiable_certificate(
    running: mock.MockCfbd,
) -> None:
    def failing(host: Any, port: Any, *_: Any) -> list[tuple[Any, ...]]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    doh_url = f"https://127.0.0.1:{running.doh_port}/dns-query"
    with pytest.raises(ResolutionError) as caught:
        Resolver(failing, doh_url, https_get).resolve(HOST, 443)
    message = str(caught.value)
    assert HOST in message
    assert doh_url in message
    assert "DoH lookup" in message
    assert "CERTIFICATE_VERIFY_FAILED" in message or "certificate" in message.lower()
    assert "make dns-check" in message


def test_doh_is_disabled_without_a_certificate(provider: StubProvider) -> None:
    server = mock.MockCfbd(provider=provider, port=0, doh_port=None, admin_port=0)
    server.start()
    try:
        assert server.doh_port is None
        assert get(server, "/info")[0] == 200
    finally:
        server.stop()


def test_servers_bind_to_loopback_only(running: mock.MockCfbd) -> None:
    assert running.host == "127.0.0.1"
    for port in (running.port, running.admin_port, running.doh_port):
        assert port is not None and port > 0


def test_provider_spec_is_imported_by_name() -> None:
    provider: object = mock.load_provider("tests.unit.test_mock_cfbd_server:StubProvider")
    assert provider is StubProvider
    with pytest.raises(ValueError):
        mock.load_provider("no-colon")


def test_the_synthetic_generator_works_as_provider(certs: tuple[Path, Path]) -> None:
    server = mock.MockCfbd(provider=mock.load_provider("tests.synthetic.fake:response_for"))
    server.start()
    try:
        status, _, body = get(server, "/games?year=2024")
        assert status == 200
        assert isinstance(json.loads(body), list)
        assert get(server, "/definitely/not/an/endpoint")[0] == 404
    finally:
        server.stop()
