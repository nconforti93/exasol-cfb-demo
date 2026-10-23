"""The app's settings: environment first, then `.env`; the bind rule; the operator decision; the
`Host` allowlist and the configured `Origin` set (pure; nothing here touches the network except an
injected resolver, and nothing secret is ever put in a message, a repr or `describe()`)."""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from vs.host import ledger, target
from vs.host.namespace import Namespace, NamespaceError

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_APP_USER = "CFB_APP_WEB"
DEFAULT_CONSOLE_USER = "CFB_CONSOLE_WEB"
DEFAULT_LEDGER_PLAN = "app"
DEFAULT_LEDGER_PATH = ledger.LEDGER_PATH
DEFAULT_STATIC_DIR = ROOT / "app" / "frontend" / "dist"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
VITE_PORT = 5173
VITE_HOSTS = ("127.0.0.1", "localhost")
HTTP_DEFAULT_PORT = 80
OPERATOR_MODES = ("auto", "on", "off")
TARGETS = tuple(target.DEPLOYMENT_DIRECTORIES)
WILDCARD = "*"
NON_LOOPBACK_REASON = "bound to a non-loopback address"

_HOST_NAME = re.compile(r"[a-z0-9._-]+")
_ORIGIN = re.compile(r"(https?)://([A-Za-z0-9._-]+|\[[0-9A-Fa-f:]+\])(?::(\d{1,5}))?")
_TRUE = frozenset({"1", "yes", "true", "on"})

Resolver = Callable[[str, int], Sequence[str]]


class SettingsError(ValueError):
    """A setting is invalid; the message names the variable and never a secret."""


@dataclass(frozen=True)
class Bind:
    """Where the server listens, with every address `host` resolves to."""

    host: str
    port: int
    addresses: tuple[str, ...]

    @property
    def loopback(self) -> bool:
        """True only when the name resolved and every address is a loopback address."""
        return bool(self.addresses) and all(_is_loopback(address) for address in self.addresses)


@dataclass(frozen=True)
class OperatorDecision:
    """Whether the operator path (the namespace owner's allowlist) is on, and why."""

    enabled: bool
    reason: str


