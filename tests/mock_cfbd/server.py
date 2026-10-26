"""Mock CFBD API server with scripted failures, a request log and a mock DoH endpoint.

Standard library only, so it also runs inside the sidecar container (see `sidecar.py`).

Three listeners, all on 127.0.0.1 by default:

* API (`--port`): serves `GET <path>?<query>` from an injected response provider.
* DoH (`--doh-port`, needs `--cert`/`--key`): HTTPS with a self-signed certificate. It logs every
  TCP connection, the TLS outcome and every query, so tests can prove that no DoH connection was
  made (system resolver used) or that a certificate failed verification.
* Admin (`--admin-port`): JSON over HTTP to script behaviours and read the logs.

Provider contract: `provider(path, query) -> result`, with `query` mapping each parameter name to
its list of values. A result is `None` (404), `bytes`/`str` (200, sent as is), `(status, body)`, or
anything JSON-serialisable (200). A `KeyError` from the provider (`fake.UnknownEndpoint`) is a
404, any other exception a 500 that names it. The provider is named `module:callable` on the
command line.

Admin API (JSON bodies and answers):

* `POST /_admin/script`  `{path, query?, times?, status?, body?, headers?, retry_after?,
  delay_ms?, oversize_bytes?}` adds a rule and answers `{id}`. `path` is exact, or a prefix when it
  ends in `*`. `query` is a subset the request must contain. The first live rule that matches
  applies, and `times` limits how often it does (default: always). A rule with only `delay_ms`
  delays and then serves the normal response; any of `status`, `body` or `oversize_bytes`
  replaces it.
* `GET /_admin/log` and `GET /_admin/doh-log`: `{entries}`.
* `GET /_admin/hits?path=&query=`: `{count}` of API requests (scripted ones included).
* `POST /_admin/config` `{echo_key}`: key-echo mode reflects the bearer token in an `X-Echo-Key`
  header and in error bodies, so tests can check that the fetch UDF redacts it.
* `POST /_admin/doh-answers` `{host: [ipv4, ...]}`: the A records the DoH endpoint serves.
* `POST /_admin/info` `{mode, base_used?, fields?}` and `GET /_admin/info`: how `/info` answers.
  The body starts from the provider's `/info` answer (else the built-in one). `fields` overrides
  `tierName`, `monthlyLimit`, `usedCalls`, `remainingCalls`, `resetAt`, `sharedPool`,
  `patronLevel`, `products` or `features`; `remainingCalls` is `monthlyLimit - usedCalls` unless
  given. `mode` `static` serves that. `mode` `counting` computes `usedCalls` as `base_used` plus
  the API requests since the last reset, `/info` excluded (the API does not charge it). Scripted
  failures count too, because retries count against the quota. `GET` answers
  `{mode, base_used, fields, data_requests}` (`mode` is `default` until one is set).
* `POST /_admin/reset`: clears the logs, the rules and the modes, `/info` included (DoH answers
  stay).
"""

import argparse
import http.client
import http.server
import importlib
import json
import signal
import ssl
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Any

LOOPBACK = "127.0.0.1"
ADMIN_PREFIX = "/_admin/"
ECHO_HEADER = "X-Echo-Key"
JSON_TYPE = "application/json"
DOH_PATH = "/dns-query"
DNS_NXDOMAIN = 3
DNS_A_TYPE = "A"
DNS_A_NUMBER = 1
DNS_TTL = 60
TLS_TIMEOUT_SECONDS = 10
POLL_SECONDS = 0.05
ADMIN_TIMEOUT_SECONDS = 30
DEFAULT_PAD = b"x" * 1024
RULE_FIELDS = frozenset(
    {
        "path",
        "query",
        "times",
        "status",
        "body",
        "headers",
        "retry_after",
        "delay_ms",
        "oversize_bytes",
    }
)
INFO_RESPONSE = {
    "tierName": "Mock",
    "monthlyLimit": 1000,
    "remainingCalls": 1000,
    "usedCalls": 0,
    "resetAt": "2099-01-01T00:00:00Z",
    "features": {
        "adjustedMetrics": False,
        "weather": False,
        "scoreboard": False,
        "livePlayByPlay": False,
        "graphQl": False,
    },
}

