"""Lifecycle of the mock CFBD server as a sidecar container in the DB container's network.

A UDF cannot reach the host (rootless podman uses `slirp4netns --disable-host-loopback`), but it
shares the network namespace of the DB container. So the mock runs in a second container started
with `--network container:<DB container>` and listens on 127.0.0.1 there; `CFBD_API_MOCK` is
`handle.address` (`http://127.0.0.1:<port>`). The host cannot reach that loopback, so the admin
API (script, hits, log, ...) is called with `podman exec -i <sidecar> python3 server.py request`
and JSON on stdin. That costs about 0.2 s per call, needs no extra mount or port, and is the same
HTTP admin API that the in-process unit tests call directly.

The sidecar needs a local Python 3 image (the python-3.12 script-language-container, so nothing is
pulled), the repo's `tests/` and `vs/` mounted read-only, and a self-signed DoH certificate that
is generated into a temp dir at start and removed at stop (the key is never committed).

Restart the sidecar after `exasol stop/start -d local`: the DB container, and with it the network
namespace, is replaced. `start()` removes a stale sidecar of the same name first.
"""

import json
import random
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.mock_cfbd.certs import generate_self_signed

REPO_ROOT = Path(__file__).resolve().parents[2]
SIDECAR_NAME = "cfbd-mock-sidecar"
LOOPBACK = "127.0.0.1"
DB_IMAGE_MARKER = "exasol/nano"
DB_PORT = "8563"
SLC_MARKER = "script-language-container"
PYTHON_MARKER = "python-3.12"
CERT_NAME = "doh-cert.pem"
KEY_NAME = "doh-key.pem"
CONTROL_DIR = "/ctl"
SERVER_IN_CONTAINER = "/repo/tests/mock_cfbd/server.py"
LISTENING_LINE = re.compile(
    r"LISTENING port=(?P<port>\d+) doh_port=(?P<doh_port>\d+) admin_port=(?P<admin_port>\d+)"
)
PORT_RANGE = (40000, 49999)
START_ATTEMPTS = 3
START_TIMEOUT_SECONDS = 30
POLL_SECONDS = 0.2
PODMAN_TIMEOUT_SECONDS = 60

Runner = Callable[[list[str], str | None], "subprocess.CompletedProcess[str]"]


class SidecarError(RuntimeError):
    """The sidecar could not be started or an admin call failed."""


def run_podman(args: list[str], stdin: str | None = None) -> "subprocess.CompletedProcess[str]":
    """Run a command on the host; the default `Runner`."""
    return subprocess.run(
        args,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=PODMAN_TIMEOUT_SECONDS,
        check=False,
    )


def find_db_container(ps_output: str) -> str | None:
    """The name of the running Exasol nano container publishing 8563, from `podman ps` lines."""
    for line in ps_output.splitlines():
        parts = line.split("|")
        if len(parts) == 3 and DB_IMAGE_MARKER in parts[1] and DB_PORT in parts[2]:
            return parts[0]
    return None


def find_python_image(images_output: str) -> str:
    """A local python-3.12 script-language-container image; never pulls."""
    for line in images_output.splitlines():
        if SLC_MARKER in line and PYTHON_MARKER in line:
            return line.strip()
    raise SidecarError(f"no local {SLC_MARKER} image with {PYTHON_MARKER} found (not pulling one)")


