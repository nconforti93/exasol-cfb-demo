"""Unit tests of `app/backend/security.py`: the `Host` allowlist (name and port), the `POST` checks
with a configured `Origin` set, CORS, the security headers, the session cookie and the
effective-privilege check of the read, console and chat users."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from app.backend import security, settings

PORT = 8000
HOSTS = settings.host_allowlist(PORT, dev=False, extra=())
ORIGINS = settings.origin_set(PORT, dev=False, cors=("http://demo.example:3000",))
CORS = ("http://demo.example:3000",)
ACTION = {"X-CFB-Action": "1", "Content-Type": "application/json"}


def config(**overrides: Any) -> security.SecurityConfig:
    values: dict[str, Any] = {
        "allowed_hosts": HOSTS,
        "origins": ORIGINS,
        "cors_origins": CORS,
    }
    return security.SecurityConfig(**{**values, **overrides})


class Calls:
    def __init__(self) -> None:
        self.count = 0
        self.session_ids: list[str] = []


def make_client(
    *, calls: Calls | None = None, base: str = "http://127.0.0.1:8000", **overrides: Any
) -> tuple[TestClient, Calls]:
    seen = calls or Calls()

    def ping(request: Request) -> Response:
        seen.count += 1
        seen.session_ids.append(request.state.session_id)
        return JSONResponse({"ok": True})

    async def act(request: Request) -> Response:
        seen.count += 1
        return JSONResponse({"acted": True})

    app = Starlette(
        routes=[
            Route("/api/ping", ping, methods=["GET", "HEAD"]),
            Route("/api/act", act, methods=["POST", "PUT", "DELETE", "PATCH"]),
        ],
        middleware=security.middleware(config(**overrides)),
    )
    return TestClient(app, base_url=base), seen


def test_hosts_match_by_name_and_port() -> None:
    for allowed_header in ("127.0.0.1:8000", "localhost:8000", "LOCALHOST:8000", "[::1]:8000"):
        assert security.host_allowed(allowed_header, HOSTS)
    refused: tuple[str | None, ...] = (
        None,
        "",
        "evil.example:8000",
        "127.0.0.1:8001",
        "localhost:9999",
        "127.0.0.1",
        "localhost:8000.evil.example",
        "evil.example:8000@127.0.0.1:8000",
    )
    for header in refused:
        assert not security.host_allowed(header, HOSTS)


def test_a_rebinding_attempt_is_refused_before_routing() -> None:
    client, calls = make_client()
    headers = {"Host": "evil.example:8000", "Origin": "http://evil.example:8000", **ACTION}
    assert client.get("/api/ping", headers={"Host": "evil.example:8000"}).status_code == 400
    assert client.post("/api/act", headers=headers, json={}).status_code == 400
    assert calls.count == 0


def test_the_wrong_port_of_an_allowed_name_is_refused() -> None:
    client, calls = make_client()
    assert client.get("/api/ping", headers={"Host": "localhost:9999"}).status_code == 400
    assert calls.count == 0


def test_a_refused_host_gets_json_without_an_echo_or_a_cookie() -> None:
    client, _ = make_client()
    response = client.get("/api/ping", headers={"Host": "evil.example:8000"})
    assert response.status_code == 400
    assert response.json()["error"] == "bad_host"
    assert "evil" not in response.text
    assert security.SESSION_COOKIE not in response.headers.get("set-cookie", "")


def test_an_allowed_host_reaches_the_route() -> None:
    client, calls = make_client()
    assert client.get("/api/ping").status_code == 200
    assert client.get("/api/ping", headers={"Host": "localhost:8000"}).status_code == 200
    assert calls.count == 2


def origin_headers(origin: str | None, **extra: str) -> dict[str, str]:
    headers = {**ACTION, **extra}
    if origin is not None:
        headers["Origin"] = origin
    return headers


@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
def test_a_valid_action_passes_for_every_unsafe_method(method: str) -> None:
    client, calls = make_client()
    response = client.request(
        method.upper(), "/api/act", headers=origin_headers("http://127.0.0.1:8000"), content=b"{}"
    )
    assert response.status_code == 200 and calls.count == 1


def test_a_post_without_the_action_header_is_refused() -> None:
    client, calls = make_client()
    headers = {"Content-Type": "application/json", "Origin": "http://127.0.0.1:8000"}
    response = client.post("/api/act", headers=headers, json={})
    assert (
        response.status_code == 403
        and response.json()["error"] == "forbidden"
        and "X-CFB-Action" in response.json()["message"]
    )
    assert calls.count == 0


def test_the_action_header_must_be_exactly_one() -> None:
    client, calls = make_client()
    headers = origin_headers("http://127.0.0.1:8000", **{"X-CFB-Action": "yes"})
    assert client.post("/api/act", headers=headers, json={}).status_code == 403
    assert calls.count == 0


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", ""])
def test_a_post_must_be_json(content_type: str) -> None:
    client, calls = make_client()
    headers = origin_headers("http://127.0.0.1:8000", **{"Content-Type": content_type})
    response = client.post("/api/act", headers=headers, content=b"{}")
    assert (
        response.status_code == 403
        and response.json()["error"] == "forbidden"
        and "application/json" in response.json()["message"]
    )
    assert calls.count == 0


def test_a_json_content_type_with_a_charset_passes() -> None:
    client, _ = make_client()
    headers = origin_headers(
        "http://127.0.0.1:8000", **{"Content-Type": "Application/JSON; charset=utf-8"}
    )
    assert client.post("/api/act", headers=headers, content=b"{}").status_code == 200


@pytest.mark.parametrize(
    "origin", ["http://attacker.example", "null", "http://127.0.0.1:8001", None, ""]
)
def test_a_post_from_another_origin_or_without_one_is_refused(origin: str | None) -> None:
    client, calls = make_client()
    response = client.post("/api/act", headers=origin_headers(origin), json={})
    assert (
        response.status_code == 403
        and response.json()["error"] == "forbidden"
        and "origin" in response.json()["message"]
    )
    assert calls.count == 0


def test_the_origin_is_never_derived_from_the_host() -> None:
    client, calls = make_client(
        allowed_hosts=HOSTS | {"evil.example:8000"}, base="http://evil.example:8000"
    )
    headers = origin_headers("http://evil.example:8000", Host="evil.example:8000")
    assert client.get("/api/ping").status_code == 200
    assert client.post("/api/act", headers=headers, json={}).status_code == 403
    assert calls.count == 1


def test_a_configured_cors_origin_may_post() -> None:
    client, _ = make_client()
    headers = origin_headers("http://demo.example:3000")
    assert client.post("/api/act", headers=headers, json={}).status_code == 200


def test_safe_methods_need_no_action_header() -> None:
    client, _ = make_client()
    assert client.get("/api/ping").status_code == 200
    assert client.head("/api/ping").status_code == 200


def test_cors_answers_only_listed_origins() -> None:
    client, _ = make_client()
    preflight = {
        "Origin": "http://demo.example:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-cfb-action,content-type",
    }
    allowed = client.options("/api/act", headers=preflight)
    assert allowed.headers["access-control-allow-origin"] == "http://demo.example:3000"
    foreign = client.options("/api/act", headers={**preflight, "Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in foreign.headers
    simple = client.get("/api/ping", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in simple.headers


def test_a_preflight_with_a_foreign_host_is_refused_before_cors() -> None:
    client, _ = make_client()
    response = client.options(
        "/api/act",
        headers={
            "Host": "evil.example:8000",
            "Origin": "http://demo.example:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 400


def test_the_security_headers_are_on_every_response() -> None:
    client, _ = make_client()
    for response in (
        client.get("/api/ping"),
        client.get("/api/ping", headers={"Host": "evil.example:8000"}),
        client.post("/api/act", json={}),
    ):
        csp = response.headers["content-security-policy"]
        assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_a_session_cookie_is_issued_once_and_is_http_only_and_strict() -> None:
    client, calls = make_client()
    first = client.get("/api/ping")
    cookie = first.headers["set-cookie"]
    assert cookie.startswith(f"{security.SESSION_COOKIE}=")
    assert "HttpOnly" in cookie and "samesite=strict" in cookie.lower() and "Path=/" in cookie
    second = client.get("/api/ping")
    assert "set-cookie" not in second.headers
    assert calls.session_ids[0] == calls.session_ids[1]


def test_a_forged_session_cookie_is_replaced() -> None:
    client, calls = make_client()
    response = client.get("/api/ping", headers={"Cookie": f"{security.SESSION_COOKIE}=<script>"})
    assert security.SESSION_COOKIE in response.headers["set-cookie"]
    assert security.valid_session_id(calls.session_ids[0])


def test_session_ids_are_random_and_well_formed() -> None:
    ids = {security.new_session_id() for _ in range(50)}
    assert len(ids) == 50 and all(security.valid_session_id(value) for value in ids)
    for bad in ("", "short", "a" * 200, "has space " + "a" * 40, None):
        assert not security.valid_session_id(bad)


def test_the_cookie_is_secure_only_when_asked() -> None:
    client, _ = make_client(secure_cookie=True)
    assert "Secure" in client.get("/api/ping").headers["set-cookie"]


PRIVILEGE_USER = "CFB_TEST_APP"
ROLE = "TEST_CFB_APP"
SCHEMAS = ("TEST_CFB_API", "TEST_CFB_API_LIVE")


def baseline_rows() -> dict[str, list[tuple[Any, ...]]]:
    """What the probe saw for a user with only CREATE SESSION and a role that has schema SELECT."""
    return {
        "session_privs": [("CREATE SESSION",), ("USE ANY SCHEMA",)],
        "session_roles": [(ROLE,)],
        "user_sys": [(PRIVILEGE_USER, "CREATE SESSION")],
        "role_sys": [("PUBLIC", "USE ANY SCHEMA")],
        "user_roles": [(PRIVILEGE_USER, ROLE)],
        "role_roles": [],
        "all_obj": [(None, schema, "SCHEMA", "SELECT", ROLE) for schema in SCHEMAS],
        "user_conn": [],
        "role_conn": [],
        "user_restricted": [],
        "role_restricted": [],
        "user_impersonation": [],
        "user_schemas": [],
        "user_objects": [],
    }


def check(
    rows: Mapping[str, Sequence[tuple[Any, ...]]],
    *,
    impersonation: Sequence[tuple[Any, ...]] | None = (),
    user: str = PRIVILEGE_USER,
    roles: Sequence[str] = (ROLE,),
    schemas: Sequence[str] = SCHEMAS,
    forbidden: Sequence[str] = ("CFB_TEST_OWNER",),
) -> security.PrivilegeReport:
    snapshot = security.PrivilegeSnapshot.from_rows(rows, impersonation)
    return security.check_effective_privileges(
        snapshot, user=user, roles=roles, schemas=schemas, forbidden_users=forbidden
    )


def messages(report: security.PrivilegeReport) -> str:
    return " | ".join(violation.message(PRIVILEGE_USER) for violation in report.violations)


def test_the_expected_privileges_pass_with_the_public_default() -> None:
    report = check(baseline_rows())
    assert report.ok and report.unverified == ()


def test_the_system_privilege_queries_cover_every_snapshot_key() -> None:
    assert set(security.PRIVILEGE_QUERIES) == set(baseline_rows())
    for sql in security.PRIVILEGE_QUERIES.values():
        assert sql.startswith('SELECT "') and ' FROM "SYS"."EXA_' in sql
    assert "EXA_DBA" not in " ".join(security.PRIVILEGE_QUERIES.values())
    assert 'FROM "SYS"."EXA_DBA_IMPERSONATION_PRIVS"' in security.IMPERSONATION_QUERY


def test_the_namespace_owner_and_sys_are_refused_as_the_read_user() -> None:
    for name in ("CFB_TEST_OWNER", "sys", "SYS"):
        report = check(baseline_rows(), user=name)
        assert not report.ok
        assert name in report.violations[0].message(name)
    assert "namespace owner" in check(baseline_rows(), user="CFB_TEST_OWNER").violations[0].reason
    assert "SYS" in check(baseline_rows(), user="sys").violations[0].reason.upper()


def test_a_direct_system_privilege_is_named_with_its_route() -> None:
    rows = baseline_rows()
    rows["session_privs"].append(("CREATE TABLE",))
    rows["user_sys"].append((PRIVILEGE_USER, "CREATE TABLE"))
    report = check(rows)
    assert not report.ok
    text = messages(report)
    assert PRIVILEGE_USER in text and "CREATE TABLE" in text and "directly" in text


def test_a_system_privilege_through_a_role_names_the_role() -> None:
    rows = baseline_rows()
    rows["session_privs"].append(("CREATE VIEW",))
    rows["role_sys"].append((ROLE, "CREATE VIEW"))
    text = messages(check(rows))
    assert "CREATE VIEW" in text and f"role {ROLE}" in text


def test_a_system_privilege_without_a_visible_grant_names_the_roles() -> None:
    rows = baseline_rows()
    rows["session_privs"].append(("DROP ANY TABLE",))
    assert f"through its roles ({ROLE})" in messages(check(rows))


def test_use_any_schema_is_tolerated_only_from_public() -> None:
    rows = baseline_rows()
    rows["user_sys"].append((PRIVILEGE_USER, "USE ANY SCHEMA"))
    assert not check(rows).ok
    only_public = baseline_rows()
    only_public["role_sys"].append((ROLE, "USE ANY SCHEMA"))
    assert not check(only_public).ok


def test_the_grant_of_insert_on_a_schema_through_the_role_is_refused() -> None:
    rows = baseline_rows()
    rows["all_obj"].append((None, "TEST_CFB_META", "SCHEMA", "INSERT", ROLE))
    text = messages(check(rows))
    assert PRIVILEGE_USER in text and "INSERT" in text and "TEST_CFB_META" in text
    assert f"role {ROLE}" in text


def test_a_select_grant_on_public_reaches_the_user() -> None:
    rows = baseline_rows()
    rows["all_obj"].append((None, "TEST_CFB_SIM", "SCHEMA", "SELECT", "PUBLIC"))
    text = messages(check(rows))
    assert "TEST_CFB_SIM" in text and "PUBLIC" in text


def test_a_grant_on_a_single_object_is_refused_even_when_it_is_a_select() -> None:
    rows = baseline_rows()
    rows["all_obj"].append(("TEST_CFB_API", "PLAYOFF_ODDS", "VIEW", "SELECT", ROLE))
    assert not check(rows).ok


def test_a_missing_schema_grant_is_refused_by_name() -> None:
    rows = baseline_rows()
    rows["all_obj"] = rows["all_obj"][:1]
    assert "TEST_CFB_API_LIVE" in messages(check(rows))


def test_a_connection_grant_is_refused_by_every_route() -> None:
    rows = baseline_rows()
    rows["role_conn"].append((ROLE, "TEST_CFBD_API_MOCK"))
    text = messages(check(rows))
    assert "TEST_CFBD_API_MOCK" in text and f"role {ROLE}" in text
    for key, grantee in (("user_conn", PRIVILEGE_USER), ("role_conn", "PUBLIC")):
        again = baseline_rows()
        again[key].append((grantee, "C"))
        assert not check(again).ok


def test_access_on_a_connection_is_refused_with_and_without_a_script() -> None:
    for key in ("user_restricted", "role_restricted"):
        rows = baseline_rows()
        rows[key].append((ROLE, "TEST_CFBD_API_MOCK", None))
        assert "TEST_CFBD_API_MOCK" in messages(check(rows))
        rows = baseline_rows()
        rows[key].append((ROLE, "TEST_CFBD_API_MOCK", "CFBD_FETCH"))
        assert "CFBD_FETCH" in messages(check(rows))


def test_an_owned_schema_or_object_is_refused() -> None:
    rows = baseline_rows()
    rows["user_schemas"].append(("MINE",))
    assert "MINE" in messages(check(rows))
    rows = baseline_rows()
    rows["user_objects"].append(("T_OWN", "TABLE"))
    assert "T_OWN" in messages(check(rows))


def test_impersonation_granted_to_the_user_is_refused_from_its_own_view() -> None:
    rows = baseline_rows()
    rows["user_impersonation"].append((PRIVILEGE_USER, "SYS"))
    report = check(rows, impersonation=None)
    assert not report.ok and "SYS" in messages(report)


def test_impersonation_through_a_role_or_public_needs_the_database_wide_view() -> None:
    blind = check(baseline_rows(), impersonation=None)
    assert blind.ok
    assert blind.unverified == (security.UNVERIFIED_IMPERSONATION,)
    for grantee in (ROLE, "PUBLIC", PRIVILEGE_USER):
        report = check(baseline_rows(), impersonation=[(grantee, "CFB_TEST_OWNER", "SYS")])
        assert not report.ok and "CFB_TEST_OWNER" in messages(report)
    unrelated = check(baseline_rows(), impersonation=[("SOMEONE_ELSE", "SYS", "SYS")])
    assert unrelated.ok and unrelated.unverified == ()


def test_an_extra_direct_role_is_refused_even_without_privileges() -> None:
    rows = baseline_rows()
    rows["session_roles"].append(("HARMLESS",))
    text = messages(check(rows))
    assert "HARMLESS" in text


def test_the_dba_role_is_refused_as_a_nested_role() -> None:
    rows = baseline_rows()
    rows["session_roles"].append(("DBA",))
    rows["role_roles"].append((ROLE, "DBA"))
    rows["session_privs"] += [("ALTER SYSTEM",), ("CREATE USER",)]
    text = messages(check(rows))
    assert "DBA" in text and "ALTER SYSTEM" in text


def test_the_console_user_may_hold_its_schema_directly_and_no_role() -> None:
    rows = baseline_rows()
    rows["session_roles"] = []
    rows["user_roles"] = []
    rows["all_obj"] = [(None, "TEST_CFBD_VS", "SCHEMA", "SELECT", "CFB_TEST_READER")]
    report = check(rows, user="CFB_TEST_READER", roles=(), schemas=("TEST_CFBD_VS",))
    assert report.ok


def test_a_long_list_of_violations_is_summarized_in_the_error() -> None:
    rows = baseline_rows()
    rows["session_privs"] += [(f"PRIV {number}",) for number in range(40)]
    with pytest.raises(security.PrivilegeError) as error:
        check(rows).raise_if_violations(PRIVILEGE_USER)
    text = str(error.value)
    assert PRIVILEGE_USER in text and "more" in text and len(text) < 1500


def test_a_clean_report_does_not_raise() -> None:
    check(baseline_rows()).raise_if_violations(PRIVILEGE_USER)


def test_the_check_is_case_insensitive_on_names() -> None:
    rows = baseline_rows()
    report = check(rows, user=PRIVILEGE_USER.lower(), roles=(ROLE.lower(),))
    assert report.ok


def test_the_public_baseline_of_a_fresh_deployment_is_use_any_schema_only() -> None:
    """Probe s9 (d): PUBLIC holds `USE ANY SCHEMA` on a fresh deployment, so a user with only
    `CREATE SESSION` sees both in `EXA_SESSION_PRIVS`. That one inherited privilege is allowed."""
    assert frozenset({"USE ANY SCHEMA"}) == security.PUBLIC_SYSTEM_PRIVILEGES
    rows = baseline_rows()
    assert ("USE ANY SCHEMA",) in rows["session_privs"] and rows["role_sys"] == [
        ("PUBLIC", "USE ANY SCHEMA")
    ]
    assert check(rows).ok


@pytest.mark.parametrize(
    "privilege",
    [
        "INSERT ANY TABLE",
        "UPDATE ANY TABLE",
        "DELETE ANY TABLE",
        "CREATE TABLE",
        "IMPORT",
        "EXPORT",
        "ACCESS ANY CONNECTION",
        "IMPERSONATE ANY USER",
    ],
)
def test_nothing_beyond_the_public_baseline_is_tolerated_from_public(privilege: str) -> None:
    rows = baseline_rows()
    rows["session_privs"].append((privilege,))
    rows["role_sys"].append(("PUBLIC", privilege))
    text = messages(check(rows))
    assert privilege in text and "PUBLIC" in text


@pytest.mark.parametrize("privilege", ["INSERT", "UPDATE", "DELETE", "EXECUTE", "IMPORT", "EXPORT"])
def test_an_object_privilege_other_than_select_from_public_is_refused(privilege: str) -> None:
    rows = baseline_rows()
    rows["all_obj"].append((None, SCHEMAS[0], "SCHEMA", privilege, "PUBLIC"))
    assert privilege in messages(check(rows))
