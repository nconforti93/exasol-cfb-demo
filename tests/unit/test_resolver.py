import ast
import json
import socket
import ssl
import types
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from vs.cfbd_vs import resolver
from vs.cfbd_vs.resolver import ResolutionError, Resolver

ROOT = Path(__file__).resolve().parents[2]
HOST = "api.collegefootballdata.com"
DOH = "https://1.1.1.1/dns-query"


def addrinfo(address: str, port: int = 443) -> tuple[Any, ...]:
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))


class FakeSystem:
    """A getaddrinfo that knows some names and fails with gaierror for the rest."""

    def __init__(self, known: Mapping[str, str] | None = None) -> None:
        self.known = dict(known or {})
        self.calls: list[str] = []

    def __call__(
        self, host: Any, port: Any, family: int = 0, type: int = 0, proto: int = 0, flags: int = 0
    ) -> list[tuple[Any, ...]]:
        self.calls.append(host)
        if host in self.known:
            return [addrinfo(self.known[host], port)]
        try:
            socket.inet_aton(host)
        except (OSError, TypeError):
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known") from None
        return [addrinfo(host, port)]


class FakeDoh:
    def __init__(self, body: Any = None, status: int = 200, error: Exception | None = None) -> None:
        self.body = body
        self.status = status
        self.error = error
        self.requests: list[tuple[str, dict[str, str], float]] = []

    def __call__(self, url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
        self.requests.append((url, dict(headers), timeout))
        if self.error is not None:
            raise self.error
        raw = self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()
        return self.status, raw


def answer(*records: tuple[int, str], status: int = 0) -> dict[str, Any]:
    return {
        "Status": status,
        "Answer": [
            {"name": HOST, "type": kind, "TTL": 300, "data": data} for kind, data in records
        ],
    }


A_RECORDS = answer((1, "104.18.1.1"), (1, "104.18.1.2"))


def make(doh: FakeDoh | None = None, doh_url: str = DOH) -> tuple[Resolver, FakeSystem, FakeDoh]:
    system = FakeSystem()
    transport = doh or FakeDoh(A_RECORDS)
    return Resolver(system, doh_url, transport), system, transport


def test_system_hit_is_returned_unchanged_without_a_doh_call() -> None:
    system = FakeSystem({HOST: "9.9.9.9"})
    doh = FakeDoh(A_RECORDS)
    found = Resolver(system, DOH, doh).getaddrinfo(HOST, 443)
    assert found == [addrinfo("9.9.9.9")]
    assert doh.requests == []


def test_ip_literals_never_use_doh() -> None:
    resolved, _, doh = make()
    assert resolved.resolve("10.88.0.1", 8080) == ["10.88.0.1"]
    assert doh.requests == []


def test_gaierror_falls_back_to_doh_and_resolves_the_address() -> None:
    resolved, system, doh = make()
    found = resolved.getaddrinfo(HOST, 443, socket.AF_INET, socket.SOCK_STREAM)
    assert [info[4] for info in found] == [("104.18.1.1", 443), ("104.18.1.2", 443)]
    assert system.calls == [HOST, "104.18.1.1", "104.18.1.2"]
    assert len(doh.requests) == 1


def test_doh_request_is_a_json_a_record_query_carrying_only_the_host_name() -> None:
    resolved, _, doh = make()
    resolved.resolve(HOST, 443)
    url, headers, timeout = doh.requests[0]
    assert url == f"{DOH}?name={HOST}&type=A"
    assert headers == {"Accept": "application/dns-json"}
    assert timeout == resolver.DOH_TIMEOUT_SECONDS


def test_host_name_is_percent_encoded_in_the_doh_query() -> None:
    resolved, _, doh = make()
    resolved.getaddrinfo("a&b=c d", 443)
    assert doh.requests[0][0] == f"{DOH}?name=a%26b%3Dc%20d&type=A"


def test_doh_url_with_a_query_string_is_extended_with_ampersand() -> None:
    resolved, _, doh = make(doh_url="https://1.1.1.1/dns-query?ct=application/dns-json")
    resolved.resolve(HOST, 443)
    assert doh.requests[0][0].endswith("?ct=application/dns-json&name=" + HOST + "&type=A")


def test_resolve_returns_sorted_unique_ipv4_addresses() -> None:
    doh = FakeDoh(answer((1, "104.18.1.2"), (1, "104.18.1.1"), (1, "104.18.1.2")))
    resolved, _, _ = make(doh)
    assert resolved.resolve(HOST, 443) == ["104.18.1.1", "104.18.1.2"]


def test_cname_and_non_a_records_are_skipped() -> None:
    doh = FakeDoh(answer((5, "edge.example.net."), (28, "2606::1"), (1, "104.18.1.1")))
    resolved, _, _ = make(doh)
    assert resolved.resolve(HOST, 443) == ["104.18.1.1"]


def test_second_resolution_is_answered_from_the_cache() -> None:
    resolved, _, doh = make()
    resolved.resolve(HOST, 443)
    resolved.resolve(HOST.upper(), 443)
    assert len(doh.requests) == 1


def test_the_cache_belongs_to_the_resolver_instance() -> None:
    first, _, first_doh = make()
    second, _, second_doh = make()
    first.resolve(HOST, 443)
    second.resolve(HOST, 443)
    assert len(first_doh.requests) == len(second_doh.requests) == 1


def test_failed_lookups_are_not_cached() -> None:
    doh = FakeDoh(answer())
    resolved, _, _ = make(doh)
    with pytest.raises(ResolutionError):
        resolved.resolve(HOST, 443)
    doh.body = A_RECORDS
    assert resolved.resolve(HOST, 443) == ["104.18.1.1", "104.18.1.2"]


@pytest.mark.parametrize(
    ("doh", "reason"),
    [
        (FakeDoh(error=ConnectionRefusedError("refused")), "ConnectionRefusedError"),
        (FakeDoh(error=TimeoutError("timed out")), "TimeoutError"),
        (
            FakeDoh(error=ssl.SSLCertVerificationError("certificate verify failed")),
            "certificate verify failed",
        ),
        (FakeDoh(body=b"<html>", status=200), "did not return JSON"),
        (FakeDoh(body=[1], status=200), "not an object"),
        (FakeDoh(body=A_RECORDS, status=503), "HTTP 503"),
        (FakeDoh(body=answer()), "no A record"),
        (FakeDoh(body=answer((5, "x.example."))), "no A record"),
        (FakeDoh(body=answer((1, "not-an-ip"))), "no A record"),
        (FakeDoh(body=answer(status=3)), "DNS status 3"),
    ],
)
def test_doh_failures_name_host_url_reason_and_the_check(doh: FakeDoh, reason: str) -> None:
    resolved, _, _ = make(doh)
    with pytest.raises(ResolutionError) as error:
        resolved.resolve(HOST, 443)
    message = str(error.value)
    assert HOST in message
    assert DOH in message
    assert reason in message
    assert "Name or service not known" in message
    assert "make dns-check" in message
    assert isinstance(error.value, socket.gaierror)
    assert (error.value.host, error.value.doh_url) == (HOST, DOH)


def test_off_reraises_the_system_error_without_a_doh_call() -> None:
    resolved, _, doh = make(doh_url="off")
    with pytest.raises(ResolutionError) as error:
        resolved.resolve(HOST, 443)
    message = str(error.value)
    assert HOST in message
    assert "Name or service not known" in message
    assert "DoH fallback is off" in message
    assert doh.requests == []


def test_non_string_hosts_keep_the_original_error() -> None:
    resolved, _, doh = make()
    with pytest.raises(socket.gaierror) as error:
        resolved.getaddrinfo(b"nope", 443)
    assert not isinstance(error.value, ResolutionError)
    assert doh.requests == []


@pytest.mark.parametrize(
    "value",
    ["http://1.1.1.1/dns-query", "https://cloudflare-dns.com/dns-query", "ftp://1.1.1.1/", "", "x"],
)
def test_doh_url_must_be_https_with_an_ip_literal_host(value: str) -> None:
    with pytest.raises(ValueError, match="DOH_URL"):
        resolver.validate_doh_url(value)


@pytest.mark.parametrize("value", ["off", DOH, "https://[2606:4700:4700::1111]/dns-query"])
def test_valid_doh_urls_are_accepted(value: str) -> None:
    assert resolver.validate_doh_url(value) == value


def test_resolver_rejects_an_invalid_doh_url_at_construction() -> None:
    with pytest.raises(ValueError, match="DOH_URL"):
        Resolver(FakeSystem(), "https://example.com/dns-query", FakeDoh())


@pytest.fixture
def restore_socket() -> Iterator[None]:
    saved = socket.getaddrinfo
    yield
    resolver.uninstall()
    socket.getaddrinfo = saved


def test_install_wraps_socket_getaddrinfo_and_uninstall_restores_it(
    restore_socket: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    system = FakeSystem({HOST: "9.9.9.9"})
    monkeypatch.setattr(socket, "getaddrinfo", system)
    resolver.install(DOH)
    assert socket.getaddrinfo is not system
    assert socket.getaddrinfo(HOST, 443)[0][4] == ("9.9.9.9", 443)
    resolver.uninstall()
    assert socket.getaddrinfo is system


def test_installing_twice_does_not_stack_wrappers(
    restore_socket: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    system = FakeSystem()
    monkeypatch.setattr(socket, "getaddrinfo", system)
    resolver.install(DOH)
    resolver.install("off")
    resolver.uninstall()
    assert socket.getaddrinfo is system


def test_install_rejects_an_invalid_url_and_leaves_socket_untouched(
    restore_socket: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    system = FakeSystem()
    monkeypatch.setattr(socket, "getaddrinfo", system)
    with pytest.raises(ValueError, match="DOH_URL"):
        resolver.install("http://1.1.1.1/")
    assert socket.getaddrinfo is system


def test_installed_resolver_falls_back_for_create_connection_style_lookups(
    restore_socket: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", FakeSystem())
    resolver.install(DOH)
    active = resolver._active
    assert active is not None
    doh = FakeDoh(A_RECORDS)
    active.transport = doh
    infos = socket.getaddrinfo(HOST, 443, 0, socket.SOCK_STREAM)
    assert infos[0][4] == ("104.18.1.1", 443)
    assert len(doh.requests) == 1


def test_module_resolve_before_install_uses_the_system_resolver_only(
    restore_socket: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", FakeSystem({HOST: "9.9.9.9"}))
    resolver.uninstall()
    assert resolver.resolve(HOST, 443) == ["9.9.9.9"]
    with pytest.raises(ResolutionError, match="DoH fallback is off"):
        resolver.resolve("unknown.example", 443)


def test_source_runs_as_a_stand_alone_module_like_the_dns_check_udf() -> None:
    source = (ROOT / "vs" / "cfbd_vs" / "resolver.py").read_text(encoding="utf-8")
    module = types.ModuleType("resolver")
    exec(compile(source, "resolver.py", "exec"), module.__dict__)
    assert callable(module.install)
    assert callable(module.resolve)
    assert module.DEFAULT_DOH_URL == DOH


FORBIDDEN_NAMES = {"getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"}


def python_sources_outside_the_resolver() -> list[Path]:
    return [
        path
        for path in sorted((ROOT / "vs").rglob("*.py"))
        if path.name != "resolver.py" and "openapi" not in path.parts
    ]


def test_no_other_module_calls_the_system_name_lookup_functions() -> None:
    offenders: list[str] = []
    for path in python_sources_outside_the_resolver():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
            if name in FORBIDDEN_NAMES:
                offenders.append(f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', '?')}: {name}")
    assert offenders == []


def test_the_resolver_module_itself_is_the_one_that_touches_getaddrinfo() -> None:
    source = (ROOT / "vs" / "cfbd_vs" / "resolver.py").read_text(encoding="utf-8")
    assert "socket.getaddrinfo" in source
