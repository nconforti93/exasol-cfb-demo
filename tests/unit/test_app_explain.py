"""Unit tests of `app/backend/explain.py`: `EXPLAIN VIRTUAL` rows into 0..n manifests."""

from __future__ import annotations

import pytest

from app.backend import explain
from app.backend.explain import ExplainError
from vs.cfbd_vs.plan import RequestPlan, build_manifest, manifest_comment

FAN_OUT = tuple(
    f"/games/teams?seasonType=both&team=Michigan&year={year}" for year in (2022, 2023, 2024)
)
COLUMNS = ["PUSHDOWN_ID", "PUSHDOWN_INVOLVED_TABLES", "PUSHDOWN_SQL"]


def pushdown(
    slices: tuple[str, ...],
    *,
    table: str = "GAMES_TEAMS",
    mode: str = "live",
    sources: list[str] | None = None,
) -> str:
    body = build_manifest(
        RequestPlan(table=table, slice_keys=slices, budget=10), mode=mode, read_through=True
    )
    body["sources"] = sources if sources is not None else ["STORED", "LIVE"]
    return f"{manifest_comment(body)}\nSELECT * FROM (SELECT 1)"


def rows(*pushdowns: str) -> list[tuple[int, str, str]]:
    return [(index, "CFBD_VS.T", sql) for index, sql in enumerate(pushdowns, start=1)]


def test_one_pushdown_becomes_one_manifest() -> None:
    result = explain.parse_explain(COLUMNS, rows(pushdown(FAN_OUT)))
    assert not result.no_virtual_table
    (manifest,) = result.manifests
    assert (manifest.table, manifest.budget, manifest.planned_requests) == ("GAMES_TEAMS", 10, 3)
    assert manifest.slice_keys == FAN_OUT and manifest.sources == ("STORED", "LIVE")


def test_several_pushdowns_become_several_manifests_in_order() -> None:
    sql = rows(pushdown(FAN_OUT[:1]), pushdown(("/games/1/preview",), table="GAMES_PREVIEW"))
    result = explain.parse_explain(COLUMNS, sql)
    assert [m.table for m in result.manifests] == ["GAMES_TEAMS", "GAMES_PREVIEW"]


def test_a_query_without_virtual_tables_has_no_pushdown() -> None:
    result = explain.parse_explain(COLUMNS, [])
    assert result.no_virtual_table and result.manifests == ()
    assert explain.parse_explain([], []).no_virtual_table


def test_a_contradiction_is_a_manifest_with_no_slices_and_no_sources() -> None:
    result = explain.parse_explain(COLUMNS, rows(pushdown((), sources=[])))
    (manifest,) = result.manifests
    assert manifest.planned_requests == 0 and manifest.sources == ()
    assert not result.no_virtual_table


def test_rows_without_the_pushdown_column_are_an_error() -> None:
    with pytest.raises(ExplainError, match="PUSHDOWN_SQL"):
        explain.parse_explain(["A", "B"], [(1, 2)])


def test_a_pushdown_without_a_manifest_is_an_error() -> None:
    with pytest.raises(ExplainError, match="manifest"):
        explain.parse_explain(COLUMNS, [(1, "T", "SELECT 1")])


def test_each_slice_key_is_shown_as_its_http_request_without_a_key() -> None:
    assert (
        explain.request_line(FAN_OUT[0])
        == "GET https://api.collegefootballdata.com/games/teams?seasonType=both&team=Michigan&year=2022"
    )
    assert explain.request_line("/games/17/preview") == (
        "GET https://api.collegefootballdata.com/games/17/preview"
    )
    assert explain.request_line("info") == "GET https://api.collegefootballdata.com/info"
    for line in (explain.request_line(key) for key in FAN_OUT):
        assert "Authorization" not in line and "Bearer" not in line and "key" not in line.lower()


def test_a_manifest_is_described_for_the_api() -> None:
    (manifest,) = explain.parse_explain(COLUMNS, rows(pushdown(FAN_OUT))).manifests
    described = explain.describe_manifest(manifest)
    assert described["table"] == "GAMES_TEAMS" and described["budget"] == 10
    assert described["slice_keys"] == list(FAN_OUT)
    assert described["requests"] == [explain.request_line(key) for key in FAN_OUT]
    assert described["sources"] == ["STORED", "LIVE"]
    assert described["planned_requests"] == 3 and described["read_through"] is True


def test_the_refusal_message_is_the_virtual_schemas_first_line_without_the_session() -> None:
    text = (
        "\n(\n  15 requests are planned for GAMES_TEAMS against a budget of 10 (Session: 1879)\n)\n"
    )
    assert (
        explain.refusal_message(text)
        == "15 requests are planned for GAMES_TEAMS against a budget of 10"
    )
    assert explain.refusal_message(RuntimeError("boom (Session: 1)")) == "boom"
    assert len(explain.refusal_message("x" * 1000)) <= explain.MESSAGE_LIMIT


def test_the_no_virtual_table_label_is_the_inspector_text() -> None:
    assert explain.NO_VIRTUAL_TABLE_TEXT == "no virtual table: 0 calls"
