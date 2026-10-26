"""The model part of the integration harness (`tests/it/harness.py`, `tests/it/model_support.py`)
over fake connections: no database, no mock sidecar."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from loader import deploy as loader_deploy
from loader.plan import default_catalog, load_plan, parse_plan
from model import deploy as model_deploy
from model.deploy import MODEL_DIR, ModelReport
from tests.it import harness, loader_support, model_support, support
from tests.synthetic import league as league_module
from tests.synthetic import scaled_league
from tests.synthetic.league import League, base_league
from tests.unit.test_it_harness import FakeConnection, Session, make_harness
from vs.host.namespace import Namespace

TEST = Namespace("TEST_")
PERF = Namespace("PERF_")
FIXTURES = Path(__file__).parent / "fixtures" / "sqlobjects"


class Importer(Session):
    """A session that also records pyexasol's `import_from_iterable` calls."""

    def __init__(self, answers: Any = None) -> None:
        super().__init__(answers)
        self.imports: list[tuple[tuple[str, str], list[tuple[Any, ...]]]] = []

    def import_from_iterable(self, rows: Any, table: tuple[str, str]) -> None:
        self.imports.append((table, list(rows)))


class ModelDeployRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, str, Path]] = []
        self.fail = False

    def __call__(
        self, connection: Any, namespace: Namespace, options: model_deploy.ModelOptions
    ) -> ModelReport:
        if self.fail:
            raise RuntimeError("model deploy broke")
        self.calls.append((connection, namespace.prefix, options.directory))
        return ModelReport(None, 1, False, ())  # type: ignore[arg-type]


@pytest.fixture
def model_deploys(monkeypatch: pytest.MonkeyPatch) -> ModelDeployRecorder:
    recorder = ModelDeployRecorder()
    monkeypatch.setattr(model_deploy, "deploy_model", recorder)
    return recorder


# --- schemas the harness owns ---------------------------------------------------------------


def test_dropping_the_namespace_covers_the_model_and_the_perf_schemas() -> None:
    connection = FakeConnection()
    harness.drop_namespace(connection, TEST)
    dropped = {s for s in connection.statements if s.startswith("DROP SCHEMA")}
    expected = {
        f"DROP SCHEMA IF EXISTS {schema} CASCADE"
        for schema in (
            "TEST_CFB_MODEL",
            "PERF_CFB_INFRA",
            "PERF_CFB_META",
            "PERF_CFB_CORE",
            "PERF_CFB_STAGE",
            "PERF_CFB_RESTORE",
            "PERF_CFB_MODEL",
        )
    }
    assert expected <= dropped
    assert not any("PERF_" in s and "VIRTUAL" in s for s in connection.statements)


def test_leftovers_look_for_the_model_and_the_perf_schemas() -> None:
    connection = FakeConnection({"EXA_ALL_SCHEMAS": [("PERF_CFB_CORE",)]})
    assert harness.leftovers(connection, TEST) == ["schema PERF_CFB_CORE"]
    listing = next(s for s in connection.statements if "FROM EXA_ALL_SCHEMAS" in s)
    for schema in ("TEST_CFB_MODEL", "PERF_CFB_MODEL", "PERF_CFB_META"):
        assert f"'{schema}'" in listing


def test_the_reset_never_truncates_the_model_schema() -> None:
    assert TEST.model_schema not in loader_support.cleanup_schemas(TEST)


def test_the_harness_names_the_model_schema() -> None:
    built, _, _ = make_harness()
    assert built.model == "TEST_CFB_MODEL"


# --- deploy_model, ensure_model -------------------------------------------------------------


def test_deploy_model_deploys_the_real_files_as_the_owner(
    model_deploys: ModelDeployRecorder,
) -> None:
    built, owner, _ = make_harness()
    report = built.deploy_model()
    assert model_deploys.calls == [(owner, "TEST_", MODEL_DIR)]
    assert built.model_report is report


def test_ensure_model_deploys_once_per_unchanged_directory(
    model_deploys: ModelDeployRecorder,
) -> None:
    built, _, _ = make_harness()
    built.ensure_model()
    built.ensure_model()
    assert len(model_deploys.calls) == 1


def test_a_fixture_directory_makes_the_next_ensure_model_deploy_the_real_files_again(
    model_deploys: ModelDeployRecorder,
) -> None:
    built, _, _ = make_harness()
    built.ensure_model()
    built.deploy_model(FIXTURES / "valid")
    built.ensure_model()
    assert [call[2] for call in model_deploys.calls] == [MODEL_DIR, FIXTURES / "valid", MODEL_DIR]


