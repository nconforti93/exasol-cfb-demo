"""Which Exasol Personal deployment the host scripts and the integration harness talk to.

`TARGET=local|aws` selects the deployment directory below `~/.exasol/personal/deployments`:
`local` is `local/`, `aws` is `default/`. Everything else is read from that directory's files at
resolve time, and nothing secret is stored on the `Target`:

- host and port: `deployment.json` (`connection.host`, `connection.dbPort`, `connection.username`);
- password: `secrets.json` (`dbPassword`), read only when a connection is made;
- TLS: `local` skips certificate validation (loopback, self-signed, as before). `aws` pins the
  SHA-256 fingerprint of the server certificate, taken from `deployment.json`
  (`nodes.*.tlsCert`, the certificate of the node named by `connection.host`) and cross-checked
  with the `Certificate Fingerprint:` line of `connection-instructions.txt`. It is passed to
  pyexasol in the DSN (`host/FINGERPRINT:port`). With no fingerprint available, resolving `aws`
  fails: it never connects unverified.

`EXASOL_DSN` (an explicit argument, the environment, then `.env`) overrides host, port and the
fingerprint. An override never loosens TLS: only `local` skips validation, and `aws` with an
override that carries no fingerprint stays on pyexasol's default chain verification.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DOTENV_PATH = ROOT / ".env"
DEPLOYMENTS_ROOT = Path.home() / ".exasol" / "personal" / "deployments"
TARGET_VARIABLE = "TARGET"
DSN_VARIABLE = "EXASOL_DSN"
DEFAULT_TARGET = "local"
DEPLOYMENT_DIRECTORIES = {"local": "local", "aws": "default"}
DEFAULT_PORT = 8563
DEFAULT_USER = "sys"
PASSWORD_KEY = "dbPassword"

TLS_UNVERIFIED_LOCAL = "unverified (local)"
TLS_PINNED = "pinned fingerprint"
TLS_CHAIN = "chain verification"

_DSN = re.compile(
    r"(?P<host>[^:/]+)(?:/(?P<fingerprint>[0-9A-Fa-f]+|nocertcheck))?(?::(?P<port>\d+))?"
)
_INSTRUCTIONS_FINGERPRINT = re.compile(r"Certificate Fingerprint:\s*([0-9A-Fa-f]{64})\b")


class TargetError(RuntimeError):
    """The target cannot be resolved or reached; the message never contains a secret."""


@dataclass(frozen=True)
class Target:
    """One resolved deployment: where to connect, as whom, and how to verify the server."""

    name: str
    host: str
    port: int
    user: str
    deployment_dir: Path
    tls: str
    fingerprint: str | None = None
    dsn_override: str | None = None

    @property
    def dsn(self) -> str:
        """`host[/FINGERPRINT]:port`, the form pyexasol parses."""
        if self.dsn_override:
            return self.dsn_override
        pin = f"/{self.fingerprint}" if self.fingerprint else ""
        return f"{self.host}{pin}:{self.port}"

    def password(self) -> str:
        """The sys password of this deployment; read on every call, never cached."""
        path = self.deployment_dir / "secrets.json"
        data = _read_json(path, "secrets file")
        password = data.get(PASSWORD_KEY)
        if not isinstance(password, str) or not password:
            raise TargetError(f"secrets file {path} has no usable {PASSWORD_KEY!r} entry")
        return password

    def connect_options(
        self, user: str | None = None, password: str | None = None
    ) -> dict[str, Any]:
        """Keyword arguments for `pyexasol.connect`; reads the sys password if none is given."""
        options: dict[str, Any] = {
            "dsn": self.dsn,
            "user": user or self.user,
            "password": self.password() if password is None else password,
        }
        if self.tls == TLS_UNVERIFIED_LOCAL:
            options["websocket_sslopt"] = {"cert_reqs": ssl.CERT_NONE}
        return options

    def connect(self, user: str | None = None, password: str | None = None) -> Any:
        """A pyexasol connection (sys by default). Failures carry no driver text."""
        import pyexasol

        options = self.connect_options(user, password)
        try:
            return pyexasol.connect(**options)
        except Exception as error:
            raise TargetError(
                f"cannot connect to Exasol at {self.describe()}: {type(error).__name__}"
            ) from None

    def describe(self) -> str:
        """`<dsn> (target <name>[, EXASOL_DSN override])`, for messages."""
        override = f", {DSN_VARIABLE} override" if self.dsn_override else ""
        return f"{self.dsn} (target {self.name}{override})"


def resolve(
    name: str | None = None,
    *,
    dsn: str | None = None,
    environ: Mapping[str, str] | None = None,
    dotenv_path: Path = DOTENV_PATH,
    deployments_root: Path = DEPLOYMENTS_ROOT,
) -> Target:
    """The target `name`, else `$TARGET`, else `local`; `dsn`, `$EXASOL_DSN`, `.env` override."""
    environ = os.environ if environ is None else environ
    selected = name or environ.get(TARGET_VARIABLE) or DEFAULT_TARGET
    if selected not in DEPLOYMENT_DIRECTORIES:
        choices = ", ".join(DEPLOYMENT_DIRECTORIES)
        raise TargetError(f"unknown target {selected!r}; choose one of: {choices}")
    directory = deployments_root / DEPLOYMENT_DIRECTORIES[selected]
    override = dsn or environ.get(DSN_VARIABLE) or parse_dotenv(dotenv_path).get(DSN_VARIABLE)
    if override:
        return _from_override(selected, directory, override)
    return _from_deployment(selected, directory)


def parse_dsn(dsn: str) -> tuple[str, int]:
    """Split `host[/fingerprint][:port]` into host and port."""
    match = _DSN.fullmatch(dsn.strip())
    if match is None:
        _, _, port_text = dsn.partition(":")
        if port_text and not port_text.isdigit():
            raise ValueError(f"invalid port in DSN {dsn!r}")
        raise ValueError(f"cannot parse DSN {dsn!r}")
    port = match.group("port")
    return match.group("host"), int(port) if port else DEFAULT_PORT


def fingerprint_of(pem_certificate: str) -> str:
    """Upper-case SHA-256 of the DER form of a PEM certificate, as pyexasol compares it."""
    try:
        der = ssl.PEM_cert_to_DER_cert(pem_certificate)
    except ValueError:
        raise TargetError("a node certificate in deployment.json is not valid PEM") from None
    return hashlib.sha256(der).hexdigest().upper()


def parse_dotenv(path: Path) -> dict[str, str]:
    """`KEY=value` lines of a `.env` file; empty when the file does not exist."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _from_override(name: str, directory: Path, override: str) -> Target:
    try:
        host, port = parse_dsn(override)
    except ValueError as error:
        raise TargetError(str(error)) from None
    tls = TLS_UNVERIFIED_LOCAL if name == "local" else TLS_CHAIN
    return Target(name, host, port, DEFAULT_USER, directory, tls, None, override.strip())


