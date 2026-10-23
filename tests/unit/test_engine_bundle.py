"""`engine/bundle.py`: the `SIM_ENGINE` script text, its `EMITS` list against the stage table and
its version (sim-engine/deploy, sim-engine/engine-contract)."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from engine import bundle, core
from vs.bundle import BundleError
from vs.host import sqlobjects
from vs.host.namespace import Namespace

STAGE_FILE = Path(bundle.ROOT) / "sql" / "sim" / "data" / "sim_stage.sql"
REAL = Namespace("")
FAKE_CORE = "VALUE = 41\n"
FAKE_UDF = (
    "from engine import core\n"
    "\n"
    "\n"
    "def run(ctx, exa):\n"
    "    ctx.emitted.append((core.VALUE + 1, exa.tag))\n"
)


@pytest.fixture
def engine_dir(tmp_path: Path) -> Path:
    (tmp_path / "core.py").write_text(FAKE_CORE, encoding="utf-8")
    (tmp_path / "udf.py").write_text(FAKE_UDF, encoding="utf-8")
    return tmp_path


@pytest.fixture
def isolated_modules() -> Iterator[None]:
    saved = {n: m for n, m in sys.modules.items() if n == "engine" or n.startswith("engine.")}
    try:
        yield
    finally:
        for name in [n for n in sys.modules if n == "engine" or n.startswith("engine.")]:
            del sys.modules[name]
        sys.modules.update(saved)


def stage_ddl_columns() -> list[tuple[str, str]]:
    text = STAGE_FILE.read_text(encoding="utf-8")
    pairs = re.findall(
        r"^\s{2}([A-Z_]+)\s+(.+?)(?: NOT NULL)? COMMENT IS ", text, flags=re.MULTILINE
    )
    return [(name, " ".join(sql_type.split())) for name, sql_type in pairs]


def test_the_emits_list_is_the_stage_table_in_order() -> None:
    (stage,) = [o for o in sqlobjects.load(STAGE_FILE.parent) if o.name == "SIM_STAGE"]
    assert [name for name, _ in bundle.STAGE_COLUMNS] == [c.name for c in stage.columns]
    assert list(bundle.STAGE_COLUMNS) == stage_ddl_columns()


def test_the_header_declares_the_input_and_the_emits_columns(engine_dir: Path) -> None:
    header = bundle.script_header("CFB_INFRA")
    assert header.startswith('CREATE OR REPLACE PYTHON3 SET SCRIPT "CFB_INFRA"."SIM_ENGINE"(RUN_ID')
    assert header.endswith(" AS\n")
    emits = header.split(" EMITS (", 1)[1].rsplit(") AS\n", 1)[0]
    assert emits == ", ".join(f"{name} {sql_type}" for name, sql_type in bundle.STAGE_COLUMNS)


def test_the_input_and_stage_columns_are_the_engines_own() -> None:
    assert bundle.MODEL_INPUT_COLUMNS == core.RUN_COLUMNS + core.MODEL_COLUMNS
    assert bundle.STAGE_COLUMNS == core.STAGE_COLUMNS


def test_the_run_columns_come_first_and_every_input_name_is_unique() -> None:
    names = [name for name, _ in bundle.MODEL_INPUT_COLUMNS]
    assert names[:4] == ["RUN_ID", "RUN_SEED", "SIMS_PER_BATCH", "BATCH_ID"]
    assert len(set(names)) == len(names)
    assert {"ROW_KIND", "GAME_ROLE", "PARAM_KEY", "PARAM_VALUE", "TEAM_SIGMA"} <= set(names)


def test_the_statement_ends_with_the_terminator_and_the_comment_names_the_version(
    engine_dir: Path,
) -> None:
    script = bundle.build(REAL, engine_dir)
    assert script.create.endswith("\n/")
    assert script.create.startswith(bundle.script_header("CFB_INFRA"))
    assert bundle.script_body(engine_dir) == script.body
    assert script.comment.startswith('COMMENT ON SCRIPT "CFB_INFRA"."SIM_ENGINE" IS ')
    assert f"ENGINE_VERSION {script.version}" in script.comment
    assert script.statements == (script.create, script.comment)


def test_the_version_is_stable_and_follows_the_body(engine_dir: Path) -> None:
    first = bundle.build(REAL, engine_dir).version
    assert bundle.build(REAL, engine_dir).version == first
    assert re.fullmatch(r"[0-9a-f]{16}", first)
    (engine_dir / "core.py").write_text(FAKE_CORE + "OTHER = 1\n", encoding="utf-8")
    assert bundle.build(REAL, engine_dir).version != first


def test_the_version_does_not_depend_on_the_namespace(engine_dir: Path) -> None:
    assert (
        bundle.build(REAL, engine_dir).version
        == bundle.build(Namespace("TEST_"), engine_dir).version
    )
    assert '"TEST_CFB_INFRA"."SIM_ENGINE"' in bundle.build(Namespace("TEST_"), engine_dir).create


def test_the_body_registers_both_modules_and_calls_the_udf(
    engine_dir: Path, isolated_modules: None
) -> None:
    class Ctx:
        emitted: list[Any] = []  # noqa: RUF012

    class Exa:
        tag = "t"

    scope: dict[str, Any] = {}
    exec(compile(bundle.script_body(engine_dir), "<script>", "exec"), scope)
    ctx = Ctx()
    scope["exa"] = Exa()
    scope["run"](ctx)
    assert ctx.emitted == [(42, "t")]
    assert {"engine", "engine.core", "engine.udf"} <= set(sys.modules)


def test_a_script_of_two_million_characters_is_refused_naming_the_size(engine_dir: Path) -> None:
    (engine_dir / "core.py").write_text("X = 1\n# " + "x" * 2_000_000 + "\n", encoding="utf-8")
    with pytest.raises(BundleError, match=r"characters long.*1,990,000"):
        bundle.build(REAL, engine_dir)


def test_the_real_sources_bundle_below_the_limit() -> None:
    script = bundle.build(REAL)
    assert len(script.create) < bundle.SIZE_LIMIT