def test_a_failed_deploy_keeps_the_digest_of_what_is_deployed(
    model_deploys: ModelDeployRecorder,
) -> None:
    built, _, _ = make_harness()
    built.ensure_model()
    model_deploys.fail = True
    with pytest.raises(RuntimeError):
        built.deploy_model(FIXTURES / "valid")
    model_deploys.fail = False
    built.ensure_model()
    assert len(model_deploys.calls) == 1


# --- seed_league ----------------------------------------------------------------------------


def test_seed_league_empties_the_tables_stores_the_league_and_sets_the_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded: list[tuple[Any, Namespace, League]] = []

    def record(connection: Any, namespace: Namespace, league: League) -> dict[str, int]:
        seeded.append((connection, namespace, league))
        return {}

    monkeypatch.setattr(model_support, "seed_league_data", record)
    built, owner, _ = make_harness()
    league = built.seed_league()
    assert [(c, n.prefix) for c, n, _ in seeded] == [(owner, "TEST_")]
    assert seeded[0][2] is league
    assert any("EXA_ALL_TABLES" in s for s in owner.statements)
    first_seed = len(owner.statements)
    assert "DELETE FROM TEST_CFB_META.LOADER_CONFIG WHERE SETTING_NAME = 'NOW_OVERRIDE'" in (
        owner.statements
    )
    clock = next(s for s in owner.statements if "INSERT INTO TEST_CFB_META.LOADER_CONFIG" in s)
    assert "'NOW_OVERRIDE'" in clock and "'2026-10-28 12:00:00'" in clock
    assert owner.statements.index(clock) < first_seed


def test_seed_league_applies_the_variant_to_the_base_league(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[League] = []

    def record(connection: Any, namespace: Namespace, league: League) -> dict[str, int]:
        seen.append(league)
        return {}

    monkeypatch.setattr(model_support, "seed_league_data", record)
    built, _, _ = make_harness()
    returned = built.seed_league(league_module.with_unaffiliated_fbs_team)
    assert seen == [returned]
    assert len(returned.rows("TEAMS")) > len(base_league().rows("TEAMS"))


def test_seed_league_marks_the_clock_dirty_so_the_reset_restores_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_support, "seed_league_data", lambda c, n, league: {})
    built, _, _ = make_harness()
    built.seed_league()
    assert built._clock_dirty


# --- write_league_recordings ----------------------------------------------------------------


def test_the_league_recordings_are_the_2026_weekly_slices(tmp_path: Path) -> None:
    built, _, _ = make_harness()
    built.replay_area = support.ReplayArea(bucketfs_root=tmp_path)
    built.replay_area.create()
    keys = built.write_league_recordings(wait=False)
    league = base_league()
    expected = [r.slice_key for r in league.replay_recordings()]
    assert keys == expected
    assert len(keys) == 4
    assert all(built.replay_area.host_file(key).exists() for key in keys)
    assert all("year=2026" in key for key in keys)


# --- hold_run_lock --------------------------------------------------------------------------


class LockSession(Session):
    def __init__(self) -> None:
        super().__init__(
            {
                "FROM EXA_ALL_SCRIPTS": (["C"], [(1,)]),
                "'acquire'": (["RUN_ID"], [(9,)]),
                "'release'": (["HELD"], [(1,)]),
            }
        )
        self.attr = {"autocommit": True}


def test_hold_run_lock_takes_a_host_kind_in_a_second_session_and_releases_it() -> None:
    built, owner, _ = make_harness()
    second = LockSession()

    @contextmanager
    def second_session() -> Iterator[Any]:
        yield second

    built.owner_session = second_session  # type: ignore[method-assign]
    with built.hold_run_lock() as lock:
        assert (lock.run_id, lock.kind) == (9, "RESTORE")
        assert second.seen("'acquire'")
        assert not second.seen("'release'")
    assert second.seen("'release', 'DONE', 9")
    assert not owner.statements


def test_hold_run_lock_refuses_a_script_kind() -> None:
    built, _, _ = make_harness()

    @contextmanager
    def second_session() -> Iterator[Any]:
        yield LockSession()

    built.owner_session = second_session  # type: ignore[method-assign]
    with (
        pytest.raises(Exception, match="host kinds"),
        built.hold_run_lock("BACKFILL"),
    ):
        pytest.fail("a script kind must not be held")


# --- reset_state(keep_data) -----------------------------------------------------------------


def test_the_reset_with_keep_data_leaves_tables_plan_and_clock_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    def record(*arguments: Any, **options: Any) -> str:
        calls.append(arguments)
        return "report"

    monkeypatch.setattr(loader_deploy, "deploy", record)
    built, owner, _ = make_harness()
    built.use_plan("base")
    built.set_now("2026-10-28 12:00:00")
    deployments = len(calls)
    owner.statements.clear()
    assert built.reset_state(keep_data=True) == []
    assert not [s for s in owner.statements if "TRUNCATE" in s or "EXA_ALL_TABLES" in s]
    assert not [s for s in owner.statements if "LOADER_CONFIG" in s]
    assert len(calls) == deployments