def system_resolver(host: str, port: int) -> list[str]:
    """Every address `host` resolves to, through the system resolver."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def resolve_bind(host: str, port: int, resolver: Resolver = system_resolver) -> Bind:
    """Resolve a bind address; a name is not trusted by its spelling."""
    try:
        addresses = tuple(resolver(host, port))
    except OSError:
        raise SettingsError(f"APP_HOST {host!r} does not resolve to an address") from None
    return Bind(host, port, addresses)


def decide_operator(mode: str, bind: Bind) -> OperatorDecision:
    """`auto`: on iff loopback; `on`: on, refusing a non-loopback bind; `off`: never on."""
    if mode not in OPERATOR_MODES:
        raise SettingsError(f"APP_OPERATOR must be one of {', '.join(OPERATOR_MODES)}")
    if mode == "off":
        return OperatorDecision(False, "APP_OPERATOR=off")
    if bind.loopback:
        return OperatorDecision(True, f"bound to loopback address {bind.host}")
    if mode == "on":
        raise SettingsError(
            f"APP_OPERATOR=on refuses to start on {bind.host}: it resolves to "
            f"{', '.join(bind.addresses) or 'no address'}, which is not loopback"
        )
    return OperatorDecision(False, NON_LOOPBACK_REASON)


def normalize_host(value: str) -> str | None:
    """`name:port` in lower case for a `Host` header or an allowlist entry; a missing port is the
    HTTP default. None when the value is not exactly a host name or address and an optional port."""
    text = value.strip().lower()
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            return None
        name, rest = text[: end + 1], text[end + 1 :]
        valid_rest = rest == "" or rest.startswith(":")
        if not valid_rest or not re.fullmatch(r"\[[0-9a-f:]+\]", name):
            return None
        has_port, port_text = bool(rest), rest[1:]
    else:
        name, separator, port_text = text.partition(":")
        if not _HOST_NAME.fullmatch(name):
            return None
        has_port = bool(separator)
    port = _port(port_text) if has_port else HTTP_DEFAULT_PORT
    return None if port is None else f"{name}:{port}"


def host_allowlist(port: int, *, dev: bool, extra: Sequence[str]) -> frozenset[str]:
    """The `Host` values that may reach the app: loopback names on the app's port, the Vite hosts in
    development, and the configured extras."""
    allowed = {f"{name}:{port}" for name in LOOPBACK_HOSTS}
    if dev:
        allowed |= {f"{name}:{VITE_PORT}" for name in VITE_HOSTS}
    allowed |= set(extra)
    return frozenset(allowed)


def origin_set(port: int, *, dev: bool, cors: Sequence[str]) -> frozenset[str]:
    """The `Origin` values that may `POST`, built from configuration only: the loopback hosts as
    `http://` origins (the Vite ones in development) plus `APP_CORS_ORIGINS`."""
    hosts = host_allowlist(port, dev=dev, extra=())
    return frozenset({f"http://{host}" for host in hosts} | set(cors))


def parse_allowed_hosts(value: str) -> tuple[str, ...]:
    """`APP_ALLOWED_HOSTS`: comma-separated `host:port`; `*` and a missing port are refused."""
    entries = _entries(value)
    if WILDCARD in entries:
        raise SettingsError("APP_ALLOWED_HOSTS must not contain '*'")
    result = []
    for entry in entries:
        if not _has_port(entry):
            raise SettingsError(f"APP_ALLOWED_HOSTS entry {entry!r} must be host:port")
        normalized = normalize_host(entry)
        if normalized is None:
            raise SettingsError(f"APP_ALLOWED_HOSTS entry {entry!r} is not a host:port")
        result.append(normalized)
    return tuple(result)


def parse_cors_origins(value: str) -> tuple[str, ...]:
    """`APP_CORS_ORIGINS`: comma-separated `scheme://host[:port]`; `*` is refused."""
    entries = _entries(value)
    if WILDCARD in entries:
        raise SettingsError("APP_CORS_ORIGINS must not contain '*'")
    result = []
    for entry in entries:
        match = _ORIGIN.fullmatch(entry.rstrip("/"))
        if not match or (match.group(3) and _port(match.group(3)) is None):
            raise SettingsError(f"APP_CORS_ORIGINS entry {entry!r} is not scheme://host[:port]")
        result.append(entry.rstrip("/").lower())
    return tuple(result)


@dataclass(frozen=True)
class Settings:
    """Everything the backend reads from its environment, validated."""

    target: str
    dsn_override: str | None
    app_user: str
    app_password: str = field(repr=False)
    console_user: str
    console_password: str | None = field(repr=False)
    bind: Bind
    operator: OperatorDecision
    allowed_hosts: frozenset[str]
    origins: frozenset[str]
    cors_origins: tuple[str, ...]
    dev: bool
    skip_read_check: bool
    ledger_plan: str
    ledger_path: Path
    ledger_enabled: bool
    prefix: str
    owner_password: str | None = field(repr=False)
    static_dir: Path

    @property
    def host(self) -> str:
        return self.bind.host

    @property
    def port(self) -> int:
        return self.bind.port

    @property
    def namespace(self) -> Namespace:
        return Namespace(self.prefix)

    @property
    def api_schema(self) -> str:
        return f"{self.prefix}CFB_API"

    @property
    def api_live_schema(self) -> str:
        return f"{self.prefix}CFB_API_LIVE"

    def describe(self) -> str:
        """One line for logs: no password, no key."""
        operator = "on" if self.operator.enabled else f"off ({self.operator.reason})"
        ledger_state = f"plan {self.ledger_plan}" if self.ledger_enabled else "off"
        return (
            f"target {self.target}, listening on {self.host}:{self.port}, operator {operator}, "
            f"read user {self.app_user}, console {'on' if self.console_password else 'off'}, "
            f"ledger {ledger_state}"
        )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str],
        *,
        dotenv_path: Path | None = None,
        resolver: Resolver = system_resolver,
    ) -> Settings:
        """Read the settings table: the environment first, then `.env`."""
        dotenv = target.parse_dotenv(dotenv_path or target.DOTENV_PATH)
        values = {**dotenv, **{key: value for key, value in environ.items() if value != ""}}

        def get(name: str, default: str = "") -> str:
            return values.get(name, default).strip()

        app_password = values.get("CFB_APP_PASSWORD", "")
        if not app_password:
            raise SettingsError(
                "CFB_APP_PASSWORD is not set; put it in the environment or in .env, "
                "then run `make app-user`"
            )
        bind = resolve_bind(
            get("APP_HOST", DEFAULT_HOST), _required_port(get("APP_PORT")), resolver
        )
        dev = get("APP_DEV").lower() in _TRUE
        extra = parse_allowed_hosts(get("APP_ALLOWED_HOSTS"))
        cors = parse_cors_origins(get("APP_CORS_ORIGINS"))
        prefix = get("APP_PREFIX")
        try:
            Namespace(prefix)
        except NamespaceError:
            raise SettingsError(
                "APP_PREFIX must be empty or UPPER_CASE words ending in '_', for example 'TEST_'"
            ) from None
        ledger_path = get("APP_LEDGER_PATH")
        if prefix and not ledger_path:
            raise SettingsError("APP_LEDGER_PATH is required with APP_PREFIX (a test namespace)")
        plan = values.get("APP_LEDGER_PLAN", DEFAULT_LEDGER_PLAN).strip()
        if not plan:
            raise SettingsError("APP_LEDGER_PLAN must not be blank")
        selected = get("TARGET", target.DEFAULT_TARGET)
        if selected not in TARGETS:
            raise SettingsError(f"TARGET must be one of {', '.join(TARGETS)}")
        return cls(
            target=selected,
            dsn_override=get(target.DSN_VARIABLE) or None,
            app_user=get("CFB_APP_USER", DEFAULT_APP_USER),
            app_password=app_password,
            console_user=get("CFB_CONSOLE_USER", DEFAULT_CONSOLE_USER),
            console_password=values.get("CFB_CONSOLE_PASSWORD") or None,
            bind=bind,
            operator=decide_operator(get("APP_OPERATOR", "auto").lower(), bind),
            allowed_hosts=host_allowlist(bind.port, dev=dev, extra=extra),
            origins=origin_set(bind.port, dev=dev, cors=cors),
            cors_origins=cors,
            dev=dev,
            skip_read_check=get("APP_SKIP_READ_CHECK").lower() in _TRUE,
            ledger_plan=plan,
            ledger_path=Path(ledger_path) if ledger_path else DEFAULT_LEDGER_PATH,
            ledger_enabled=get("LEDGER").lower() != "off",
            prefix=prefix,
            owner_password=values.get("CFB_TEST_OWNER_PASSWORD") or None,
            static_dir=Path(get("APP_STATIC_DIR")) if get("APP_STATIC_DIR") else DEFAULT_STATIC_DIR,
        )


def _entries(value: str) -> list[str]:
    return [entry.strip() for entry in value.split(",") if entry.strip()]


def _has_port(entry: str) -> bool:
    return bool(re.search(r"(?<!:):\d+$", entry))


def _port(text: str) -> int | None:
    if not text.isdigit():
        return None
    number = int(text)
    return number if 1 <= number <= 65535 else None


def _required_port(text: str) -> int:
    if not text:
        return DEFAULT_PORT
    port = _port(text)
    if port is None:
        raise SettingsError("APP_PORT must be a port number between 1 and 65535")
    return port


def _is_loopback(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped.is_loopback
    return parsed.is_loopback
