"""Pure parts of the integration harness (`tests/it/guards.py`, `tests/it/support.py`)."""

import json
from datetime import UTC
from pathlib import Path

import pytest

from tests.it import guards, support
from tests.synthetic import fake
from vs.cfbd_vs import replay
from vs.host.namespace import MOCK_CONNECTION, REAL_CONNECTION

REAL = REAL_CONNECTION


def write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8")


def test_a_real_connection_reference_in_a_test_file_is_reported(tmp_path: Path) -> None:
    write(tmp_path, "test_a.py", f'NAME = "{REAL}"\n')
    problems = guards.forbidden_references(tmp_path)
    assert len(problems) == 1
    assert "test_a.py:1" in problems[0]


def test_the_mock_connection_is_allowed(tmp_path: Path) -> None:
    write(tmp_path, "test_a.py", f'NAME = "{MOCK_CONNECTION}"\nOTHER = "{MOCK_CONNECTION}_2"\n')
    assert guards.forbidden_references(tmp_path) == []


def test_every_occurrence_on_a_line_is_checked(tmp_path: Path) -> None:
    write(tmp_path, "test_a.py", f"X = '{MOCK_CONNECTION} {REAL}'\n")
    assert len(guards.forbidden_references(tmp_path)) == 1


def test_non_python_files_and_subdirectories_are_scanned(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    write(tmp_path / "data", "fixture.sql", f"SELECT '{REAL}';\n")
    assert len(guards.forbidden_references(tmp_path)) == 1


def test_the_guard_module_itself_and_caches_are_skipped(tmp_path: Path) -> None:
    write(tmp_path, "guards.py", f"{REAL}\n")
    (tmp_path / "__pycache__").mkdir()
    write(tmp_path / "__pycache__", "x.pyc", REAL)
    assert guards.forbidden_references(tmp_path) == []


def test_the_real_tests_it_directory_is_clean() -> None:
    assert guards.forbidden_references(Path(guards.__file__).parent) == []


def property_rows(**by_schema: dict[str, str]) -> list[tuple[str, str, str]]:
    return [
        (schema, name, value)
        for schema, props in by_schema.items()
        for name, value in props.items()
    ]


def test_a_test_schema_on_the_real_connection_is_flagged() -> None:
    rows = property_rows(TEST_CFBD_VS={"CONNECTION_NAME": REAL})
    assert guards.schemas_on_real_connection(rows, "TEST_") == ["TEST_CFBD_VS"]


def test_a_test_schema_without_a_connection_property_uses_the_default_and_is_flagged() -> None:
    rows = property_rows(TEST_CFBD_VS={"INFRA_SCHEMA": "TEST_CFB_INFRA"})
    assert guards.schemas_on_real_connection(rows, "TEST_") == ["TEST_CFBD_VS"]


def test_a_test_schema_on_the_mock_is_fine() -> None:
    rows = property_rows(TEST_CFBD_VS={"CONNECTION_NAME": MOCK_CONNECTION})
    assert guards.schemas_on_real_connection(rows, "TEST_") == []


def test_the_real_schema_is_not_a_test_schema() -> None:
    rows = property_rows(CFBD_VS={"CONNECTION_NAME": REAL})
    assert guards.schemas_on_real_connection(rows, "TEST_") == []


def test_a_differently_named_schema_pointing_at_test_infra_is_flagged() -> None:
    rows = property_rows(
        OTHER_VS={"CONNECTION_NAME": REAL, "INFRA_SCHEMA": "TEST_CFB_INFRA"},
        UNRELATED={"CONNECTION_NAME": REAL},
    )
    assert guards.schemas_on_real_connection(rows, "TEST_") == ["OTHER_VS"]


def test_a_test_schema_with_no_property_row_at_all_is_flagged() -> None:
    assert guards.schemas_on_real_connection([], "TEST_", ["TEST_CFBD_VS"]) == ["TEST_CFBD_VS"]


def test_a_schema_with_a_mock_row_is_not_flagged_when_listed_by_name() -> None:
    rows = property_rows(TEST_CFBD_VS={"CONNECTION_NAME": MOCK_CONNECTION})
    assert guards.schemas_on_real_connection(rows, "TEST_", ["TEST_CFBD_VS"]) == []


def test_listed_schemas_outside_the_test_prefix_are_ignored() -> None:
    assert guards.schemas_on_real_connection([], "TEST_", ["CFBD_VS", "TESTX_VS"]) == []


@pytest.mark.parametrize("value", ["", None])
def test_an_empty_connection_name_falls_back_to_the_real_one_and_is_flagged(
    value: str | None,
) -> None:
    rows = [("TEST_CFBD_VS", "CONNECTION_NAME", value)]
    assert guards.schemas_on_real_connection(rows, "TEST_") == ["TEST_CFBD_VS"]


def test_a_lower_case_real_connection_value_is_flagged() -> None:
    rows = property_rows(TEST_CFBD_VS={"CONNECTION_NAME": REAL.lower()})
    assert guards.schemas_on_real_connection(rows, "TEST_") == ["TEST_CFBD_VS"]


def test_the_underscore_in_the_prefix_is_not_a_wildcard() -> None:
    rows = property_rows(TESTX_VS={"CONNECTION_NAME": REAL})
    assert guards.schemas_on_real_connection(rows, "TEST_") == []


def test_setting_the_real_connection_is_refused() -> None:
    with pytest.raises(guards.GuardError):
        guards.check_property_change("CONNECTION_NAME", REAL)
    with pytest.raises(guards.GuardError):
        guards.check_property_change("connection_name", REAL.lower())


@pytest.mark.parametrize("value", ["", "   "])
def test_unsetting_the_connection_is_refused_because_it_falls_back_to_the_real_one(
    value: str,
) -> None:
    with pytest.raises(guards.GuardError, match="falls back"):
        guards.check_property_change("CONNECTION_NAME", value)


def test_an_empty_value_for_another_property_passes_the_guard() -> None:
    guards.check_property_change("FETCH_MODE", "")


def test_other_property_changes_pass_the_guard() -> None:
    guards.check_property_change("CONNECTION_NAME", MOCK_CONNECTION)
    guards.check_property_change("FETCH_MODE", "replay")


def test_passwords_and_keys_are_random_and_long() -> None:
    first, second = support.new_secret(), support.new_secret()
    assert first != second
    assert len(first) >= 24


def test_the_marker_key_is_recognisable() -> None:
    assert support.new_marker_key().startswith(support.MARKER_PREFIX)


def test_where_clause_uses_the_echo_columns_of_the_table() -> None:
    table = "CALENDAR"
    filters = fake.minimal_filter(table)
    clause = support.where_clause(table, filters)
    assert clause == "P_YEAR = 2024"


def test_where_clause_quotes_strings_and_joins_conjuncts() -> None:
    clause = support.where_clause("TEAMS_MATCHUP", {"team1": "O'Brien", "team2": "Ohio State"})
    assert clause == "P_TEAM1 = 'O''Brien' AND P_TEAM2 = 'Ohio State'"


def test_where_clause_rejects_an_unknown_parameter() -> None:
    with pytest.raises(support.HarnessError, match="nope"):
        support.where_clause("CALENDAR", {"nope": 1})


def test_where_clause_writes_booleans_as_sql_literals() -> None:
    table = next(
        t
        for t in fake.catalog().tables
        if any(p.type == "boolean" for p in t.params) and t.read_through
    )
    param = next(p for p in table.params if p.type == "boolean")
    assert support.where_clause(table.name, {param.name: True}) == f"{param.column} = TRUE"


def test_slice_key_splits_into_path_and_decoded_query() -> None:
    assert support.split_slice_key("/games?seasonType=both&team=Ohio%20State") == (
        "/games",
        {"seasonType": "both", "team": "Ohio State"},
    )
    assert support.split_slice_key("/teams") == ("/teams", {})


def test_synthetic_recording_matches_the_provider_response() -> None:
    key = fake.slice_key_for("CALENDAR")
    recording = support.synthetic_recording(key)
    path, query = support.split_slice_key(key)
    assert recording.slice_key == key
    assert recording.status == 200
    assert recording.body.encode("utf-8") == fake.response_for(path, query)
    assert recording.requested_at.tzinfo is UTC


def test_recording_from_a_body_object_is_compact_json() -> None:
    recording = support.recording_for("/x?a=1", {"b": [1, 2]}, status=404)
    assert json.loads(recording.body) == {"b": [1, 2]}
    assert recording.status == 404


def test_udf_and_host_paths_map_onto_each_other(tmp_path: Path) -> None:
    area = support.ReplayArea(
        service="svc", bucket="bkt", directory="cfbd/test-replay", bucketfs_root=tmp_path
    )
    assert area.udf_path == "/buckets/svc/bkt/cfbd/test-replay"
    assert area.host_dir == tmp_path / "svc" / "bkt" / "cfbd" / "test-replay"
    key = "/games?year=2024"
    relative = replay.relative_path(key)
    assert area.udf_file(key) == f"/buckets/svc/bkt/cfbd/test-replay/{relative}"
    assert area.host_file(key) == area.host_dir / relative


def test_replay_area_creates_writes_and_cleans_up_only_what_it_made(tmp_path: Path) -> None:
    (tmp_path / "svc").mkdir()
    area = support.ReplayArea(
        service="svc", bucket="bkt", directory="cfbd/test-replay", bucketfs_root=tmp_path
    )
    area.create()
    key = "/games?year=2024"
    area.write(support.recording_for(key, {"ok": True}))
    assert area.host_file(key).exists()
    assert area.clear() == [area.udf_file(key)]
    assert not area.host_file(key).exists()
    assert area.host_dir.exists()
    area.remove()
    assert (tmp_path / "svc").exists()
    assert not (tmp_path / "svc" / "bkt").exists()


def test_replay_area_keeps_directories_that_were_there_before(tmp_path: Path) -> None:
    (tmp_path / "svc" / "bkt" / "cfbd").mkdir(parents=True)
    (tmp_path / "svc" / "bkt" / "cfbd" / "other.txt").write_text("keep", encoding="utf-8")
    area = support.ReplayArea(
        service="svc", bucket="bkt", directory="cfbd/test-replay", bucketfs_root=tmp_path
    )
    area.create()
    area.remove()
    assert (tmp_path / "svc" / "bkt" / "cfbd" / "other.txt").exists()
    assert not area.host_dir.exists()


def test_wait_script_polls_until_every_path_is_in_the_wanted_state() -> None:
    sql = support.wait_script_sql("TEST_CFB_HARNESS")
    assert sql.startswith("CREATE OR REPLACE PYTHON3 SCALAR SCRIPT TEST_CFB_HARNESS.WAIT_FOR_FILES")
    assert "os.path.exists" in sql
    assert "time.sleep" in sql
