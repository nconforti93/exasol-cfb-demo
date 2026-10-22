"""`loader/exapump.py`: the exapump child-process environment, from fake targets (no exapump)."""

import json
import os
import ssl
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from loader import exapump
from loader.exapump import ExapumpError
from vs.host.target import (
    TLS_CHAIN,
    TLS_PINNED,
    TLS_UNVERIFIED_LOCAL,
    Target,
    fingerprint_of,
)

PASSWORD = "marker p@ss/w:rd#1?&"
PINNED = "A66203816B5D3CC557B853D900161F9C06229158E067866477B7AB0B507239BB"
AWS_HOST = "ec2-1-2-3-4.eu-central-1.compute.amazonaws.com"
CERT_PEM = """\
-----BEGIN CERTIFICATE-----
MIIBiDCCAS+gAwIBAgIUAvXh06qu8MZShNM2V8XOlwFy7skwCgYIKoZIzj0EAwIw
GTEXMBUGA1UEAwwOZmFrZS1ub2RlLnRlc3QwIBcNMjYxMDIyMTEwODA1WhgPMjEy
NjA5MjgxMTA4MDVaMBkxFzAVBgNVBAMMDmZha2Utbm9kZS50ZXN0MFkwEwYHKoZI
zj0CAQYIKoZIzj0DAQcDQgAE1S3YIht+Z5CYuJKYWUvWnDEdsjb0BhMH6RDQFr9u
w/26tAYQejPhd7K8awwBzeFa7bbvBWkna8izJYTQPXQcoaNTMFEwHQYDVR0OBBYE
FFkxZx/XwgAi0QpMGmydbe98bqiTMB8GA1UdIwQYMBaAFFkxZx/XwgAi0QpMGmyd
be98bqiTMA8GA1UdEwEB/wQFMAMBAf8wCgYIKoZIzj0EAwIDRwAwRAIgfs/eE5/t
y1tV8X3PL/ob3ARfWw2BWOEO55crFvOtILoCIBQhqosTJgCMoqc1hl9C+xG2lYdQ
hJlNOCpsIhEeDtUj
-----END CERTIFICATE-----
"""


def deployment(tmp_path: Path) -> Path:
    directory = tmp_path / "deployment"
    directory.mkdir()
    (directory / "secrets.json").write_text(json.dumps({"dbPassword": PASSWORD}), encoding="utf-8")
    return directory


def local_target(tmp_path: Path, host: str = "127.0.0.1", port: int = 8563) -> Target:
    return Target("local", host, port, "sys", deployment(tmp_path), TLS_UNVERIFIED_LOCAL)


def aws_target(tmp_path: Path) -> Target:
    return Target("aws", AWS_HOST, 8563, "sys", deployment(tmp_path), TLS_PINNED, PINNED)


def no_certificate(host: str, port: int) -> str:
    raise AssertionError(f"no certificate should be fetched for {host}:{port}")


def serve_certificate(host: str, port: int) -> str:
    return CERT_PEM


def test_the_dsn_goes_into_the_environment_only(tmp_path: Path) -> None:
    env, args = exapump.exapump_env(aws_target(tmp_path), environ={"PATH": "/bin"})
    parts = urlsplit(env["EXAPUMP_DSN"])
    assert parts.scheme == "exasol"
    assert (parts.hostname, parts.port, parts.username) == (AWS_HOST, 8563, "sys")
    assert unquote(parts.password or "") == PASSWORD
    assert env["PATH"] == "/bin"
    assert not any("exasol://" in arg for arg in args)


def test_no_secret_appears_in_the_arguments(tmp_path: Path) -> None:
    _, args = exapump.exapump_env(aws_target(tmp_path), environ={})
    assert not any(PASSWORD in arg or "p@ss" in arg for arg in args)
    assert "EXAPUMP_DSN" not in " ".join(args)


def test_a_pinned_target_passes_its_fingerprint_in_lower_case_hex(tmp_path: Path) -> None:
    _, args = exapump.exapump_env(
        aws_target(tmp_path), environ={}, fetch_certificate=no_certificate
    )
    assert args == ["--certificate-fingerprint", PINNED.lower()]


def test_a_loopback_target_computes_the_server_certificate_fingerprint_at_run_time(
    tmp_path: Path,
) -> None:
    seen: list[tuple[str, int]] = []

    def fetch(host: str, port: int) -> str:
        seen.append((host, port))
        return CERT_PEM

    _, args = exapump.exapump_env(
        local_target(tmp_path, port=9563), environ={}, fetch_certificate=fetch
    )
    assert seen == [("127.0.0.1", 9563)]
    assert args == ["--certificate-fingerprint", fingerprint_of(CERT_PEM).lower()]


@pytest.mark.parametrize("host", ["127.0.0.1", "127.1.2.3", "::1", "localhost"])
def test_every_loopback_spelling_is_accepted_for_the_unverified_local_target(
    tmp_path: Path, host: str
) -> None:
    env, args = exapump.exapump_env(
        local_target(tmp_path, host=host), environ={}, fetch_certificate=serve_certificate
    )
    assert args[0] == "--certificate-fingerprint"
    assert env["EXAPUMP_DSN"].startswith("exasol://sys:")


