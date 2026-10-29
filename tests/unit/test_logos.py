import json
from pathlib import Path

import pytest

from scripts import logos

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 40
HOST_URL = "https://cdn.collegefootballdata.com/logos/500/130.png"
DARK_URL = "https://cdn.collegefootballdata.com/logos-dark/500/130.png"
LIGHT_ONLY = json.dumps([HOST_URL])
BOTH = json.dumps([HOST_URL, DARK_URL, "https://cdn.collegefootballdata.com/logos/256/130.png"])
NOW = "2026-10-23T10:00:00+00:00"


class FakeFetcher:
    def __init__(self, responses: dict[str, logos.Fetched] | None = None) -> None:
        self.responses = responses or {}
        self.urls: list[str] = []

    def __call__(self, url: str) -> logos.Fetched:
        self.urls.append(url)
        return self.responses.get(url, logos.Fetched("image/png", PNG))


def teams(*rows: tuple[int, str | None]) -> list[logos.TeamLogos]:
    return [logos.team_logos(team_id, value) for team_id, value in rows]


def run(
    cache: Path,
    rows: list[logos.TeamLogos],
    fetcher: FakeFetcher,
    force: bool = False,
) -> logos.Report:
    return logos.run(rows, cache, fetcher, force=force, workers=1, now=lambda: NOW)


def test_team_logos_picks_light_and_dark_from_the_stored_list() -> None:
    entry = logos.team_logos(130, BOTH)
    assert entry.light == HOST_URL
    assert entry.dark == DARK_URL


def test_team_logos_tolerates_missing_and_malformed_values() -> None:
    for value in (None, "", "not json", "{}", "[]", "[1, 2]"):
        entry = logos.team_logos(7, value)
        assert (entry.light, entry.dark) == (None, None)


def test_http_is_upgraded_to_https() -> None:
    assert (
        logos.allowed_url("http://cdn.collegefootballdata.com/x.png")
        == "https://cdn.collegefootballdata.com/x.png"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/x.png",
        "https://collegefootballdata.com.evil.example/x.png",
        "https://notcollegefootballdata.com/x.png",
        "https://user:pw@cdn.collegefootballdata.com/x.png",
        "https://cdn.collegefootballdata.com:8443/x.png",
        "ftp://cdn.collegefootballdata.com/x.png",
        "file:///etc/passwd",
        "//cdn.collegefootballdata.com/x.png",
        "",
    ],
)
def test_other_hosts_and_schemes_are_refused(url: str) -> None:
    assert logos.allowed_url(url) is None


def test_the_bare_domain_and_subdomains_are_allowed() -> None:
    assert logos.allowed_url("https://collegefootballdata.com/x.png")
    assert logos.allowed_url("https://s.cdn.collegefootballdata.com/x.png")


def test_fetches_both_variants_and_writes_a_manifest(tmp_path: Path) -> None:
    fetcher = FakeFetcher()
    report = run(tmp_path, teams((130, BOTH)), fetcher)
    assert (tmp_path / "130.png").read_bytes() == PNG
    assert (tmp_path / "130-dark.png").read_bytes() == PNG
    assert fetcher.urls == [HOST_URL, DARK_URL]
    assert (report.teams, report.files, report.skipped, report.external_fetches) == (1, 2, 0, 2)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    entry = manifest["teams"]["130"]
    assert entry["host"] == "cdn.collegefootballdata.com"
    assert entry["fetched_at"] == NOW
    assert entry["files"] == {"light": "130.png", "dark": "130-dark.png"}
    assert entry["bytes"] == {"light": len(PNG), "dark": len(PNG)}


def test_existing_files_are_skipped_unless_forced(tmp_path: Path) -> None:
    run(tmp_path, teams((130, BOTH)), FakeFetcher())
    again = FakeFetcher()
    report = run(tmp_path, teams((130, BOTH)), again)
    assert again.urls == []
    assert (report.files, report.skipped, report.external_fetches) == (0, 2, 0)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["teams"]["130"]["files"] == {"light": "130.png", "dark": "130-dark.png"}
    forced = FakeFetcher()
    report = run(tmp_path, teams((130, BOTH)), forced, force=True)
    assert len(forced.urls) == 2
    assert report.external_fetches == 2


