"""Pure parts of the loader side of the integration harness: variants, SQL text, cleanup scope."""

from __future__ import annotations

from pathlib import Path

import pytest

from loader import deploy as loader_deploy
from tests.it import loader_support as ls
from tests.it.support import HarnessError
from vs.host.namespace import Namespace

TEST = Namespace("TEST_")


def test_every_variant_file_is_listed_and_the_base_is_one_of_them() -> None:
    names = ls.variant_names()
    assert ls.BASE_VARIANT in names
    assert names == sorted(
        path.name.removeprefix("load-plan.").removesuffix(".yaml")
        for path in ls.VARIANT_DIR.glob("load-plan.*.yaml")
    )


@pytest.mark.parametrize("name", ls.variant_names())
def test_every_variant_is_a_valid_plan_in_the_test_namespace(name: str) -> None:
    loader_deploy.validate(TEST, loader_deploy.LoaderOptions(plan_text=ls.variant_text(name)))


def test_an_unknown_variant_names_the_known_ones() -> None:
    with pytest.raises(HarnessError, match=r"no-such.*base"):
        ls.variant_text("no-such")


@pytest.mark.parametrize("name", ["../base", "base.yaml", "Base", ""])
def test_a_variant_name_is_a_plain_word(name: str) -> None:
    with pytest.raises(HarnessError):
        ls.variant_text(name)


def test_the_refresh_variants_register_the_step_scripts_the_harness_creates() -> None:
    plan_text = ls.variant_text("refresh-steps")
    assert "TEST_CFB_INFRA.TEST_STEP_RECORD" in plan_text
    assert "TEST_CFB_INFRA.TEST_STEP_FAIL" in ls.variant_text("refresh-step-fail")
    assert "TEST_CFB_INFRA.TEST_STEP_SLEEP" in ls.variant_text("refresh-step-sleep")


def test_a_plan_state_compares_by_variant_and_config() -> None:
    first = ls.PlanState("base", ls.freeze({"a": "1"}))
    assert first == ls.PlanState("base", ls.freeze({"a": "1"}))
    assert first != ls.PlanState("base", ls.freeze({"a": "2"}))
    assert first != ls.PlanState("gaps", ls.freeze({"a": "1"}))
    assert ls.PlanState("base", ls.freeze(None)) == ls.PlanState("base", ls.freeze({}))


def test_the_clock_override_is_an_ordinary_loader_config_entry() -> None:
    state = ls.PlanState("base", ls.freeze({"now_override": "2020-10-20"}))
    assert state.config_overrides() == {"now_override": "2020-10-20"}
    assert state.now == "2020-10-20"
    assert ls.PlanState("base", ()).now is None


def test_a_script_call_renders_every_argument_as_a_literal() -> None:
    sql = ls.script_call_sql("TEST_CFB_INFRA.BACKFILL", "demo", None, 3)
    assert sql == "EXECUTE SCRIPT TEST_CFB_INFRA.BACKFILL('demo', NULL, 3)"


def test_a_script_call_escapes_quotes_and_never_adds_with_output() -> None:
    sql = ls.script_call_sql("TEST_CFB_INFRA.REFRESH_WEEK", 2020, 7, "re'gular")
    assert sql == "EXECUTE SCRIPT TEST_CFB_INFRA.REFRESH_WEEK(2020, 7, 're''gular')"
    assert "WITH OUTPUT" not in sql


def test_a_script_name_must_be_a_qualified_identifier() -> None:
    with pytest.raises(HarnessError):
        ls.script_call_sql("X; DROP USER SYS", 1)


def test_the_kill_statement_needs_a_numeric_session_id() -> None:
    assert ls.kill_statement_sql(123456789012345) == "KILL STATEMENT IN SESSION 123456789012345"
    with pytest.raises(HarnessError):
        ls.kill_statement_sql("1; DROP USER SYS")  # type: ignore[arg-type]


def test_the_harness_clock_writes_utc_like_the_loader_views() -> None:
    assert ls.UTC_NOW == "CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC')"


def test_cleanup_keeps_the_plan_tables_and_truncates_every_other_table() -> None:
    rows = [
        ("TEST_CFB_CORE", "GAMES"),
        ("TEST_CFB_META", "LOAD_LOG"),
        ("TEST_CFB_META", "LOAD_RUN"),
        ("TEST_CFB_META", "LOAD_PLAN"),
        ("TEST_CFB_META", "LOAD_PLAN_GAPS"),
        ("TEST_CFB_META", "LOADER_CONFIG"),
        ("TEST_CFB_META", "REFRESH_STEPS"),
        ("TEST_CFB_META", "SEASON_NUMBERS"),
        ("TEST_CFB_STAGE", "GAMES"),
        ("TEST_CFB_INFRA", "TEST_STEP_CALLS"),
    ]
    assert ls.tables_to_truncate(rows, TEST) == [
        ("TEST_CFB_CORE", "GAMES"),
        ("TEST_CFB_META", "LOAD_LOG"),
        ("TEST_CFB_META", "LOAD_RUN"),
        ("TEST_CFB_STAGE", "GAMES"),
        ("TEST_CFB_INFRA", "TEST_STEP_CALLS"),
    ]


def test_a_plan_table_name_in_another_schema_is_not_kept() -> None:
    rows = [("TEST_CFB_CORE", "LOAD_PLAN")]
    assert ls.tables_to_truncate(rows, TEST) == rows


def test_cleanup_covers_core_meta_stage_and_infra_schemas() -> None:
    assert ls.cleanup_schemas(TEST) == [
        "TEST_CFB_CORE",
        "TEST_CFB_META",
        "TEST_CFB_STAGE",
        "TEST_CFB_INFRA",
    ]


def test_a_private_directory_is_created_with_owner_only_access(tmp_path: Path) -> None:
    created = ls.private_directory(tmp_path / "snapshots")
    assert created.is_dir()
    assert created.stat().st_mode & 0o777 == 0o700


def test_a_private_directory_tightens_an_existing_one(tmp_path: Path) -> None:
    existing = tmp_path / "open"
    existing.mkdir(mode=0o755)
    existing.chmod(0o755)
    assert ls.private_directory(existing).stat().st_mode & 0o777 == 0o700


def test_the_kept_plan_tables_are_the_five_configuration_tables() -> None:
    assert set(ls.PLAN_TABLES) == {
        "LOAD_PLAN",
        "LOAD_PLAN_GAPS",
        "LOADER_CONFIG",
        "REFRESH_STEPS",
        "SEASON_NUMBERS",
    }