INFO_PATH = "/info"
INFO_MODES = ("static", "counting")
INFO_NUMBER_FIELDS = ("monthlyLimit", "remainingCalls", "usedCalls")
INFO_FIELDS = frozenset(
    {
        *INFO_NUMBER_FIELDS,
        "tierName",
        "resetAt",
        "sharedPool",
        "patronLevel",
        "products",
        "features",
    }
)
INFO_SPEC_KEYS = frozenset({"mode", "base_used", "fields"})

Query = dict[str, list[str]]
Provider = Callable[[str, Query], Any]


class AdminError(Exception):
    """An admin call failed with an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"admin {status}: {message}")
        self.status = status


@dataclass
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes
    delay_seconds: float = 0.0


@dataclass
class Rule:
    id: int
    path: str
    query: dict[str, str]
    times: int | None
    status: int | None
    body: bytes | None
    headers: dict[str, str]
    retry_after: float | None
    delay_ms: int
    oversize_bytes: int | None

    def matches(self, path: str, query: Query) -> bool:
        if self.times is not None and self.times <= 0:
            return False
        if self.path.endswith("*"):
            if not path.startswith(self.path[:-1]):
                return False
        elif path != self.path:
            return False
        return all(value in query.get(name, []) for name, value in self.query.items())

    @property
    def replaces_response(self) -> bool:
        return self.status is not None or self.body is not None or self.oversize_bytes is not None


def parse_rule(rule_id: int, spec: Mapping[str, Any]) -> Rule:
    """Validate a rule spec from the admin API and build the rule."""
    unknown = set(spec) - RULE_FIELDS
    if unknown:
        raise ValueError(f"unknown rule fields: {sorted(unknown)}")
    path = spec.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("rule needs a 'path' that starts with '/'")
    status = spec.get("status")
    if status is not None and not (isinstance(status, int) and 100 <= status <= 599):
        raise ValueError(f"invalid status {status!r}")
    times = spec.get("times")
    if times is not None and not (isinstance(times, int) and times >= 0):
        raise ValueError(f"invalid times {times!r}")
    retry_after = spec.get("retry_after")
    if retry_after is not None and not isinstance(retry_after, int | float):
        raise ValueError(f"invalid retry_after {retry_after!r}")
    delay_ms = spec.get("delay_ms", 0)
    if not isinstance(delay_ms, int) or delay_ms < 0:
        raise ValueError(f"invalid delay_ms {delay_ms!r}")
    oversize = spec.get("oversize_bytes")
    if oversize is not None and not (isinstance(oversize, int) and oversize > 0):
        raise ValueError(f"invalid oversize_bytes {oversize!r}")
    body = spec.get("body")
    return Rule(
        id=rule_id,
        path=path,
        query={str(k): str(v) for k, v in (spec.get("query") or {}).items()},
        times=times,
        status=status,
        body=None if body is None else to_bytes(body),
        headers={str(k): str(v) for k, v in (spec.get("headers") or {}).items()},
        retry_after=retry_after,
        delay_ms=delay_ms,
        oversize_bytes=oversize,
    )


def to_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode()
    return json.dumps(value).encode()


def oversized_body(size: int) -> bytes:
    """A valid JSON array of one object, padded to at least `size` bytes."""
    prefix, suffix = b'[{"pad":"', b'"}]'
    padding = max(0, size - len(prefix) - len(suffix))
    return prefix + (DEFAULT_PAD * (padding // len(DEFAULT_PAD) + 1))[:padding] + suffix


def bearer_token(headers: Mapping[str, str]) -> str | None:
    authorization = headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


@dataclass
class InfoMode:
    """How `/info` answers: `static` fields, or `counting` data requests on top of `base_used`."""

    mode: str
    base_used: int
    fields: dict[str, Any]


def parse_info_mode(spec: Any) -> InfoMode:
    """Validate an `/_admin/info` body; raises `ValueError` with the reason."""
    if not isinstance(spec, Mapping):
        raise ValueError("info spec must be a JSON object")
    unknown = set(spec) - INFO_SPEC_KEYS
    if unknown:
        raise ValueError(f"unknown info spec keys: {sorted(unknown)}")
    mode = spec.get("mode")
    if mode not in INFO_MODES:
        raise ValueError(f"mode must be one of {', '.join(INFO_MODES)}, got {mode!r}")
    base_used = spec.get("base_used", 0)
    if not _is_count(base_used):
        raise ValueError(f"base_used must be a non-negative integer, got {base_used!r}")
    fields = spec.get("fields") or {}
    if not isinstance(fields, Mapping):
        raise ValueError("fields must be a JSON object")
    unknown_fields = set(fields) - INFO_FIELDS
    if unknown_fields:
        raise ValueError(f"unknown info fields: {sorted(unknown_fields)}")
    for name in INFO_NUMBER_FIELDS:
        if name in fields and not _is_count(fields[name]):
            raise ValueError(f"{name} must be a non-negative integer, got {fields[name]!r}")
    if mode == "counting" and "usedCalls" in fields:
        raise ValueError("counting mode computes usedCalls; give base_used instead")
    return InfoMode(mode, base_used, dict(fields))


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass
class MockState:
    """Everything the three listeners share; the HTTP handlers only translate to and from it."""

    provider: Provider | None = None
    echo_key: bool = False
    rules: list[Rule] = field(default_factory=list)
    doh_answers: dict[str, list[str]] = field(default_factory=dict)
    info: InfoMode | None = None
    _log: list[dict[str, Any]] = field(default_factory=list)
    _doh_log: list[dict[str, Any]] = field(default_factory=list)
    _next_rule_id: int = 1
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add_rule(self, spec: Mapping[str, Any]) -> int:
        with self._lock:
            rule = parse_rule(self._next_rule_id, spec)
            self._next_rule_id += 1
            self.rules.append(rule)
            return rule.id

    def set_info(self, spec: Any) -> dict[str, Any]:
        """Set how `/info` answers (see the module docstring) and return the new state."""
        mode = parse_info_mode(spec)
        with self._lock:
            self.info = mode
        return self.info_state()

    def info_state(self) -> dict[str, Any]:
        with self._lock:
            info = self.info
            return {
                "mode": "default" if info is None else info.mode,
                "base_used": 0 if info is None else info.base_used,
                "fields": {} if info is None else dict(info.fields),
                "data_requests": self._data_requests(),
            }

    def _data_requests(self) -> int:
        return sum(1 for entry in self._log if entry["path"] != INFO_PATH)

    def reset(self) -> None:
        with self._lock:
            self._log.clear()
            self._doh_log.clear()
            self.rules.clear()
            self.echo_key = False
            self.info = None

    def handle(self, method: str, target: str, headers: Mapping[str, str]) -> Reply:
        """Decide the reply for one API request and record it in the request log."""
        parsed = urllib.parse.urlsplit(target)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        with self._lock:
            rule = next((r for r in self.rules if r.matches(path, query)), None)
            if rule is not None and rule.times is not None:
                rule.times -= 1
            reply = self._reply(method, path, query, rule, bearer_token(headers))
            self._log.append(
                {
                    "seq": len(self._log) + 1,
                    "time": time.time(),
                    "method": method,
                    "target": target,
                    "path": path,
                    "query": query,
                    "headers": dict(headers),
                    "status": reply.status,
                    "scripted": rule is not None,
                    "rule": None if rule is None else rule.id,
                }
            )
        return reply

    def hits(self, path: str | None = None, query: Mapping[str, str] | None = None) -> int:
        """Number of API requests whose path (exact, or prefix with a trailing `*`) matches."""
        wanted = query or {}
        with self._lock:
            return sum(
                1
                for entry in self._log
                if _path_matches(path, entry["path"])
                and all(v in entry["query"].get(k, []) for k, v in wanted.items())
            )

    def log(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._log)

    def doh_log(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._doh_log)

    def doh_connections(self) -> int:
        with self._lock:
            return sum(1 for entry in self._doh_log if entry["kind"] == "connect")

    def record_doh(self, kind: str, **details: Any) -> None:
        with self._lock:
            self._doh_log.append(
                {"seq": len(self._doh_log) + 1, "time": time.time(), "kind": kind, **details}
            )

    def doh_answer(self, name: str, record_type: str) -> dict[str, Any]:
        with self._lock:
            addresses = self.doh_answers.get(name.lower().rstrip("."), [])
        if record_type.upper() != DNS_A_TYPE:
            return {"Status": 0}
        if not addresses:
            return {"Status": DNS_NXDOMAIN}
        return {
            "Status": 0,
            "Answer": [
                {"name": name, "type": DNS_A_NUMBER, "TTL": DNS_TTL, "data": address}
                for address in addresses
            ],
        }

    def _reply(
        self, method: str, path: str, query: Query, rule: Rule | None, key: str | None
    ) -> Reply:
        delay = 0.0 if rule is None else rule.delay_ms / 1000
        if rule is not None and rule.replaces_response:
            reply = self._scripted(rule, key)
        elif method != "GET":
            reply = self._error(HTTPStatus.METHOD_NOT_ALLOWED, key)
        elif path == INFO_PATH and self.info is not None:
            reply = self._info_reply(path, query, key, self.info)
        else:
            reply = self._from_provider(path, query, key)
        reply.delay_seconds = delay
        if self.echo_key and key is not None:
            reply.headers[ECHO_HEADER] = key
        return reply

    def _info_reply(self, path: str, query: Query, key: str | None, info: InfoMode) -> Reply:
        reply = self._from_provider(path, query, key)
        try:
            body = json.loads(reply.body)
        except ValueError:
            return reply
        if reply.status != HTTPStatus.OK or not isinstance(body, dict):
            return reply
        body.update(info.fields)
        if info.mode == "counting":
            body["usedCalls"] = info.base_used + self._data_requests()
        used, limit = body.get("usedCalls"), body.get("monthlyLimit")
        if (
            isinstance(used, int | float)
            and isinstance(limit, int | float)
            and "remainingCalls" not in info.fields
        ):
            body["remainingCalls"] = max(0, limit - used)
        return Reply(int(reply.status), reply.headers, to_bytes(body))

    def _scripted(self, rule: Rule, key: str | None) -> Reply:
        status = rule.status if rule.status is not None else HTTPStatus.OK
        if rule.oversize_bytes is not None:
            body = oversized_body(rule.oversize_bytes)
        elif rule.body is not None:
            body = rule.body
        else:
            body = self._error(status, key).body
        headers = {"Content-Type": JSON_TYPE}
        if rule.retry_after is not None:
            headers["Retry-After"] = f"{rule.retry_after:g}"
        headers.update(rule.headers)
        return Reply(int(status), headers, body)

    def _from_provider(self, path: str, query: Query, key: str | None) -> Reply:
        try:
            result = None if self.provider is None else self.provider(path, query)
        except KeyError as error:
            return self._error(
                HTTPStatus.NOT_FOUND, key, f"no synthetic response for {path}: {error}"
            )
        except Exception as error:
            detail = f"provider failed for {path}: {type(error).__name__}: {error}"
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, key, detail)
        if result is None and path == "/info":
            result = INFO_RESPONSE
        if result is None:
            return self._error(HTTPStatus.NOT_FOUND, key, f"no synthetic response for {path}")
        status, payload = result if isinstance(result, tuple) else (HTTPStatus.OK, result)
        return Reply(int(status), {"Content-Type": JSON_TYPE}, to_bytes(payload))

    def _error(self, status: int, key: str | None, detail: str | None = None) -> Reply:
        phrase = HTTPStatus(status).phrase if status in HTTPStatus else str(status)
        message = detail or phrase
        if self.echo_key and key is not None:
            message = f"{message} (key {key})"
        return Reply(int(status), {"Content-Type": JSON_TYPE}, to_bytes({"error": message}))


def _path_matches(pattern: str | None, path: str) -> bool:
    if pattern is None:
        return True
    return path.startswith(pattern[:-1]) if pattern.endswith("*") else path == pattern


def _quiet(handler: http.server.BaseHTTPRequestHandler) -> None:
    handler.log_message = lambda *args, **kwargs: None  # type: ignore[method-assign]


class ApiHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: "ApiServer"

    def setup(self) -> None:
        super().setup()
        _quiet(self)

    def _serve(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        headers = {name.lower(): value for name, value in self.headers.items()}
        reply = self.server.state.handle(self.command, self.path, headers)
        if reply.delay_seconds:
            time.sleep(reply.delay_seconds)
        self.send_response(reply.status)
        for name, value in reply.headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(reply.body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(reply.body)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _serve


class QuietServer(http.server.ThreadingHTTPServer):
    """Threaded server that ignores clients resetting their connection."""

    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)


class ApiServer(QuietServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: MockState) -> None:
        super().__init__(address, ApiHandler)
        self.state = state


class AdminHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: "AdminServer"

    def setup(self) -> None:
        super().setup()
        _quiet(self)

    def do_GET(self) -> None:
        self._dispatch(None)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            self._answer(HTTPStatus.BAD_REQUEST, {"error": "body is not JSON"})
            return
        self._dispatch(payload)

    def _dispatch(self, payload: Any) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        route = (self.command, parsed.path.removeprefix(ADMIN_PREFIX))
        if not parsed.path.startswith(ADMIN_PREFIX):
            self._answer(HTTPStatus.NOT_FOUND, {"error": f"not an admin path: {parsed.path}"})
            return
        try:
            self._answer(HTTPStatus.OK, self.server.call(route, params, payload))
        except KeyError:
            self._answer(HTTPStatus.NOT_FOUND, {"error": f"unknown admin route {route}"})
        except (ValueError, TypeError, AttributeError) as error:
            self._answer(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def _answer(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode()
        self.send_response(int(status))
        self.send_header("Content-Type", JSON_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class AdminServer(QuietServer):
    daemon_threads = True

    def __init__(
        self, address: tuple[str, int], state: MockState, ports: Callable[[], dict[str, Any]]
    ) -> None:
        super().__init__(address, AdminHandler)
        self.state = state
        self.ports = ports

    def call(self, route: tuple[str, str], params: dict[str, list[str]], payload: Any) -> Any:
        state = self.state
        match route:
            case ("GET", "health"):
                return {"ok": True, **self.ports()}
            case ("GET", "log"):
                return {"entries": state.log()}
            case ("GET", "doh-log"):
                return {"entries": state.doh_log()}
            case ("GET", "hits"):
                query = {
                    k: v[0] for k, v in urllib.parse.parse_qs(params.get("query", [""])[0]).items()
                }
                paths = params.get("path")
                return {"count": state.hits(paths[0] if paths else None, query)}
            case ("POST", "script"):
                return {"id": state.add_rule(payload)}
            case ("POST", "config"):
                state.echo_key = bool(payload["echo_key"])
                return {"echo_key": state.echo_key}
            case ("POST", "info"):
                return state.set_info(payload)
            case ("GET", "info"):
                return state.info_state()
            case ("POST", "doh-answers"):
                state.doh_answers.update(
                    {str(h).lower(): [str(a) for a in addrs] for h, addrs in payload.items()}
                )
                return {"hosts": sorted(state.doh_answers)}
            case ("POST", "reset"):
                state.reset()
                return {"ok": True}
        raise KeyError(route)


class DohHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server: "DohServer"

    def setup(self) -> None:
        super().setup()
        _quiet(self)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        name = (params.get("name") or [""])[0]
        record_type = (params.get("type") or [DNS_A_TYPE])[0]
        answer = self.server.state.doh_answer(name, record_type)
        self.server.state.record_doh(
            "query",
            path=parsed.path,
            name=name,
            type=record_type,
            status=answer["Status"],
            accept=self.headers.get("Accept"),
        )
        body = json.dumps(answer).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/dns-json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class DohServer(QuietServer):
    """HTTPS server that logs each TCP connection before the TLS handshake can fail."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: MockState, cert: Path, key: Path) -> None:
        super().__init__(address, DohHandler)
        self.state = state
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(str(cert), str(key))

    def get_request(self) -> tuple[Any, Any]:
        connection, address = self.socket.accept()
        self.state.record_doh("connect", peer=f"{address[0]}:{address[1]}")
        return connection, address

    def finish_request(self, request: Any, client_address: Any) -> None:
        request.settimeout(TLS_TIMEOUT_SECONDS)
        try:
            secured = self.context.wrap_socket(request, server_side=True)
        except (OSError, ssl.SSLError) as error:
            self.state.record_doh("tls", ok=False, error=f"{type(error).__name__}: {error}")
            return
        self.state.record_doh("tls", ok=True, version=secured.version())
        try:
            self.RequestHandlerClass(secured, client_address, self)  # type: ignore[arg-type]
        except OSError:
            pass
        finally:
            secured.close()


