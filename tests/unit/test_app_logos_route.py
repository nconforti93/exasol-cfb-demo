from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backend.routes import logos

PNG = b"\x89PNG\r\n\x1a\n" + b"light"
DARK_PNG = b"\x89PNG\r\n\x1a\n" + b"dark"


def client_for(cache: Path, enabled: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(logos.make_router(cache, enabled=enabled))
    return TestClient(app)


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    directory = tmp_path / "logos"
    directory.mkdir()
    (directory / "102.png").write_bytes(PNG)
    (directory / "103.png").write_bytes(PNG)
    (directory / "103-dark.png").write_bytes(DARK_PNG)
    return directory


def test_logo_route_statuses(cache: Path) -> None:
    client = client_for(cache)
    found = client.get("/api/logos/102.png")
    assert found.status_code == 200
    assert found.content == PNG
    assert found.headers["content-type"] == "image/png"
    assert found.headers["cache-control"] == "public, max-age=86400"
    missing = client.get("/api/logos/101.png")
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"
    bad = client.get("/api/logos/x.png")
    assert bad.status_code == 400
    assert bad.json()["error"] == "invalid_argument"


def test_dark_variant_and_fallback_to_light(cache: Path) -> None:
    client = client_for(cache)
    assert client.get("/api/logos/103.png?variant=dark").content == DARK_PNG
    assert client.get("/api/logos/103.png").content == PNG
    assert client.get("/api/logos/102.png?variant=dark").content == PNG
    assert client.get("/api/logos/102.png?variant=light").content == PNG


def test_unknown_variant_is_a_400(cache: Path) -> None:
    assert client_for(cache).get("/api/logos/102.png?variant=neon").status_code == 400


@pytest.mark.parametrize("name", ["-1", "1.5", "%20", "1e3", "0x10", "10" * 20, "٣"])
def test_a_non_integer_id_is_a_400_before_any_file_access(
    cache: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    touched: list[object] = []
    original = Path.is_file

    def spy(self: Path) -> bool:
        touched.append(self)
        return original(self)

    monkeypatch.setattr(Path, "is_file", spy)
    response = client_for(cache).get(f"/api/logos/{name}.png")
    assert response.status_code == 400
    assert touched == []


def test_no_path_traversal_out_of_the_cache(cache: Path) -> None:
    (cache.parent / "secret.png").write_bytes(PNG)
    client = client_for(cache)
    for path in ("/api/logos/..%2Fsecret.png", "/api/logos/../secret.png"):
        assert client.get(path).status_code in (400, 404)


def test_logos_off_serves_nothing(cache: Path) -> None:
    response = client_for(cache, enabled=False).get("/api/logos/102.png")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_has_logo_matches_cache(cache: Path) -> None:
    assert logos.has_logo(cache, 102, enabled=True)
    assert not logos.has_logo(cache, 101, enabled=True)
    assert not logos.has_logo(cache, 102, enabled=False)
    assert not logos.has_logo(cache / "missing", 102, enabled=True)