def test_the_normal_reset_still_empties_the_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader_deploy, "deploy", lambda *a, **k: "report")
    owner = Session({"FROM EXA_ALL_TABLES": (["S", "T"], [("TEST_CFB_CORE", "GAMES")])})
    built, _, _ = make_harness(owner)
    built.use_plan("base")
    assert built.reset_state() == []
    assert "TRUNCATE TABLE TEST_CFB_CORE.GAMES" in owner.statements


# --- the scaled league ----------------------------------------------------------------------


def test_seed_scaled_league_targets_the_perf_namespace_as_the_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, Namespace, bool]] = []

    def record(connection: Any, namespace: Namespace, *, force: bool, log: Any) -> dict[str, int]:
        calls.append((connection, namespace, force))
        return {"GAMES": 3}

    monkeypatch.setattr(model_support, "seed_scaled_namespace", record)
    built, owner, _ = make_harness()
    assert built.seed_scaled_league(force=True) == {"GAMES": 3}
    assert [(c, n.prefix, f) for c, n, f in calls] == [(owner, "PERF_", True)]


# --- model_support --------------------------------------------------------------------------


def test_the_seed_clock_is_the_leagues_test_clock() -> None:
    assert model_support.SEED_CLOCK == "2026-10-28 12:00:00"
    assert (
        league_module.NOW_OVERRIDE.strftime(model_support.CLOCK_FORMAT) == model_support.SEED_CLOCK
    )


def test_the_model_plan_variant_is_the_real_plan_with_the_model_steps() -> None:
    real = load_plan()
    variant = parse_plan(
        loader_support.variant_text(model_support.MODEL_VARIANT), default_catalog()
    )
    assert variant.entries == real.entries
    assert variant.config == real.config
    assert variant.snapshot == real.snapshot
    assert [(s.step_no, s.name, s.script) for s in variant.refresh_steps] == [
        (100, "MODEL", "TEST_CFB_INFRA.REFRESH_MODEL"),
        (200, "PREDICT", "TEST_CFB_INFRA.TEST_STEP_RECORD"),
    ]


def test_the_perf_plan_has_no_registered_steps_and_is_valid_in_the_perf_namespace() -> None:
    text = model_support.perf_plan_text()
    assert "refresh_steps: []" in text
    assert "REFRESH_MODEL" not in text
    loader_deploy.validate(PERF, loader_deploy.LoaderOptions(plan_text=text))


def test_the_perf_loader_deploy_skips_the_views_that_read_the_virtual_schema() -> None:
    options = loader_deploy.LoaderOptions(plan_text=model_support.perf_plan_text())
    prepared = loader_deploy._prepare(PERF, options)
    connection = Session()
    deployment = model_support._PerfDeployment(connection, PERF, options, prepared)
    deployment._create_views()
    created = [s.split('"."')[1].split('"')[0] for s in connection.statements]
    assert "QUOTA" not in created
    assert created[:2] == ["LOADER_SETTINGS", "LOAD_SLICES"]
    assert not any("PERF_CFBD_VS" in s and "INFO" in s for s in connection.statements)


def test_the_digest_of_a_directory_follows_its_sql_files_only(tmp_path: Path) -> None:
    (tmp_path / "100_a.sql").write_text("SELECT 1;\n")
    first = model_support.directory_digest(tmp_path)
    (tmp_path / "notes.txt").write_text("not sql")
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "300_x.sql").write_text("SELECT 2;\n")
    assert model_support.directory_digest(tmp_path) == first
    (tmp_path / "100_a.sql").write_text("SELECT 3;\n")
    assert model_support.directory_digest(tmp_path) != first
    assert model_support.directory_digest(FIXTURES / "valid") != first


def test_inserting_no_rows_sends_nothing() -> None:
    connection = Importer()
    assert model_support.insert_rows(connection, "S", "T", []) == 0
    assert connection.imports == []


def test_rows_are_imported_as_one_csv_import_into_the_qualified_table() -> None:
    connection = Importer()
    assert model_support.insert_rows(connection, "S", "T", iter([(1, "a"), (2, None)])) == 2
    assert connection.imports == [(("S", "T"), [(1, "a"), (2, None)])]


def test_the_league_is_stored_table_by_table_with_its_load_log_last() -> None:
    connection = Importer()
    league = base_league()
    counts = model_support.seed_league_data(connection, TEST, league)
    tables = [table for table, _ in connection.imports]
    assert tables == [
        *((TEST.core_schema, name) for name in league.tables()),
        (TEST.meta_schema, "LOAD_LOG"),
    ]
    assert counts["GAMES"] == len(league.rows("GAMES"))
    assert counts["LOAD_LOG"] == len(league.slices)
    assert all(rows for _, rows in connection.imports)


