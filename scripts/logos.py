"""`make logos`: fill the private team-logo cache from the stored team list (plan.md, Logos).

Reads `LOGOS` of `CFB_CORE.TEAMS` as the owner (`vs/host/target.py`; stored data only, so 0 CFBD
calls) and downloads each FBS team's light and dark PNG into the cache directory, by default
`~/.cfb-demo/logos` (`CFB_LOGO_DIR`, else `APP_LOGO_DIR`). This is an external fetch to the logo
host, not a CFBD call. It runs by hand and never from the app. Rules: https only (http is
upgraded), hosts under `collegefootballdata.com` only (CFBD's logo CDN, decision [43]), no
redirects, a PNG content type and signature, at most 1 MB, an existing file is skipped unless
`--force`. A `manifest.json` records what was saved.
The cache must lie outside the repository: logos are never committed.

`--target local|aws` (or `TARGET`) selects the deployment; `--all` adds FCS teams; `--force`
fetches again.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from app.backend.settings import DEFAULT_LOGO_DIR
from vs.host import target

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_HOST_SUFFIX = "collegefootballdata.com"
SUMMARY_HOST = "cdn.collegefootballdata.com"
MAX_BYTES = 1_000_000
TIMEOUT_SECONDS = 10.0
WORKERS = 4
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MANIFEST_NAME = "manifest.json"
USER_AGENT = "exasol-cfb-demo-logos/1 (one-time cache fill)"
CACHE_VARIABLES = ("CFB_LOGO_DIR", "APP_LOGO_DIR")

EXIT_OK = 0
EXIT_ERROR = 2

Variant = Literal["light", "dark"]


class LogosError(RuntimeError):
    """The run cannot start; the message never contains a secret."""


class FetchError(RuntimeError):
    """One download failed (status, size, timeout); the run goes on."""


@dataclass(frozen=True)
class Fetched:
    """The answer of one GET: its content type and body."""

    content_type: str
    body: bytes


Fetcher = Callable[[str], Fetched]


@dataclass(frozen=True)
class TeamLogos:
    """The logo URLs the stored list gives one team."""

    team_id: int
    light: str | None
    dark: str | None
    school: str = ""


@dataclass
class Report:
    """What a run did."""

    teams: int = 0
    files: int = 0
    skipped: int = 0
    external_fetches: int = 0
    refused: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    missing_light: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class _Job:
    team_id: int
    variant: Variant
    url: str
    path: Path


@dataclass(frozen=True)
class _Outcome:
    job: _Job
    fetched: bool = False
    size: int = 0
    problem: str | None = None
    refused: bool = False


def teams_sql(*, include_fcs: bool) -> str:
    """The newest stored row per team id, FBS (and FCS with `include_fcs`); stored data only."""
    classes = "'FBS', 'FCS'" if include_fcs else "'FBS'"
    return (
        "SELECT ID, SCHOOL, LOGOS FROM ("
        "SELECT ID, SCHOOL, LOGOS, CLASSIFICATION, ROW_NUMBER() OVER "
        "(PARTITION BY ID ORDER BY REQ_AT DESC, ROW_NO DESC) AS RN FROM CFB_CORE.TEAMS) "
        f"WHERE RN = 1 AND UPPER(CLASSIFICATION) IN ({classes}) ORDER BY ID"
    )


def team_logos(team_id: int, value: str | None, school: str = "") -> TeamLogos:
    """The light URL (the first one) and the dark URL (the first with `dark` in it) of a `LOGOS`
    JSON list; anything else gives a team with no logos."""
    urls = _url_list(value)
    light = urls[0] if urls else None
    dark = next((url for url in urls if "dark" in url.lower()), None)
    return TeamLogos(team_id, light, dark, school)


def allowed_url(url: str) -> str | None:
    """The https form of a logo URL, or None when its scheme, host, port or credentials are not
    allowed."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        return None
    if port not in (None, 80 if parts.scheme == "http" else 443):
        return None
    if host != ALLOWED_HOST_SUFFIX and not host.endswith("." + ALLOWED_HOST_SUFFIX):
        return None
    return parts._replace(scheme="https", netloc=host).geturl()