def _from_deployment(name: str, directory: Path) -> Target:
    path = directory / "deployment.json"
    data = _read_json(path, "deployment file")
    connection = data.get("connection")
    host = connection.get("host") if isinstance(connection, dict) else None
    port = connection.get("dbPort") if isinstance(connection, dict) else None
    if not isinstance(host, str) or not host or not isinstance(port, int):
        raise TargetError(f"{path} has no usable connection.host and connection.dbPort")
    user = connection.get("username") if isinstance(connection, dict) else None
    user = user if isinstance(user, str) and user else DEFAULT_USER
    if name == "local":
        return Target(name, host, port, user, directory, TLS_UNVERIFIED_LOCAL)
    fingerprint = _pinned_fingerprint(directory, data, host)
    return Target(name, host, port, user, directory, TLS_PINNED, fingerprint)


def _pinned_fingerprint(directory: Path, deployment: Mapping[str, Any], host: str) -> str:
    from_certificate = _certificate_fingerprint(deployment, host)
    from_instructions = _instructions_fingerprint(directory / "connection-instructions.txt")
    if from_certificate and from_instructions and from_certificate != from_instructions:
        raise TargetError(
            f"the node certificate in deployment.json and the fingerprint in "
            f"connection-instructions.txt disagree in {directory}; refusing to pick one"
        )
    pinned = from_certificate or from_instructions
    if pinned is None:
        raise TargetError(
            f"no certificate fingerprint for host {host} in {directory} "
            "(deployment.json nodes.*.tlsCert, connection-instructions.txt); "
            "the aws target never connects without verifying the server"
        )
    return pinned


def _certificate_fingerprint(deployment: Mapping[str, Any], host: str) -> str | None:
    nodes = deployment.get("nodes")
    if not isinstance(nodes, dict):
        return None
    certificates = [
        (node, node.get("tlsCert")) for node in nodes.values() if isinstance(node, dict)
    ]
    pems = [(node, pem) for node, pem in certificates if isinstance(pem, str) and pem]
    named = [pem for node, pem in pems if host in (node.get("dnsName"), node.get("publicIp"))]
    candidates = {fingerprint_of(pem) for pem in (named or [pem for _, pem in pems])}
    return candidates.pop() if len(candidates) == 1 else None


def _instructions_fingerprint(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _INSTRUCTIONS_FINGERPRINT.search(text)
    return match.group(1).upper() if match else None


def _read_json(path: Path, what: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise TargetError(f"{what} not found: {path}") from None
    except (OSError, ValueError):
        raise TargetError(f"{what} is unreadable or not JSON: {path}") from None
    if not isinstance(data, dict):
        raise TargetError(f"{what} is not a JSON object: {path}")
    return data
