"""The app's secrets file: `~/.cfb-demo/app.env`, outside the repository and outside OneDrive.

Lookup order for every setting: the process environment, then this file; the repository `.env`
(git-ignored, but synced by OneDrive) stays a source for non-secret keys such as `EXASOL_DSN` only.
A repository `.env` that holds a `*_PASSWORD` or `*_API_KEY` key stops the program with a message
that names the keys (never a value). The secrets file itself is refused when it lies inside the
repository or under a path containing "OneDrive", and when group or others can access it.

`CFB_APP_ENV=<path>` replaces the default location. Nothing here ever puts a value into a message.
"""

from __future__ import annotations

import re
import stat
from collections.abc import Mapping
from pathlib import Path

from vs.host import target

ROOT = Path(__file__).resolve().parents[2]
SECRETS_PATH_VARIABLE = "CFB_APP_ENV"
DEFAULT_SECRETS_PATH = Path.home() / ".cfb-demo" / "app.env"
SECRETS_MODE = 0o600
SECRETS_DIRECTORY_MODE = 0o700
SYNCED_FOLDER_MARKER = "onedrive"
SECRET_KEY = re.compile(r".*(_PASSWORD|_API_KEY)")
HOME_HINT = "~/.cfb-demo/app.env"


class SecretsFileError(RuntimeError):
    """The secrets file or the repository `.env` is not acceptable; the message holds no value."""


def is_secret_key(key: str) -> bool:
    """True for a `*_PASSWORD` or `*_API_KEY` key."""
    return SECRET_KEY.fullmatch(key) is not None


def secrets_path(environ: Mapping[str, str]) -> Path:
    """`CFB_APP_ENV` when set, else `~/.cfb-demo/app.env`."""
    override = environ.get(SECRETS_PATH_VARIABLE, "").strip()
    return Path(override).expanduser() if override else DEFAULT_SECRETS_PATH


def check_location(path: Path, repo_root: Path = ROOT) -> None:
    """Refuse a path inside the repository or under a synced OneDrive folder."""
    resolved = path.expanduser().absolute().resolve()
    candidates = (path.expanduser().absolute(), resolved)
    if any(SYNCED_FOLDER_MARKER in str(candidate).lower() for candidate in candidates):
        raise SecretsFileError(
            f"refusing secrets file {path}: its path contains 'OneDrive', which syncs to the "
            f"cloud; keep it at {HOME_HINT} or point {SECRETS_PATH_VARIABLE} outside OneDrive"
        )
    if resolved.is_relative_to(repo_root.resolve()):
        raise SecretsFileError(
            f"refusing secrets file {path}: it lies inside the repository; keep it at "
            f"{HOME_HINT} or point {SECRETS_PATH_VARIABLE} outside the repository"
        )


def check_mode(path: Path) -> None:
    """Refuse a secrets file that group or others can access."""
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise SecretsFileError(
            f"refusing secrets file {path}: mode {mode:04o} is wider than {SECRETS_MODE:04o}; "
            f"run `chmod 600 {path}`"
        )


def check_repo_dotenv(dotenv_path: Path) -> dict[str, str]:
    """The repository `.env`; fails fast when it holds a secret key, naming the keys."""
    values = target.parse_dotenv(dotenv_path)
    secret_keys = sorted(key for key in values if is_secret_key(key))
    if secret_keys:
        raise SecretsFileError(
            f"{dotenv_path} holds secret keys ({', '.join(secret_keys)}); it is synced by "
            f"OneDrive and no longer read for secrets. Move them to {HOME_HINT} "
            f"(`make app-env` creates it) and delete them from .env"
        )
    return values


def read_secrets(path: Path, repo_root: Path = ROOT) -> dict[str, str]:
    """The keys of the secrets file, after the location and permission checks; empty when absent."""
    check_location(path, repo_root)
    if not path.exists():
        return {}
    check_mode(path)
    return target.parse_dotenv(path)


def load_values(
    environ: Mapping[str, str],
    *,
    dotenv_path: Path = target.DOTENV_PATH,
    secrets_file: Path | None = None,
    repo_root: Path = ROOT,
) -> dict[str, str]:
    """Settings values: the non-empty environment, then the secrets file, then repo `.env`
    (non-secret keys only)."""
    dotenv = check_repo_dotenv(dotenv_path)
    secrets = read_secrets(secrets_file or secrets_path(environ), repo_root)
    present = {key: value for key, value in environ.items() if value != ""}
    return {**dotenv, **secrets, **present}
