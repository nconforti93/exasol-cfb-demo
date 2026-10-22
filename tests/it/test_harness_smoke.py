"""Proof that the harness works end to end: deploy, live via the mock, replay, stored, EXPLAIN."""

from __future__ import annotations

import pytest

from tests.it.harness import READER_USER, Harness, Mock
from tests.it.support import where_clause
from tests.synthetic import fake
from vs.cfbd_vs.pushdown_sql import SOURCE_STORED

pytestmark = pytest.mark.it

TABLE = "CALENDAR"


def calendar_query(harness: Harness, columns: str = "*") -> str:
    where = where_clause(TABLE, fake.minimal_filter(TABLE))
    return f"SELECT {columns} FROM {harness.vs}.{TABLE} WHERE {where}"


def test_deploy_left_a_working_namespace(harness: Harness) -> None:
    report = harness.deploy_report
    assert report is not None
    assert report.fetch_udf == "unchanged"
    assert harness.properties()["CONNECTION_NAME"] == "CFBD_API_MOCK"
    assert harness.query(f"SELECT COUNT(*) FROM {harness.meta}.LOAD_LOG") == [(0,)]


def test_info_is_read_live_through_the_mock_with_one_request(harness: Harness, mock: Mock) -> None:
    rows = harness.query(f"SELECT * FROM {harness.vs}.INFO")
    assert len(rows) == 1
    assert harness.mock_hits() == 1
    assert harness.mock_hits("/info") == 1
    assert harness.marker_key not in repr(rows)


def test_a_replay_query_reads_a_synthetic_recording_without_touching_the_mock(
    harness: Harness, mock: Mock
) -> None:
    slice_key = fake.slice_key_for(TABLE)
    harness.write_recordings([slice_key])
    harness.use_replay()
    rows = harness.query_dicts(calendar_query(harness, "REQ_SLICE, REQ_SOURCE"))
    assert rows
    assert {row["REQ_SLICE"] for row in rows} == {slice_key}
    assert {row["REQ_SOURCE"] for row in rows} == {"REPLAY"}
    assert harness.mock_hits() == 0


def test_a_staged_slice_comes_back_stored_with_zero_mock_hits(harness: Harness, mock: Mock) -> None:
    slice_keys = harness.stage_slices(TABLE)
    assert slice_keys == [fake.slice_key_for(TABLE)]
    harness.replay_area.clear()
    harness.wait_for_files([harness.replay_area.udf_file(k) for k in slice_keys], present=False)
    rows = harness.query_dicts(calendar_query(harness, "REQ_SLICE, REQ_SOURCE"))
    assert rows
    assert {row["REQ_SOURCE"] for row in rows} == {SOURCE_STORED}
    assert harness.mock_hits() == 0
    stored = harness.query(f"SELECT COUNT(*) FROM {harness.meta}.LOAD_LOG WHERE IS_FINAL")
    assert stored == [(1,)]


def test_explain_returns_the_manifest_of_a_planned_query(harness: Harness, mock: Mock) -> None:
    manifest = harness.explain(calendar_query(harness))
    assert manifest.table == TABLE
    assert manifest.slice_keys == (fake.slice_key_for(TABLE),)
    assert manifest.fetch_mode == "live"
    assert manifest.sources == ("STORED", "LIVE")
    assert harness.mock_hits() == 0


def test_state_is_clean_again_after_the_tests_above(harness: Harness) -> None:
    assert harness.query(f"SELECT COUNT(*) FROM {harness.meta}.LOAD_LOG") == [(0,)]
    assert harness.query(f"SELECT COUNT(*) FROM {harness.core}.{TABLE}") == [(0,)]
    assert harness.properties() == harness.baseline_properties
    assert not list(harness.replay_area.host_dir.rglob("*.cfbd.json"))


def test_the_reader_selects_through_the_virtual_schema_only(harness: Harness) -> None:
    assert harness.query("SELECT CURRENT_USER", user="reader") == [(READER_USER,)]
    assert harness.query(f"SELECT COUNT(*) FROM {harness.vs}.INFO", user="reader") == [(1,)]


def test_mock_hits_need_the_mock_fixture(harness: Harness) -> None:
    with pytest.raises(RuntimeError, match="mock"):
        harness.mock_hits()