def test_an_ipv6_host_is_bracketed_in_the_dsn(tmp_path: Path) -> None:
    env, _ = exapump.exapump_env(
        local_target(tmp_path, host="::1"), environ={}, fetch_certificate=serve_certificate
    )
    assert env["EXAPUMP_DSN"].endswith("@[::1]:8563")


@pytest.mark.parametrize("host", ["10.1.2.3", "db.example.com", "8.8.8.8"])
def test_an_unverified_target_on_another_host_is_refused(tmp_path: Path, host: str) -> None:
    with pytest.raises(ExapumpError, match="loopback") as raised:
        exapump.exapump_env(
            local_target(tmp_path, host=host), environ={}, fetch_certificate=no_certificate
        )
    assert PASSWORD not in str(raised.value)


def test_a_chain_verified_target_passes_no_fingerprint(tmp_path: Path) -> None:
    target = Target("aws", AWS_HOST, 8563, "sys", deployment(tmp_path), TLS_CHAIN)
    env, args = exapump.exapump_env(target, environ={}, fetch_certificate=no_certificate)
    assert args == []
    assert "EXAPUMP_DSN" in env


def test_a_pinned_target_without_a_fingerprint_is_refused(tmp_path: Path) -> None:
    target = Target("aws", AWS_HOST, 8563, "sys", deployment(tmp_path), TLS_PINNED, None)
    with pytest.raises(ExapumpError, match="fingerprint"):
        exapump.exapump_env(target, environ={})


def test_an_unreadable_server_certificate_is_reported_without_driver_text(
    tmp_path: Path,
) -> None:
    def refuse(host: str, port: int) -> str:
        raise ConnectionRefusedError(f"secret detail {PASSWORD}")

    with pytest.raises(ExapumpError, match=r"127.0.0.1:8563.*ConnectionRefusedError") as raised:
        exapump.exapump_env(local_target(tmp_path), environ={}, fetch_certificate=refuse)
    assert PASSWORD not in str(raised.value)
    assert "secret detail" not in str(raised.value)


def test_the_returned_environment_is_a_copy_that_replaces_an_inherited_dsn(
    tmp_path: Path,
) -> None:
    inherited = {"PATH": "/bin", "EXAPUMP_DSN": "exasol://other:other@elsewhere:1"}
    env, _ = exapump.exapump_env(aws_target(tmp_path), environ=inherited)
    assert env is not inherited
    assert inherited["EXAPUMP_DSN"] == "exasol://other:other@elsewhere:1"
    assert env["EXAPUMP_DSN"] != inherited["EXAPUMP_DSN"]


def test_the_dsn_reaches_the_child_and_no_later_process(tmp_path: Path) -> None:
    before = os.environ.get("EXAPUMP_DSN")
    env, _ = exapump.exapump_env(aws_target(tmp_path), fetch_certificate=no_certificate)
    probe = [sys.executable, "-I", "-c", "import os; print('EXAPUMP_DSN' in os.environ)"]
    child = subprocess.run(probe, env=env, capture_output=True, text=True, check=True)
    later = subprocess.run(
        probe,
        env={k: v for k, v in os.environ.items() if k != "EXAPUMP_DSN"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert child.stdout.strip() == "True"
    assert later.stdout.strip() == "False"
    assert os.environ.get("EXAPUMP_DSN") == before


def test_the_password_is_read_when_called_and_not_kept(tmp_path: Path) -> None:
    target = aws_target(tmp_path)
    secrets = target.deployment_dir / "secrets.json"
    first, _ = exapump.exapump_env(target, environ={}, fetch_certificate=no_certificate)
    secrets.write_text(json.dumps({"dbPassword": "rotated"}), encoding="utf-8")
    second, _ = exapump.exapump_env(target, environ={}, fetch_certificate=no_certificate)
    assert unquote(urlsplit(first["EXAPUMP_DSN"]).password or "") == PASSWORD
    assert unquote(urlsplit(second["EXAPUMP_DSN"]).password or "") == "rotated"


def test_a_missing_secrets_file_is_a_target_error_without_the_password(tmp_path: Path) -> None:
    from vs.host.target import TargetError

    directory = tmp_path / "empty"
    directory.mkdir()
    target = Target("aws", AWS_HOST, 8563, "sys", directory, TLS_PINNED, PINNED)
    with pytest.raises(TargetError, match="secrets file not found"):
        exapump.exapump_env(target, environ={})


def test_the_default_certificate_reader_is_ssl_get_server_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, int], float]] = []

    def fake_get(address: tuple[str, int], timeout: float = 0) -> str:
        calls.append((address, timeout))
        return CERT_PEM

    monkeypatch.setattr(ssl, "get_server_certificate", fake_get)
    assert exapump.server_certificate("127.0.0.1", 8563) == CERT_PEM
    assert calls == [(("127.0.0.1", 8563), exapump.CERTIFICATE_TIMEOUT_SECONDS)]
