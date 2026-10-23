"""`vs/host/appenv.py` and `scripts/app_env.py`: the secrets file outside the repo and OneDrive.

Every secret here is a marker string; nothing reads the real `~/.cfb-demo`.
"""

import os
import stat
from pathlib import Path

import pytest

from app.backend.settings import Settings, SettingsError
from scripts import app_env, grants
from vs.host import appenv

MARKER = "marker-secret-value-4711"


def secrets_file(tmp_path: Path, text: str, mode: int = 0o600) -> Path:
    path = tmp_path / "home" / "app.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    return root


def test_the_default_location_is_in_the_home_directory_and_cfb_app_env_overrides_it() -> None:
    assert appenv.secrets_path({}) == appenv.DEFAULT_SECRETS_PATH
    assert appenv.secrets_path({"CFB_APP_ENV": "/srv/x/app.env"}) == Path("/srv/x/app.env")
    assert appenv.secrets_path({"CFB_APP_ENV": "  "}) == appenv.DEFAULT_SECRETS_PATH


def test_the_environment_wins_over_the_secrets_file(tmp_path: Path) -> None:
    file = secrets_file(tmp_path, f"CFB_APP_PASSWORD=from-file\nCFB_CONSOLE_PASSWORD={MARKER}\n")
    values = appenv.load_values(
        {"CFB_APP_PASSWORD": "from-env", "CFB_CONSOLE_PASSWORD": ""},
        dotenv_path=tmp_path / "no.env",
        secrets_file=file,
        repo_root=repo(tmp_path),
    )
    assert values["CFB_APP_PASSWORD"] == "from-env"
    assert values["CFB_CONSOLE_PASSWORD"] == MARKER


def test_the_secrets_file_wins_over_the_repo_dotenv_for_non_secret_keys(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("EXASOL_DSN=a:1\nAPP_PORT=9001\n", encoding="utf-8")
    file = secrets_file(tmp_path, "APP_PORT=9002\n")
    values = appenv.load_values({}, dotenv_path=dotenv, secrets_file=file, repo_root=repo(tmp_path))
    assert values == {"EXASOL_DSN": "a:1", "APP_PORT": "9002"}


def test_a_missing_secrets_file_is_empty(tmp_path: Path) -> None:
    assert appenv.read_secrets(tmp_path / "home" / "none.env", repo(tmp_path)) == {}


def test_a_secrets_file_inside_the_repo_is_refused(tmp_path: Path) -> None:
    root = repo(tmp_path)
    inside = root / "secrets" / "app.env"
    with pytest.raises(appenv.SecretsFileError, match="inside the repository"):
        appenv.check_location(inside, root)


def test_a_symlink_into_the_repo_is_refused(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "real.env").write_text("X=1\n", encoding="utf-8")
    link = tmp_path / "link.env"
    link.symlink_to(root / "real.env")
    with pytest.raises(appenv.SecretsFileError, match="inside the repository"):
        appenv.check_location(link, root)


@pytest.mark.parametrize("folder", ["OneDrive - EXASOL AG", "onedrive", "My OneDrive"])
def test_a_path_in_a_synced_folder_is_refused(tmp_path: Path, folder: str) -> None:
    path = tmp_path / folder / "app.env"
    with pytest.raises(appenv.SecretsFileError, match="OneDrive"):
        appenv.read_secrets(path, repo(tmp_path))


def test_the_refusal_message_never_holds_a_secret_value(tmp_path: Path) -> None:
    file = secrets_file(tmp_path, f"CFB_APP_PASSWORD={MARKER}\n", mode=0o644)
    with pytest.raises(appenv.SecretsFileError) as error:
        appenv.read_secrets(file, repo(tmp_path))
    assert MARKER not in str(error.value)


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644, 0o660, 0o666, 0o607])
def test_a_secrets_file_wider_than_0600_is_refused(tmp_path: Path, mode: int) -> None:
    file = secrets_file(tmp_path, "CFB_APP_PASSWORD=x\n", mode=mode)
    with pytest.raises(appenv.SecretsFileError, match="wider than 0600"):
        appenv.read_secrets(file, repo(tmp_path))


@pytest.mark.parametrize("mode", [0o600, 0o400])
def test_a_secrets_file_of_0600_or_tighter_is_read(tmp_path: Path, mode: int) -> None:
    file = secrets_file(tmp_path, f"CFB_APP_PASSWORD={MARKER}\n", mode=mode)
    assert appenv.read_secrets(file, repo(tmp_path)) == {"CFB_APP_PASSWORD": MARKER}


@pytest.mark.parametrize(
    "key",
    ["CFB_APP_PASSWORD", "CFB_TEST_OWNER_PASSWORD", "CFB_CHAT_PASSWORD", "ANTHROPIC_API_KEY"],
)
def test_a_secret_key_in_the_repo_dotenv_fails_fast_naming_the_key_only(
    tmp_path: Path, key: str
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"EXASOL_DSN=a:1\n{key}={MARKER}\n", encoding="utf-8")
    with pytest.raises(appenv.SecretsFileError, match=key) as error:
        appenv.check_repo_dotenv(dotenv)
    assert MARKER not in str(error.value)
    assert "~/.cfb-demo/app.env" in str(error.value)


