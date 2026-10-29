"""Keep the failed preview draft reproducible and deploy only supported live views."""

from pathlib import Path

from analyses import deploy, live_views
from vs.host.namespace import Namespace


def test_preview_file_is_exact_catalog_render() -> None:
    stored = Path("specs/_plans/add-cfb-analyses/game_preview_draft.sql").read_text(
        encoding="utf-8"
    )
    assert stored == live_views.preview_ddl()
    assert 'v."P_GAME_ID"' in stored
    assert 'v."REQ_ROWS"' in stored
    assert 'v."ROW_NO"' not in stored
    assert 'v."ASSEMBLED_AT"' not in stored


def test_live_schema_deploys_only_quota() -> None:
    api, live = deploy.prepare(Namespace("TEST_"))
    assert {item.name for item in live} == {"QUOTA"}
    assert len(api) == 20
    assert all(item.schema == "TEST_CFB_API_LIVE" for item in live)
