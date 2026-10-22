"""The DoH probe UDF of the `network` integration test always takes the DoH path (m24)."""

import json
import socket
from types import SimpleNamespace

from tests.it import doh_probe


def run_probe(host: str, doh_url: str) -> dict[str, object]:
    namespace: dict[str, object] = {}
    exec(compile(doh_probe.probe_body(), "doh_probe", "exec"), namespace)
    ctx = SimpleNamespace(HOST=host, PORT=443, DOH_URL=doh_url)
    report = json.loads(namespace["run"](ctx))  # type: ignore[operator]
    assert isinstance(report, dict)
    return report


def test_the_system_resolver_is_made_to_fail_so_a_resolving_sandbox_still_uses_doh() -> None:
    socket.getaddrinfo("localhost", 443)
    report = run_probe("localhost", "https://127.0.0.1:1/dns-query")
    assert report["ok"] is False
    assert report["stage"] == "resolve"
    error = str(report["error"])
    assert "injected" in error
    assert "DoH lookup via https://127.0.0.1:1/dns-query" in error


def test_the_probe_restores_the_system_resolver() -> None:
    before = socket.getaddrinfo
    run_probe("localhost", "https://127.0.0.1:1/dns-query")
    assert socket.getaddrinfo is before
    assert socket.getaddrinfo("localhost", 443)


def test_ip_literals_still_resolve_through_the_injected_failure() -> None:
    namespace: dict[str, object] = {}
    exec(compile(doh_probe.probe_body(), "doh_probe", "exec"), namespace)
    failing = namespace["failing_system_resolver"]
    assert failing("127.0.0.1", 443, socket.AF_INET, socket.SOCK_STREAM)  # type: ignore[operator]