def test_the_clock_is_replaced_in_loader_config() -> None:
    connection = Session()
    model_support.write_clock(connection, PERF, "2026-10-28 12:00:00")
    assert connection.statements == [
        "DELETE FROM PERF_CFB_META.LOADER_CONFIG WHERE SETTING_NAME = 'NOW_OVERRIDE'",
        "INSERT INTO PERF_CFB_META.LOADER_CONFIG (SETTING_NAME, SETTING_VALUE) "
        "VALUES ('NOW_OVERRIDE', '2026-10-28 12:00:00')",
    ]
    model_support.write_clock(connection, PERF, None)
    assert connection.statements[-1].startswith("DELETE FROM PERF_CFB_META.LOADER_CONFIG")


def stored_answers(games: int, log: int) -> dict[str, tuple[list[str], list[tuple[Any, ...]]]]:
    return {
        "FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = 'PERF_CFB_CORE'": (["C"], [(1,)]),
        "PERF_CFB_CORE.GAMES": (["C"], [(games,)]),
        "PERF_CFB_META.LOAD_LOG": (["C"], [(log,)]),
    }


def test_the_scaled_league_counts_as_stored_only_with_every_game_and_a_log() -> None:
    total = scaled_league.total_games()
    assert model_support.scaled_league_is_stored(Session(stored_answers(total, 5)), PERF)
    assert not model_support.scaled_league_is_stored(Session(stored_answers(total - 1, 5)), PERF)
    assert not model_support.scaled_league_is_stored(Session(stored_answers(total, 0)), PERF)
    assert not model_support.scaled_league_is_stored(
        Session({"FROM EXA_ALL_TABLES": (["C"], [(0,)])}), PERF
    )


def small_league(monkeypatch: pytest.MonkeyPatch) -> None:
    def few(self: scaled_league.ScaledLeague) -> Iterator[Any]:
        yield from scaled_league.static_slices()
        yield from scaled_league.season_slices(2023)
        yield from scaled_league.season_slices(2024)

    monkeypatch.setattr(scaled_league.ScaledLeague, "iter_slices", few)


def test_seeding_an_already_stored_namespace_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_support, "deploy_perf_foundation", lambda c, n: ["foundation ok"])
    monkeypatch.setattr(model_support, "scaled_league_is_stored", lambda c, n: True)
    connection = Importer()
    messages: list[str] = []
    assert model_support.seed_scaled_namespace(connection, PERF, log=messages.append) == {}
    assert connection.imports == []
    assert not connection.seen("TRUNCATE")
    assert any("already stored" in m for m in messages)


def test_seeding_empties_the_data_tables_then_loads_in_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_support, "deploy_perf_foundation", lambda c, n: [])
    monkeypatch.setattr(model_support, "scaled_league_is_stored", lambda c, n: False)
    monkeypatch.setattr(model_support, "IMPORT_BATCH_ROWS", 200)
    small_league(monkeypatch)
    connection = Importer(
        {
            "FROM EXA_ALL_TABLES": (
                ["S", "T"],
                [
                    ("PERF_CFB_CORE", "GAMES"),
                    ("PERF_CFB_META", "LOAD_LOG"),
                    ("PERF_CFB_META", "LOAD_PLAN"),
                ],
            )
        }
    )
    totals = model_support.seed_scaled_namespace(connection, PERF, force=False)
    assert connection.seen("TRUNCATE TABLE PERF_CFB_CORE.GAMES")
    assert connection.seen("TRUNCATE TABLE PERF_CFB_META.LOAD_LOG")
    assert not connection.seen("LOAD_PLAN")[1:]
    games = [rows for table, rows in connection.imports if table == ("PERF_CFB_CORE", "GAMES")]
    assert len(games) > 1
    assert sum(len(rows) for rows in games) == totals["GAMES"]
    assert totals["GAMES"] == scaled_league.game_count(2023) + scaled_league.game_count(2024)
    log = [rows for table, rows in connection.imports if table == ("PERF_CFB_META", "LOAD_LOG")]
    assert sum(len(rows) for rows in log) == totals["LOAD_LOG"]


def test_a_forced_seed_reloads_even_a_stored_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_support, "deploy_perf_foundation", lambda c, n: [])
    monkeypatch.setattr(model_support, "scaled_league_is_stored", lambda c, n: True)
    small_league(monkeypatch)
    connection = Importer()
    totals = model_support.seed_scaled_namespace(connection, PERF, force=True)
    assert totals["GAMES"] == scaled_league.game_count(2023) + scaled_league.game_count(2024)
