"""Name resolution seam for the fetch UDF (D21): system resolver first, DNS over HTTPS second.

The UDF sandbox on Exasol Personal nano has an empty `/etc/resolv.conf` and `/etc/hosts`, so
`socket.getaddrinfo` fails for every host name. `install` wraps it: the original is tried first
and its result is returned unchanged; only on `socket.gaierror` does the wrapper ask a DoH
endpoint for A records, cache them for the lifetime of the process, and resolve the address.
`http.client` keeps `server_hostname=<host>`, so SNI and certificate checks stay on the name.

This module is self-contained on purpose: `scripts/dns.py` executes its source as a stand-alone
module inside the dns-check UDF. It is the only module that may touch `socket.getaddrinfo`.
"""

import http.client
import ipaddress
import json
import socket
import ssl
import urllib.parse
from collections.abc import Callable, Mapping
from typing import Any

DEFAULT_DOH_URL = "https://1.1.1.1/dns-query"
DOH_OFF = "off"
DOH_TIMEOUT_SECONDS = 10
DOH_MAX_BODY_BYTES = 1_000_000
DOH_ACCEPT = "application/dns-json"
DNS_CHECK_HINT = "Run `make dns-check` to diagnose name resolution in the UDF sandbox."
A_RECORD_TYPE = 1
NO_ERROR_STATUS = 0
HTTP_OK = 200
DEFAULT_HTTPS_PORT = 443
ORIGINAL_ATTRIBUTE = "_cfbd_original"

AddressInfo = list[tuple[Any, ...]]
GetAddrInfo = Callable[..., AddressInfo]
Transport = Callable[[str, Mapping[str, str], float], tuple[int, bytes]]


class ResolutionError(socket.gaierror):
    """A host name could not be resolved, by the system resolver or by DoH."""

    def __init__(self, host: str, doh_url: str, reason: str) -> None:
        super().__init__(socket.EAI_NONAME, reason)
        self.host = host
        self.doh_url = doh_url
        self.reason = reason

    def __str__(self) -> str:
        return self.reason


def validate_doh_url(value: str) -> str:
    """Return `value` if it is `off` or an https URL whose host is an IP literal, else raise."""
    if value == DOH_OFF:
        return value
    parsed = urllib.parse.urlsplit(value)
    try:
        ipaddress.ip_address(parsed.hostname or "")
    except ValueError:
        raise ValueError(
            f"DOH_URL must be 'off' or an https URL whose host is an IP literal, got {value!r}"
        ) from None
    if parsed.scheme != "https":
        raise ValueError(f"DOH_URL must use https, got {value!r}")
    return value


