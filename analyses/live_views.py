"""Render the live game-preview view from the virtual-table catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CATALOG = Path(__file__).resolve().parents[1] / "vs" / "catalog" / "catalog.json"
EXCLUDED = frozenset({"ROW_NO", "ASSEMBLED_AT"})


def _quoted_comment(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def preview_ddl(catalog_path: Path = CATALOG) -> str:
    """Keep source names and column comments, especially pushdown key P_GAME_ID."""
    catalog: dict[str, Any] = json.loads(catalog_path.read_text(encoding="utf-8"))
    table = next(item for item in catalog["tables"] if item["name"] == "GAMES_PREVIEW")
    columns = [item for item in table["columns"] if item["name"] not in EXCLUDED]
    declarations = ",\n".join(
        f"  {item['name']} COMMENT IS {_quoted_comment(item['comment'])}" for item in columns
    )
    names = ",\n".join(f'       v."{item["name"]}"' for item in columns)
    return (
        "CREATE OR REPLACE FORCE VIEW {API_LIVE_SCHEMA}.GAME_PREVIEW (\n"
        f"{declarations}\n) AS\nSELECT\n{names}\n"
        '  FROM {VS_NAME}."GAMES_PREVIEW" v\n'
        "COMMENT IS 'One row per requested game preview. A query with a literal P_GAME_ID "
        "makes one CFBD call per game id in live mode; replay and stored slices make none.';\n"
    )
