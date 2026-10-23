"""The model deployer and the model side of the harness, on Exasol Personal local.

Proves what the unit tests can only fake: the transaction, the compile pass, the read-back, the
orphan drop and the run lock of `vs/host/sqlobjects.py` / `model/deploy.py`; seeding the synthetic
league by CSV import (types, NULLs, booleans, the loader's views over it); the replay recordings;
`hold_run_lock`; and the `PERF_` namespace without a virtual schema. The deploy scenarios of the
specs run on the fixture directories of `tests/it/fixtures/model_sql/` (`valid`, `later_file`,
`db_error`, `orphan`, `lint_errors`); the model objects have their own test files.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from loader.runlock import RunLockRefused
from model import deploy as model_deploy
from tests.it import model_support
from tests.it.harness import SYS, Harness, Mock
from tests.synthetic import league as league_module
from tests.synthetic import scaled_league
from tests.synthetic.league import League
from tests.synthetic.seed_sql import seed_columns, seed_rows
from vs.host.deploy import DeployError
from vs.host.namespace import Namespace

PLAN_VARIANT = "model"
pytestmark = pytest.mark.it

FIXTURES = Path(__file__).parent / "fixtures" / "model_sql"
ALLOWED_META_OBJECTS = frozenset({"LOAD_PLAN", "LOAD_PLAN_GAPS", "LOAD_SLICES"})

SEEDS = {
    "ERA": "010_era.sql",
    "CLASSIFICATION_LABEL": "011_classification_label.sql",
    "CONFERENCE_TIER": "012_conference_tier.sql",
    "CONFERENCE_CHAMPIONSHIP": "013_conference_championship.sql",
    "POLL": "014_poll.sql",
    "DATASET": "015_dataset.sql",
    "CLASSIFICATION_TRANSITION": "016_classification_transition.sql",
}
DERIVED = (
    "GAME", "SEASON_WEEK", "TEAM", "RATING_SP", "RANKING", "TEAM_MEMBERSHIP", "TEAM_SEASON",
    "AS_OF_POINT",
)  # fmt: skip

GOOD_TABLE = """\
CREATE OR REPLACE TABLE {MODEL_SCHEMA}.KEYED (
  K DECIMAL(4,0) NOT NULL COMMENT IS 'Key',
  V VARCHAR(20) UTF8 COMMENT IS 'Value'
) COMMENT IS 'One row per key.';

INSERT INTO {MODEL_SCHEMA}.KEYED VALUES (1, 'a'), (2, 'b');
"""

KEYED_AS_VIEW = """\
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.KEYED (
  K COMMENT IS 'Key',
  V COMMENT IS 'Value'
) AS
SELECT "ID", "SEASON_TYPE" FROM {CORE_SCHEMA}."GAMES"
COMMENT IS 'One row per game, under the name of a former table.';
"""


def plain(value: Any) -> Any:
    """A cell comparable across the seed parser (`int`, `Decimal`, `bool`, `str`) and the driver."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    return Decimal(value).normalize()


