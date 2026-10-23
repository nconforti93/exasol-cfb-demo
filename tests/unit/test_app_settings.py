"""Unit tests of `app/backend/settings.py`: the settings table, the bind rule, the operator
decision, the `Host` allowlist and the configured `Origin` set. No network and no database."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.backend import settings
from app.backend.settings import Bind, Settings, SettingsError

PASSWORD = "app-secret-123"
CONSOLE_PASSWORD = "console-secret-456"


def fake_resolver(table: dict[str, list[str]]) -> settings.Resolver:
    def resolve(host: str, port: int) -> list[str]:
        if host not in table:
            raise OSError("name not known")
        return table[host]

    return resolve


LOCAL = fake_resolver(
    {
        "127.0.0.1": ["127.0.0.1"],
        "localhost": ["127.0.0.1", "::1"],
        "::1": ["::1"],
        "0.0.0.0": ["0.0.0.0"],
        "rebound.example": ["127.0.0.1", "203.0.113.7"],
        "mapped.example": ["::ffff:127.0.0.1"],
        "lan.example": ["192.168.1.20"],
        "scoped": ["fe80::1%eth0"],
    }
)


def bind(host: str, port: int = 8000) -> Bind:
    return settings.resolve_bind(host, port, LOCAL)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "mapped.example"])
def test_a_bind_is_loopback_when_every_resolved_address_is(host: str) -> None:
    assert bind(host).loopback


@pytest.mark.parametrize("host", ["0.0.0.0", "rebound.example", "lan.example", "scoped"])
def test_a_bind_is_not_loopback_when_any_address_is_not(host: str) -> None:
    assert not bind(host).loopback


def test_localhost_resolving_to_a_foreign_address_is_not_loopback() -> None:
    hijacked = fake_resolver({"localhost": ["127.0.0.1", "10.0.0.5"]})
    assert not settings.resolve_bind("localhost", 8000, hijacked).loopback


def test_a_name_that_does_not_resolve_is_an_error_naming_the_variable() -> None:
    with pytest.raises(SettingsError, match="APP_HOST"):
        settings.resolve_bind("nowhere.example", 8000, LOCAL)


def test_an_empty_resolution_is_not_loopback() -> None:
    assert not settings.resolve_bind("empty", 8000, fake_resolver({"empty": []})).loopback


def test_the_operator_follows_the_bind_in_auto_mode() -> None:
    on = settings.decide_operator("auto", bind("127.0.0.1"))
    off = settings.decide_operator("auto", bind("0.0.0.0"))
    assert on.enabled and "loopback" in on.reason
    assert not off.enabled and off.reason == "bound to a non-loopback address"


def test_operator_on_refuses_a_non_loopback_bind_naming_the_variable_and_address() -> None:
    with pytest.raises(SettingsError, match=r"APP_OPERATOR.*0\.0\.0\.0"):
        settings.decide_operator("on", bind("0.0.0.0"))
    assert settings.decide_operator("on", bind("127.0.0.1")).enabled


def test_operator_off_is_never_on() -> None:
    decision = settings.decide_operator("off", bind("127.0.0.1"))
    assert not decision.enabled and "APP_OPERATOR=off" in decision.reason


def test_an_unknown_operator_mode_is_refused() -> None:
    with pytest.raises(SettingsError, match="APP_OPERATOR"):
        settings.decide_operator("maybe", bind("127.0.0.1"))


def test_the_host_allowlist_holds_the_loopback_names_with_the_port() -> None:
    allowed = settings.host_allowlist(8000, dev=False, extra=())
    assert allowed == {"127.0.0.1:8000", "localhost:8000", "[::1]:8000"}


def test_dev_adds_the_vite_hosts_and_extra_hosts_are_added() -> None:
    allowed = settings.host_allowlist(8000, dev=True, extra=("demo.example:9000",))
    assert {"127.0.0.1:5173", "localhost:5173", "demo.example:9000"} <= allowed
    assert "127.0.0.1:8000" in allowed


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("127.0.0.1:8000", "127.0.0.1:8000"),
        ("LOCALHOST:8000", "localhost:8000"),
        ("[::1]:8000", "[::1]:8000"),
        ("[::1]", "[::1]:80"),
        ("localhost", "localhost:80"),
        ("  localhost:8000 ", "localhost:8000"),
        ("", None),
        ("evil.example:8000/x", None),
        ("user@localhost:8000", None),
        ("localhost:notaport", None),
        ("localhost:0", None),
        ("localhost:", None),
        ("[::1]:", None),
        ("localhost:70000", None),
        ("local host:8000", None),
        ("[::1:8000", None),
    ],
)
def test_host_headers_are_normalized_by_name_and_port(header: str, expected: str | None) -> None:
    assert settings.normalize_host(header) == expected


def test_the_origin_set_is_built_from_configuration_only() -> None:
    origins = settings.origin_set(8000, dev=False, cors=("http://demo.example:3000",))
    assert origins == {
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://[::1]:8000",
        "http://demo.example:3000",
    }


def test_dev_adds_the_vite_origins() -> None:
    origins = settings.origin_set(8000, dev=True, cors=())
    assert {"http://127.0.0.1:5173", "http://localhost:5173"} <= origins


def test_extra_allowed_hosts_do_not_become_origins() -> None:
    assert not any(
        "demo.example" in origin for origin in settings.origin_set(8000, dev=False, cors=())
    )


@pytest.mark.parametrize("value", ["*", "a:1,*", " * "])
def test_a_wildcard_is_refused_in_both_lists(value: str) -> None:
    with pytest.raises(SettingsError, match="APP_ALLOWED_HOSTS"):
        settings.parse_allowed_hosts(value)
    with pytest.raises(SettingsError, match="APP_CORS_ORIGINS"):
        settings.parse_cors_origins(value)


def test_allowed_hosts_need_a_port_and_are_normalized() -> None:
    assert settings.parse_allowed_hosts("Demo.Example:9000, localhost:80") == (
        "demo.example:9000",
        "localhost:80",
    )
    assert settings.parse_allowed_hosts("") == ()
    with pytest.raises(SettingsError, match="host:port"):
        settings.parse_allowed_hosts("demo.example")


def test_cors_origins_are_scheme_host_port_only() -> None:
    assert settings.parse_cors_origins("http://127.0.0.1:5173/, https://Demo.example") == (
        "http://127.0.0.1:5173",
        "https://demo.example",
    )
    for bad in ("demo.example", "http://demo.example/path", "ftp://demo.example", "null"):
        with pytest.raises(SettingsError, match="APP_CORS_ORIGINS"):
            settings.parse_cors_origins(bad)


def environment(**overrides: str) -> dict[str, str]:
    base = {"CFB_APP_PASSWORD": PASSWORD}
    return {**base, **overrides}


NO_DOTENV = Path("/nonexistent/.env")


def load(env: dict[str, str], dotenv: Path = NO_DOTENV) -> Settings:
    return Settings.from_env(env, dotenv_path=dotenv, resolver=LOCAL)


def test_defaults_follow_the_settings_table(tmp_path: Path) -> None:
    loaded = load(environment(), tmp_path / "missing.env")
    assert (loaded.target, loaded.dsn_override) == ("local", None)
    assert (loaded.app_user, loaded.console_user) == ("CFB_APP_WEB", "CFB_CONSOLE_WEB")
    assert (loaded.host, loaded.port) == ("127.0.0.1", 8000)
    assert loaded.operator.enabled and loaded.bind.loopback
    assert loaded.allowed_hosts == {"127.0.0.1:8000", "localhost:8000", "[::1]:8000"}
    assert loaded.cors_origins == () and not loaded.dev and not loaded.skip_read_check
    assert loaded.ledger_plan == "app" and loaded.ledger_enabled
    assert loaded.ledger_path == settings.DEFAULT_LEDGER_PATH
    assert loaded.prefix == "" and loaded.console_password is None
    assert loaded.static_dir == settings.DEFAULT_STATIC_DIR
    assert loaded.logo_dir == settings.DEFAULT_LOGO_DIR and loaded.logos_enabled


def test_logo_settings_come_from_the_environment(tmp_path: Path) -> None:
    none = tmp_path / "x.env"
    custom = load(environment(APP_LOGO_DIR=str(tmp_path / "marks"), APP_LOGOS="off"), none)
    assert custom.logo_dir == tmp_path / "marks" and not custom.logos_enabled
    assert load(environment(APP_LOGOS="ON"), none).logos_enabled
    with pytest.raises(SettingsError, match="APP_LOGOS"):
        load(environment(APP_LOGOS="maybe"), none)


def test_the_environment_wins_over_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        f"CFB_APP_PASSWORD=from-dotenv\nCFB_CONSOLE_PASSWORD={CONSOLE_PASSWORD}\nAPP_PORT=9001\n"
    )
    loaded = load({"CFB_APP_PASSWORD": PASSWORD, "APP_PORT": "9002"}, dotenv)
    assert loaded.app_password == PASSWORD
    assert loaded.console_password == CONSOLE_PASSWORD
    assert loaded.port == 9002


def test_the_read_password_is_required_and_the_error_names_only_the_variable() -> None:
    with pytest.raises(SettingsError, match="CFB_APP_PASSWORD") as error:
        load({})
    assert PASSWORD not in str(error.value)


def test_no_password_appears_in_the_repr_or_description() -> None:
    loaded = load(
        environment(CFB_CONSOLE_PASSWORD=CONSOLE_PASSWORD, CFB_TEST_OWNER_PASSWORD="o-pw")
    )
    text = repr(loaded) + loaded.describe()
    for secret in (PASSWORD, CONSOLE_PASSWORD, "o-pw"):
        assert secret not in text


def test_ports_are_validated() -> None:
    for bad in ("0", "70000", "http"):
        with pytest.raises(SettingsError, match="APP_PORT"):
            load(environment(APP_PORT=bad))


def test_a_network_bind_turns_the_operator_off_and_adds_allowed_hosts() -> None:
    loaded = load(
        environment(APP_HOST="lan.example", APP_ALLOWED_HOSTS="lan.example:8000", APP_PORT="8000")
    )
    assert not loaded.operator.enabled
    assert "lan.example:8000" in loaded.allowed_hosts


def test_operator_on_with_a_network_bind_refuses_to_start() -> None:
    with pytest.raises(SettingsError, match="APP_OPERATOR"):
        load(environment(APP_HOST="0.0.0.0", APP_OPERATOR="on"))


def test_a_prefix_needs_a_valid_form_and_an_explicit_ledger_path() -> None:
    with pytest.raises(SettingsError, match="APP_LEDGER_PATH"):
        load(environment(APP_PREFIX="TEST_"))
    loaded = load(environment(APP_PREFIX="TEST_", APP_LEDGER_PATH="/tmp/ledger.jsonl"))
    assert loaded.prefix == "TEST_" and loaded.ledger_path == Path("/tmp/ledger.jsonl")
    with pytest.raises(SettingsError, match="APP_PREFIX"):
        load(environment(APP_PREFIX="1bad", APP_LEDGER_PATH="/tmp/ledger.jsonl"))


@pytest.mark.parametrize(
    ("value", "enabled"), [("off", False), ("OFF", False), ("on", True), ("", True)]
)
def test_the_ledger_switch_follows_the_loader_rule(value: str, enabled: bool) -> None:
    env = environment(LEDGER=value) if value else environment()
    assert load(env).ledger_enabled is enabled


def test_flags_accept_yes_and_one() -> None:
    loaded = load(environment(APP_DEV="1", APP_SKIP_READ_CHECK="yes"))
    assert loaded.dev and loaded.skip_read_check
    assert {"127.0.0.1:5173", "localhost:5173"} <= loaded.allowed_hosts
    assert not load(environment(APP_DEV="0", APP_SKIP_READ_CHECK="no")).dev


def test_the_dsn_override_and_target_are_read() -> None:
    loaded = load(environment(TARGET="aws", EXASOL_DSN="db.example/ABCDEF:8563"))
    assert (loaded.target, loaded.dsn_override) == ("aws", "db.example/ABCDEF:8563")
    with pytest.raises(SettingsError, match="TARGET"):
        load(environment(TARGET="mars"))


def test_the_ledger_plan_is_configurable_and_a_blank_plan_is_refused() -> None:
    assert load(environment(APP_LEDGER_PLAN="add-cfb-app")).ledger_plan == "add-cfb-app"
    with pytest.raises(SettingsError, match="APP_LEDGER_PLAN"):
        load(environment(APP_LEDGER_PLAN="  "))
