"""Browser safety and privilege proof (pure, plus small ASGI middlewares).

Middleware order, outermost first: security headers, `Host` allowlist, `POST` checks, CORS, session
cookie. A request with a foreign `Host` never reaches a route, a cookie or the database; an unsafe
request needs `X-CFB-Action: 1`, a JSON content type and an `Origin` from a set that was built from
configuration and is never derived from the request.

The effective-privilege check at the end is a function of (user, roles, schemas): the read user, the
console user and Phase 7's chat user use the same code with their own expectations.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.backend.settings import normalize_host

HOST_ERROR = "bad_host"
FORBIDDEN_ERROR = "forbidden"
SESSION_COOKIE = "cfb_session"
ACTION_HEADER = "x-cfb-action"
ACTION_VALUE = "1"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'; object-src 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
}
CORS_METHODS = ("GET", "POST")
CORS_HEADERS = ("X-CFB-Action", "Content-Type")

_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{32,64}")
_JSON_TYPE = re.compile(r"application/json\s*(;.*)?", re.IGNORECASE)


@dataclass(frozen=True)
class SecurityConfig:
    """What the middlewares need, all of it from configuration."""

    allowed_hosts: frozenset[str]
    origins: frozenset[str]
    cors_origins: Sequence[str] = ()
    secure_cookie: bool = False


def host_allowed(header: str | None, allowed: Iterable[str]) -> bool:
    """True when the `Host` header's name and port are both in the allowlist."""
    if not header:
        return False
    normalized = normalize_host(header)
    return normalized is not None and normalized in set(allowed)


def new_session_id() -> str:
    """An anonymous random session id: it grants nothing, it only scopes per-browser state."""
    return secrets.token_urlsafe(24)


def valid_session_id(value: str | None) -> bool:
    return value is not None and _SESSION_ID.fullmatch(value) is not None


