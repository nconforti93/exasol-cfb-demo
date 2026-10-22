"""Probe body of the dns-check UDF. Embedded verbatim into the UDF script: stdlib only.

No `from __future__` import and no annotations that need Python 3.10 at runtime, because the
file is concatenated into the script text and has to run on the SLC's Python.
"""

import socket
import ssl
from collections.abc import Callable
from typing import Any

CONNECT_TIMEOUT_SECONDS = 10
RESOLV_CONF_PATH = "/etc/resolv.conf"
HOSTS_PATH = "/etc/hosts"


def read_text_file(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def describe_text_file(read_text: Callable[[str], str], path: str) -> str:
    try:
        text = read_text(path)
    except OSError:
        return "missing"
    active = [
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    if not active:
        return "empty"
    return f"{len(active)} active line(s)"


def common_name(cert: dict[str, Any]) -> Any:
    for rdn in cert.get("subject", ()):
        for key, value in rdn:
            if key == "commonName":
                return value
    return None


def tls_handshake(sock: socket.socket, host: str) -> dict[str, Any]:
    context = ssl.create_default_context()
    with context.wrap_socket(sock, server_hostname=host) as tls:
        cert = tls.getpeercert() or {}
        return {
            "verified": True,
            "version": tls.version(),
            "subject_cn": common_name(cert),
            "not_after": cert.get("notAfter"),
        }


def new_report(
    host: str, port: int, doh_url: str, read_text: Callable[[str], str]
) -> dict[str, Any]:
    return {
        "host": host,
        "port": port,
        "doh_url": doh_url,
        "ok": False,
        "stage": None,
        "error": None,
        "path": None,
        "system_ok": None,
        "system_error": None,
        "addresses": [],
        "resolv_conf": describe_text_file(read_text, RESOLV_CONF_PATH),
        "hosts": describe_text_file(read_text, HOSTS_PATH),
        "tls": None,
    }


def fail(report: dict[str, Any], stage: str, error: BaseException) -> dict[str, Any]:
    report["stage"] = stage
    report["error"] = f"{type(error).__name__}: {error}"
    return report


def probe(
    host: str,
    port: int,
    doh_url: str,
    resolver: Any,
    getaddrinfo: Callable[..., Any] = socket.getaddrinfo,
    connect: Callable[..., Any] = socket.create_connection,
    handshake: Callable[[Any, str], dict[str, Any]] = tls_handshake,
    read_text: Callable[[str], str] = read_text_file,
) -> dict[str, Any]:
    """Resolve host, open a TLS connection and verify the certificate. Sends no HTTP request.

    `getaddrinfo` defaults to the original function, bound before `resolver.install` wraps it,
    so the first lookup reveals whether the system resolver works on its own.
    """
    report = new_report(host, port, doh_url, read_text)
    try:
        getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        report["system_ok"] = True
    except socket.gaierror as error:
        report["system_ok"] = False
        report["system_error"] = str(error)
    try:
        resolver.install(doh_url)
        report["addresses"] = list(resolver.resolve(host, port))
    except Exception as error:
        return fail(report, "resolve", error)
    report["path"] = "system" if report["system_ok"] else "doh"
    try:
        sock = connect((host, port), timeout=CONNECT_TIMEOUT_SECONDS)
    except OSError as error:
        return fail(report, "connect", error)
    try:
        report["tls"] = handshake(sock, host)
    except (ssl.SSLError, OSError) as error:
        sock.close()
        return fail(report, "tls", error)
    report["ok"] = True
    return report