@dataclass
class MockHandle:
    """A running sidecar. Methods call the mock's admin API through `podman exec`."""

    name: str
    db_container: str
    port: int
    doh_port: int
    admin_port: int
    ip: str = LOOPBACK
    cert_dir: Path | None = None
    runner: Runner = run_podman

    @property
    def address(self) -> str:
        """The value for `CFBD_API_MOCK`."""
        return f"http://{self.ip}:{self.port}"

    @property
    def doh_url(self) -> str:
        """A `DOH_URL` pointing at the mock DoH endpoint (self-signed, so it fails verification)."""
        return f"https://{self.ip}:{self.doh_port}/dns-query"

    def hits(self, path: str | None = None, query: Mapping[str, str] | None = None) -> int:
        """Number of API requests so far; `path` is exact, or a prefix with a trailing `*`."""
        params = {}
        if path is not None:
            params["path"] = path
        if query:
            params["query"] = urllib.parse.urlencode(query)
        suffix = f"?{urllib.parse.urlencode(params)}" if params else ""
        return int(self._admin("GET", f"/_admin/hits{suffix}")["count"])

    def log(self) -> list[dict[str, Any]]:
        """The API request log: method, path, query, headers, status, scripted."""
        return list(self._admin("GET", "/_admin/log")["entries"])

    def doh_log(self) -> list[dict[str, Any]]:
        """The DoH log: one `connect`, `tls` and `query` entry per connection and query."""
        return list(self._admin("GET", "/_admin/doh-log")["entries"])

    def script(
        self,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        times: int | None = None,
        status: int | None = None,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        retry_after: float | None = None,
        delay_ms: int | None = None,
        oversize_bytes: int | None = None,
    ) -> int:
        """Script a behaviour for matching requests and return the rule id.

        `body` may be a str, bytes-free JSON value or a dict. A rule that has only `delay_ms`
        delays and serves the normal response; `status`, `body` or `oversize_bytes` replace it.
        """
        given = {
            "query": query,
            "times": times,
            "status": status,
            "body": body,
            "headers": headers,
            "retry_after": retry_after,
            "delay_ms": delay_ms,
            "oversize_bytes": oversize_bytes,
        }
        rule = {"path": path, **{k: v for k, v in given.items() if v is not None}}
        return int(self._admin("POST", "/_admin/script", rule)["id"])

    def set_echo_key(self, enabled: bool) -> None:
        """Key-echo mode: reflect the bearer token in `X-Echo-Key` and in error bodies."""
        self._admin("POST", "/_admin/config", {"echo_key": enabled})

    def set_info(
        self,
        mode: str,
        *,
        base_used: int | None = None,
        used: int | None = None,
        limit: int | None = None,
        remaining: int | None = None,
        tier: str | None = None,
        reset_at: str | None = None,
    ) -> dict[str, Any]:
        """Set how `/info` answers: `static` fields, or `counting` (`usedCalls` = `base_used` +
        the API requests since the last reset; `/info` itself is free). Cleared by `reset`."""
        named = {
            "usedCalls": used,
            "monthlyLimit": limit,
            "remainingCalls": remaining,
            "tierName": tier,
            "resetAt": reset_at,
        }
        fields = {name: value for name, value in named.items() if value is not None}
        spec: dict[str, Any] = {"mode": mode}
        if base_used is not None:
            spec["base_used"] = base_used
        if fields:
            spec["fields"] = fields
        answer: dict[str, Any] = self._admin("POST", "/_admin/info", spec)
        return answer

    def info_state(self) -> dict[str, Any]:
        """`{mode, base_used, fields, data_requests}`: how `/info` answers and what it counted."""
        answer: dict[str, Any] = self._admin("GET", "/_admin/info")
        return answer

    def set_doh_answers(self, answers: Mapping[str, list[str]]) -> None:
        """Set the A records the mock DoH endpoint serves, by host name."""
        self._admin("POST", "/_admin/doh-answers", dict(answers))

    def reset(self) -> None:
        """Clear the logs, the scripted rules, key-echo mode and the `/info` mode."""
        self._admin("POST", "/_admin/reset")

    def stop(self) -> None:
        """Remove the sidecar container and the temp certificate directory."""
        self.runner(["podman", "rm", "-f", "--ignore", self.name], None)
        if self.cert_dir is not None:
            shutil.rmtree(self.cert_dir, ignore_errors=True)

    def _admin(self, method: str, path: str, payload: Any = None) -> Any:
        command = [
            "podman",
            "exec",
            "-i",
            self.name,
            "python3",
            SERVER_IN_CONTAINER,
            "request",
            "--admin-port",
            str(self.admin_port),
        ]
        if payload is not None:
            command.append("--stdin")
        command += [method, path]
        done = self.runner(command, None if payload is None else json.dumps(payload))
        if done.returncode != 0:
            raise SidecarError(f"admin {method} {path} failed: {done.stderr.strip()}")
        return json.loads(done.stdout)


_current: MockHandle | None = None