def test_non_secret_keys_may_stay_in_the_repo_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("EXASOL_DSN=a:1\nCFB_APP_USER=U\n", encoding="utf-8")
    assert appenv.check_repo_dotenv(dotenv) == {"EXASOL_DSN": "a:1", "CFB_APP_USER": "U"}
    assert appenv.check_repo_dotenv(tmp_path / "none") == {}


def test_settings_refuse_a_repo_dotenv_with_a_password(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"CFB_APP_PASSWORD={MARKER}\n", encoding="utf-8")
    with pytest.raises(SettingsError, match="CFB_APP_PASSWORD") as error:
        Settings.from_env({"CFB_APP_PASSWORD": "from-env"}, dotenv_path=dotenv)
    assert MARKER not in str(error.value)


def test_settings_read_the_passwords_from_the_secrets_file(tmp_path: Path) -> None:
    file = secrets_file(tmp_path, f"CFB_APP_PASSWORD={MARKER}\nCFB_CONSOLE_PASSWORD=c-{MARKER}\n")
    loaded = Settings.from_env(
        {},
        dotenv_path=tmp_path / "no.env",
        secrets_file=file,
        resolver=lambda host, port: ["127.0.0.1"],
    )
    assert loaded.app_password == MARKER
    assert loaded.console_password == f"c-{MARKER}"


def test_settings_error_for_a_missing_password_points_to_the_secrets_file(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match=r"~/\.cfb-demo/app\.env"):
        Settings.from_env({}, dotenv_path=tmp_path / "no.env")


def test_the_owner_password_comes_from_the_secrets_file_and_dotenv_is_not_read(
    tmp_path: Path,
) -> None:
    file = secrets_file(tmp_path, f"CFB_TEST_OWNER_PASSWORD={MARKER}\n")
    assert grants.owner_password({}, tmp_path / "no.env", file) == MARKER
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"CFB_TEST_OWNER_PASSWORD={MARKER}\n", encoding="utf-8")
    with pytest.raises(grants.GrantsError, match="CFB_TEST_OWNER_PASSWORD"):
        grants.owner_password({}, dotenv, file)


def test_init_creates_the_file_with_0600_random_passwords_and_a_placeholder(
    tmp_path: Path,
) -> None:
    path = tmp_path / "home" / ".cfb-demo" / "app.env"
    created, kept = app_env.initialize(path, repo(tmp_path))
    assert created == ["CFB_APP_PASSWORD", "CFB_CONSOLE_PASSWORD"] and kept == []
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    values = appenv.read_secrets(path, repo(tmp_path))
    assert set(values) == {"CFB_APP_PASSWORD", "CFB_CONSOLE_PASSWORD"}
    assert values["CFB_APP_PASSWORD"] != values["CFB_CONSOLE_PASSWORD"]
    assert all(
        len(value) >= 32 and value.replace("-", "").replace("_", "").isalnum()
        for value in values.values()
    )
    assert "# ANTHROPIC_API_KEY=" in path.read_text(encoding="utf-8")


def test_init_never_overwrites_an_existing_key(tmp_path: Path) -> None:
    file = secrets_file(tmp_path, f"CFB_APP_PASSWORD={MARKER}\nANTHROPIC_API_KEY=sk-x")
    created, kept = app_env.initialize(file, repo(tmp_path))
    assert created == ["CFB_CONSOLE_PASSWORD"] and kept == ["CFB_APP_PASSWORD"]
    values = appenv.read_secrets(file, repo(tmp_path))
    assert values["CFB_APP_PASSWORD"] == MARKER and values["ANTHROPIC_API_KEY"] == "sk-x"
    before = file.read_text(encoding="utf-8")
    assert app_env.initialize(file, repo(tmp_path)) == ([], list(app_env.GENERATED_KEYS))
    assert file.read_text(encoding="utf-8") == before
    assert stat.S_IMODE(file.stat().st_mode) == 0o600


def test_init_refuses_a_synced_folder_the_repo_and_a_wide_file(tmp_path: Path) -> None:
    root = repo(tmp_path)
    with pytest.raises(appenv.SecretsFileError, match="OneDrive"):
        app_env.initialize(tmp_path / "OneDrive" / "app.env", root)
    with pytest.raises(appenv.SecretsFileError, match="inside the repository"):
        app_env.initialize(root / "app.env", root)
    wide = secrets_file(tmp_path, "CFB_APP_PASSWORD=x\n", mode=0o644)
    with pytest.raises(appenv.SecretsFileError, match="wider than 0600"):
        app_env.initialize(wide, root)
    assert not (root / "app.env").exists()


def test_the_command_prints_names_and_path_but_never_a_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "home" / "app.env"
    assert app_env.main(["init"], {"CFB_APP_ENV": str(path)}) == 0
    shown = capsys.readouterr().out
    assert str(path) in shown and "CFB_APP_PASSWORD" in shown
    for value in appenv.read_secrets(path, repo(tmp_path)).values():
        assert value not in shown
    assert os.access(path, os.R_OK)


def test_the_command_exits_1_with_a_message_when_the_path_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert app_env.main(["init"], {"CFB_APP_ENV": str(tmp_path / "OneDrive" / "app.env")}) == 1
    assert "OneDrive" in capsys.readouterr().err