def resolve_cache_dir(environ: Mapping[str, str]) -> Path:
    """`CFB_LOGO_DIR`, else `APP_LOGO_DIR`, else `~/.cfb-demo/logos`; never inside the repo."""
    chosen = next((environ[name] for name in CACHE_VARIABLES if environ.get(name)), None)
    directory = Path(chosen).expanduser() if chosen else DEFAULT_LOGO_DIR
    if directory.resolve().is_relative_to(ROOT):
        raise LogosError(
            f"the logo cache {directory} is inside the repository; logos are never committed"
        )
    return directory


def run(
    rows: Sequence[TeamLogos],
    cache: Path,
    fetcher: Fetcher,
    *,
    force: bool = False,
    workers: int = WORKERS,
    now: Callable[[], str] | None = None,
) -> Report:
    """Download what is missing into `cache`, write the manifest and report."""
    stamp = now or _utc_now
    cache.mkdir(parents=True, exist_ok=True)
    report = Report(teams=len(rows))
    jobs: list[_Job] = []
    for row in rows:
        variants: list[tuple[Variant, str | None]] = [("light", row.light), ("dark", row.dark)]
        for variant, url in variants:
            job = _plan(row, variant, url, cache, force, report)
            if job:
                jobs.append(job)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        outcomes = list(pool.map(lambda job: _download(job, fetcher), jobs))
    fetched_ids = set()
    for outcome in outcomes:
        report.external_fetches += outcome.fetched
        label = f"{outcome.job.team_id} {outcome.job.variant}"
        if outcome.problem is None:
            report.files += 1
            fetched_ids.add(outcome.job.team_id)
        elif outcome.refused:
            report.refused.append(f"{label}: {outcome.problem}")
        else:
            report.failed.append(f"{label}: {outcome.problem}")
    report.missing_light = [
        row.team_id for row in rows if not _file(cache, row.team_id, "light").exists()
    ]
    _write_manifest(cache, rows, fetched_ids, stamp())
    return report


def summary(report: Report) -> str:
    """The one-line result."""
    return (
        f"{report.teams} teams, {report.files} files, {report.skipped} skipped, 0 CFBD calls, "
        f"{report.external_fetches} external fetches to {SUMMARY_HOST}"
    )


def https_fetcher() -> Fetcher:
    """The real GET: no redirects, a timeout, and at most `MAX_BYTES` read."""
    client = httpx.Client(
        timeout=TIMEOUT_SECONDS, follow_redirects=False, headers={"User-Agent": USER_AGENT}
    )

    def fetch(url: str) -> Fetched:
        try:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise FetchError(f"HTTP {response.status_code}")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise FetchError("larger than 1 MB")
                return Fetched(response.headers.get("content-type", ""), bytes(body))
        except httpx.HTTPError as error:
            raise FetchError(type(error).__name__) from None

    return fetch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", default=None, help="local or aws (default: $TARGET, else local)"
    )
    parser.add_argument("--all", action="store_true", help="also fetch FCS teams")
    parser.add_argument("--force", action="store_true", help="fetch files that already exist")
    args = parser.parse_args(argv)
    try:
        cache = resolve_cache_dir(os.environ)
        rows = read_teams(target.resolve(args.target), include_fcs=args.all)
    except (LogosError, target.TargetError) as error:
        print(f"logos failed: {error}", file=sys.stderr)
        return EXIT_ERROR
    report = run(rows, cache, https_fetcher(), force=args.force)
    print(_details(report, rows, cache))
    return EXIT_OK


def read_teams(deployment: target.Target, *, include_fcs: bool) -> list[TeamLogos]:
    """The stored teams with their logo URLs, read as the owner."""
    connection = deployment.connect()
    try:
        fetched = connection.execute(teams_sql(include_fcs=include_fcs)).fetchall()
    finally:
        connection.close()
    return [team_logos(int(team_id), logos, school or "") for team_id, school, logos in fetched]


