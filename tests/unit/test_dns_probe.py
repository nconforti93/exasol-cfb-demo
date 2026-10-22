import socket
import ssl
from collections.abc import Callable
from typing import Any

from scripts import dns, dns_udf

HOST = "api.collegefootballdata.com"
DOH = "https://1.1.1.1/dns-query"


class FakeSocket:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeResolver:
    def __init__(self, addresses: list[str] | None = None, error: Exception | None = None) -> None:
        self.addresses = addresses or []
        self.error = error
        self.installed_with: list[str] = []

    def install(self, doh_url: str) -> None:
        self.installed_with.append(doh_url)

    def resolve(self, host: str, port: int) -> list[str]:
        if self.error:
            raise self.error
        return self.addresses


def lookup_ok(*_args: object) -> list[object]:
    return [object()]


def lookup_fails(*_args: object) -> list[object]:
    raise socket.gaierror(-3, "Temporary failure in name resolution")


def connect_ok(_address: tuple[str, int], timeout: float) -> FakeSocket:
    return FakeSocket()


def make_connect_failing(error: Exception) -> Callable[[tuple[str, int], float], FakeSocket]:
    def connect(_address: tuple[str, int], timeout: float) -> FakeSocket:
        raise error

    return connect


def handshake_ok(_sock: object, host: str) -> dict[str, object]:
    return {
        "verified": True,
        "version": "TLSv1.3",
        "subject_cn": host,
        "not_after": "Jan  1 00:00:00 2030 GMT",
    }


def handshake_fails(_sock: object, _host: str) -> dict[str, object]:
    raise ssl.SSLCertVerificationError("certificate verify failed")


def read_empty(_path: str) -> str:
    return ""


def run_probe(
    resolver: FakeResolver,
    getaddrinfo: Callable[..., Any] = lookup_ok,
    connect: Callable[..., Any] = connect_ok,
    handshake: Callable[..., dict[str, object]] = handshake_ok,
    read_text: Callable[[str], str] = read_empty,
) -> dict[str, object]:
    return dns_udf.probe(
        HOST,
        443,
        DOH,
        resolver,
        getaddrinfo=getaddrinfo,
        connect=connect,
        handshake=handshake,
        read_text=read_text,
    )


def test_system_resolution_success_reports_system_path() -> None:
    report = run_probe(FakeResolver(["1.2.3.4"]))
    assert report["ok"] is True
    assert report["path"] == "system"
    assert report["system_ok"] is True
    assert report["addresses"] == ["1.2.3.4"]
    assert report["tls"] == handshake_ok(None, HOST)
    assert report["error"] is None


def test_gaierror_then_resolver_success_reports_doh_path() -> None:
    resolver = FakeResolver(["104.18.1.1", "104.18.1.2"])
    report = run_probe(resolver, getaddrinfo=lookup_fails)
    assert report["ok"] is True
    assert report["path"] == "doh"
    assert report["system_ok"] is False
    assert "name resolution" in str(report["system_error"])
    assert report["addresses"] == ["104.18.1.1", "104.18.1.2"]


def test_resolver_is_installed_with_the_doh_url() -> None:
    resolver = FakeResolver(["1.2.3.4"])
    run_probe(resolver)
    assert resolver.installed_with == [DOH]


def test_resolution_failure_stops_at_resolve_stage() -> None:
    resolver = FakeResolver(error=OSError("DoH lookup failed"))
    report = run_probe(resolver, getaddrinfo=lookup_fails)
    assert report["ok"] is False
    assert report["stage"] == "resolve"
    assert report["path"] is None
    assert "DoH lookup failed" in str(report["error"])
    assert report["tls"] is None


def test_connect_failure_stops_at_connect_stage() -> None:
    report = run_probe(
        FakeResolver(["1.2.3.4"]), connect=make_connect_failing(OSError("Network is unreachable"))
    )
    assert report["ok"] is False
    assert report["stage"] == "connect"
    assert report["path"] == "system"
    assert "Network is unreachable" in str(report["error"])


def test_tls_failure_reports_tls_stage_and_closes_the_socket() -> None:
    created: list[FakeSocket] = []

    def connect(_address: tuple[str, int], timeout: float) -> FakeSocket:
        created.append(FakeSocket())
        return created[0]

    report = run_probe(FakeResolver(["1.2.3.4"]), connect=connect, handshake=handshake_fails)
    assert report["ok"] is False
    assert report["stage"] == "tls"
    assert "certificate verify failed" in str(report["error"])
    assert created[0].closed is True


def test_resolver_file_states_are_described() -> None:
    texts = {
        "/etc/resolv.conf": "",
        "/etc/hosts": "# comment\n127.0.0.1 localhost\n::1 localhost\n",
    }
    report = run_probe(FakeResolver(["1.2.3.4"]), read_text=lambda path: texts[path])
    assert report["resolv_conf"] == "empty"
    assert report["hosts"] == "2 active line(s)"


def test_missing_resolver_file_is_reported_missing() -> None:
    def read_missing(_path: str) -> str:
        raise FileNotFoundError

    report = run_probe(FakeResolver(["1.2.3.4"]), read_text=read_missing)
    assert report["resolv_conf"] == "missing"
    assert report["hosts"] == "missing"


def test_report_is_json_serializable() -> None:
    import json

    json.dumps(run_probe(FakeResolver(["1.2.3.4"])))


def test_common_name_is_read_from_the_certificate_subject() -> None:
    cert = {"subject": ((("countryName", "US"),), (("commonName", HOST),))}
    assert dns_udf.common_name(cert) == HOST
    assert dns_udf.common_name({}) is None


def test_describe_text_file_ignores_comments_and_blank_lines() -> None:
    text = "\n# c\nnameserver 1.1.1.1\n   \n"
    assert dns_udf.describe_text_file(lambda _p: text, "/etc/resolv.conf") == "1 active line(s)"


def test_probe_module_source_compiles_and_needs_no_third_party_package() -> None:
    source = dns.probe_source()
    compile(source, "dns_udf.py", "exec")
    assert "pyexasol" not in source
