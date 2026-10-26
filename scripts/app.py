"""Serve the app, run its loopback development pair, and provision its two users."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import uvicorn

from app.backend.main import create_app
from app.backend.settings import DEFAULT_STATIC_DIR, Settings, SettingsError
from vs.host import appenv, target

_IDENTIFIER = re.compile(r"[A-Z][A-Z0-9_]*\Z")
VITE_ORIGINS = "http://127.0.0.1:5173,http://localhost:5173"


class AppCliError(RuntimeError):
    """A safe CLI failure; no password or driver text is included."""


def serve(
    *,
    static_dir: Path | None = None,
    runner: Callable[..., Any] = uvicorn.run,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Serve the built SPA and API as one worker on the configured bind."""
    values = os.environ if environ is None else environ
    built = static_dir or Path(values.get("APP_STATIC_DIR", DEFAULT_STATIC_DIR))
    if not (built / "index.html").is_file():
        raise AppCliError("built frontend is missing; run make app-build")
    settings = Settings.from_env(values)
    if static_dir is not None:
        settings = replace(settings, static_dir=built)
    runner(create_app(settings), host=settings.host, port=settings.port, workers=1)


def check_vite_args(args: Sequence[str]) -> list[str]:
    """Allow Vite flags except any host override, which would expose the dev server."""
    normalized = list(args)
    if normalized[:1] == ["--"]:
        normalized.pop(0)
    for value in normalized:
        if value == "-h" or value.startswith("--host") or value.startswith("--server.host"):
            raise AppCliError("the Vite host is fixed at 127.0.0.1; remove the host override")
    return normalized


def dev(args: Sequence[str]) -> None:
    """Run uvicorn reload and Vite together on loopback until interrupted."""
    vite_args = check_vite_args(args)
    values = dict(os.environ)
    values["APP_DEV"] = "1"
    values["APP_CORS_ORIGINS"] = VITE_ORIGINS
    settings = Settings.from_env(values)
    if not settings.bind.loopback:
        raise AppCliError("development mode requires a loopback APP_HOST")
    backend = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.backend.main:app_factory",
            "--factory",
            "--reload",
            "--host",
            settings.host,
            "--port",
            str(settings.port),
        ],
        env=values,
    )
    frontend = subprocess.Popen(
        ["npm", "run", "dev", "--", *vite_args], cwd=DEFAULT_STATIC_DIR.parent, env=values
    )
    try:
        while backend.poll() is None and frontend.poll() is None:
            time.sleep(0.1)
        if (backend.poll() or 0) != 0 or (frontend.poll() or 0) != 0:
            raise AppCliError("a development server exited with an error")
    finally:
        backend.terminate()
        frontend.terminate()
        backend.wait()
        frontend.wait()


def user(
    *,
    values: Mapping[str, str],
    connector: Callable[..., Any],
    rotate: bool = False,
) -> tuple[str, ...]:
    """Create the read user, and the console user if configured, without printing passwords."""
    app_password = values.get("CFB_APP_PASSWORD", "")
    if not app_password:
        raise AppCliError("CFB_APP_PASSWORD is required in the environment or ~/.cfb-demo/app.env")
    read_name = values.get("CFB_APP_USER", "CFB_APP_WEB")
    console_name = values.get("CFB_CONSOLE_USER", "CFB_CONSOLE_WEB")
    role_prefix = values.get("APP_PREFIX", "")
    names = [(read_name, app_password, f"{role_prefix}CFB_APP")]
    console_password = values.get("CFB_CONSOLE_PASSWORD", "")
    if console_password:
        names.append((console_name, console_password, f"{role_prefix}CFB_READER"))
    for name, _, role in names:
        if not _IDENTIFIER.fullmatch(name) or not _IDENTIFIER.fullmatch(role):
            raise AppCliError("app user and role names must be uppercase SQL identifiers")
    connection = connector()
    completed = []
    try:
        for name, password, role in names:
            try:
                existing = connection.execute(
                    'SELECT "USER_NAME" FROM "SYS"."EXA_DBA_USERS" WHERE "USER_NAME" = {name}',
                    {"name": name},
                ).fetchone()
                if existing and rotate:
                    connection.execute(
                        f"ALTER USER {name} IDENTIFIED BY {{password!q}}", {"password": password}
                    )
                elif not existing:
                    connection.execute(
                        f"CREATE USER {name} IDENTIFIED BY {{password!q}}", {"password": password}
                    )
                connection.execute(f"GRANT CREATE SESSION TO {name}")
                connection.execute(f"GRANT {role} TO {name}")
            except Exception:
                raise AppCliError(
                    f"could not provision {name}; check database permissions"
                ) from None
            completed.append(name)
    finally:
        connection.close()
    return tuple(completed)


def main(argv: list[str] | None = None) -> int:
    """Parse one app command; errors print only safe, secret-free text."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve")
    commands.add_parser("dev")
    users = commands.add_parser("user")
    users.add_argument("--rotate", action="store_true")
    args, extra = parser.parse_known_args(argv)
    try:
        if args.command == "serve":
            if extra:
                parser.error("serve takes no extra arguments")
            serve()
        elif args.command == "dev":
            dev(extra)
        elif args.command == "user":
            if extra:
                parser.error("user takes no extra arguments")
            values = appenv.load_values(os.environ)
            selected = values.get("TARGET", "local")
            deployment = target.resolve(selected, dsn=values.get("EXASOL_DSN") or None)
            names = user(values=values, connector=deployment.connect, rotate=args.rotate)
            print("provisioned " + ", ".join(names))
    except (AppCliError, SettingsError, appenv.SecretsFileError, target.TargetError) as error:
        print(f"app: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
