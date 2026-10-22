import json
import re
from pathlib import Path

import pytest

from scripts import dbcheck, dns
from vs.cfbd_vs import resolver
from vs.host import target

HOST = "api.collegefootballdata.com"


def make_report(**overrides: object) -> dict[str, object]:
    report: dict[str, object] = {
        "host": HOST,
        "port": 443,
        "doh_url": "https://1.1.1.1/dns-query",
        "ok": True,
        "stage": None,
        "error": None,
        "path": "doh",
        "system_ok": False,
        "system_error": "Temporary failure in name resolution",
        "addresses": ["104.18.1.1", "104.18.1.2"],
        "resolv_conf": "empty",
        "hosts": "empty",
        "tls": {
            "verified": True,
            "version": "TLSv1.3",
            "subject_cn": HOST,
            "not_after": "Jan  1 00:00:00 2030 GMT",
        },
    }
    report.update(overrides)
    return report


def test_exit_code_zero_when_ok() -> None:
    assert dns.exit_code(make_report()) == dns.EXIT_OK == 0


def test_exit_code_nonzero_when_failed() -> None:
    assert dns.exit_code(make_report(ok=False)) == dns.EXIT_FAILED != 0


def test_exit_code_fails_when_tls_not_verified_even_if_ok_is_claimed() -> None:
    assert dns.exit_code(make_report(tls=None)) == dns.EXIT_FAILED


def test_report_success_shows_path_addresses_and_tls() -> None:
    text = dns.format_report(make_report())
    assert "OK" in text.splitlines()[0]
    assert HOST in text
    assert "doh" in text
    assert "104.18.1.1, 104.18.1.2" in text
    assert "TLSv1.3" in text
    assert "verified" in text
    assert "Temporary failure in name resolution" in text
    assert "resolv.conf" in text
    assert "empty" in text
    assert "https://1.1.1.1/dns-query" in text


def test_report_failure_names_stage_and_error() -> None:
    text = dns.format_report(
        make_report(
            ok=False,
            stage="resolve",
            error="DoH lookup failed",
            path=None,
            addresses=[],
            tls=None,
        )
    )
    assert "FAIL" in text.splitlines()[0]
    assert "resolve" in text
    assert "DoH lookup failed" in text
    assert "not established" in text


def test_report_states_that_no_http_request_was_sent() -> None:
    assert "0 CFBD calls" in dns.format_report(make_report())


def test_parse_report_reads_json_text() -> None:
    assert dns.parse_report(json.dumps(make_report()))["host"] == HOST


def test_parse_report_rejects_garbage() -> None:
    with pytest.raises(dns.DnsCheckError):
        dns.parse_report("not json")


def test_default_doh_url_comes_from_the_environment() -> None:
    assert dns.default_doh_url({"DOH_URL": "https://9.9.9.9/dns-query"}) == (
        "https://9.9.9.9/dns-query"
    )
    assert dns.default_doh_url({}) == "https://1.1.1.1/dns-query"
    assert dns.default_doh_url({"DOH_URL": "off"}) == "off"


def test_fallback_resolver_source_is_used_until_the_module_exists(tmp_path: Path) -> None:
    source = dns.load_resolver_source(tmp_path / "resolver.py")
    assert source == dns.SYSTEM_ONLY_RESOLVER_SOURCE


def test_bundled_resolver_module_is_used_when_present(tmp_path: Path) -> None:
    module = tmp_path / "resolver.py"
    module.write_text(
        "def install(doh_url):\n    pass\n\n\ndef resolve(host, port):\n    return []\n"
    )
    assert dns.load_resolver_source(module) == module.read_text()


def test_udf_source_exposes_run_and_the_resolver_module() -> None:
    namespace: dict[str, object] = {}
    exec(compile(dns.build_udf_source(dns.SYSTEM_ONLY_RESOLVER_SOURCE), "udf", "exec"), namespace)
    assert callable(namespace["run"])
    assert callable(namespace["resolver"].resolve)  # type: ignore[attr-defined]
    assert callable(namespace["probe"])


def test_udf_source_keeps_future_imports_of_the_resolver_working() -> None:
    resolver = (
        "from __future__ import annotations\n\n"
        "def install(doh_url: str | None) -> None:\n    pass\n\n\n"
        "def resolve(host: str, port: int) -> list[str]:\n    return ['9.9.9.9']\n"
    )
    namespace: dict[str, object] = {}
    exec(compile(dns.build_udf_source(resolver), "udf", "exec"), namespace)
    assert namespace["resolver"].resolve("h", 1) == ["9.9.9.9"]  # type: ignore[attr-defined]


