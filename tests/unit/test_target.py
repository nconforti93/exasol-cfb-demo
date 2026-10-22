"""`vs/host/target.py`: Exasol Personal deployment targets, from fake deployment directories.

Every secret in these tests is a marker string; nothing reads the real `~/.exasol`.
"""

import hashlib
import json
import ssl
from pathlib import Path
from typing import Any, Never

import pytest

from vs.host import target
from vs.host.target import Target, TargetError

PASSWORD = "marker-sys-password-1234"
NODE_CERT = """\
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
NODE_FINGERPRINT = "A66203816B5D3CC557B853D900161F9C06229158E067866477B7AB0B507239BB"
OTHER_CERT = """\
-----BEGIN CERTIFICATE-----
MIIBizCCATGgAwIBAgIUborthGgQQP7j6pbesOrP6B4CyPMwCgYIKoZIzj0EAwIw
GjEYMBYGA1UEAwwPb3RoZXItbm9kZS50ZXN0MCAXDTI2MTAyMjExMDgwNVoYDzIx
MjYwOTI4MTEwODA1WjAaMRgwFgYDVQQDDA9vdGhlci1ub2RlLnRlc3QwWTATBgcq
hkjOPQIBBggqhkjOPQMBBwNCAARZMhMJ1yJQ64ookpIpU3fXDzN93Ql22Ic76pTH
bJJgzQe+KRto6q48rfP6AdMrY/4TB8mcYjLedhh8Bf3+nQ46o1MwUTAdBgNVHQ4E
FgQUXUXn4QegKdJmodpqoHI7ro4KnOgwHwYDVR0jBBgwFoAUXUXn4QegKdJmodpq
oHI7ro4KnOgwDwYDVR0TAQH/BAUwAwEB/zAKBggqhkjOPQQDAgNIADBFAiEAwbXZ
Ultp0ubXGlaOnU4SnAVz9VSqh0N6W9yH2Ndku1MCIExn87TESTPnLcSy+52r2NCJ
MuiBV0SOMnhiiwOR8I3j
-----END CERTIFICATE-----
"""
OTHER_FINGERPRINT = "8DA7CDAB12E77741E25C4BBF9F21677257E68507767F67D5C8E8900876FBC336"
AWS_HOST = "ec2-1-2-3-4.eu-central-1.compute.amazonaws.com"
OTHER_HOST = "ec2-5-6-7-8.eu-central-1.compute.amazonaws.com"


def write_json(path: Path, content: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content), encoding="utf-8")


def make_local(root: Path, *, host: str = "127.0.0.1", port: int = 8563) -> Path:
    directory = root / "local"
    write_json(
        directory / "deployment.json",
        {"backend": "local", "connection": {"host": host, "dbPort": port, "username": "sys"}},
    )
    write_json(directory / "secrets.json", {"dbPassword": PASSWORD})
    return directory


def make_aws(
    root: Path,
    *,
    node_certs: dict[str, tuple[str, str]] | None = None,
    instructions_fingerprint: str | None = None,
    password_file: object = None,
) -> Path:
    """`node_certs` maps a node name to `(host name, PEM)`; the default is one node."""
    directory = root / "default"
    certs = node_certs if node_certs is not None else {"n11": (AWS_HOST, NODE_CERT)}
    write_json(
        directory / "deployment.json",
        {
            "backend": "tofu",
            "connection": {"host": AWS_HOST, "dbPort": 8563, "username": "sys"},
            "nodes": {
                name: {"dnsName": host, "publicIp": "9.9.9.9", "tlsCert": pem}
                for name, (host, pem) in certs.items()
            },
        },
    )
    write_json(
        directory / "secrets.json",
        {"dbPassword": PASSWORD, "dbUsername": "sys", "adminUiPassword": "marker-admin"}
        if password_file is None
        else password_file,
    )
    text = "Deployment State: running\n"
    if instructions_fingerprint:
        text += f"  - Certificate Fingerprint: {instructions_fingerprint}\n"
    (directory / "connection-instructions.txt").write_text(text, encoding="utf-8")
    return directory


def resolve(root: Path, name: str | None = None, **kwargs: Any) -> Target:
    kwargs.setdefault("environ", {})
    kwargs.setdefault("dotenv_path", root / "absent.env")
    return target.resolve(name, deployments_root=root, **kwargs)


# --- selecting the deployment ---


def test_local_is_the_default_and_reads_host_and_port_from_the_deployment(tmp_path: Path) -> None:
    make_local(tmp_path, host="127.0.0.2", port=9000)
    resolved = resolve(tmp_path)
    assert resolved.name == "local"
    assert (resolved.host, resolved.port) == ("127.0.0.2", 9000)
    assert resolved.dsn == "127.0.0.2:9000"
    assert resolved.user == "sys"
    assert resolved.deployment_dir == tmp_path / "local"


def test_the_target_environment_variable_selects_aws(tmp_path: Path) -> None:
    make_aws(tmp_path)
    assert resolve(tmp_path, environ={"TARGET": "aws"}).name == "aws"


def test_an_explicit_name_beats_the_environment(tmp_path: Path) -> None:
    make_local(tmp_path)
    assert resolve(tmp_path, "local", environ={"TARGET": "aws"}).name == "local"


def test_an_empty_target_variable_means_local(tmp_path: Path) -> None:
    make_local(tmp_path)
    assert resolve(tmp_path, environ={"TARGET": ""}).name == "local"


def test_an_unknown_target_is_refused_and_lists_the_choices(tmp_path: Path) -> None:
    with pytest.raises(TargetError, match=r"unknown target 'prod'.*local.*aws"):
        resolve(tmp_path, "prod")


def test_aws_maps_to_the_default_deployment_directory(tmp_path: Path) -> None:
    make_aws(tmp_path)
    assert resolve(tmp_path, "aws").deployment_dir == tmp_path / "default"


def test_a_missing_deployment_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(TargetError, match=r"deployment\.json"):
        resolve(tmp_path, "local")


@pytest.mark.parametrize(
    "content", ["not json", "[]", "{}", '{"connection": {}}', '{"connection": {"host": "h"}}']
)
def test_an_unusable_deployment_file_is_refused(tmp_path: Path, content: str) -> None:
    directory = tmp_path / "local"
    directory.mkdir()
    (directory / "deployment.json").write_text(content, encoding="utf-8")
    with pytest.raises(TargetError, match=r"deployment\.json"):
        resolve(tmp_path, "local")


# --- DSN override ---


def test_exasol_dsn_in_the_environment_overrides_the_deployment(tmp_path: Path) -> None:
    resolved = resolve(tmp_path, environ={"EXASOL_DSN": "10.0.0.1:9999"})
    assert (resolved.host, resolved.port, resolved.dsn) == ("10.0.0.1", 9999, "10.0.0.1:9999")
    assert resolved.name == "local"


def test_exasol_dsn_in_dotenv_overrides_when_the_environment_has_none(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("EXASOL_DSN=10.0.0.2:1234\n", encoding="utf-8")
    assert resolve(tmp_path, dotenv_path=dotenv).dsn == "10.0.0.2:1234"
    assert resolve(tmp_path, dotenv_path=dotenv, environ={"EXASOL_DSN": "h:1"}).dsn == "h:1"


def test_an_explicit_dsn_beats_environment_and_dotenv(tmp_path: Path) -> None:
    resolved = resolve(tmp_path, dsn="explicit:5", environ={"EXASOL_DSN": "env:6"})
    assert resolved.dsn == "explicit:5"


def test_an_override_with_a_fingerprint_keeps_it_out_of_host_and_port(tmp_path: Path) -> None:
    resolved = resolve(tmp_path, dsn="db.example/ABCDEF0123:8564")
    assert (resolved.host, resolved.port) == ("db.example", 8564)
    assert resolved.dsn == "db.example/ABCDEF0123:8564"


def test_the_port_defaults_to_8563() -> None:
    assert target.parse_dsn("db.local") == ("db.local", 8563)
    assert target.parse_dsn("127.0.0.1:8563") == ("127.0.0.1", 8563)


def test_a_bad_port_is_refused() -> None:
    with pytest.raises(ValueError, match="port"):
        target.parse_dsn("127.0.0.1:abc")


def test_dotenv_parser_reads_values_and_skips_comments(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# comment\nEXASOL_DSN=127.0.0.1:8563\n\nCFB_TEST_OWNER_PASSWORD="quoted value"\nEMPTY=\n'
    )
    assert target.parse_dotenv(env_file) == {
        "EXASOL_DSN": "127.0.0.1:8563",
        "CFB_TEST_OWNER_PASSWORD": "quoted value",
        "EMPTY": "",
    }


def test_dotenv_missing_file_is_empty(tmp_path: Path) -> None:
    assert target.parse_dotenv(tmp_path / "absent.env") == {}


# --- TLS ---


def test_local_skips_certificate_validation_and_says_so(tmp_path: Path) -> None:
    make_local(tmp_path)
    options = resolve(tmp_path, "local").connect_options("sys", "pw")
    assert options["websocket_sslopt"] == {"cert_reqs": ssl.CERT_NONE}
    assert options["dsn"] == "127.0.0.1:8563"


def test_aws_pins_the_fingerprint_of_the_connection_hosts_certificate(tmp_path: Path) -> None:
    make_aws(tmp_path)
    resolved = resolve(tmp_path, "aws")
    assert resolved.fingerprint == NODE_FINGERPRINT
    assert resolved.dsn == f"{AWS_HOST}/{NODE_FINGERPRINT}:8563"
    options = resolved.connect_options("sys", "pw")
    assert "websocket_sslopt" not in options
    assert options["dsn"] == resolved.dsn


def test_a_socket_timeout_is_passed_only_when_asked_for(tmp_path: Path) -> None:
    make_local(tmp_path)
    resolved = resolve(tmp_path, "local")
    assert "socket_timeout" not in resolved.connect_options("sys", "pw")
    assert resolved.connect_options("sys", "pw", socket_timeout=600)["socket_timeout"] == 600


def test_the_fingerprint_is_the_sha256_of_the_der_certificate() -> None:
    der = ssl.PEM_cert_to_DER_cert(NODE_CERT)
    assert (
        hashlib.sha256(der).hexdigest().upper()
        == NODE_FINGERPRINT
        == target.fingerprint_of(NODE_CERT)
    )


def test_aws_picks_the_certificate_of_the_node_it_connects_to(tmp_path: Path) -> None:
    make_aws(
        tmp_path,
        node_certs={"n11": (OTHER_HOST, OTHER_CERT), "n12": (AWS_HOST, NODE_CERT)},
    )
    assert resolve(tmp_path, "aws").fingerprint == NODE_FINGERPRINT


def test_aws_refuses_when_the_connection_host_matches_no_node(tmp_path: Path) -> None:
    make_aws(tmp_path, node_certs={"n11": (OTHER_HOST, OTHER_CERT), "n12": (OTHER_HOST, NODE_CERT)})
    with pytest.raises(TargetError, match="fingerprint"):
        resolve(tmp_path, "aws")


def test_aws_accepts_one_certificate_shared_by_all_nodes_for_any_host_name(
    tmp_path: Path,
) -> None:
    make_aws(tmp_path, node_certs={"n11": (OTHER_HOST, NODE_CERT), "n12": ("x", NODE_CERT)})
    assert resolve(tmp_path, "aws").fingerprint == NODE_FINGERPRINT


def test_aws_uses_the_fingerprint_from_the_connection_instructions_without_node_certs(
    tmp_path: Path,
) -> None:
    make_aws(tmp_path, node_certs={}, instructions_fingerprint=NODE_FINGERPRINT.lower())
    assert resolve(tmp_path, "aws").fingerprint == NODE_FINGERPRINT


def test_aws_refuses_when_instructions_and_certificate_disagree(tmp_path: Path) -> None:
    make_aws(tmp_path, instructions_fingerprint=OTHER_FINGERPRINT)
    with pytest.raises(TargetError, match="disagree"):
        resolve(tmp_path, "aws")


def test_aws_never_falls_back_to_unverified_without_a_fingerprint(tmp_path: Path) -> None:
    make_aws(tmp_path, node_certs={})
    with pytest.raises(TargetError, match="never connects without verifying"):
        resolve(tmp_path, "aws")


def test_aws_with_a_dsn_override_without_fingerprint_stays_verified_by_the_chain(
    tmp_path: Path,
) -> None:
    resolved = resolve(tmp_path, "aws", dsn="other.example:8563")
    assert resolved.dsn == "other.example:8563"
    assert "websocket_sslopt" not in resolved.connect_options("sys", "pw")


def test_a_local_dsn_override_is_still_unverified_only_for_local(tmp_path: Path) -> None:
    assert resolve(tmp_path, "local", dsn="h:1").connect_options("sys", "pw")[
        "websocket_sslopt"
    ] == {"cert_reqs": ssl.CERT_NONE}


# --- password ---


def test_the_password_comes_from_the_secrets_file_of_the_selected_deployment(
    tmp_path: Path,
) -> None:
    make_local(tmp_path)
    make_aws(tmp_path)
    write_json(tmp_path / "default" / "secrets.json", {"dbPassword": "aws-marker"})
    assert resolve(tmp_path, "local").password() == PASSWORD
    assert resolve(tmp_path, "aws").password() == "aws-marker"


def test_the_password_is_read_only_when_asked_for(tmp_path: Path) -> None:
    make_local(tmp_path)
    (tmp_path / "local" / "secrets.json").unlink()
    resolved = resolve(tmp_path, "local")
    with pytest.raises(TargetError, match=r"secrets\.json"):
        resolved.password()


@pytest.mark.parametrize(
    "content", ["not json", "{}", '{"dbPassword": ""}', "[]", '{"dbPassword": 5}']
)
def test_unusable_secrets_fail_with_the_path_and_without_the_content(
    tmp_path: Path, content: str
) -> None:
    make_local(tmp_path)
    (tmp_path / "local" / "secrets.json").write_text(content, encoding="utf-8")
    with pytest.raises(TargetError, match=r"secrets\.json") as error:
        resolve(tmp_path, "local").password()
    assert content not in str(error.value)


def test_the_target_object_never_holds_or_shows_the_password(tmp_path: Path) -> None:
    make_aws(tmp_path)
    resolved = resolve(tmp_path, "aws")
    assert PASSWORD not in repr(resolved)
    assert PASSWORD not in str(resolved.connect_options("sys", "other")["dsn"])


# --- connecting ---


def test_connect_passes_user_password_and_tls_options_to_pyexasol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_aws(tmp_path)
    seen: dict[str, Any] = {}

    def fake_connect(**options: Any) -> str:
        seen.update(options)
        return "connection"

    monkeypatch.setattr("pyexasol.connect", fake_connect)
    assert resolve(tmp_path, "aws").connect() == "connection"
    assert seen["user"] == "sys"
    assert seen["password"] == PASSWORD
    assert seen["dsn"] == f"{AWS_HOST}/{NODE_FINGERPRINT}:8563"


def test_connect_as_another_user_does_not_read_the_sys_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_local(tmp_path)
    (tmp_path / "local" / "secrets.json").unlink()
    monkeypatch.setattr("pyexasol.connect", lambda **options: options)
    options = resolve(tmp_path, "local").connect("CFB_TEST_OWNER", "owner-pw")
    assert (options["user"], options["password"]) == ("CFB_TEST_OWNER", "owner-pw")


def test_a_connect_failure_names_target_and_error_type_but_never_the_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_local(tmp_path)

    def failing(**options: Any) -> Never:
        raise RuntimeError(f"login failed for password {options['password']}")

    monkeypatch.setattr("pyexasol.connect", failing)
    with pytest.raises(TargetError) as error:
        resolve(tmp_path, "local").connect()
    assert PASSWORD not in str(error.value)
    assert "RuntimeError" in str(error.value)
    assert "target local" in str(error.value)
    assert error.value.__suppress_context__