class MockCfbd:
    """The three listeners and their shared state, started on background threads."""

    def __init__(
        self,
        provider: Provider | None = None,
        host: str = LOOPBACK,
        port: int = 0,
        doh_port: int | None = None,
        admin_port: int = 0,
        cert: Path | None = None,
        key: Path | None = None,
    ) -> None:
        self.host = host
        self.state = MockState(provider=provider)
        self.api = ApiServer((host, port), self.state)
        self.admin = AdminServer((host, admin_port), self.state, self._ports)
        self.doh: DohServer | None = None
        if doh_port is not None:
            if cert is None or key is None:
                raise ValueError("the DoH endpoint needs a certificate and a key")
            self.doh = DohServer((host, doh_port), self.state, cert, key)
        self._threads: list[threading.Thread] = []

    @property
    def port(self) -> int:
        return int(self.api.server_address[1])

    @property
    def admin_port(self) -> int:
        return int(self.admin.server_address[1])

    @property
    def doh_port(self) -> int | None:
        return None if self.doh is None else int(self.doh.server_address[1])

    def start(self) -> None:
        servers = [self.api, self.admin] + ([self.doh] if self.doh else [])
        for server in servers:
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": POLL_SECONDS}, daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        servers = [self.api, self.admin] + ([self.doh] if self.doh else [])
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in self._threads:
            thread.join(timeout=5)

    def _ports(self) -> dict[str, int | None]:
        return {"port": self.port, "doh_port": self.doh_port, "admin_port": self.admin_port}