def https_get(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    """GET `url` over verified TLS and return `(status, body bytes)`."""
    parsed = urllib.parse.urlsplit(url)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    connection = http.client.HTTPSConnection(
        parsed.hostname or "",
        parsed.port or DEFAULT_HTTPS_PORT,
        timeout=timeout,
        context=ssl.create_default_context(),
    )
    try:
        connection.request("GET", target, headers=headers)
        response = connection.getresponse()
        return response.status, response.read(DOH_MAX_BODY_BYTES)
    finally:
        connection.close()


class Resolver:
    """The getaddrinfo wrapper. `original` and `transport` are injectable for tests."""

    def __init__(
        self,
        original: GetAddrInfo,
        doh_url: str = DEFAULT_DOH_URL,
        transport: Transport = https_get,
    ) -> None:
        self.original = original
        self.doh_url = validate_doh_url(doh_url)
        self.transport = transport
        self.cache: dict[str, list[str]] = {}

    def getaddrinfo(
        self, host: Any, port: Any, family: int = 0, type: int = 0, proto: int = 0, flags: int = 0
    ) -> AddressInfo:
        try:
            return self.original(host, port, family, type, proto, flags)
        except socket.gaierror as error:
            if not isinstance(host, str):
                raise
            return self._fallback(host, port, family, type, proto, flags, error)

    def resolve(self, host: str, port: Any) -> list[str]:
        """Sorted unique IPv4 addresses of `host`."""
        infos = self.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        return sorted({info[4][0] for info in infos})

    def _fallback(
        self,
        host: str,
        port: Any,
        family: int,
        type: int,
        proto: int,
        flags: int,
        error: socket.gaierror,
    ) -> AddressInfo:
        if self.doh_url == DOH_OFF:
            raise ResolutionError(
                host,
                self.doh_url,
                f"cannot resolve host '{host}': {_describe(error)}. "
                f"The DoH fallback is off (DOH_URL=off). {DNS_CHECK_HINT}",
            ) from error
        addresses = self._lookup(host, error)
        infos: AddressInfo = []
        for address in addresses:
            infos.extend(self.original(address, port, family, type, proto, flags))
        return infos

    def _lookup(self, host: str, system_error: socket.gaierror) -> list[str]:
        key = host.lower()
        if key not in self.cache:
            try:
                self.cache[key] = self._query(host)
            except (OSError, http.client.HTTPException, ValueError) as error:
                raise ResolutionError(
                    host,
                    self.doh_url,
                    f"cannot resolve host '{host}': the system resolver failed "
                    f"({_describe(system_error)}) and the DoH lookup via {self.doh_url} failed: "
                    f"{_describe(error)}. {DNS_CHECK_HINT}",
                ) from error
        return self.cache[key]

    def _query(self, host: str) -> list[str]:
        separator = "&" if urllib.parse.urlsplit(self.doh_url).query else "?"
        query = urllib.parse.urlencode({"name": host, "type": "A"}, quote_via=urllib.parse.quote)
        status, body = self.transport(
            f"{self.doh_url}{separator}{query}",
            {"Accept": DOH_ACCEPT},
            DOH_TIMEOUT_SECONDS,
        )
        if status != HTTP_OK:
            raise ValueError(f"DoH endpoint answered HTTP {status}")
        return _a_records(host, body)


def _a_records(host: str, body: bytes) -> list[str]:
    try:
        answer = json.loads(body)
    except ValueError:
        raise ValueError("DoH endpoint did not return JSON") from None
    if not isinstance(answer, dict):
        raise ValueError("DoH endpoint returned JSON that is not an object")
    status = answer.get("Status", NO_ERROR_STATUS)
    if status != NO_ERROR_STATUS:
        raise ValueError(f"DoH endpoint answered DNS status {status} for '{host}'")
    addresses: list[str] = []
    for record in answer.get("Answer") or []:
        if isinstance(record, dict) and record.get("type") == A_RECORD_TYPE:
            address = _ipv4(record.get("data"))
            if address is not None and address not in addresses:
                addresses.append(address)
    if not addresses:
        raise ValueError(f"DoH endpoint returned no A record for '{host}'")
    return addresses


def _ipv4(value: Any) -> str | None:
    try:
        return str(ipaddress.IPv4Address(value))
    except ValueError:
        return None


def _describe(error: BaseException) -> str:
    message = str(error).strip()
    return f"{type(error).__name__}: {message}" if message else type(error).__name__


_active: Resolver | None = None


def install(doh_url: str = DEFAULT_DOH_URL) -> None:
    """Wrap `socket.getaddrinfo` for this process. Calling it again only changes `doh_url`."""
    global _active
    original = getattr(socket.getaddrinfo, ORIGINAL_ATTRIBUTE, socket.getaddrinfo)
    resolver = Resolver(original, doh_url)
    if _active is not None:
        resolver.cache = _active.cache
    _active = resolver
    socket.getaddrinfo = _wrapper_for(resolver, original)


def uninstall() -> None:
    """Restore the original `socket.getaddrinfo`."""
    global _active
    socket.getaddrinfo = getattr(socket.getaddrinfo, ORIGINAL_ATTRIBUTE, socket.getaddrinfo)
    _active = None


def resolve(host: str, port: Any) -> list[str]:
    """Sorted unique IPv4 addresses of `host`, through the installed resolver if there is one."""
    if _active is not None:
        return _active.resolve(host, port)
    return Resolver(socket.getaddrinfo, DOH_OFF).resolve(host, port)


def _wrapper_for(resolver: Resolver, original: GetAddrInfo) -> GetAddrInfo:
    def getaddrinfo(
        host: Any, port: Any, family: int = 0, type: int = 0, proto: int = 0, flags: int = 0
    ) -> AddressInfo:
        return resolver.getaddrinfo(host, port, family, type, proto, flags)

    setattr(getaddrinfo, ORIGINAL_ATTRIBUTE, original)
    return getaddrinfo