def _details(report: Report, rows: Sequence[TeamLogos], cache: Path) -> str:
    names = {row.team_id: row.school for row in rows}
    lines = [summary(report), f"cache: {cache}"]
    lines += [f"refused {line}" for line in report.refused[:5]]
    if len(report.refused) > 5:
        lines.append(f"... and {len(report.refused) - 5} more refused")
    lines += [f"failed {line}" for line in report.failed]
    lines.append(f"{len(report.missing_light)} of {report.teams} teams have no light logo")
    lines += [f"  no logo: {team_id} {names[team_id]}" for team_id in report.missing_light[:20]]
    return "\n".join(lines)


def _plan(
    row: TeamLogos, variant: Variant, url: str | None, cache: Path, force: bool, report: Report
) -> _Job | None:
    if url is None:
        return None
    path = _file(cache, row.team_id, variant)
    if path.exists() and not force:
        report.skipped += 1
        return None
    safe = allowed_url(url)
    if safe is None:
        host = urlsplit(url).hostname or ""
        report.refused.append(f"{row.team_id} {variant}: host not allowed ({host})")
        return None
    return _Job(row.team_id, variant, safe, path)


def _download(job: _Job, fetcher: Fetcher) -> _Outcome:
    try:
        answer = fetcher(job.url)
    except FetchError as error:
        return _Outcome(job, fetched=True, problem=str(error))
    problem = png_problem(answer)
    if problem:
        return _Outcome(job, fetched=True, problem=problem, refused=True)
    _write_atomic(job.path, answer.body)
    return _Outcome(job, fetched=True, size=len(answer.body))


def png_problem(answer: Fetched) -> str | None:
    """Why a download is not an acceptable logo, or None."""
    if not answer.body:
        return "empty body"
    if len(answer.body) > MAX_BYTES:
        return "larger than 1 MB"
    content_type = answer.content_type.split(";")[0].strip().lower()
    if content_type != "image/png":
        return f"content type {content_type or 'missing'} is not image/png"
    if not answer.body.startswith(PNG_SIGNATURE):
        return "not a PNG (bad signature)"
    return None


def _write_atomic(path: Path, body: bytes) -> None:
    partial = path.with_name(path.name + ".part")
    partial.write_bytes(body)
    os.replace(partial, path)


def _file(cache: Path, team_id: int, variant: Variant) -> Path:
    return cache / (f"{team_id}-dark.png" if variant == "dark" else f"{team_id}.png")


def _write_manifest(
    cache: Path, rows: Sequence[TeamLogos], fetched_ids: set[int], stamp: str
) -> None:
    path = cache / MANIFEST_NAME
    previous = _read_manifest(path)
    for row in rows:
        files = {v: _file(cache, row.team_id, v) for v in ("light", "dark")}
        present = {variant: file for variant, file in files.items() if file.exists()}
        key = str(row.team_id)
        if not present:
            previous.pop(key, None)
            continue
        old = previous.get(key, {})
        refreshed = row.team_id in fetched_ids or not old
        host = urlsplit(allowed_url(row.light or row.dark or "") or "").hostname or old.get("host")
        previous[key] = {
            "files": {variant: file.name for variant, file in present.items()},
            "host": host,
            "bytes": {variant: file.stat().st_size for variant, file in present.items()},
            "fetched_at": stamp if refreshed else old.get("fetched_at", stamp),
        }
    body = json.dumps(
        {"teams": dict(sorted(previous.items(), key=lambda kv: int(kv[0])))}, indent=2
    )
    _write_atomic(path, (body + "\n").encode())


def _read_manifest(path: Path) -> dict[str, dict[str, Any]]:
    try:
        teams = json.loads(path.read_text())["teams"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return teams if isinstance(teams, dict) else {}


def _url_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str)]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


if __name__ == "__main__":
    sys.exit(main())