def admin_request(
    port: int,
    method: str,
    path: str,
    payload: Any = None,
    host: str = LOOPBACK,
    timeout: float = ADMIN_TIMEOUT_SECONDS,
) -> Any:
    """Call the admin API and return the decoded JSON answer; raise AdminError on a non-200."""
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        body = None if payload is None else json.dumps(payload).encode()
        headers = {} if body is None else {"Content-Type": JSON_TYPE}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        answer = json.loads(response.read() or b"null")
    finally:
        connection.close()
    if response.status != HTTPStatus.OK:
        raise AdminError(response.status, str(answer.get("error", answer)))
    return answer


def load_provider(spec: str) -> Provider:
    """Import `module:callable`."""
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"provider must be 'module:callable', got {spec!r}")
    provider: Provider = getattr(importlib.import_module(module_name), attribute)
    return provider


def _serve(arguments: argparse.Namespace) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    provider = load_provider(arguments.provider) if arguments.provider else None
    doh_port = arguments.doh_port
    server = MockCfbd(
        provider=provider,
        host=arguments.host,
        port=arguments.port,
        doh_port=doh_port,
        admin_port=arguments.admin_port,
        cert=Path(arguments.cert) if arguments.cert else None,
        key=Path(arguments.key) if arguments.key else None,
    )
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    server.start()
    print(
        f"LISTENING port={server.port} doh_port={server.doh_port} admin_port={server.admin_port}",
        flush=True,
    )
    stopped.wait()
    server.stop()
    return 0


