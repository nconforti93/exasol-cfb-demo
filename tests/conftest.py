"""Every test starts without the developer's real secrets file."""

from pathlib import Path

import pytest

from vs.host import appenv


@pytest.fixture(autouse=True)
def no_real_secrets_file(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    absent = tmp_path_factory.mktemp("secrets") / "app.env"
    monkeypatch.setattr(appenv, "DEFAULT_SECRETS_PATH", absent)
    assert not Path(absent).exists()