def test_a_team_without_a_dark_logo_gets_only_the_light_file(tmp_path: Path) -> None:
    report = run(tmp_path, teams((130, LIGHT_ONLY)), FakeFetcher())
    assert sorted(path.name for path in tmp_path.glob("*.png")) == ["130.png"]
    assert report.files == 1


def test_foreign_hosts_are_reported_and_never_fetched(tmp_path: Path) -> None:
    foreign = json.dumps(["https://cdn.example.org/logos/500/2.png"])
    fetcher = FakeFetcher()
    report = run(tmp_path, teams((2, foreign)), fetcher)
    assert fetcher.urls == []
    assert report.refused == ["2 light: host not allowed (cdn.example.org)"]
    assert report.missing_light == [2]
    assert list(tmp_path.glob("*.png")) == []


@pytest.mark.parametrize(
    "response",
    [
        logos.Fetched("text/html", PNG),
        logos.Fetched("image/png", b"GIF89a" + b"x" * 40),
        logos.Fetched("image/png", PNG + b"x" * logos.MAX_BYTES),
        logos.Fetched("image/png", b""),
    ],
)
def test_wrong_type_signature_or_size_is_refused_and_leaves_no_file(
    tmp_path: Path, response: logos.Fetched
) -> None:
    fetcher = FakeFetcher({HOST_URL: response})
    report = run(tmp_path, teams((130, LIGHT_ONLY)), fetcher)
    assert report.files == 0
    assert len(report.refused) == 1
    assert report.external_fetches == 1
    assert list(tmp_path.iterdir()) == [tmp_path / "manifest.json"]


def test_a_fetch_error_is_reported_and_the_run_continues(tmp_path: Path) -> None:
    class Failing(FakeFetcher):
        def __call__(self, url: str) -> logos.Fetched:
            if url == HOST_URL:
                raise logos.FetchError("HTTP 404")
            return super().__call__(url)

    other = "https://cdn.collegefootballdata.com/logos/500/99.png"
    report = run(tmp_path, teams((130, LIGHT_ONLY), (99, json.dumps([other]))), Failing())
    assert report.failed == ["130 light: HTTP 404"]
    assert (tmp_path / "99.png").exists()
    assert report.missing_light == [130]


def test_summary_line_counts_everything() -> None:
    report = logos.Report(teams=3, files=4, skipped=2, external_fetches=4)
    assert logos.summary(report) == (
        "3 teams, 4 files, 2 skipped, 0 CFBD calls, "
        "4 external fetches to cdn.collegefootballdata.com"
    )


def test_cache_directory_inside_the_repository_is_refused() -> None:
    with pytest.raises(logos.LogosError):
        logos.resolve_cache_dir({"CFB_LOGO_DIR": str(logos.ROOT / "cache" / "logos")})


def test_cache_directory_defaults_and_overrides(tmp_path: Path) -> None:
    assert logos.resolve_cache_dir({}) == Path.home() / ".cfb-demo" / "logos"
    assert logos.resolve_cache_dir({"APP_LOGO_DIR": str(tmp_path)}) == tmp_path
    other = tmp_path / "other"
    env = {"APP_LOGO_DIR": str(tmp_path), "CFB_LOGO_DIR": str(other)}
    assert logos.resolve_cache_dir(env) == other


def test_the_selection_reads_stored_teams_only() -> None:
    for sql in (logos.teams_sql(include_fcs=False), logos.teams_sql(include_fcs=True)):
        assert "CFB_CORE.TEAMS" in sql
        assert "CFBD_VS" not in sql
        assert "REQ_AT DESC" in sql
    assert "'FCS'" not in logos.teams_sql(include_fcs=False)
    assert "'FCS'" in logos.teams_sql(include_fcs=True)
