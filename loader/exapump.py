"""The environment and arguments for exapump child processes, from a resolved target.

`exapump_env(target)` returns what a `subprocess` call needs to run `exapump export`, `upload` or
`sql` against that deployment:

- `env`: a copy of the process environment plus `EXAPUMP_DSN=exasol://user:password@host:port`.
  The DSN carries the sys password, so it exists only in this copy, which is handed to one child
  process. It is never put on a command line, written to a file or logged, and `os.environ` is
  not changed.
- `args`: `--certificate-fingerprint <hex>` (SHA-256 of the server's DER certificate), so the
  child pins the server like pyexasol does. A pinned target (aws) passes its recorded
  fingerprint. The unverified local target (loopback only) passes the fingerprint of the
  certificate the server presents right now. A target on chain verification passes nothing.

Target resolution, the password file and AWS certificate pinning belong to `vs/host/target.py`;
this module adds nothing but the exapump spelling of them. The file format of a snapshot is not
decided here.
"""

from __future__ import annotations

import ipaddress
import os
import ssl
from collections.abc import Callable, Mapping
from urllib.parse import quote

from vs.host.target import (
    TLS_CHAIN,
    TLS_PINNED,
    TLS_UNVERIFIED_LOCAL,
    Target,
    fingerprint_of,
)

DSN_VARIABLE = "EXAPUMP_DSN"
FINGERPRINT_FLAG = "--certificate-fingerprint"
CERTIFICATE_TIMEOUT_SECONDS = 10.0
LOCALHOST = "localhost"

CertificateReader = Callable[[str, int], str]


class ExapumpError(RuntimeError):
    """The exapump environment cannot be built safely; the message never contains a secret."""


def server_certificate(host: str, port: int) -> str:
    """The PEM certificate the server at `host:port` presents (a TLS handshake, no login)."""
    return ssl.get_server_certificate((host, port), timeout=CERTIFICATE_TIMEOUT_SECONDS)


def exapump_env(
    target: Target,
    *,
    environ: Mapping[str, str] | None = None,
    fetch_certificate: CertificateReader = server_certificate,
) -> tuple[dict[str, str], list[str]]:
    """`(env, args)` for one exapump child process against `target`; see the module docstring."""
    base = os.environ if environ is None else environ
    env = {**base, DSN_VARIABLE: _dsn(target)}
    return env, _pin_arguments(target, fetch_certificate)


def is_loopback(host: str) -> bool:
    """True for `localhost` and any loopback IP address."""
    if host.lower() == LOCALHOST:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _dsn(target: Target) -> str:
    user = quote(target.user, safe="")
    password = quote(target.password(), safe="")
    host = f"[{target.host}]" if ":" in target.host else target.host
    return f"exasol://{user}:{password}@{host}:{target.port}"


def _pin_arguments(target: Target, fetch_certificate: CertificateReader) -> list[str]:
    if target.tls == TLS_CHAIN:
        return []
    if target.tls == TLS_PINNED:
        if not target.fingerprint:
            raise ExapumpError(
                f"target {target.name} is pinned but has no certificate fingerprint; "
                "refusing to run exapump unverified"
            )
        return [FINGERPRINT_FLAG, target.fingerprint.lower()]
    if target.tls == TLS_UNVERIFIED_LOCAL:
        return [FINGERPRINT_FLAG, _loopback_fingerprint(target, fetch_certificate)]
    raise ExapumpError(f"target {target.name} has an unknown TLS mode {target.tls!r}")


def _loopback_fingerprint(target: Target, fetch_certificate: CertificateReader) -> str:
    if not is_loopback(target.host):
        raise ExapumpError(
            f"target {target.name} is unverified but its host {target.host} is not a loopback "
            "address; the password is not sent to another host without certificate verification"
        )
    try:
        pem = fetch_certificate(target.host, target.port)
    except Exception as error:
        raise ExapumpError(
            f"cannot read the server certificate of {target.host}:{target.port}: "
            f"{type(error).__name__}"
        ) from None
    return fingerprint_of(pem).lower()