def write(directory: Path, files: dict[str, str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory


def model_objects(harness: Harness) -> dict[str, str]:
    rows = harness.query(
        "SELECT OBJECT_NAME, OBJECT_TYPE FROM EXA_ALL_OBJECTS "
        f"WHERE ROOT_NAME = '{harness.model}' ORDER BY OBJECT_NAME"
    )
    return {str(name): str(kind) for name, kind in rows}


def fixture_directory(name: str) -> Path:
    return FIXTURES / name


def copy_fixture(name: str, destination: Path, *, without: tuple[str, ...] = ()) -> Path:
    destination.mkdir(parents=True)
    for file in fixture_directory(name).glob("*.sql"):
        if file.name not in without:
            (destination / file.name).write_text(file.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


def counted_report_line(harness: Harness) -> str:
    """The `DETAIL` the DEPLOY run of the deployed `sql/model/` carries."""
    kinds = list(model_objects(harness).values())
    return (
        f"{harness.model}: {kinds.count('TABLE')} tables, {kinds.count('VIEW')} views, "
        f"{kinds.count('FUNCTION')} functions"
    )


def model_state(harness: Harness) -> dict[str, Any]:
    """Everything a deploy can change: objects, comments, columns with comments, view texts and
    the rows of every table of the model schema."""
    schema = harness.model
    objects = harness.query(
        "SELECT OBJECT_NAME, OBJECT_TYPE, OBJECT_COMMENT FROM EXA_ALL_OBJECTS "
        f"WHERE ROOT_NAME = '{schema}' ORDER BY OBJECT_NAME"
    )
    columns = harness.query(
        "SELECT COLUMN_TABLE, COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT, STATUS "
        "FROM EXA_ALL_COLUMNS "
        f"WHERE COLUMN_SCHEMA = '{schema}' ORDER BY COLUMN_TABLE, COLUMN_ORDINAL_POSITION"
    )
    views = harness.query(
        f"SELECT VIEW_NAME, VIEW_TEXT FROM EXA_ALL_VIEWS WHERE VIEW_SCHEMA = '{schema}' "
        "ORDER BY VIEW_NAME"
    )
    rows = {
        str(name): sorted(
            (
                tuple(plain(cell) for cell in row)
                for row in harness.query(f"SELECT * FROM {schema}.{name}")
            ),
            key=repr,
        )
        for name, kind, _ in objects
        if kind == "TABLE"
    }
    return {"objects": objects, "columns": columns, "views": views, "rows": rows}


def model_dependencies(harness: Harness) -> list[tuple[str, str, str, str]]:
    return [
        (str(a), str(b), str(c), str(d))
        for a, b, c, d in harness.query(
            "SELECT OBJECT_NAME, REFERENCED_OBJECT_SCHEMA, REFERENCED_OBJECT_NAME, "
            "REFERENCED_OBJECT_TYPE FROM EXA_ALL_DEPENDENCIES "
            f"WHERE OBJECT_SCHEMA = '{harness.model}' AND OBJECT_TYPE = 'VIEW' ORDER BY 1, 2, 3"
        )
    ]


@pytest.fixture(autouse=True)
def real_model_afterwards(harness: Harness) -> Iterator[None]:
    """Every test starts from, and leaves, the deployed `sql/model/` (fixture files are orphans)."""
    yield
    harness.ensure_model()


# --- the deployer ---------------------------------------------------------------------------


def test_the_seed_tables_are_deployed_with_their_rows_and_comments(harness: Harness) -> None:
    report = harness.deploy_model()
    objects = model_objects(harness)
    assert {name for name, kind in objects.items() if kind == "TABLE"} == {*SEEDS, *DERIVED}
    for table, file in SEEDS.items():
        count = harness.query(f"SELECT COUNT(*) FROM {harness.model}.{table}")[0][0]
        assert int(count) == len(seed_rows(file)), table
    assert report.objects.comments_found == report.objects.comments_expected
    assert report.objects.comment_percent == 100
    assert "15 tables" in "\n".join(report.lines)
    assert f"derived tables filled: {', '.join(DERIVED)}" in report.lines
    assert harness.query(
        f"SELECT OBJECT_COMMENT FROM EXA_ALL_OBJECTS WHERE ROOT_NAME = '{harness.model}' "
        "AND OBJECT_NAME = 'POLL'"
    )[0][0].startswith("One row per CFBD poll name")


def test_reference_values_survive_the_engine_with_their_types(harness: Harness) -> None:
    harness.ensure_model()
    stored = harness.query(f"SELECT * FROM {harness.model}.ERA ORDER BY ERA_KIND, ERA_CODE")
    assert [tuple(plain(cell) for cell in row) for row in stored] == sorted(
        tuple(plain(cell) for cell in row) for row in seed_rows("010_era.sql")
    )
    assert seed_columns("010_era.sql")[:2] == ["ERA_KIND", "ERA_CODE"]


def test_a_second_deploy_keeps_the_same_rows_without_duplicates(harness: Harness) -> None:
    harness.deploy_model()
    first = {t: harness.query(f"SELECT COUNT(*) FROM {harness.model}.{t}")[0][0] for t in SEEDS}
    objects = model_objects(harness)
    report = harness.deploy_model()
    second = {t: harness.query(f"SELECT COUNT(*) FROM {harness.model}.{t}")[0][0] for t in SEEDS}
    assert first == second
    assert model_objects(harness) == objects
    assert report.objects.dropped == ()


def test_a_valid_directory_deploys_and_a_second_deploy_changes_nothing(
    harness: Harness,
) -> None:
    folder = fixture_directory("valid")
    harness.deploy_model(folder)
    first = model_state(harness)
    assert harness.query(f"SELECT K, DOUBLE_K FROM {harness.model}.KEYED_VIEW ORDER BY K") == [
        (1, 2),
        (2, 4),
        (3, 6),
    ]
    assert {name: kind for name, kind, _ in first["objects"]} == {
        "KEYED": "TABLE",
        "DOUBLE_IT": "FUNCTION",
        "KEYED_VIEW": "VIEW",
    }
    assert {status for *_, status in first["columns"]} == {None}
    assert [(c[1], c[3]) for c in first["columns"] if c[0] == "KEYED_VIEW"] == [
        ("K", "Key of the row"),
        ("DOUBLE_K", "Twice the key"),
    ]
    harness.deploy_model(folder)
    assert model_state(harness) == first
    assert first["rows"] == {"KEYED": [(1, "a"), (2, "b"), (3, None)]}


def test_a_view_in_an_earlier_file_may_read_a_later_one(harness: Harness, tmp_path: Path) -> None:
    harness.deploy_model(fixture_directory("later_file"))
    assert harness.query(f"SELECT K, V FROM {harness.model}.VIEW_A ORDER BY K") == [
        (1, "A"),
        (2, "B"),
    ]
    assert harness.query(f"SELECT COUNT(*) FROM {harness.model}.VIEW_B") == [(2,)]
    comments = harness.query(
        "SELECT COLUMN_TABLE, COLUMN_NAME, COLUMN_COMMENT FROM EXA_ALL_COLUMNS "
        f"WHERE COLUMN_SCHEMA = '{harness.model}' AND COLUMN_TABLE LIKE 'VIEW_%' "
        "ORDER BY COLUMN_TABLE, COLUMN_ORDINAL_POSITION"
    )
    assert comments == [
        ("VIEW_A", "K", "Key of the row"),
        ("VIEW_A", "V", "Value of the row, upper case"),
        ("VIEW_B", "K", "Key of the row"),
        ("VIEW_B", "V", "Value of the row"),
    ]
    assert harness.query(
        "SELECT REFERENCED_OBJECT_NAME FROM EXA_ALL_DEPENDENCIES "
        f"WHERE OBJECT_SCHEMA = '{harness.model}' AND OBJECT_NAME = 'VIEW_A'"
    ) == [("VIEW_B",)]


def test_a_view_that_only_fails_at_its_first_query_fails_the_deploy_and_changes_nothing(
    harness: Harness,
) -> None:
    harness.deploy_model()
    before = model_state(harness)
    with pytest.raises(
        DeployError, match=r"compile 300_third_view\.sql: VIEW THIRD_VIEW: .*NO_SUCH_COLUMN"
    ):
        harness.deploy_model(fixture_directory("db_error"))
    assert model_state(harness) == before
    assert not {"FIRST_VIEW", "SECOND_VIEW", "THIRD_VIEW"} & set(model_objects(harness))
    assert harness.runs()[-1]["RUN_STATUS"] == "FAILED"


def test_objects_that_no_file_defines_are_dropped(harness: Harness, tmp_path: Path) -> None:
    harness.deploy_model(fixture_directory("orphan"))
    assert {"KEPT", "OLD_VIEW"} <= set(model_objects(harness))
    smaller = copy_fixture("orphan", tmp_path / "m", without=("200_old_view.sql",))
    report = harness.deploy_model(smaller)
    assert "VIEW OLD_VIEW" in report.objects.dropped
    assert "OLD_VIEW" not in model_objects(harness)
    assert "dropped: VIEW OLD_VIEW" in "\n".join(report.lines)
    assert harness.query(f"SELECT COUNT(*) FROM {harness.model}.KEPT") == [(1,)]
    harness.deploy_model()
    assert "KEPT" not in model_objects(harness)


def test_an_object_that_changes_its_kind_is_replaced(harness: Harness, tmp_path: Path) -> None:
    as_table = write(tmp_path / "t", {"100_keyed.sql": GOOD_TABLE})
    as_view = write(tmp_path / "v", {"100_keyed.sql": KEYED_AS_VIEW})
    harness.deploy_model(as_table)
    report = harness.deploy_model(as_view)
    assert report.objects.retyped == ("KEYED (was TABLE)",)
    assert model_objects(harness)["KEYED"] == "VIEW"


def test_lint_problems_are_listed_one_per_rule_and_nothing_is_sent(harness: Harness) -> None:
    harness.deploy_model(fixture_directory("valid"))
    before = model_state(harness)
    runs_before = harness.runs()
    with pytest.raises(model_deploy.ModelLintError, match="nothing was sent") as caught:
        harness.deploy_model(fixture_directory("lint_errors"))
    problems = caught.value.problems
    assert [problem.split(": ")[:3] for problem in problems] == [
        ["100_game_a.sql", "GAME_A", "comment"],
        ["110_b.sql", "B", "grain"],
        ["120_c.sql", "C", "reserved-word"],
        ["130_game_list.sql", "game_list", "ddl-name"],
        ["140_d.sql", "D", "reference"],
    ]
    assert model_state(harness) == before
    assert harness.runs() == runs_before
    assert not {"GAME_A", "B", "C", "D"} & set(model_objects(harness))


def test_a_deploy_is_refused_while_a_loader_run_holds_the_lock(harness: Harness) -> None:
    harness.deploy_model()
    before = model_objects(harness)
    with harness.hold_run_lock("RESTORE") as lock:
        with pytest.raises(RunLockRefused, match=f"RESTORE run {lock.run_id} is running"):
            harness.deploy_model()
        assert [(r["RUN_KIND"], r["RUN_STATUS"]) for r in harness.runs()] == [
            ("DEPLOY", "DONE"),
            ("RESTORE", "RUNNING"),
        ]
    assert model_objects(harness) == before
    harness.deploy_model()
    runs = harness.runs()
    assert [(r["RUN_KIND"], r["RUN_STATUS"]) for r in runs] == [
        ("DEPLOY", "DONE"),
        ("RESTORE", "DONE"),
        ("DEPLOY", "DONE"),
    ]
    assert runs[-1]["RUN_ARGS"] == "make model"
    assert runs[-1]["DETAIL"].startswith(counted_report_line(harness))


def test_the_deploy_run_is_logged_failed_when_the_deploy_fails(
    harness: Harness, tmp_path: Path
) -> None:
    folder = write(
        tmp_path / "m", {"100_keyed.sql": GOOD_TABLE.replace("VARCHAR(20)", "NOT_A_TYPE(20)")}
    )
    with pytest.raises(DeployError, match=r"100_keyed\.sql"):
        harness.deploy_model(folder)
    last = harness.runs()[-1]
    assert (last["RUN_KIND"], last["RUN_STATUS"]) == ("DEPLOY", "FAILED")
    assert "100_keyed.sql" in last["DETAIL"]


def test_the_model_never_reads_the_virtual_schema(harness: Harness, mock: Mock) -> None:
    harness.ensure_model()
    harness.seed_league()
    harness.set_property("FETCH_MODE", "live")
    fetches = harness.query(f"SELECT COUNT(*) FROM {harness.meta}.FETCH_LOG")
    views = [
        str(name)
        for (name,) in harness.query(
            f"SELECT VIEW_NAME FROM EXA_ALL_VIEWS WHERE VIEW_SCHEMA = '{harness.model}' "
            "ORDER BY VIEW_NAME"
        )
    ]
    assert views
    for view in views:
        harness.query(f"SELECT * FROM {harness.model}.{view}")
    assert harness.mock_hits() == 0
    assert harness.query(f"SELECT COUNT(*) FROM {harness.meta}.FETCH_LOG") == fetches
    allowed_schemas = {harness.model, harness.core, harness.meta}
    for view, schema, name, _kind in model_dependencies(harness):
        assert schema in allowed_schemas, (view, schema, name)
        if schema == harness.meta:
            assert name in ALLOWED_META_OBJECTS, (view, name)
    reached = harness.query(
        "SELECT DISTINCT REFERENCED_OBJECT_SCHEMA FROM EXA_DBA_DEPENDENCIES_RECURSIVE "
        f"WHERE OBJECT_SCHEMA = '{harness.model}' AND OBJECT_TYPE = 'VIEW'",
        user=SYS,
    )
    assert harness.vs not in {str(schema) for (schema,) in reached}


# --- the league -----------------------------------------------------------------------------


def stored_counts(harness: Harness, league: League) -> dict[str, int]:
    return {
        table: int(harness.query(f"SELECT COUNT(*) FROM {harness.core}.{table}")[0][0])
        for table in league.tables()
    }


def test_the_league_is_stored_with_every_row_and_slice_registered(harness: Harness) -> None:
    league = harness.seed_league()
    assert stored_counts(harness, league) == {t: len(league.rows(t)) for t in league.tables()}
    assert harness.query(f"SELECT COUNT(*) FROM {harness.meta}.LOAD_LOG") == [(len(league.slices),)]
    stored = [s for s in harness.load_slices() if s["IS_STORED"]]
    assert len(stored) == len(league.slices)
    assert harness.query(f"SELECT COUNT(*) FROM {harness.meta}.LOAD_ORPHANS") == [(0,)]


def test_seeding_again_replaces_the_data_instead_of_doubling_it(harness: Harness) -> None:
    league = harness.seed_league()
    again = harness.seed_league(league_module.with_unaffiliated_fbs_team)
    assert stored_counts(harness, again) == {t: len(again.rows(t)) for t in again.tables()}
    assert len(again.rows("TEAMS")) == len(league.rows("TEAMS")) + 1


def test_the_clock_is_the_leagues_and_the_reset_returns_the_modules_clock(harness: Harness) -> None:
    harness.seed_league()
    now = harness.query(
        "SELECT TO_CHAR(NOW_UTC, 'YYYY-MM-DD HH24:MI:SS'), CURRENT_SEASON "
        f"FROM {harness.meta}.LOADER_SETTINGS"
    )
    assert now == [("2026-10-28 12:00:00", 2026)]
    assert harness.reset_state() == []
    assert harness.query(f"SELECT COUNT(*) FROM {harness.core}.GAMES") == [(0,)]
    assert harness.query(f"SELECT COUNT(*) FROM {harness.meta}.LOAD_LOG") == [(0,)]
    after = harness.query(
        f"SELECT TO_CHAR(NOW_UTC, 'YYYY-MM-DD') FROM {harness.meta}.LOADER_SETTINGS"
    )
    assert after != [("2026-10-28",)]


def test_values_nulls_booleans_and_timestamps_come_back_as_the_league_made_them(
    harness: Harness,
) -> None:
    league = harness.seed_league()
    columns = [
        c.name
        for c in league.get("GAMES", league_module.season_key("GAMES", 2024)).table_def().columns
    ]
    expected = {
        row[columns.index("ID")]: dict(zip(columns, row, strict=True))
        for row in league.rows("GAMES")
    }
    game_id = league.game_id("ashford_brookvale_2024")
    stored = harness.query_dicts(f"SELECT * FROM {harness.core}.GAMES WHERE ID = {game_id}")[0]
    wanted = expected[game_id]
    for name in (
        "SEASON",
        "WEEK",
        "SEASON_TYPE",
        "HOME_TEAM",
        "AWAY_TEAM",
        "HOME_POINTS",
        "AWAY_POINTS",
    ):
        assert str(stored[name]) == str(wanted[name]), name
    assert datetime.fromisoformat(str(stored["START_DATE"])) == wanted["START_DATE"]
    assert datetime.fromisoformat(str(stored["REQ_AT"])) == wanted["REQ_AT"]
    nulls = sum(1 for row in expected.values() if row["HOME_PREGAME_ELO"] is None)
    assert nulls > 0
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.core}.GAMES WHERE HOME_PREGAME_ELO IS NULL"
    ) == [(nulls,)]
    log = {
        (r[0], r[1]): r[2]
        for r in harness.query(
            f"SELECT TABLE_NAME, SLICE_KEY, IS_FINAL FROM {harness.meta}.LOAD_LOG"
        )
    }
    assert log[("GAMES", league_module.season_key("GAMES", 2025))] is True
    assert log[("GAMES", league_module.season_key("GAMES", 2026))] is False


def test_the_league_recordings_are_the_2026_weekly_slices_a_udf_can_read(harness: Harness) -> None:
    keys = harness.write_league_recordings()
    assert len(keys) == 4
    for key in keys:
        assert harness.replay_area.host_file(key).exists()
    harness.wait_for_files([harness.replay_area.udf_file(key) for key in keys])


# --- the performance namespace --------------------------------------------------------------


@pytest.fixture
def two_seasons(monkeypatch: pytest.MonkeyPatch) -> None:
    def few(self: scaled_league.ScaledLeague) -> Iterator[Any]:
        yield from scaled_league.static_slices()
        yield from scaled_league.season_slices(2023)
        yield from scaled_league.season_slices(2024)

    monkeypatch.setattr(scaled_league.ScaledLeague, "iter_slices", few)


def test_the_perf_namespace_is_built_without_a_virtual_schema_and_takes_the_model(
    harness: Harness, two_seasons: None
) -> None:
    perf = Namespace(model_support.PERF_PREFIX)
    totals = harness.seed_scaled_league(force=True)
    expected = scaled_league.game_count(2023) + scaled_league.game_count(2024)
    assert totals["GAMES"] == expected
    assert harness.query(f"SELECT COUNT(*) FROM {perf.core_schema}.GAMES") == [(expected,)]
    assert harness.query(
        f"SELECT COUNT(*) FROM EXA_ALL_VIRTUAL_SCHEMAS WHERE SCHEMA_NAME = '{perf.vs_name}'"
    ) == [(0,)]
    clock = harness.query(
        f"SELECT TO_CHAR(NOW_UTC, 'YYYY-MM-DD HH24:MI:SS') FROM {perf.meta_schema}.LOADER_SETTINGS"
    )
    assert clock == [("2026-10-28 12:00:00",)]
    report = model_deploy.deploy_model(harness.owner, perf)
    assert report.objects.schema == "PERF_CFB_MODEL"
    assert harness.query("SELECT COUNT(*) FROM PERF_CFB_MODEL.ERA")[0][0] == len(
        seed_rows(SEEDS["ERA"])
    )
