import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from vs.cfbd_vs.catalog import Catalog
from vs.generate import GENERATOR_VERSION, main

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_SPEC = REPO_ROOT / "vs" / "openapi" / "cfbd-openapi-5.33.0.json"
REAL_OVERRIDES = REPO_ROOT / "vs" / "catalog-overrides.yaml"
REAL_KEYWORDS = REPO_ROOT / "vs" / "gen" / "exasol_reserved_keywords.txt"
COMMITTED_CATALOG = REPO_ROOT / "vs" / "catalog" / "catalog.json"
COMMITTED_MIRRORS = REPO_ROOT / "sql" / "core" / "cfb_core_mirrors.sql"

INT = {"type": "integer", "format": "int32"}
STR = {"type": "string"}


def query(name: str, schema: Any, description: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "in": "query",
        "required": False,
        "description": description,
        "schema": schema,
    }


def get_op(schema: Any, *parameters: Any, description: str = "") -> dict[str, Any]:
    return {
        "get": {
            "description": description,
            "parameters": list(parameters),
            "responses": {"200": {"content": {"application/json": {"schema": schema}}}},
        }
    }


SYNTHETIC_SPEC: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {"title": "Synthetic", "version": "1.2.3"},
    "paths": {
        "/widgets": get_op(
            {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": INT,
                        "name": STR,
                        "parts": {
                            "type": "array",
                            "items": {"type": "object", "properties": {"label": STR}},
                        },
                    },
                },
            },
            query("year", INT, "Season year. Required unless `team` is specified."),
            query("team", STR, "Team name. Required unless `year` is specified."),
        ),
        "/status": get_op({"type": "object", "properties": {"ok": {"type": "boolean"}}}),
    },
}

SYNTHETIC_OVERRIDES = """
endpoints:
  /widgets:
    rules:
      - {id: year_or_team, type: one_of, params: [year, team], why: "give year or team"}
  /status:
    read_through: false
"""


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.spec = root / "spec.json"
        self.overrides = root / "overrides.yaml"
        self.keywords = root / "keywords.txt"
        self.catalog = root / "out" / "catalog" / "catalog.json"
        self.mirrors = root / "out" / "sql" / "cfb_core_mirrors.sql"

    def write(self, spec: dict[str, Any], overrides: str, keywords: str = "DATE\nVALUE\n") -> None:
        self.spec.write_text(json.dumps(spec), encoding="utf-8")
        self.overrides.write_text(textwrap.dedent(overrides), encoding="utf-8")
        self.keywords.write_text(keywords, encoding="utf-8")

    def args(self) -> list[str]:
        return [
            "--spec",
            str(self.spec),
            "--overrides",
            str(self.overrides),
            "--keywords",
            str(self.keywords),
            "--catalog-out",
            str(self.catalog),
            "--mirrors-out",
            str(self.mirrors),
        ]

    def outputs_exist(self) -> bool:
        return self.catalog.exists() or self.mirrors.exists()


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    space = Workspace(tmp_path)
    space.write(SYNTHETIC_SPEC, SYNTHETIC_OVERRIDES)
    return space


def test_synthetic_run_writes_catalog_and_mirrors(workspace: Workspace) -> None:
    assert main(workspace.args()) == 0

    catalog = Catalog.from_json(workspace.catalog.read_text(encoding="utf-8"))
    assert [t.name for t in catalog.tables] == ["STATUS", "WIDGETS", "WIDGETS_PARTS"]
    assert catalog.spec_version == "1.2.3"
    assert catalog.generator_version == GENERATOR_VERSION
    assert catalog.catalog_hash == catalog.content_hash()


