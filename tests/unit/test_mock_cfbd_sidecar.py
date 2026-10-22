import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.mock_cfbd import sidecar

PS = (
    "pgvector|docker.io/pgvector/pgvector:pg17|0.0.0.0:5432->5432/tcp\n"
    "exasol-db-abc123|docker.io/exasol/nano:2026.2.0-nano.3|127.0.0.1:8563->8563/tcp\n"
)
IMAGES = (
    "docker.io/exasol/script-language-container:standard-EXASOL-all-r-4.4-release_x\n"
    "docker.io/exasol/script-language-container:standard-EXASOL-all-python-3.12-release_x\n"
)
LISTENING = "LISTENING port=41000 doh_port=42000 admin_port=43000\n"


class FakePodman:
    """Records every podman call and answers from a script keyed by sub-command."""

    def __init__(self, logs: str = LISTENING, admin_answer: Any = None) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.logs = logs
        self.admin_answer = admin_answer if admin_answer is not None else {"ok": True}

    def __call__(
        self, args: list[str], stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, stdin))
        out = ""
        match args[:2]:
            case ["podman", "ps"]:
                out = PS
            case ["podman", "images"]:
                out = IMAGES
            case ["podman", "logs"]:
                out = self.logs
            case ["podman", "exec"]:
                out = json.dumps(self.admin_answer)
        return subprocess.CompletedProcess(args, 0, out, "")

    def verbs(self) -> list[str]:
        return [call[0][1] for call in self.calls]


def test_db_container_is_found_by_image_and_port() -> None:
    assert sidecar.find_db_container(PS) == "exasol-db-abc123"
    assert sidecar.find_db_container("pgvector|docker.io/pgvector/pgvector:pg17|5432") is None


def test_python_image_is_a_local_python_slc() -> None:
    assert sidecar.find_python_image(IMAGES).endswith("python-3.12-release_x")
    with pytest.raises(sidecar.SidecarError):
        sidecar.find_python_image("docker.io/pgvector/pgvector:pg17\n")


def test_start_runs_an_init_sidecar_in_the_db_network_namespace() -> None:
    podman = FakePodman()
    handle = sidecar.start(runner=podman, provider=None)
    try:
        run = next(args for args, _ in podman.calls if args[1] == "run")
        assert "--init" in run
        assert run[run.index("--network") + 1] == "container:exasol-db-abc123"
        mounts = [run[i + 1] for i, arg in enumerate(run) if arg == "-v"]
        assert all(mount.endswith(":ro") for mount in mounts)
        assert any(mount.endswith(":/repo/tests:ro") for mount in mounts)
        assert "--rm" not in run
        assert (handle.ip, handle.port, handle.doh_port, handle.admin_port) == (
            "127.0.0.1",
            41000,
            42000,
            43000,
        )
        assert handle.address == "http://127.0.0.1:41000"
        assert handle.doh_url == "https://127.0.0.1:42000/dns-query"
        assert handle.cert_dir is not None and (handle.cert_dir / sidecar.CERT_NAME).exists()
    finally:
        handle.stop()
    assert handle.cert_dir is not None and not handle.cert_dir.exists()
    assert podman.calls[-1][0][:3] == ["podman", "rm", "-f"]


def test_start_passes_the_provider_and_removes_a_stale_sidecar_first() -> None:
    podman = FakePodman()
    handle = sidecar.start(runner=podman, provider="tests.synthetic.fake:response_for")
    try:
        assert podman.verbs().index("rm") < podman.verbs().index("run")
        run = next(args for args, _ in podman.calls if args[1] == "run")
        assert run[run.index("--provider") + 1] == "tests.synthetic.fake:response_for"
    finally:
        handle.stop()


def test_start_reports_a_crashed_server_with_its_logs() -> None:
    podman = FakePodman(logs="Traceback (most recent call last):\nValueError: bad provider\n")
    with pytest.raises(sidecar.SidecarError, match="bad provider"):
        sidecar.start(runner=podman, provider=None)
    assert podman.calls[-1][0][:3] == ["podman", "rm", "-f"]


def make_handle(podman: FakePodman) -> sidecar.MockHandle:
    return sidecar.MockHandle(
        name="cfbd-mock",
        db_container="db",
        port=41000,
        doh_port=42000,
        admin_port=43000,
        runner=podman,
    )


def test_hits_calls_the_admin_api_through_podman_exec() -> None:
    podman = FakePodman(admin_answer={"count": 4})
    assert make_handle(podman).hits("/games", query={"year": "2024"}) == 4
    args, stdin = podman.calls[-1]
    assert args[:3] == ["podman", "exec", "-i"]
    assert "--admin-port" in args and "43000" in args
    assert "GET" in args
    assert any(a.startswith("/_admin/hits?") and "path=%2Fgames" in a for a in args)
    assert stdin is None


def test_script_sends_only_the_given_fields_on_stdin() -> None:
    podman = FakePodman(admin_answer={"id": 7})
    rule_id = make_handle(podman).script("/games", status=429, retry_after=2, times=1)
    args, stdin = podman.calls[-1]
    assert rule_id == 7
    assert "--stdin" in args
    assert json.loads(stdin or "") == {
        "path": "/games",
        "status": 429,
        "retry_after": 2,
        "times": 1,
    }


def test_log_reset_echo_and_doh_helpers() -> None:
    podman = FakePodman(admin_answer={"entries": [{"seq": 1}], "ok": True})
    handle = make_handle(podman)
    assert handle.log() == [{"seq": 1}]
    assert handle.doh_log() == [{"seq": 1}]
    handle.set_echo_key(True)
    assert json.loads(podman.calls[-1][1] or "") == {"echo_key": True}
    handle.set_doh_answers({"h.example": ["203.0.113.1"]})
    assert json.loads(podman.calls[-1][1] or "") == {"h.example": ["203.0.113.1"]}
    handle.reset()
    assert "/_admin/reset" in podman.calls[-1][0]


def test_admin_failure_raises_with_stderr() -> None:
    def failing(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "admin 400: bad rule")

    with pytest.raises(sidecar.SidecarError, match="bad rule"):
        sidecar.MockHandle(
            name="x", db_container="db", port=1, doh_port=2, admin_port=3, runner=failing
        ).reset()


def test_module_level_wrappers_use_the_current_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    podman = FakePodman(admin_answer={"count": 2})
    monkeypatch.setattr(sidecar, "_current", make_handle(podman))
    assert sidecar.hits("/games") == 2
    monkeypatch.setattr(sidecar, "_current", None)
    with pytest.raises(sidecar.SidecarError):
        sidecar.hits("/games")


def test_server_file_is_mounted_where_the_sidecar_runs_it() -> None:
    assert (Path(__file__).resolve().parents[1] / "mock_cfbd" / "server.py").exists()
    assert sidecar.SERVER_IN_CONTAINER == "/repo/tests/mock_cfbd/server.py"
