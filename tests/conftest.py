"""Suite-wide guards: no test reads the developer's real secrets file, and no test may reach the
network through the Anthropic SDK.

`httpx2` is the SDK's HTTP library. Its real transport is replaced by one that raises, and the
chat's HTTP-client factory is replaced by one whose client has an in-process transport that raises
on any request. A test that wants to see requests installs its own recorder
(`tests/unit/chat_sdk_fakes.py`) through `monkeypatch.setattr(model, "http_client_factory", ...)`.
The marker key `sk-ant-test-CFBCHATMARKER-0000` is never valid, so a misrouted test would fail
rather than spend.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from vs.host import appenv

try:
    import httpx2
except ImportError:  # the chat dependency group is not installed
    httpx2 = None  # type: ignore[assignment]

NO_NETWORK = "tests never reach the network: install a recorder transport (chat_sdk_fakes.Recorder)"


@pytest.fixture(autouse=True)
def no_real_secrets_file(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    absent = tmp_path_factory.mktemp("secrets") / "app.env"
    monkeypatch.setattr(appenv, "DEFAULT_SECRETS_PATH", absent)
    assert not Path(absent).exists()


def _refuse(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError(NO_NETWORK)


@pytest.fixture(autouse=True)
def no_anthropic_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    if httpx2 is None:
        yield
        return
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", _refuse)
    chat_model = sys.modules.get("app.backend.chat.model")
    if chat_model is not None:
        from anthropic import DefaultHttpxClient

        def closed_factory() -> Any:
            return DefaultHttpxClient(
                transport=httpx2.MockTransport(_refuse),
                event_hooks={"request": [chat_model.request_guard]},
            )

        monkeypatch.setattr(chat_model, "http_client_factory", closed_factory)
    yield
