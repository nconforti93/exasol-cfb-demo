"""`make grants`: the reader role of the real deployment, or the owner of a test namespace.

Real deployment ([20]): `sys` creates and owns every object, so there is no owner user, no owner
password and no script-bound connection grant. This only creates the role `CFB_READER` (if
missing) and grants it `SELECT ON CFBD_VS` (once the virtual schema exists). Every step is
idempotent. The trade-off of [20]: `sys` can read every connection, so "only `CFBD_FETCH` can
read the key" holds for readers, not for the owner.

`--prefix TEST_` targets the integration-test namespace, which keeps a dedicated owner user
(`CFB_TEST_OWNER`) and the bound grant `GRANT ACCESS ON CONNECTION <c> FOR SCRIPT <s> TO <owner>`.
Re-run it after every `make deploy --prefix TEST_` that replaced `CFBD_FETCH`: `CREATE OR
REPLACE` drops that grant, even for identical text. Never `ACCESS ANY CONNECTION`.

`--target local|aws` (or `TARGET`) selects the Exasol Personal deployment, see `vs/host/target.py`.
The sys password is read at run time from that deployment's secrets file. The test owner's
password comes from `CFB_TEST_OWNER_PASSWORD` (environment, then `~/.cfb-demo/app.env`). Neither is
printed or written, and errors from statements that carry a password are redacted.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from vs.host import appenv, redact, target
from vs.host.namespace import Namespace, NamespaceError

OWNER_PASSWORD_VARIABLE = "CFB_TEST_OWNER_PASSWORD"

OWNER_PRIVILEGES = (
    "CREATE SESSION",
    "CREATE SCHEMA",
    "CREATE TABLE",
    "CREATE VIEW",
    "CREATE SCRIPT",
    "CREATE VIRTUAL SCHEMA",
)

EXIT_OK = 0
EXIT_ERROR = 2

_IDENTIFIER = re.compile(r"[A-Z][A-Z0-9_]*")
_QUALIFIED_IDENTIFIER = re.compile(r"[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*")


class GrantsError(RuntimeError):
    """The grants cannot be computed or applied."""


@dataclass(frozen=True)
class Step:
    """One SQL statement; secret values travel in `params`, never inside `sql`."""

    description: str
    sql: str
    params: Mapping[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Report:
    lines: tuple[str, ...]
    script_grant: str
    exit_code: int
    reader_grant: str = "not needed"


class Database(Protocol):
    def user_exists(self, name: str) -> bool: ...

    def script_exists(self, qualified_name: str) -> bool: ...

    def connection_exists(self, name: str) -> bool: ...

    def script_grant_exists(self, connection: str, script: str, grantee: str) -> bool: ...

    def role_exists(self, name: str) -> bool: ...

    def schema_exists(self, name: str) -> bool: ...

    def select_grant_exists(self, schema: str, grantee: str) -> bool: ...

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> None: ...


def create_role_step(role: str) -> Step:
    _require_identifier(role)
    return Step(f"create role {role}", f"CREATE ROLE {role}")


def select_grant_step(schema: str, grantee: str) -> Step:
    _require_identifier(schema)
    _require_identifier(grantee)
    return Step(f"grant SELECT on {schema} to {grantee}", f"GRANT SELECT ON {schema} TO {grantee}")


def create_user_step(owner: str, password: str) -> Step:
    _require_identifier(owner)
    return Step(
        f"create user {owner}",
        f"CREATE USER {owner} IDENTIFIED BY {{password!q}}",
        {"password": password},
    )


def owner_privileges_step(owner: str) -> Step:
    _require_identifier(owner)
    return Step(
        f"grant system privileges to {owner}",
        f"GRANT {', '.join(OWNER_PRIVILEGES)} TO {owner}",
    )


def script_grant_step(connection: str, script: str, grantee: str) -> Step:
    _require_identifier(connection)
    _require_identifier(grantee)
    if "." not in script:
        raise GrantsError(f"script {script!r} must be schema-qualified, as SCHEMA.SCRIPT")
    if not _QUALIFIED_IDENTIFIER.fullmatch(script):
        raise GrantsError(f"script {script!r} is not a valid identifier")
    return Step(
        f"bind {connection} to {script} for {grantee}",
        f"GRANT ACCESS ON CONNECTION {connection} FOR SCRIPT {script} TO {grantee}",
    )


def resolve_connection(namespace: Namespace, requested: str | None) -> str:
    try:
        return namespace.resolve_connection(requested)
    except NamespaceError as error:
        raise GrantsError(str(error)) from None


def apply(
    db: Database, namespace: Namespace, connection: str, owner_password: str | None = None
) -> Report:
    """Bring the grants of the namespace to the desired state."""
    if namespace.is_real:
        return _apply_real(db, namespace)
    return _apply_owned(db, namespace, connection, owner_password)


def _apply_real(db: Database, namespace: Namespace) -> Report:
    role = namespace.reader_role
    lines: list[str] = []
    if db.role_exists(role):
        lines.append(f"role {role}: exists")
    else:
        step = create_role_step(role)
        db.execute(step.sql, step.params)
        lines.append(f"done: {step.description}")
    reader_grant, grant_lines = _grant_reader(db, namespace)
    lines.append(f"fetch UDF {namespace.fetch_script}: owned by {namespace.owner_user}, no grant")
    return Report(tuple(lines + grant_lines), "not needed", EXIT_OK, reader_grant)


def _grant_reader(db: Database, namespace: Namespace) -> tuple[str, list[str]]:
    schema, role = namespace.vs_name, namespace.reader_role
    if not db.schema_exists(schema):
        return "pending", [
            f"virtual schema {schema}: not deployed yet, "
            "run `make deploy`, then `make grants` again"
        ]
    if db.select_grant_exists(schema, role):
        return "already granted", [f"grant SELECT on {schema} to {role}: already in place"]
    step = select_grant_step(schema, role)
    db.execute(step.sql, step.params)
    return "granted", [f"done: {step.description}"]


def _apply_owned(
    db: Database, namespace: Namespace, connection: str, owner_password: str | None
) -> Report:
    owner = namespace.owner_user
    steps: list[Step] = []
    lines: list[str] = []
    if db.user_exists(owner):
        lines.append(f"user {owner}: exists, password unchanged")
    else:
        if not owner_password:
            raise GrantsError(
                f"user {owner} does not exist and {OWNER_PASSWORD_VARIABLE} is not set "
                f"(environment or {appenv.HOME_HINT})"
            )
        steps.append(create_user_step(owner, owner_password))
    steps.append(owner_privileges_step(owner))
    for step in steps:
        db.execute(step.sql, step.params)
        lines.append(f"done: {step.description}")
    script_grant, script_lines, exit_code = _bind_connection(db, namespace, connection)
    return Report(tuple(lines + script_lines), script_grant, exit_code)


def owner_password(
    environ: Mapping[str, str],
    dotenv: Path = target.DOTENV_PATH,
    secrets_file: Path | None = None,
) -> str | None:
    """The owner password: environment, then `~/.cfb-demo/app.env`; a secret in `.env` fails."""
    try:
        values = appenv.load_values(environ, dotenv_path=dotenv, secrets_file=secrets_file)
    except appenv.SecretsFileError as error:
        raise GrantsError(str(error)) from None
    return values.get(OWNER_PASSWORD_VARIABLE) or None


class PyexasolDatabase:
    """`Database` over a pyexasol connection."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def user_exists(self, name: str) -> bool:
        return self._count("SELECT COUNT(*) FROM EXA_DBA_USERS WHERE USER_NAME = {n}", n=name) > 0

    def script_exists(self, qualified_name: str) -> bool:
        schema, _, script = qualified_name.partition(".")
        return (
            self._count(
                "SELECT COUNT(*) FROM EXA_DBA_SCRIPTS "
                "WHERE SCRIPT_SCHEMA = {s} AND SCRIPT_NAME = {n}",
                s=schema,
                n=script,
            )
            > 0
        )

    def connection_exists(self, name: str) -> bool:
        return (
            self._count(
                "SELECT COUNT(*) FROM EXA_DBA_CONNECTIONS WHERE CONNECTION_NAME = {n}", n=name
            )
            > 0
        )

    def script_grant_exists(self, connection: str, script: str, grantee: str) -> bool:
        schema, _, name = script.partition(".")
        return (
            self._count(
                "SELECT COUNT(*) FROM EXA_DBA_RESTRICTED_OBJ_PRIVS "
                "WHERE OBJECT_NAME = {c} AND FOR_OBJECT_SCHEMA = {s} "
                "AND FOR_OBJECT_NAME = {n} AND GRANTEE = {g}",
                c=connection,
                s=schema,
                n=name,
                g=grantee,
            )
            > 0
        )

    def role_exists(self, name: str) -> bool:
        return self._count("SELECT COUNT(*) FROM EXA_DBA_ROLES WHERE ROLE_NAME = {n}", n=name) > 0

    def schema_exists(self, name: str) -> bool:
        return (
            self._count(
                "SELECT COUNT(*) FROM EXA_DBA_VIRTUAL_SCHEMAS WHERE SCHEMA_NAME = {n}", n=name
            )
            > 0
        )

    def select_grant_exists(self, schema: str, grantee: str) -> bool:
        return (
            self._count(
                "SELECT COUNT(*) FROM EXA_DBA_OBJ_PRIVS "
                "WHERE OBJECT_SCHEMA IS NULL AND OBJECT_NAME = {s} AND OBJECT_TYPE = 'SCHEMA' "
                "AND PRIVILEGE = 'SELECT' AND GRANTEE = {g}",
                s=schema,
                g=grantee,
            )
            > 0
        )

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> None:
        """Run a statement; an error never carries a parameter value (a password)."""
        try:
            self._connection.execute(sql, dict(params) if params else None)
        except Exception as error:
            secrets = [value for value in (params or {}).values() if isinstance(value, str)]
            clean = redact.scrub(error, secrets)
            if clean is error:
                raise
            raise GrantsError(f"statement failed: {sql}: {_first_line(clean)}") from None

    def _count(self, sql: str, **params: str) -> int:
        return int(self._connection.execute(sql, params).fetchone()[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="", help="namespace prefix, for example TEST_")
    parser.add_argument(
        "--connection",
        default=None,
        help="connection bound to the fetch UDF; test namespaces only (default CFBD_API_MOCK)",
    )
    parser.add_argument(
        "--target", default=None, help="local or aws (default: $TARGET, else local)"
    )
    parser.add_argument("--dsn", default=None, help="overrides host and port of the target")
    args = parser.parse_args(argv)
    try:
        namespace = Namespace(args.prefix)
        connection = resolve_connection(namespace, args.connection)
        report = _run(namespace, connection, target.resolve(args.target, dsn=args.dsn))
    except (GrantsError, NamespaceError, target.TargetError) as error:
        print(f"grants failed: {error}", file=sys.stderr)
        return EXIT_ERROR
    print("\n".join(report.lines))
    return report.exit_code


def _bind_connection(
    db: Database, namespace: Namespace, connection: str
) -> tuple[str, list[str], int]:
    script = namespace.fetch_script
    owner = namespace.owner_user
    if not db.script_exists(script):
        return (
            "pending",
            [f"script {script}: not deployed yet, run `make deploy`, then `make grants` again"],
            EXIT_OK,
        )
    if not db.connection_exists(connection):
        message = (
            f"connection {connection}: does not exist. Create it first, see docs/user-guide.md"
        )
        return "connection missing", [message], EXIT_ERROR
    if db.script_grant_exists(connection, script, owner):
        return (
            "already granted",
            [f"grant {connection} -> {script} for {owner}: already in place"],
            EXIT_OK,
        )
    step = script_grant_step(connection, script, owner)
    db.execute(step.sql, step.params)
    return "granted", [f"done: {step.description}"], EXIT_OK


def _run(namespace: Namespace, connection: str, deployment: target.Target) -> Report:
    sys_password = deployment.password()
    exa = deployment.connect(password=sys_password)
    secrets = [sys_password, owner_password(os.environ)]
    try:
        return apply(PyexasolDatabase(exa), namespace, connection, owner_password(os.environ))
    except GrantsError:
        raise
    except Exception as error:
        message = redact.redact(f"Exasol rejected a statement: {_first_line(error)}", secrets)
        raise GrantsError(message) from None
    finally:
        exa.close()


def _first_line(error: BaseException) -> str:
    message = str(getattr(error, "message", error)).strip()
    return message.splitlines()[0] if message else type(error).__name__


def _require_identifier(value: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise GrantsError(f"{value!r} is not a valid UPPER_CASE identifier")


if __name__ == "__main__":
    sys.exit(main())
