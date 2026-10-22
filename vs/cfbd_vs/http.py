"""HTTP client for the CFBD API: authenticated GET, retries, error mapping and key redaction.

Transport, clock and jitter are injected, so retry and redaction behavior is tested without a
network. Name resolution is not done here: the transport's `socket` calls go through the
resolver seam (`resolver.py`), and a `socket.gaierror` coming out of them becomes a
non-retried `NameResolutionError`.

The module is named `http` inside the `cfbd_vs` package. A bundler must never register it under
the bare name `http`, which would shadow the standard library module of the same name.
"""

import gzip
import http.client
import json
import random
import re
import socket
import ssl
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

MAX_ATTEMPTS = 4
ATTEMPT_TIMEOUT_SECONDS = 30.0
BACKOFF_BASE_SECONDS = 1.0
JITTER_FRACTION = 0.2
RETRY_AFTER_LIMIT_SECONDS = 30.0
DETAIL_LIMIT = 300
REDACTED = "[REDACTED]"
DNS_CHECK_HINT = "Run `make dns-check` to diagnose name resolution in the UDF sandbox."
USER_AGENT = "cfbd-vs/1"
HTTP_SCHEMES = ("http", "https")
STATUS_UNAUTHORIZED = 401
STATUS_FORBIDDEN = 403
STATUS_TOO_MANY_REQUESTS = 429
STATUS_SERVER_ERROR = 500
STATUS_SUCCESS = range(200, 300)
DETAIL_FIELDS = ("message", "error", "detail", "title")

_SLICE_KEY = re.compile(r"/[\x21-\x7e]*\Z")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


class FetchError(Exception):
    """A request failed. Messages name the slice key and never contain the key."""

    def __init__(self, message: str, slice_key: str, status: int | None, attempts: int) -> None:
        super().__init__(message)
        self.slice_key = slice_key
        self.status = status
        self.attempts = attempts


class AccessError(FetchError):
    """HTTP 401 or 403: the key is rejected or the endpoint needs a higher tier."""


class ClientError(FetchError):
    """Any other non-retryable HTTP status."""


class RetriesExhausted(FetchError):
    """A retryable failure persisted through every attempt (or Retry-After was too long)."""


class NameResolutionError(FetchError):
    """The host name could not be resolved. Never retried."""


class TlsError(FetchError):
    """The server's certificate did not verify. Never retried."""


class RequestCancelled(Exception):
    """The caller's cancel signal was set, so the next attempt was not sent. Not a `FetchError`:
    the request did not fail, the caller stopped it because something else failed."""


@dataclass(frozen=True)
class TransportResponse:
    """What a transport returns for one HTTP exchange. Header names are lower case."""

    status: int
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[[str, str, Mapping[str, str], float], TransportResponse]


class Clock(Protocol):
    """Time source: monotonic seconds, sleeping, and the current UTC time."""

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...

    def utcnow(self) -> datetime: ...


class SystemClock:
    """The real clock."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def utcnow(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class FetchResult:
    """A successful response: body text with the key's forms redacted, plus the accounting."""

    slice_key: str
    status: int
    body: str
    attempts: int
    ms: int
    requested_at: datetime


def redact_key(text: str, key: str) -> str:
    """Replace the key in its raw, JSON-escaped and percent-encoded forms, and nothing else.

    For response bodies: they are data, so a phrase such as "standard bearer Smith" stays.
    """
    if key:
        for form in _key_forms(key):
            text = text.replace(form, REDACTED)
    return text


def redact(text: str, key: str) -> str:
    """`redact_key`, plus any `Bearer <token>`: for error text, which can echo request headers."""
    return _BEARER.sub(f"Bearer {REDACTED}", redact_key(text, key))