def _request(arguments: argparse.Namespace) -> int:
    payload = json.load(sys.stdin) if arguments.stdin else None
    try:
        answer = admin_request(arguments.admin_port, arguments.method, arguments.path, payload)
    except AdminError as error:
        print(error, file=sys.stderr)
        return 1
    print(json.dumps(answer))
    return 0


def main(argv: list[str] | None = None) -> int:
    """`server.py [serve] --port N ...` runs the mock; `server.py request M PATH` calls admin."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="run the mock (default)")
    serve.add_argument("--host", default=LOOPBACK)
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--doh-port", type=int, default=None)
    serve.add_argument("--admin-port", type=int, default=0)
    serve.add_argument("--cert", help="PEM certificate for the DoH endpoint")
    serve.add_argument("--key", help="PEM private key for the DoH endpoint")
    serve.add_argument("--provider", help="module:callable returning synthetic responses")
    request = sub.add_parser("request", help="call the admin API of a running mock")
    request.add_argument("method", choices=["GET", "POST"])
    request.add_argument("path")
    request.add_argument("--admin-port", type=int, required=True)
    request.add_argument("--stdin", action="store_true", help="read the JSON body from stdin")
    if not arguments or arguments[0] not in {"serve", "request", "-h", "--help"}:
        arguments.insert(0, "serve")
    parsed = parser.parse_args(arguments)
    return _request(parsed) if parsed.command == "request" else _serve(parsed)


if __name__ == "__main__":
    sys.exit(main())
