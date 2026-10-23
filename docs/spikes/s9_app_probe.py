#!/usr/bin/env python3
"""S9 app probe (task 1.1 / 1.10, plan add-cfb-app).

`--part early` checks three platform facts the app's security design rests on, as `sys` on a
deployment (default: Exasol Personal local), in a `PROBE_APP_*` namespace only:

(a) Lua scripts run with the caller's rights (decisive for decision [2]: the operator path).
(d) Which dictionary views show a user's effective privileges (decision [25]'s start check),
    what the user sees itself and what only the database-wide views show; and whether
    `CREATE USER ... IDENTIFIED BY` text is masked in the audit and statistics views.
(e) `ALTER SESSION SET QUERY_TIMEOUT` is accepted, aborts a 35 s statement at 30 s, and lets
    the same statement finish at 300 s.

Safety rules: only `PROBE_APP_*` users, roles, a connection (dummy address 192.0.2.1, never
contacted, dummy secret), schemas, and a Python UDF that sleeps. Everything is dropped at the
end (also after a failure) unless `--keep`. No CFBD call. The sys password is read by
`vs/host/target.py` at connect time and never printed; probe passwords are random, in memory.

Usage:
    uv run python docs/spikes/s9_app_probe.py --part early [--only a,d,e] [--keep]

Writes cache/probe/s9-app-probe-early.json (git-ignored). `--part late` (task 1.10) is not
implemented here yet.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from vs.host import target as target_module  # noqa: E402

RESULTS_FILE = REPO_ROOT / "cache" / "probe" / "s9-app-probe-early.json"

OWNER = "PROBE_APP_OWNER"
SCHEMA = "PROBE_APP_S"
SCHEMA_2 = "PROBE_APP_S2"
CALLER = "PROBE_APP_CALLER"
READER_USER = "PROBE_APP_RU"
ROLE = "PROBE_APP_ROLE"
INNER_ROLE = "PROBE_APP_INNER"
CONNECTION = "PROBE_APP_CONN"
PASSWORD_USER = "PROBE_APP_PW"
TIMEOUT_USER = "PROBE_APP_TO"
USER_SCHEMA = "PROBE_APP_RU_S"
ALL_USERS = (OWNER, CALLER, READER_USER, PASSWORD_USER, TIMEOUT_USER)
ALL_ROLES = (ROLE, INNER_ROLE)
ALL_SCHEMAS = (SCHEMA, SCHEMA_2, USER_SCHEMA)
SECRET_TOKEN = "ProbeSecretToken" + secrets.token_hex(4)

SLOW_SECONDS = 35
SHORT_TIMEOUT = 30
LONG_TIMEOUT = 300
SOCKET_TIMEOUT = 90

USER_VIEWS = (
    "EXA_USER_SYS_PRIVS",
    "EXA_USER_OBJ_PRIVS",
    "EXA_USER_ROLE_PRIVS",
    "EXA_USER_CONNECTION_PRIVS",
    "EXA_USER_IMPERSONATION_PRIVS",
    "EXA_USER_RESTRICTED_OBJ_PRIVS",
    "EXA_ROLE_SYS_PRIVS",
    "EXA_ROLE_OBJ_PRIVS",
    "EXA_ROLE_ROLE_PRIVS",
    "EXA_ROLE_CONNECTION_PRIVS",
    "EXA_ROLE_RESTRICTED_OBJ_PRIVS",
    "EXA_SESSION_PRIVS",
    "EXA_SESSION_ROLES",
    "EXA_ALL_OBJ_PRIVS",
    "EXA_ALL_CONNECTIONS",
    "EXA_ALL_SCHEMAS",
    "EXA_ALL_OBJECTS",
    "EXA_USER_SCHEMAS",
    "EXA_USER_OBJECTS",
    "EXA_DBA_SYS_PRIVS",
)
DBA_VIEWS = (
    "EXA_DBA_SYS_PRIVS",
    "EXA_DBA_OBJ_PRIVS",
    "EXA_DBA_ROLE_PRIVS",
    "EXA_DBA_CONNECTION_PRIVS",
    "EXA_DBA_IMPERSONATION_PRIVS",
    "EXA_DBA_RESTRICTED_OBJ_PRIVS",
    "EXA_DBA_SCHEMAS",
    "EXA_DBA_OBJECTS",
)
RELEVANT = tuple(ALL_USERS) + tuple(ALL_ROLES) + ("PUBLIC",)


class Probe:
    def __init__(self) -> None:
        self.target = target_module.resolve()
        self.passwords = {user: "pw_" + secrets.token_urlsafe(12) for user in ALL_USERS}
        self.results: dict[str, Any] = {}

    def sys(self, socket_timeout: float | None = None) -> Any:
        return self.target.connect(socket_timeout=socket_timeout)

    def as_user(self, user: str, socket_timeout: float | None = None) -> Any:
        return self.target.connect(user, self.passwords[user], socket_timeout=socket_timeout)

    def run(self, sys_conn: Any, statement: str, *args: Any) -> None:
        sys_conn.execute(statement.format(*args) if args else statement)

    def cleanup(self) -> list[str]:
        problems: list[str] = []
        conn = self.sys()
        try:
            for user in ALL_USERS:
                _try(problems, conn, f"DROP USER IF EXISTS {user} CASCADE")
            for role in ALL_ROLES:
                _try(problems, conn, f"DROP ROLE IF EXISTS {role} CASCADE")
            for schema in ALL_SCHEMAS:
                _try(problems, conn, f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            _try(problems, conn, f"DROP CONNECTION IF EXISTS {CONNECTION}")
            left = conn.execute(
                "SELECT 'USER', USER_NAME FROM EXA_ALL_USERS WHERE USER_NAME LIKE 'PROBE_APP%' "
                "UNION ALL SELECT 'ROLE', ROLE_NAME FROM EXA_ALL_ROLES WHERE ROLE_NAME LIKE 'PROBE_APP%' "
                "UNION ALL SELECT 'SCHEMA', SCHEMA_NAME FROM EXA_ALL_SCHEMAS "
                "WHERE SCHEMA_NAME LIKE 'PROBE_APP%' "
                "UNION ALL SELECT 'CONNECTION', CONNECTION_NAME FROM EXA_DBA_CONNECTIONS "
                "WHERE CONNECTION_NAME LIKE 'PROBE_APP%'"
            ).fetchall()
            if left:
                problems.append(f"left over: {left}")
        finally:
            conn.close()
        return problems


def _try(problems: list[str], conn: Any, statement: str) -> None:
    try:
        conn.execute(statement)
    except Exception as error:
        problems.append(f"{statement}: {_first_line(error)}")


def _first_line(error: BaseException) -> str:
    message = getattr(error, "message", None) or str(error)
    lines = [line.strip() for line in str(message).splitlines() if line.strip() not in ("", "(", ")")]
    return " | ".join(lines)[:400] if lines else type(error).__name__


def _outcome(call: Callable[[], Any]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        value = call()
        return {"ok": True, "value": value, "seconds": round(time.monotonic() - started, 2)}
    except Exception as error:
        return {
            "ok": False,
            "error": _first_line(error),
            "seconds": round(time.monotonic() - started, 2),
        }


def setup_namespace(probe: Probe) -> None:
    sys_conn = probe.sys()
    try:
        for user in (OWNER, CALLER, READER_USER, TIMEOUT_USER):
            password = probe.passwords[user]
            sys_conn.execute(f"CREATE USER {user} IDENTIFIED BY {{password!q}}", {"password": password})
            sys_conn.execute(f"GRANT CREATE SESSION TO {user}")
        sys_conn.execute(
            f"GRANT CREATE SCHEMA, CREATE TABLE, CREATE VIEW, CREATE SCRIPT TO {OWNER}"
        )
    finally:
        sys_conn.close()


def part_a(probe: Probe) -> dict[str, Any]:
    """Invoker rights: a user with only EXECUTE on two scripts cannot read or write."""
    owner = probe.as_user(OWNER)
    try:
        owner.execute(f"CREATE SCHEMA {SCHEMA}")
        owner.execute(f"CREATE TABLE {SCHEMA}.T_DATA (N DECIMAL(9,0))")
        owner.execute(f"INSERT INTO {SCHEMA}.T_DATA VALUES (1)")
        owner.execute(f"CREATE VIEW {SCHEMA}.V_DATA AS SELECT N FROM {SCHEMA}.T_DATA")
        owner.execute(
            f"CREATE OR REPLACE LUA SCRIPT {SCHEMA}.R_READ_T() AS\n"
            f"  local r = query([[SELECT COUNT(*) FROM {SCHEMA}.T_DATA]])\n"
            f"  return {{rows_affected = r[1][1]}}\n"
        )
        owner.execute(
            f"CREATE OR REPLACE LUA SCRIPT {SCHEMA}.R_WRITE_T() AS\n"
            f"  query([[INSERT INTO {SCHEMA}.T_DATA VALUES (2)]])\n"
            f"  return {{rows_affected = 1}}\n"
        )
        owner.execute(
            f"CREATE OR REPLACE LUA SCRIPT {SCHEMA}.R_VIEW_T() AS\n"
            f"  local r = query([[SELECT COUNT(*) FROM {SCHEMA}.V_DATA]])\n"
            f"  return {{rows_affected = r[1][1]}}\n"
        )
        owner_runs = {
            "read": _outcome(lambda: owner.execute(f"EXECUTE SCRIPT {SCHEMA}.R_READ_T()").rowcount()),
            "write": _outcome(
                lambda: owner.execute(f"EXECUTE SCRIPT {SCHEMA}.R_WRITE_T()").rowcount()
            ),
        }
        owner.execute(f"DELETE FROM {SCHEMA}.T_DATA WHERE N = 2")
        owner_rows_before = owner.execute(f"SELECT COUNT(*) FROM {SCHEMA}.T_DATA").fetchval()
    finally:
        owner.close()

    sys_conn = probe.sys()
    try:
        for script in ("R_READ_T", "R_WRITE_T", "R_VIEW_T"):
            sys_conn.execute(f"GRANT EXECUTE ON {SCHEMA}.{script} TO {CALLER}")
        sys_conn.execute(f"GRANT SELECT ON {SCHEMA}.V_DATA TO {CALLER}")
    finally:
        sys_conn.close()

    caller = probe.as_user(CALLER)
    try:
        runs = {
            "read": _outcome(lambda: caller.execute(f"EXECUTE SCRIPT {SCHEMA}.R_READ_T()").rowcount()),
            "write": _outcome(
                lambda: caller.execute(f"EXECUTE SCRIPT {SCHEMA}.R_WRITE_T()").rowcount()
            ),
            "view_in_script": _outcome(
                lambda: caller.execute(f"EXECUTE SCRIPT {SCHEMA}.R_VIEW_T()").rowcount()
            ),
            "view_direct": _outcome(
                lambda: caller.execute(f"SELECT COUNT(*) FROM {SCHEMA}.V_DATA").fetchval()
            ),
            "table_direct": _outcome(
                lambda: caller.execute(f"SELECT COUNT(*) FROM {SCHEMA}.T_DATA").fetchval()
            ),
        }
    finally:
        caller.close()

    sys_conn = probe.sys()
    try:
        rows_after = sys_conn.execute(f"SELECT COUNT(*) FROM {SCHEMA}.T_DATA").fetchval()
        version = sys_conn.execute(
            "SELECT PARAM_VALUE FROM EXA_METADATA WHERE PARAM_NAME = 'databaseProductVersion'"
        ).fetchval()
    finally:
        sys_conn.close()
    invoker = (
        owner_runs["read"]["ok"]
        and owner_runs["write"]["ok"]
        and not runs["read"]["ok"]
        and not runs["write"]["ok"]
        and rows_after == owner_rows_before
    )
    return {
        "version": version,
        "owner_runs": owner_runs,
        "caller_runs": runs,
        "rows_before_caller": owner_rows_before,
        "rows_after_caller": rows_after,
        "caller_privileges": "CREATE SESSION, EXECUTE on the three scripts, SELECT on the view",
        "invoker_rights_confirmed": invoker,
        "view_runs_with_owner_rights": runs["view_direct"]["ok"] and not runs["table_direct"]["ok"],
    }


def _dba_snapshot(sys_conn: Any) -> dict[str, list[list[Any]]]:
    snapshot: dict[str, list[list[Any]]] = {}
    marks = ", ".join(f"'{name}'" for name in RELEVANT)
    for view in DBA_VIEWS:
        columns = list(sys_conn.execute(f"SELECT * FROM SYS.{view} LIMIT 0").columns())
        key = {
            "EXA_DBA_SCHEMAS": "SCHEMA_OWNER",
            "EXA_DBA_OBJECTS": "OWNER",
        }.get(view, "GRANTEE")
        rows = sys_conn.execute(f"SELECT * FROM SYS.{view} WHERE {key} IN ({marks})").fetchall()
        snapshot[view] = _sorted_rows(columns, rows, only_probe=view in ("EXA_DBA_OBJECTS",))
    return snapshot


def _sorted_rows(columns: list[str], rows: list[Any], *, only_probe: bool) -> list[list[Any]]:
    result = []
    for row in rows:
        values = [_plain(value) for value in row]
        if only_probe and not any(str(v).startswith("PROBE_APP") for v in values):
            continue
        result.append(values)
    return sorted(result, key=lambda values: [str(v) for v in values])


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _user_snapshot(probe: Probe, user: str) -> dict[str, Any]:
    conn = probe.as_user(user)
    snapshot: dict[str, Any] = {}
    try:
        for view in USER_VIEWS:
            try:
                cursor = conn.execute(f"SELECT * FROM SYS.{view}")
                columns = list(cursor.columns())
                rows = cursor.fetchall()
            except Exception as error:
                snapshot[view] = {"error": _first_line(error).split(" (Session:")[0]}
                continue
            keep = []
            for row in rows:
                values = [_plain(value) for value in row]
                text = " ".join(str(v) for v in values)
                if view in ("EXA_SESSION_PRIVS", "EXA_SESSION_ROLES"):
                    keep.append(values)
                elif "PROBE_APP" in text or "PUBLIC" in values:
                    keep.append(values)
            snapshot[view] = {
                "columns": columns,
                "rows": sorted(keep, key=lambda v: [str(x) for x in v]),
                "total_rows": len(rows),
            }
    finally:
        conn.close()
    return snapshot


def _delta(base: dict[str, Any], now: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for view, data in now.items():
        old = base.get(view, {})
        if isinstance(data, dict) and "rows" in data:
            before = {json.dumps(r) for r in old.get("rows", [])}
            added = [r for r in data["rows"] if json.dumps(r) not in before]
            after = {json.dumps(r) for r in data["rows"]}
            removed = [json.loads(r) for r in sorted(before - after)]
            if added or removed:
                changes[view] = {"added": added, "removed": removed}
        elif isinstance(data, list):
            before = {json.dumps(r) for r in old}
            added = [r for r in data if json.dumps(r) not in before]
            if added:
                changes[view] = {"added": added}
        elif data != old:
            changes[view] = {"now": data, "before": old}
    return changes


def part_d(probe: Probe) -> dict[str, Any]:
    sys_conn = probe.sys()
    try:
        sys_conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        sys_conn.execute(f"CREATE SCHEMA {SCHEMA_2}")
        sys_conn.execute(f"CREATE TABLE {SCHEMA_2}.T_OTHER (N DECIMAL(9,0))")
        sys_conn.execute(f"CREATE ROLE {INNER_ROLE}")
        sys_conn.execute(f"CREATE ROLE {ROLE}")
        sys_conn.execute(f"GRANT SELECT ON SCHEMA {SCHEMA} TO {INNER_ROLE}")
        sys_conn.execute(f"GRANT {INNER_ROLE} TO {ROLE}")
        sys_conn.execute(f"GRANT {ROLE} TO {READER_USER}")
        sys_conn.execute(
            f"CREATE CONNECTION {CONNECTION} TO 'https://192.0.2.1' USER 'probe' "
            f"IDENTIFIED BY 'not-a-secret'"
        )
        public = {
            "sys_privs": sys_conn.execute(
                "SELECT PRIVILEGE FROM SYS.EXA_DBA_SYS_PRIVS WHERE GRANTEE = 'PUBLIC' ORDER BY 1"
            ).fetchall(),
            "obj_privs": sys_conn.execute(
                "SELECT OBJECT_SCHEMA, OBJECT_NAME, PRIVILEGE FROM SYS.EXA_DBA_OBJ_PRIVS "
                "WHERE GRANTEE = 'PUBLIC' ORDER BY 1, 2, 3"
            ).fetchall(),
            "role_privs": sys_conn.execute(
                "SELECT GRANTED_ROLE FROM SYS.EXA_DBA_ROLE_PRIVS WHERE GRANTEE = 'PUBLIC'"
            ).fetchall(),
            "connection_privs": sys_conn.execute(
                "SELECT GRANTED_CONNECTION FROM SYS.EXA_DBA_CONNECTION_PRIVS "
                "WHERE GRANTEE = 'PUBLIC'"
            ).fetchall(),
        }
        public["obj_priv_count"] = len(public["obj_privs"])
        public["obj_privs"] = public["obj_privs"][:12]
        baseline_dba = _dba_snapshot(sys_conn)
    finally:
        sys_conn.close()
    baseline_user = _user_snapshot(probe, READER_USER)

    def step(
        name: str,
        grant: list[str],
        revoke: list[str],
        *,
        as_owner: bool = False,
        try_impersonate: bool = False,
    ) -> dict[str, Any]:
        sys_conn = probe.sys()
        try:
            for statement in grant:
                sys_conn.execute(statement)
            dba_now = _dba_snapshot(sys_conn)
        finally:
            sys_conn.close()
        user_now = _user_snapshot(probe, READER_USER)
        extra: dict[str, Any] = {}
        if try_impersonate:
            conn = probe.as_user(READER_USER)
            try:
                extra["impersonate_owner"] = _outcome(
                    lambda: (
                        conn.execute(f"IMPERSONATE {OWNER}"),
                        conn.execute("SELECT CURRENT_USER").fetchval(),
                    )[1]
                )
            finally:
                conn.close()
        if as_owner:
            conn = probe.as_user(READER_USER)
            try:
                conn.execute(f"CREATE SCHEMA {USER_SCHEMA}")
                conn.execute(f"CREATE TABLE {USER_SCHEMA}.T_OWN (N DECIMAL(9,0))")
                conn.close()
                user_now = _user_snapshot(probe, READER_USER)
                dba_conn = probe.sys()
                try:
                    dba_now = _dba_snapshot(dba_conn)
                finally:
                    dba_conn.close()
            except Exception as error:
                extra["error"] = _first_line(error)
        result = {
            "user_sees": _delta(baseline_user, user_now),
            "dba_views_show": _delta(baseline_dba, dba_now),
            **extra,
        }
        sys_conn = probe.sys()
        try:
            for statement in revoke:
                try:
                    sys_conn.execute(statement)
                except Exception as error:
                    result.setdefault("revoke_errors", []).append(_first_line(error))
        finally:
            sys_conn.close()
        return result

    steps: dict[str, Any] = {}
    steps["create_table_to_user"] = step(
        "create_table_to_user",
        [f"GRANT CREATE TABLE TO {READER_USER}"],
        [f"REVOKE CREATE TABLE FROM {READER_USER}"],
    )
    steps["insert_on_schema_to_role"] = step(
        "insert_on_schema_to_role",
        [f"GRANT INSERT ON SCHEMA {SCHEMA} TO {ROLE}"],
        [f"REVOKE INSERT ON SCHEMA {SCHEMA} FROM {ROLE}"],
    )
    steps["create_view_to_inner_role"] = step(
        "create_view_to_inner_role",
        [f"GRANT CREATE VIEW TO {INNER_ROLE}"],
        [f"REVOKE CREATE VIEW FROM {INNER_ROLE}"],
    )
    steps["select_on_schema_to_public"] = step(
        "select_on_schema_to_public",
        [f"GRANT SELECT ON SCHEMA {SCHEMA_2} TO PUBLIC"],
        [f"REVOKE SELECT ON SCHEMA {SCHEMA_2} FROM PUBLIC"],
    )
    steps["grant_connection_to_role"] = step(
        "grant_connection_to_role",
        [f"GRANT CONNECTION {CONNECTION} TO {ROLE}"],
        [f"REVOKE CONNECTION {CONNECTION} FROM {ROLE}"],
    )
    steps["grant_access_on_connection_to_role"] = step(
        "grant_access_on_connection_to_role",
        [f"GRANT ACCESS ON CONNECTION {CONNECTION} TO {ROLE}"],
        [f"REVOKE ACCESS ON CONNECTION {CONNECTION} FROM {ROLE}"],
    )
    steps["grant_access_on_connection_for_script_to_role"] = step(
        "grant_access_on_connection_for_script_to_role",
        [f"GRANT ACCESS ON CONNECTION {CONNECTION} FOR SCRIPT {SCHEMA}.R_READ_T TO {ROLE}"],
        [f"REVOKE ACCESS ON CONNECTION {CONNECTION} FOR SCRIPT {SCHEMA}.R_READ_T FROM {ROLE}"],
    )
    steps["connection_to_inner_role"] = step(
        "connection_to_inner_role",
        [f"GRANT CONNECTION {CONNECTION} TO {INNER_ROLE}"],
        [f"REVOKE CONNECTION {CONNECTION} FROM {INNER_ROLE}"],
    )
    steps["connection_to_public"] = step(
        "connection_to_public",
        [f"GRANT CONNECTION {CONNECTION} TO PUBLIC"],
        [f"REVOKE CONNECTION {CONNECTION} FROM PUBLIC"],
    )
    steps["access_on_connection_to_public"] = step(
        "access_on_connection_to_public",
        [f"GRANT ACCESS ON CONNECTION {CONNECTION} TO PUBLIC"],
        [f"REVOKE ACCESS ON CONNECTION {CONNECTION} FROM PUBLIC"],
    )
    steps["impersonation_to_role"] = step(
        "impersonation_to_role",
        [f"GRANT IMPERSONATION ON {OWNER} TO {ROLE}"],
        [f"REVOKE IMPERSONATION ON {OWNER} FROM {ROLE}"],
        try_impersonate=True,
    )
    steps["impersonation_to_public"] = step(
        "impersonation_to_public",
        [f"GRANT IMPERSONATION ON {OWNER} TO PUBLIC"],
        [f"REVOKE IMPERSONATION ON {OWNER} FROM PUBLIC"],
        try_impersonate=True,
    )
    steps["dba_role_to_inner_role"] = step(
        "dba_role_to_inner_role",
        [f"GRANT DBA TO {INNER_ROLE}"],
        [f"REVOKE DBA FROM {INNER_ROLE}"],
    )
    steps["impersonation_to_user"] = step(
        "impersonation_to_user",
        [f"GRANT IMPERSONATION ON {OWNER} TO {READER_USER}"],
        [f"REVOKE IMPERSONATION ON {OWNER} FROM {READER_USER}"],
        try_impersonate=True,
    )
    steps["owned_schema_and_table"] = step(
        "owned_schema_and_table",
        [f"GRANT CREATE SCHEMA, CREATE TABLE TO {READER_USER}"],
        [
            f"DROP SCHEMA IF EXISTS {USER_SCHEMA} CASCADE",
            f"REVOKE CREATE SCHEMA, CREATE TABLE FROM {READER_USER}",
        ],
        as_owner=True,
    )
    clean_again = _delta(baseline_user, _user_snapshot(probe, READER_USER))
    return {
        "baseline_user_sees": baseline_user,
        "baseline_dba_views": baseline_dba,
        "public_on_fresh_deployment": public,
        "steps": steps,
        "back_to_baseline_after_revokes": not clean_again,
        "residual_delta": clean_again,
        "password_masking": password_masking(probe),
    }


def password_masking(probe: Probe) -> dict[str, Any]:
    """Is the password masked in the audit and statistics views (review m2)?"""
    conn = probe.sys()
    try:
        report: dict[str, Any] = {}
        report["audit_rows_total"] = _outcome(
            lambda: conn.execute("SELECT COUNT(*) FROM EXA_DBA_AUDIT_SQL").fetchval()
        )
        conn.execute("ALTER SESSION SET PROFILE = 'ON'")
        conn.execute(
            f"CREATE USER {PASSWORD_USER} IDENTIFIED BY {{password!q}}",
            {"password": SECRET_TOKEN + "create"},
        )
        conn.execute(
            f"ALTER USER {PASSWORD_USER} IDENTIFIED BY {{password!q}}",
            {"password": SECRET_TOKEN + "alter"},
        )
        conn.execute("ALTER SESSION SET PROFILE = 'OFF'")
        conn.execute("FLUSH STATISTICS")
        time.sleep(2)
        pattern = f"%USER {PASSWORD_USER}%IDENTIFIED%"
        views = [
            "EXA_STATISTICS.EXA_DBA_AUDIT_SQL",
            "EXA_STATISTICS.EXA_DBA_PROFILE_LAST_DAY",
            "EXA_STATISTICS.EXA_USER_PROFILE_LAST_DAY",
            "SYS.EXA_DBA_SESSIONS",
        ]
        report["views_with_sql_text"] = views
        for view in views:
            report[view] = _outcome(
                lambda view=view: [
                    row[0][:120]
                    for row in conn.execute(
                        f"SELECT DISTINCT SQL_TEXT FROM {view} WHERE SQL_TEXT LIKE '{pattern}'"
                    ).fetchall()
                ]
            )
            report[view + " token rows"] = _outcome(
                lambda view=view: conn.execute(
                    f"SELECT COUNT(*) FROM {view} WHERE SQL_TEXT LIKE '{pattern}' "
                    "AND INSTR(SQL_TEXT, 'ProbeSecretToken') > 0"
                ).fetchval()
            )
        return report
    finally:
        conn.close()


def part_e(probe: Probe) -> dict[str, Any]:
    sys_conn = probe.sys()
    try:
        sys_conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        sys_conn.execute(
            f"CREATE OR REPLACE PYTHON3 SCALAR SCRIPT {SCHEMA}.SLOW(seconds INT) RETURNS INT AS\n"
            "import time\n"
            "def run(ctx):\n"
            "    time.sleep(ctx.seconds)\n"
            "    return ctx.seconds\n"
        )
        sys_conn.execute(f"GRANT EXECUTE ON {SCHEMA}.SLOW TO {TIMEOUT_USER}")
        default_timeout = sys_conn.execute(
            "SELECT SESSION_VALUE, SYSTEM_VALUE FROM EXA_PARAMETERS "
            "WHERE PARAMETER_NAME = 'QUERY_TIMEOUT'"
        ).fetchall()
    except Exception as error:
        default_timeout = [f"unreadable: {_first_line(error)}"]
    finally:
        sys_conn.close()

    results: dict[str, Any] = {"default_session_query_timeout": default_timeout}
    outcomes: dict[str, Any] = {}

    def attempt(label: str, timeout: int | None, socket_timeout: float) -> None:
        conn = probe.as_user(TIMEOUT_USER, socket_timeout=socket_timeout)
        try:
            if timeout is not None:
                accepted = _outcome(lambda: conn.execute(f"ALTER SESSION SET QUERY_TIMEOUT = {timeout}"))
            else:
                accepted = {"ok": True, "skipped": True}
            run = _outcome(
                lambda: conn.execute(f"SELECT {SCHEMA}.SLOW({SLOW_SECONDS}) FROM DUAL").fetchval()
            )
            outcomes[label] = {"alter_session": accepted, "statement": run}
        finally:
            conn.close()

    def heavy_sql() -> None:
        conn = probe.as_user(TIMEOUT_USER, socket_timeout=SOCKET_TIMEOUT)
        try:
            conn.execute(f"ALTER SESSION SET QUERY_TIMEOUT = {SHORT_TIMEOUT}")
            outcomes["timeout_30_cpu_bound_sql"] = _outcome(
                lambda: conn.execute(
                    "SELECT COUNT(*) FROM (SELECT LEVEL AS L FROM DUAL CONNECT BY LEVEL <= 200000) A, "
                    "(SELECT LEVEL AS M FROM DUAL CONNECT BY LEVEL <= 200000) B "
                    "WHERE A.L * 3 + B.M * 7 = -5"
                ).fetchval()
            )
        finally:
            conn.close()

    threads = [
        threading.Thread(target=heavy_sql),
        threading.Thread(target=attempt, args=("timeout_30", SHORT_TIMEOUT, SOCKET_TIMEOUT)),
        threading.Thread(target=attempt, args=("timeout_300", LONG_TIMEOUT, SOCKET_TIMEOUT)),
        threading.Thread(target=attempt, args=("driver_socket_30", LONG_TIMEOUT, 30)),
        threading.Thread(target=attempt, args=("driver_socket_10", LONG_TIMEOUT, 10)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    results["outcomes"] = outcomes
    results["slow_seconds"] = SLOW_SECONDS
    return results


PARTS: dict[str, Callable[[Probe], dict[str, Any]]] = {"a": part_a, "d": part_d, "e": part_e}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--part", choices=["early"], required=True)
    parser.add_argument("--only", default="a,d,e")
    parser.add_argument("--keep", action="store_true")
    arguments = parser.parse_args()
    wanted = [part for part in arguments.only.split(",") if part]

    probe = Probe()
    print(f"target: {probe.target.describe()}")
    problems = probe.cleanup()
    if problems:
        print(f"cleanup before: {problems}")
    exit_code = 0
    try:
        setup_namespace(probe)
        for part in wanted:
            print(f"part ({part}) ...", flush=True)
            started = time.monotonic()
            try:
                probe.results[part] = PARTS[part](probe)
            except Exception as error:
                probe.results[part] = {"error": f"{type(error).__name__}: {_first_line(error)}"}
                exit_code = 1
            print(f"part ({part}) done in {time.monotonic() - started:.1f} s", flush=True)
    finally:
        if arguments.keep:
            print("kept PROBE_APP_* objects (--keep)")
        else:
            left = probe.cleanup()
            probe.results["cleanup"] = {"problems": left}
            print(f"cleanup after: {left or 'clean'}")
            if left:
                exit_code = 1
        RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_FILE.write_text(json.dumps(probe.results, indent=2, default=str))
        print(f"wrote {RESULTS_FILE}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