def start(
    provider: str | None = None,
    runner: Runner = run_podman,
    repo_root: Path = REPO_ROOT,
) -> MockHandle:
    """Start the sidecar in the running DB container's network namespace and return its handle.

    `provider` is `module:callable` (importable inside the sidecar with `tests/` and `vs/` on
    its path, standard library only), for example `tests.synthetic.fake:response_for`. Without
    it, only scripted rules and the built-in `/info` answer.
    """
    global _current
    db_container = find_db_container(
        runner(["podman", "ps", "--format", "{{.Names}}|{{.Image}}|{{.Ports}}"], None).stdout
    )
    if db_container is None:
        raise SidecarError(
            "no running Exasol nano container publishing 8563 (exasol start -d local)"
        )
    image = find_python_image(
        runner(["podman", "images", "--format", "{{.Repository}}:{{.Tag}}"], None).stdout
    )
    runner(["podman", "rm", "-f", "--ignore", SIDECAR_NAME], None)
    cert_dir = Path(tempfile.mkdtemp(prefix="cfbd-mock-"))
    try:
        generate_self_signed(cert_dir, LOOPBACK)
        handle = _launch(runner, db_container, image, cert_dir, provider, repo_root)
    except BaseException:
        runner(["podman", "rm", "-f", "--ignore", SIDECAR_NAME], None)
        shutil.rmtree(cert_dir, ignore_errors=True)
        raise
    _current = handle
    return handle


def stop() -> None:
    """Stop the current sidecar, if any."""
    global _current
    if _current is not None:
        _current.stop()
        _current = None


def hits(path: str | None = None, query: Mapping[str, str] | None = None) -> int:
    """`MockHandle.hits` of the current sidecar."""
    return _require().hits(path, query)


def log() -> list[dict[str, Any]]:
    """`MockHandle.log` of the current sidecar."""
    return _require().log()


def script(path: str, **behaviour: Any) -> int:
    """`MockHandle.script` of the current sidecar."""
    return _require().script(path, **behaviour)


def _require() -> MockHandle:
    if _current is None:
        raise SidecarError("no sidecar is running; call start() first")
    return _current


def _launch(
    runner: Runner,
    db_container: str,
    image: str,
    cert_dir: Path,
    provider: str | None,
    repo_root: Path,
) -> MockHandle:
    failure = ""
    for _ in range(START_ATTEMPTS):
        port, doh_port, admin_port = random.sample(range(*PORT_RANGE), 3)
        command = _run_command(
            db_container, image, cert_dir, provider, repo_root, (port, doh_port, admin_port)
        )
        started = runner(command, None)
        if started.returncode != 0:
            raise SidecarError(f"podman run failed: {started.stderr.strip()}")
        failure = _wait_until_listening(runner)
        listening = LISTENING_LINE.search(failure)
        if listening:
            return MockHandle(
                name=SIDECAR_NAME,
                db_container=db_container,
                port=int(listening["port"]),
                doh_port=int(listening["doh_port"]),
                admin_port=int(listening["admin_port"]),
                cert_dir=cert_dir,
                runner=runner,
            )
        runner(["podman", "rm", "-f", "--ignore", SIDECAR_NAME], None)
    raise SidecarError(f"the mock server did not start:\n{failure}")


def _run_command(
    db_container: str,
    image: str,
    cert_dir: Path,
    provider: str | None,
    repo_root: Path,
    ports: tuple[int, int, int],
) -> list[str]:
    port, doh_port, admin_port = ports
    command = [
        "podman",
        "run",
        "-d",
        "--init",
        "--name",
        SIDECAR_NAME,
        "--network",
        f"container:{db_container}",
        "-v",
        f"{repo_root / 'tests'}:/repo/tests:ro",
        "-v",
        f"{repo_root / 'vs'}:/repo/vs:ro",
        "-v",
        f"{cert_dir}:{CONTROL_DIR}:ro",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "--entrypoint",
        "python3",
        image,
        SERVER_IN_CONTAINER,
        "serve",
        "--host",
        LOOPBACK,
        "--port",
        str(port),
        "--doh-port",
        str(doh_port),
        "--admin-port",
        str(admin_port),
        "--cert",
        f"{CONTROL_DIR}/{CERT_NAME}",
        "--key",
        f"{CONTROL_DIR}/{KEY_NAME}",
    ]
    if provider is not None:
        command += ["--provider", provider]
    return command


def _wait_until_listening(runner: Runner) -> str:
    """Return the container log once it has the LISTENING line, or the log of the failure."""
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    logs = ""
    while time.monotonic() < deadline:
        done = runner(["podman", "logs", SIDECAR_NAME], None)
        logs = done.stdout + done.stderr
        if LISTENING_LINE.search(logs):
            return logs
        if "Traceback" in logs or "Error" in logs:
            if "Address already in use" not in logs:
                raise SidecarError(f"the mock server crashed on start:\n{logs}")
            return logs
        time.sleep(POLL_SECONDS)
    return logs or "timed out waiting for LISTENING"