def test_a_comment_only_overrides_edit_keeps_the_hash(workspace: Workspace) -> None:
    assert main(workspace.args()) == 0
    before = Catalog.from_json(workspace.catalog.read_text(encoding="utf-8")).catalog_hash

    workspace.overrides.write_text(
        "# a reviewer note\n" + workspace.overrides.read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert main(workspace.args()) == 0

    assert Catalog.from_json(workspace.catalog.read_text(encoding="utf-8")).catalog_hash == before


def test_a_content_change_changes_the_hash(workspace: Workspace) -> None:
    assert main(workspace.args()) == 0
    before = Catalog.from_json(workspace.catalog.read_text(encoding="utf-8")).catalog_hash

    workspace.overrides.write_text(
        workspace.overrides.read_text(encoding="utf-8").replace(
            "give year or team", "year or team"
        ),
        encoding="utf-8",
    )
    assert main(workspace.args()) == 0

    assert Catalog.from_json(workspace.catalog.read_text(encoding="utf-8")).catalog_hash != before


def test_mirrors_cover_exactly_the_read_through_tables(workspace: Workspace) -> None:
    assert main(workspace.args()) == 0

    sql = workspace.mirrors.read_text(encoding="utf-8")
    catalog = Catalog.from_json(workspace.catalog.read_text(encoding="utf-8"))
    assert "CREATE TABLE CFB_CORE.WIDGETS (" in sql
    assert "CREATE TABLE CFB_CORE.WIDGETS_PARTS (" in sql
    assert "CFB_CORE.STATUS" not in sql
    widgets = catalog.table("WIDGETS")
    for column in widgets.columns:
        assert f"  {column.name} {column.sql_type}" in sql
    assert catalog.catalog_hash in sql


def test_two_runs_are_byte_identical(workspace: Workspace) -> None:
    assert main(workspace.args()) == 0
    first = (workspace.catalog.read_bytes(), workspace.mirrors.read_bytes())

    assert main(workspace.args()) == 0

    assert (workspace.catalog.read_bytes(), workspace.mirrors.read_bytes()) == first


def test_invalid_overrides_list_every_error_and_write_nothing(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace.overrides.write_text(
        textwrap.dedent(
            """
            endpoints:
              /nowhere: {read_through: false}
              /widgets:
                rules:
                  - {id: bad, type: required, params: [nope]}
            exclude:
              - {path: /status}
            """
        ),
        encoding="utf-8",
    )

    assert main(workspace.args()) != 0

    err = capsys.readouterr().err
    assert "/nowhere" in err
    assert "nope" in err
    assert "reason" in err
    assert not workspace.outputs_exist()


def test_uncovered_prose_rule_stops_generation(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace.overrides.write_text("endpoints: {}\n", encoding="utf-8")

    assert main(workspace.args()) != 0

    err = capsys.readouterr().err
    assert "/widgets" in err
    assert "Required unless `team` is specified." in err
    assert not workspace.outputs_exist()


def test_table_errors_are_all_listed(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = json.loads(json.dumps(SYNTHETIC_SPEC))
    spec["paths"]["/widget/{id}"] = get_op({"type": "object", "properties": {"ok": STR}})
    spec["paths"]["/widget/{key}"] = get_op({"type": "object", "properties": {"ok": STR}})
    spec["paths"]["/status"] = get_op({"type": "object", "properties": {"date": STR, "Date": STR}})
    workspace.write(spec, SYNTHETIC_OVERRIDES)

    assert main(workspace.args()) != 0

    err = capsys.readouterr().err
    assert "WIDGET" in err
    assert "DATE" in err
    assert not workspace.outputs_exist()


def test_missing_keywords_file_is_reported_by_name(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace.keywords.unlink()

    assert main(workspace.args()) != 0

    assert str(workspace.keywords) in capsys.readouterr().err
    assert not workspace.outputs_exist()


def test_reserved_keywords_rename_columns(workspace: Workspace) -> None:
    spec = json.loads(json.dumps(SYNTHETIC_SPEC))
    spec["paths"]["/status"] = get_op({"type": "object", "properties": {"value": STR}})
    workspace.write(spec, SYNTHETIC_OVERRIDES)

    assert main(workspace.args()) == 0

    status = Catalog.from_json(workspace.catalog.read_text(encoding="utf-8")).table("STATUS")
    assert "VALUE_" in [c.name for c in status.columns]


def test_failed_second_write_leaves_the_first_output_untouched(workspace: Workspace) -> None:
    workspace.catalog.parent.mkdir(parents=True)
    workspace.catalog.write_text("old catalog\n", encoding="utf-8")
    workspace.mirrors.mkdir(parents=True)

    assert main(workspace.args()) != 0

    assert workspace.catalog.read_text(encoding="utf-8") == "old catalog\n"
    assert sorted(p.name for p in workspace.catalog.parent.iterdir()) == ["catalog.json"]


def test_real_spec_generates_the_committed_outputs(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    space.spec, space.overrides, space.keywords = REAL_SPEC, REAL_OVERRIDES, REAL_KEYWORDS

    assert main(space.args()) == 0

    catalog = Catalog.from_json(space.catalog.read_text(encoding="utf-8"))
    endpoint_tables = [t for t in catalog.tables if t.kind == "endpoint"]
    assert len(endpoint_tables) + len(catalog.excluded) == 93
    assert catalog.spec_version == "5.33.0"
    assert catalog.catalog_hash == catalog.content_hash()
    assert space.catalog.read_bytes() == COMMITTED_CATALOG.read_bytes()
    assert space.mirrors.read_bytes() == COMMITTED_MIRRORS.read_bytes()
    mirrored = {t.name for t in catalog.tables if t.read_through}
    created = {
        line.split()[2].removeprefix("CFB_CORE.")
        for line in space.mirrors.read_text(encoding="utf-8").splitlines()
        if line.startswith("CREATE TABLE ")
    }
    assert created == mirrored


def with_response(workspace: Workspace, schema: Any, *parameters: Any) -> None:
    spec = json.loads(json.dumps(SYNTHETIC_SPEC))
    spec["paths"]["/status"] = get_op(schema, *parameters)
    workspace.write(spec, SYNTHETIC_OVERRIDES)


def seed_outputs(workspace: Workspace) -> tuple[bytes, bytes]:
    workspace.catalog.parent.mkdir(parents=True)
    workspace.mirrors.parent.mkdir(parents=True, exist_ok=True)
    workspace.catalog.write_bytes(b"previous catalog\n")
    workspace.mirrors.write_bytes(b"previous mirrors\n")
    return b"previous catalog\n", b"previous mirrors\n"


INVALID_OVERRIDES = {
    "unknown endpoint": ("endpoints:\n  /nowhere: {read_through: false}\n", "/nowhere"),
    "unknown parameter": (
        "endpoints:\n  /widgets:\n    rules:\n"
        "      - {id: r, type: required, params: [year, nope]}\n",
        "nope",
    ),
    "unknown response field": (
        "endpoints:\n  /widgets:\n    fields:\n      nope: {type: string}\n"
        "    rules:\n      - {id: r, type: one_of, params: [year, team], why: w}\n",
        "unknown response field 'nope'",
    ),
    "exclusion without reason": (
        "endpoints:\n  /widgets:\n    rules:\n"
        "      - {id: r, type: one_of, params: [year, team], why: w}\n"
        "exclude:\n  - {path: /status}\n",
        "reason must be a non-empty string",
    ),
}


@pytest.mark.parametrize("overrides, expected", INVALID_OVERRIDES.values(), ids=INVALID_OVERRIDES)
def test_invalid_overrides_stop_generation(
    workspace: Workspace, capsys: pytest.CaptureFixture[str], overrides: str, expected: str
) -> None:
    """Scenario endpoint-catalog: Invalid overrides stop generation (each kind of bad entry)."""
    workspace.overrides.write_text(overrides, encoding="utf-8")
    before = seed_outputs(workspace)

    assert main(workspace.args()) != 0

    err = capsys.readouterr().err
    assert expected in err
    assert (workspace.catalog.read_bytes(), workspace.mirrors.read_bytes()) == before
    assert sorted(p.name for p in workspace.catalog.parent.iterdir()) == ["catalog.json"]


def test_uncovered_prose_rules_stop_generation(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scenario endpoint-catalog: Prose-only rules must be covered (all uncovered are listed)."""
    workspace.overrides.write_text("endpoints: {}\n", encoding="utf-8")
    before = seed_outputs(workspace)

    assert main(workspace.args()) != 0

    err = capsys.readouterr().err
    assert "Required unless `team` is specified." in err
    assert "Required unless `year` is specified." in err
    assert (workspace.catalog.read_bytes(), workspace.mirrors.read_bytes()) == before


def test_acknowledged_prose_rules_do_not_stop_generation(workspace: Workspace) -> None:
    """Scenario endpoint-catalog: Prose-only rules must be covered (acknowledgements cover)."""
    workspace.overrides.write_text(
        textwrap.dedent(
            """
            acknowledged:
              - {endpoint: /widgets, param: year, why: "checked by hand"}
              - {endpoint: /widgets, param: team, why: "checked by hand"}
            """
        ),
        encoding="utf-8",
    )

    assert main(workspace.args()) == 0


def test_generation_deterministic_and_committed_current(tmp_path: Path) -> None:
    """Scenario endpoint-catalog: deterministic generation, committed files are current."""
    first, second = Workspace(tmp_path / "first"), Workspace(tmp_path / "second")
    for space in (first, second):
        space.root.mkdir()
        space.spec, space.overrides, space.keywords = REAL_SPEC, REAL_OVERRIDES, REAL_KEYWORDS

    assert main(first.args()) == 0
    assert main(second.args()) == 0

    assert first.catalog.read_bytes() == second.catalog.read_bytes()
    assert first.mirrors.read_bytes() == second.mirrors.read_bytes()
    assert first.catalog.read_bytes() == COMMITTED_CATALOG.read_bytes()
    assert first.mirrors.read_bytes() == COMMITTED_MIRRORS.read_bytes()


def test_colliding_table_names_stop_generation(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scenario endpoint-catalog: Table names derive from the path (a collision names both)."""
    spec = json.loads(json.dumps(SYNTHETIC_SPEC))
    spec["paths"]["/widget/{id}"] = get_op({"type": "object", "properties": {"ok": STR}})
    spec["paths"]["/widget/{key}"] = get_op({"type": "object", "properties": {"ok": STR}})
    workspace.write(spec, SYNTHETIC_OVERRIDES)

    assert main(workspace.args()) != 0

    err = capsys.readouterr().err
    assert "table name WIDGET is produced by both /widget/{id} and /widget/{key}" in err
    assert not workspace.outputs_exist()


def test_column_collisions_stop_generation(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scenario endpoint-catalog: Column name collisions (fields, parameter, metadata, position)."""
    with_response(
        workspace,
        {
            "type": "object",
            "properties": {
                "userName": STR,
                "user_name": STR,
                "pYear": STR,
                "reqSlice": STR,
                "rowNo": INT,
            },
        },
        query("year", INT),
    )

    assert main(workspace.args()) != 0

    err = capsys.readouterr().err
    assert "column USER_NAME is produced by both field userName and field user_name" in err
    assert "column P_YEAR is produced by both field pYear and parameter year" in err
    assert "column REQ_SLICE is produced by both field reqSlice and request metadata" in err
    assert "column ROW_NO is produced by both the row position and field rowNo" in err
    assert not workspace.outputs_exist()