def session_cookie(session_id: str, *, secure: bool) -> str:
    """The `Set-Cookie` value for a session id."""
    parts = [f"{SESSION_COOKIE}={session_id}", "HttpOnly", "SameSite=Strict", "Path=/"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def middleware(config: SecurityConfig) -> list[Middleware]:
    """The security stack for `Starlette(middleware=...)`, outermost first."""
    return [
        Middleware(SecurityHeadersMiddleware),
        Middleware(HostMiddleware, allowed_hosts=config.allowed_hosts),
        Middleware(ActionMiddleware, origins=config.origins),
        Middleware(
            CORSMiddleware,
            allow_origins=list(config.cors_origins),
            allow_methods=list(CORS_METHODS),
            allow_headers=list(CORS_HEADERS),
            allow_credentials=True,
        ),
        Middleware(SessionMiddleware, secure=config.secure_cookie),
    ]


class SecurityHeadersMiddleware:
    """Adds the fixed security headers to every response, errors included."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


class HostMiddleware:
    """Refuses any request whose `Host` is not in the allowlist, before routing (status 400)."""

    def __init__(self, app: ASGIApp, *, allowed_hosts: Iterable[str]) -> None:
        self.app = app
        self.allowed = frozenset(allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            hosts = _header_values(scope, b"host")
            if len(hosts) != 1 or not host_allowed(hosts[0], self.allowed):
                await _refuse(send, 400, HOST_ERROR, "this host name is not served")
                return
        await self.app(scope, receive, send)


class ActionMiddleware:
    """Every unsafe request needs the action header, a JSON body type and an allowed `Origin`."""

    def __init__(self, app: ASGIApp, *, origins: Iterable[str]) -> None:
        self.app = app
        self.origins = frozenset(origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            verdict = self._verdict(scope)
            if verdict is not None:
                code, message = verdict
                await _refuse(send, 403, code, message)
                return
        await self.app(scope, receive, send)

    def _verdict(self, scope: Scope) -> tuple[str, str] | None:
        if _header_values(scope, ACTION_HEADER.encode()) != [ACTION_VALUE]:
            return FORBIDDEN_ERROR, "the X-CFB-Action: 1 header is required"
        content_types = _header_values(scope, b"content-type")
        if len(content_types) != 1 or not _JSON_TYPE.fullmatch(content_types[0].strip()):
            return FORBIDDEN_ERROR, "the content type must be application/json"
        origins = _header_values(scope, b"origin")
        if len(origins) != 1 or origins[0] not in self.origins:
            return FORBIDDEN_ERROR, "the request does not come from this app's own origin"
        return None


class SessionMiddleware:
    """Gives each browser an anonymous session id (`scope["state"]["session_id"]`) by cookie."""

    def __init__(self, app: ASGIApp, *, secure: bool = False) -> None:
        self.app = app
        self.secure = secure

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        presented = _cookies(scope).get(SESSION_COOKIE)
        session_id = presented if valid_session_id(presented) else new_session_id()
        assert session_id is not None
        state: MutableMapping[str, Any] = scope.setdefault("state", {})
        state["session_id"] = session_id

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start" and session_id != presented:
                headers = MutableHeaders(scope=message)
                headers.append("Set-Cookie", session_cookie(session_id, secure=self.secure))
            await send(message)

        await self.app(scope, receive, send_with_cookie)


def _header_values(scope: Scope, name: bytes) -> list[str]:
    return [value.decode("latin-1") for key, value in scope["headers"] if key.lower() == name]


def _cookies(scope: Scope) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for header in _header_values(scope, b"cookie"):
        for part in header.split(";"):
            key, separator, value = part.strip().partition("=")
            if separator:
                cookies.setdefault(key, value)
    return cookies


async def _refuse(send: Send, status: int, code: str, message: str) -> None:
    body = json.dumps({"error": code, "message": message}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


PUBLIC = "PUBLIC"
REQUIRED_SYSTEM_PRIVILEGE = "CREATE SESSION"
# What PUBLIC holds on a fresh deployment (probe s9 (d), docs/spikes/s9-app-probe.md): the one
# system privilege `USE ANY SCHEMA`. It lets a user name a schema and reads nothing, so it is
# tolerated when PUBLIC is its only source. Every other privilege, from any route, is a violation.
PUBLIC_SYSTEM_PRIVILEGES = frozenset({"USE ANY SCHEMA"})
SYS_USER = "SYS"
UNVERIFIED_IMPERSONATION = "impersonation granted through a role or PUBLIC"
MAX_LISTED_VIOLATIONS = 8

PRIVILEGE_QUERIES: dict[str, str] = {
    "session_privs": 'SELECT "PRIVILEGE" FROM "SYS"."EXA_SESSION_PRIVS"',
    "session_roles": 'SELECT "ROLE_NAME" FROM "SYS"."EXA_SESSION_ROLES"',
    "user_sys": 'SELECT "GRANTEE", "PRIVILEGE" FROM "SYS"."EXA_USER_SYS_PRIVS"',
    "role_sys": 'SELECT "GRANTEE", "PRIVILEGE" FROM "SYS"."EXA_ROLE_SYS_PRIVS"',
    "user_roles": 'SELECT "GRANTEE", "GRANTED_ROLE" FROM "SYS"."EXA_USER_ROLE_PRIVS"',
    "role_roles": 'SELECT "GRANTEE", "GRANTED_ROLE" FROM "SYS"."EXA_ROLE_ROLE_PRIVS"',
    "all_obj": (
        'SELECT "OBJECT_SCHEMA", "OBJECT_NAME", "OBJECT_TYPE", "PRIVILEGE", "GRANTEE" '
        'FROM "SYS"."EXA_ALL_OBJ_PRIVS"'
    ),
    "user_conn": 'SELECT "GRANTEE", "GRANTED_CONNECTION" FROM "SYS"."EXA_USER_CONNECTION_PRIVS"',
    "role_conn": 'SELECT "GRANTEE", "GRANTED_CONNECTION" FROM "SYS"."EXA_ROLE_CONNECTION_PRIVS"',
    "user_restricted": (
        'SELECT "GRANTEE", "OBJECT_NAME", "FOR_OBJECT_NAME" '
        'FROM "SYS"."EXA_USER_RESTRICTED_OBJ_PRIVS"'
    ),
    "role_restricted": (
        'SELECT "GRANTEE", "OBJECT_NAME", "FOR_OBJECT_NAME" '
        'FROM "SYS"."EXA_ROLE_RESTRICTED_OBJ_PRIVS"'
    ),
    "user_impersonation": (
        'SELECT "GRANTEE", "IMPERSONATION_ON" FROM "SYS"."EXA_USER_IMPERSONATION_PRIVS"'
    ),
    "user_schemas": 'SELECT "SCHEMA_NAME" FROM "SYS"."EXA_USER_SCHEMAS"',
    "user_objects": 'SELECT "OBJECT_NAME", "OBJECT_TYPE" FROM "SYS"."EXA_USER_OBJECTS"',
}
IMPERSONATION_QUERY = (
    'SELECT "GRANTEE", "IMPERSONATION_ON", "GRANTOR" FROM "SYS"."EXA_DBA_IMPERSONATION_PRIVS"'
)

Rows = Sequence[Sequence[Any]]


class PrivilegeError(RuntimeError):
    """A user holds more (or less) than it should; the message names user, privilege and route."""


@dataclass(frozen=True)
class PrivilegeViolation:
    """One privilege a user must not hold (or one it lacks), with where it comes from."""

    reason: str

    def message(self, user: str) -> str:
        return f"{user} {self.reason}"


@dataclass(frozen=True)
class PrivilegeReport:
    """The result of the check: violations, and what could not be verified from the user's side."""

    violations: tuple[PrivilegeViolation, ...]
    unverified: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.violations

    def raise_if_violations(self, user: str) -> None:
        """Raise `PrivilegeError` naming the user and the first violations."""
        if self.ok:
            return
        listed = [violation.message(user) for violation in self.violations[:MAX_LISTED_VIOLATIONS]]
        hidden = len(self.violations) - len(listed)
        more = f"; and {hidden} more" if hidden > 0 else ""
        raise PrivilegeError(f"{user} may do more than it should: {'; '.join(listed)}{more}")


@dataclass(frozen=True)
class PrivilegeSnapshot:
    """The rows of `PRIVILEGE_QUERIES` as the user saw them, plus the database-wide impersonation
    grants when the operator path read them (None when it could not)."""

    rows: Mapping[str, Rows]
    impersonation: Rows | None

    @classmethod
    def from_rows(cls, rows: Mapping[str, Rows], impersonation: Rows | None) -> PrivilegeSnapshot:
        missing = set(PRIVILEGE_QUERIES) - set(rows)
        if missing:
            raise ValueError(f"privilege snapshot lacks {', '.join(sorted(missing))}")
        return cls(rows, impersonation)

    def column(self, key: str, index: int = 0) -> list[str]:
        return [_upper(row[index]) for row in self.rows[key]]


def check_effective_privileges(
    snapshot: PrivilegeSnapshot,
    *,
    user: str,
    roles: Sequence[str],
    schemas: Sequence[str],
    forbidden_users: Sequence[str] = (),
) -> PrivilegeReport:
    """Prove that `user` holds exactly `CREATE SESSION`, the given roles and `SELECT` on the given
    schemas, by every route, and owns nothing, holds no connection and may impersonate no one."""
    name = _upper(user)
    violations: list[str] = []
    violations += _identity(name, [_upper(other) for other in forbidden_users])
    violations += _system(snapshot, name)
    violations += _role_set(snapshot, [_upper(role) for role in roles])
    violations += _objects(snapshot, name, [_upper(schema) for schema in schemas])
    violations += _connections(snapshot, name)
    violations += _ownership(snapshot)
    impersonation, unverified = _impersonation(snapshot, name)
    violations += impersonation
    return PrivilegeReport(
        tuple(PrivilegeViolation(text) for text in violations), (unverified,) if unverified else ()
    )


def _identity(name: str, forbidden: Sequence[str]) -> list[str]:
    if name == SYS_USER:
        return ["is SYS, the database administrator, and must not be used by the app"]
    if name in forbidden:
        return ["is the namespace owner and must not be used as the app's user"]
    return []


def _route(grantee: str, name: str) -> str:
    if grantee == name:
        return "granted to the user directly"
    if grantee == PUBLIC:
        return "granted to PUBLIC"
    return f"granted through the role {grantee}"


def _system(snapshot: PrivilegeSnapshot, name: str) -> list[str]:
    effective = set(snapshot.column("session_privs"))
    grants: dict[str, list[str]] = {}
    for grantee, privilege in [*snapshot.rows["user_sys"], *snapshot.rows["role_sys"]]:
        grants.setdefault(_upper(privilege), []).append(_upper(grantee))
    roles = sorted(set(snapshot.column("session_roles")))
    found: list[str] = []
    if REQUIRED_SYSTEM_PRIVILEGE not in effective:
        found.append(f"lacks the system privilege {REQUIRED_SYSTEM_PRIVILEGE}")
    for privilege in sorted(effective - {REQUIRED_SYSTEM_PRIVILEGE}):
        grantees = grants.get(privilege, [])
        if privilege in PUBLIC_SYSTEM_PRIVILEGES and grantees and set(grantees) == {PUBLIC}:
            continue
        if grantees:
            route = "; ".join(_route(grantee, name) for grantee in sorted(set(grantees)))
        elif roles:
            route = f"granted through its roles ({', '.join(roles)})"
        else:
            route = "granted by an unknown route"
        found.append(f"holds the system privilege {privilege}, {route}")
    return found


def _role_set(snapshot: PrivilegeSnapshot, expected: Sequence[str]) -> list[str]:
    held = set(snapshot.column("session_roles"))
    found = [
        f"holds the role {role}, which is not expected" for role in sorted(held - set(expected))
    ]
    found += [f"lacks the role {role}" for role in sorted(set(expected) - held)]
    return found


def _objects(snapshot: PrivilegeSnapshot, name: str, schemas: Sequence[str]) -> list[str]:
    found: list[str] = []
    granted: set[str] = set()
    for schema, object_name, kind, privilege, grantee in snapshot.rows["all_obj"]:
        target = f"{_upper(kind).lower()} {_qualified(schema, object_name)}"
        privilege_name = _upper(privilege)
        is_expected = (
            _upper(kind) == "SCHEMA"
            and _upper(object_name) in schemas
            and privilege_name == "SELECT"
        )
        if is_expected:
            granted.add(_upper(object_name))
        else:
            found.append(f"holds {privilege_name} on {target}, {_route(_upper(grantee), name)}")
    found += [f"lacks SELECT on the schema {schema}" for schema in schemas if schema not in granted]
    return found


def _qualified(schema: object, name: object) -> str:
    return f"{schema}.{name}" if schema else str(name)


def _connections(snapshot: PrivilegeSnapshot, name: str) -> list[str]:
    found = []
    for key in ("user_conn", "role_conn"):
        for grantee, connection in snapshot.rows[key]:
            found.append(
                f"holds the connection {connection}, {_route(_upper(grantee), name)} "
                "(GRANT CONNECTION)"
            )
    for key in ("user_restricted", "role_restricted"):
        for grantee, connection, script in snapshot.rows[key]:
            scope = f" for the script {script}" if script else ""
            found.append(
                f"may access the connection {connection}{scope}, {_route(_upper(grantee), name)} "
                "(GRANT ACCESS ON CONNECTION)"
            )
    return found


def _ownership(snapshot: PrivilegeSnapshot) -> list[str]:
    found = [f"owns the schema {schema[0]}" for schema in snapshot.rows["user_schemas"]]
    found += [f"owns the {kind.lower()} {name}" for name, kind in snapshot.rows["user_objects"]]
    return found


def _impersonation(snapshot: PrivilegeSnapshot, name: str) -> tuple[list[str], str | None]:
    relevant = {name, PUBLIC, *snapshot.column("session_roles")}
    pairs = {
        (_upper(grantee), str(target))
        for grantee, target, *_ in snapshot.rows["user_impersonation"]
    }
    if snapshot.impersonation is not None:
        pairs |= {
            (_upper(grantee), str(target))
            for grantee, target, *_ in snapshot.impersonation
            if _upper(grantee) in relevant
        }
    found = [
        f"may impersonate {target}, {_route(grantee, name)}" for grantee, target in sorted(pairs)
    ]
    return found, UNVERIFIED_IMPERSONATION if snapshot.impersonation is None else None


def _upper(value: object) -> str:
    return str(value).strip().upper()