def test_udf_ddl_names_schema_script_and_parameters() -> None:
    ddl = dns.udf_ddl("def run(ctx):\n    return ''\n")
    assert ddl.startswith("CREATE OR REPLACE PYTHON3 SCALAR SCRIPT CFB_TOOLS.DNS_CHECK(")
    assert "RETURNS VARCHAR(2000000) UTF8" in ddl
    assert "def run(ctx)" in ddl


def test_udf_parameters_are_declared_in_the_case_the_udf_reads_them() -> None:
    """Exasol 2026.1.1 keeps the case of unquoted UDF parameter names (`host` stays `host`), so
    `ctx.HOST` only works when the parameter is declared upper case."""
    declared = re.findall(r"\b([A-Za-z_]+) (?:VARCHAR|DECIMAL)\(", dns.UDF_SIGNATURE)
    read = set(re.findall(r"\bctx\.([A-Za-z_]+)", dns.UDF_TEMPLATE))
    assert read
    assert read <= set(declared)
    assert all(name == name.upper() for name in declared)


def test_system_only_resolver_has_the_resolver_interface() -> None:
    namespace: dict[str, object] = {}
    exec(compile(dns.SYSTEM_ONLY_RESOLVER_SOURCE, "resolver", "exec"), namespace)
    assert callable(namespace["install"])
    assert callable(namespace["resolve"])


def test_the_default_doh_url_is_the_runtimes() -> None:
    assert dns.default_doh_url({}) == resolver.DEFAULT_DOH_URL


def test_unreachable_message_carries_the_start_hint() -> None:
    message = dbcheck.unreachable_message("127.0.0.1", 8563)
    assert "exasol start -d local" in message
    assert "127.0.0.1:8563" in message
    assert "exakit" in message


def test_unreachable_message_for_aws_names_the_target_and_not_the_local_hint() -> None:
    message = dbcheck.unreachable_message("db.example", 8563, "aws")
    assert "target aws" in message
    assert "db.example:8563" in message
    assert "exasol start -d local" not in message


def test_dbcheck_probes_the_resolved_targets_host_and_port(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    probed: list[tuple[str, int]] = []
    monkeypatch.setattr(
        target,
        "resolve",
        lambda name=None, **kwargs: target.Target(
            "aws", "db.example", 9000, "sys", Path("/x"), target.TLS_PINNED, "AB"
        ),
    )

    def record(host: str, port: int) -> bool:
        probed.append((host, port))
        return False

    monkeypatch.setattr(dbcheck, "check", record)
    assert dbcheck.main([]) == 1
    assert probed == [("db.example", 9000)]
    assert "target aws" in capsys.readouterr().err


def test_dbcheck_target_option_is_passed_to_the_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    names: list[str | None] = []

    def fake_resolve(name: str | None = None, **kwargs: object) -> target.Target:
        names.append(name)
        return target.Target("local", "h", 1, "sys", Path("/x"), target.TLS_UNVERIFIED_LOCAL)

    monkeypatch.setattr(target, "resolve", fake_resolve)
    monkeypatch.setattr(dbcheck, "check", lambda host, port: True)
    assert dbcheck.main(["--target", "local"]) == 0
    assert names == ["local"]


class FakeStatement:
    def fetchone(self) -> tuple[str]:
        return (json.dumps(make_report()),)


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.closed = False

    def execute(self, sql: str, params: object = None) -> FakeStatement:
        self.statements.append(sql)
        return FakeStatement()

    def close(self) -> None:
        self.closed = True


def test_run_check_connects_through_the_target_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    deployment = target.Target(
        "aws", "db.example", 8563, "sys", Path("/x"), target.TLS_PINNED, "AB"
    )
    monkeypatch.setattr(target.Target, "connect", lambda self, user=None, password=None: connection)
    report = dns.run_check(HOST, 443, "off", deployment)
    assert report["host"] == HOST
    assert connection.closed
    assert any("CREATE OR REPLACE PYTHON3" in statement for statement in connection.statements)


def test_dns_module_has_no_password_source_of_its_own() -> None:
    assert not hasattr(dns, "read_sys_password")
    assert not hasattr(dns, "PASSWORD_FILE")


def test_check_returns_failure_when_nothing_listens() -> None:
    assert dbcheck.check("127.0.0.1", 1, timeout=0.2) is False