def http_transport(
    address: str, target: str, headers: Mapping[str, str], timeout: float
) -> TransportResponse:
    """GET `<address base path><target>`. TLS server name and Host stay on the address host."""
    parts = urllib.parse.urlsplit(address)
    if parts.scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            parts.hostname or "", parts.port, timeout=timeout, context=ssl.create_default_context()
        )
    else:
        connection = http.client.HTTPConnection(parts.hostname or "", parts.port, timeout=timeout)
    try:
        connection.request("GET", parts.path.rstrip("/") + target, headers=dict(headers))
        response = connection.getresponse()
        body = response.read()
        reply_headers = {name.lower(): value for name, value in response.getheaders()}
    finally:
        connection.close()
    if reply_headers.get("content-encoding", "").lower() == "gzip":
        body = gzip.decompress(body)
    return TransportResponse(status=response.status, headers=reply_headers, body=body)


class CfbdClient:
    """Issues `GET <address><slice key>` with the key as bearer token, with retries."""

    def __init__(
        self,
        address: str,
        key: str,
        *,
        transport: Transport = http_transport,
        clock: Clock | None = None,
        uniform: Callable[[float, float], float] = random.uniform,
        max_attempts: int = MAX_ATTEMPTS,
        timeout: float = ATTEMPT_TIMEOUT_SECONDS,
    ) -> None:
        parts = urllib.parse.urlsplit(address)
        if parts.scheme not in HTTP_SCHEMES or not parts.hostname:
            raise ValueError(f"connection address {address!r} must be an http(s) URL with a host")
        if not key:
            raise ValueError("the connection holds no key")
        self._address = address
        self._host = parts.hostname
        self._key = key
        self._transport = transport
        self._clock = clock or SystemClock()
        self._uniform = uniform
        self._max_attempts = max_attempts
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"CfbdClient(address={self._address!r})"

    def get(self, slice_key: str, *, cancel: threading.Event | None = None) -> FetchResult:
        """Fetch one slice. Raises a `FetchError` subclass when it cannot succeed.

        `cancel` is checked before every attempt and before every retry wait: once it is set, no
        further attempt is sent and `RequestCancelled` is raised. An attempt already sent runs to
        its end, and a final failure it yields is still raised as such.
        """
        if not _SLICE_KEY.match(slice_key):
            raise ValueError(f"slice key {slice_key!r} must be a path starting with '/'")
        started = self._clock.monotonic()
        requested_at = self._clock.utcnow()
        attempts = 0
        while True:
            _check_cancel(slice_key, cancel)
            attempts += 1
            failure = self._attempt(slice_key, attempts)
            if isinstance(failure, TransportResponse):
                elapsed = self._clock.monotonic() - started
                return FetchResult(
                    slice_key=slice_key,
                    status=failure.status,
                    body=redact_key(_decode(failure.body), self._key),
                    attempts=attempts,
                    ms=round(elapsed * 1000),
                    requested_at=requested_at,
                )
            self._wait_or_raise(slice_key, attempts, failure, cancel)

    def _attempt(self, slice_key: str, attempts: int) -> "TransportResponse | _Retryable":
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT,
        }
        try:
            response = self._transport(self._address, slice_key, headers, self._timeout)
        except socket.gaierror as error:
            raise self._resolution_error(slice_key, attempts, error) from None
        except ssl.SSLCertVerificationError as error:
            raise TlsError(
                self._redact(f"{slice_key}: TLS certificate verification failed: {error}"),
                slice_key,
                None,
                attempts,
            ) from None
        except (OSError, http.client.HTTPException) as error:
            reason = f"{type(error).__name__}: {error}"
            return _Retryable(status=None, detail=self._redact(reason), retry_after=None)
        if response.status in STATUS_SUCCESS:
            return response
        self._raise_if_final(slice_key, attempts, response)
        retry_after = {name.lower(): value for name, value in response.headers.items()}.get(
            "retry-after"
        )
        return _Retryable(
            status=response.status,
            detail=self._detail(response.body),
            retry_after=self._retry_after(retry_after),
            retry_after_text=retry_after,
        )

    def _raise_if_final(self, slice_key: str, attempts: int, response: TransportResponse) -> None:
        status = response.status
        if status == STATUS_TOO_MANY_REQUESTS or status >= STATUS_SERVER_ERROR:
            return
        detail = self._detail(response.body)
        if status in (STATUS_UNAUTHORIZED, STATUS_FORBIDDEN):
            raise AccessError(
                f"{slice_key}: HTTP {status}: the key has no access to this endpoint (the API "
                f"refused the request). Check that the connection holds a valid key and that "
                f"its tier or feature includes this endpoint. "
                f"API message: {detail}",
                slice_key,
                status,
                attempts,
            )
        raise ClientError(f"{slice_key}: HTTP {status}: {detail}", slice_key, status, attempts)

    def _wait_or_raise(
        self,
        slice_key: str,
        attempts: int,
        failure: "_Retryable",
        cancel: threading.Event | None,
    ) -> None:
        if failure.retry_after is not None and failure.retry_after > RETRY_AFTER_LIMIT_SECONDS:
            raise self._exhausted(
                slice_key,
                attempts,
                failure,
                f"Retry-After {failure.retry_after_text} exceeds the "
                f"{RETRY_AFTER_LIMIT_SECONDS:g} s limit, not waiting",
            )
        if attempts >= self._max_attempts:
            raise self._exhausted(slice_key, attempts, failure, None)
        _check_cancel(slice_key, cancel)
        self._clock.sleep(self._wait_seconds(attempts, failure.retry_after))

    def _wait_seconds(self, attempts: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return retry_after
        jitter = self._uniform(-JITTER_FRACTION, JITTER_FRACTION)
        return BACKOFF_BASE_SECONDS * 2.0 ** (attempts - 1) * (1 + jitter)

    def _exhausted(
        self, slice_key: str, attempts: int, failure: "_Retryable", note: str | None
    ) -> RetriesExhausted:
        what = f"HTTP {failure.status}" if failure.status is not None else "request failed"
        count = f"after {attempts} attempt{'s' if attempts != 1 else ''}"
        text = f"{slice_key}: {what} {count}: {failure.detail}"
        if note:
            text += f" ({note})"
        return RetriesExhausted(self._redact(text), slice_key, failure.status, attempts)

    def _resolution_error(
        self, slice_key: str, attempts: int, error: socket.gaierror
    ) -> NameResolutionError:
        reason = str(error)
        if "make dns-check" not in reason:
            reason = f"{reason}. {DNS_CHECK_HINT}"
        text = f"{slice_key}: name resolution failed for host '{self._host}': {reason}"
        return NameResolutionError(self._redact(text), slice_key, None, attempts)

    def _detail(self, body: bytes) -> str:
        text = self._redact(_decode(body)).strip()
        if not text:
            return "no response body"
        try:
            parsed: Any = json.loads(text)
        except ValueError:
            return text[:DETAIL_LIMIT]
        if isinstance(parsed, dict):
            for name in DETAIL_FIELDS:
                if isinstance(parsed.get(name), str):
                    return str(parsed[name])[:DETAIL_LIMIT]
        return text[:DETAIL_LIMIT]

    def _retry_after(self, value: str | None) -> float | None:
        if value is None:
            return None
        try:
            return max(float(value), 0.0)
        except ValueError:
            pass
        try:
            moment = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        remaining: float = (moment - self._clock.utcnow()).total_seconds()
        return max(remaining, 0.0)

    def _redact(self, text: str) -> str:
        return redact(text, self._key)


@dataclass(frozen=True)
class _Retryable:
    status: int | None
    detail: str
    retry_after: float | None
    retry_after_text: str | None = None


def _check_cancel(slice_key: str, cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise RequestCancelled(f"{slice_key}: not sent, the fetch was cancelled")


def _decode(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")


def _key_forms(key: str) -> list[str]:
    forms = [
        key,
        json.dumps(key)[1:-1],
        json.dumps(key, ensure_ascii=False)[1:-1],
        urllib.parse.quote(key, safe=""),
    ]
    return sorted(set(forms), key=len, reverse=True)
