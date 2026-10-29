"""`GET /api/logos/{team_id}.png[?variant=dark]`: team logos from the private cache that
`make logos` fills (plan.md, Logos). No database; the id is validated before any file is touched."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response

CACHE_CONTROL = "public, max-age=86400"
VARIANTS = ("light", "dark")
_TEAM_ID = re.compile(r"[0-9]{1,12}", re.ASCII)


def logo_path(logo_dir: Path, team_id: int, variant: Literal["light", "dark"] = "light") -> Path:
    """Where a team's logo file lives in the cache."""
    suffix = "-dark" if variant == "dark" else ""
    return logo_dir / f"{team_id}{suffix}.png"


def has_logo(logo_dir: Path, team_id: int, *, enabled: bool) -> bool:
    """Whether the browser may ask for this team's logo: logos are on and the file exists."""
    return enabled and logo_path(logo_dir, team_id).is_file()


def make_router(logo_dir: Path, *, enabled: bool) -> APIRouter:
    """The logo route over one cache directory; `enabled=False` is `APP_LOGOS=off`."""
    router = APIRouter()

    @router.get("/api/logos/{team_id}.png")
    def get_logo(team_id: str, variant: str = "light") -> Response:
        if not _TEAM_ID.fullmatch(team_id):
            return _error(400, "invalid_argument", "team id must be an integer")
        if variant not in VARIANTS:
            return _error(400, "invalid_argument", "variant must be light or dark")
        path = _existing_file(logo_dir, int(team_id), variant) if enabled else None
        if path is None:
            return _error(404, "not_found", f"no logo for team {int(team_id)}")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": CACHE_CONTROL})

    return router


def _existing_file(logo_dir: Path, team_id: int, variant: str) -> Path | None:
    wanted: Literal["light", "dark"] = "dark" if variant == "dark" else "light"
    for candidate in (logo_path(logo_dir, team_id, wanted), logo_path(logo_dir, team_id)):
        if candidate.is_file():
            return candidate
    return None


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status)
