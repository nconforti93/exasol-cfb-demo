"""The first serving slice is a zero-call view layer with stable names."""

from analyses import deploy
from vs.host.namespace import Namespace


def test_first_slice_lints_and_renders_only_model_views() -> None:
    api, live = deploy.prepare(Namespace("TEST_"))
    assert len(api) == 8
    assert live == []
    assert {item.name for item in api} == {
        "CURRENT_POINT",
        "AS_OF_POINTS",
        "TEAMS",
        "GAMES",
        "TEAM_GAMES",
        "TEAM_SEASONS",
        "RANKINGS",
        "SP_RATINGS",
    }
    assert all(item.kind == "VIEW" and item.schema == "TEST_CFB_API" for item in api)
    assert all(item.comment and item.comment.startswith("One row per ") for item in api)
    assert all(item.columns and all(column.comment for column in item.columns) for item in api)
    assert all("TEST_CFB_CORE" not in statement for item in api for statement in item.statements)


def test_namespace_separates_live_access_from_chat_access() -> None:
    namespace = Namespace("TEST_")
    assert namespace.api_schema == "TEST_CFB_API"
    assert namespace.api_live_schema == "TEST_CFB_API_LIVE"
    assert namespace.app_role == "TEST_CFB_APP"
    assert namespace.mcp_role == "TEST_CFB_MCP"
