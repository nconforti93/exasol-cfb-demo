"""`model/dictionary.py`: the generated data dictionary.

The committed `docs/model-dictionary.md` equals what `make model-docs` would write."""

from __future__ import annotations

from pathlib import Path

import pytest

from model import dictionary
from model.deploy import MODEL_DIR, ModelLintError
from vs.host import sqlobjects
from vs.host.deploy import DeployError
from vs.host.namespace import Namespace

FIXTURES = Path(__file__).parent / "fixtures" / "sqlobjects"
VALID = FIXTURES / "valid"


def generated(directory: Path = VALID) -> str:
    return dictionary.render_dictionary(directory)


def test_the_committed_dictionary_equals_the_generated_one() -> None:
    committed = dictionary.DICTIONARY_PATH.read_text(encoding="utf-8")
    assert committed == dictionary.render_dictionary(MODEL_DIR), (
        "docs/model-dictionary.md is stale: run `make model-docs`"
    )


def test_every_object_has_one_section_in_file_order() -> None:
    text = generated()
    sections = [line for line in text.splitlines() if line.startswith("## ")]
    assert sections == ["## Objects", "## ERA", "## GREAT_CIRCLE_KM", "## GAME", "## SEASON"]


def test_the_overview_lists_kind_file_and_grain_of_each_object() -> None:
    text = generated()
    assert "| `GAME` | view | `100_game.sql` | One row per game (deduplicated by game id) |" in text
    era = (
        "| `ERA` | table | `010_era.sql` | One row per era kind and season range; seeded from git |"
    )
    assert era in text
    assert "| `GREAT_CIRCLE_KM` | function | `020_great_circle_km.sql` |" in text


def test_a_section_carries_the_full_comment_the_columns_and_what_it_reads() -> None:
    text = generated()
    game = text.split("## GAME\n", 1)[1].split("\n## ", 1)[0]
    assert "One row per game (deduplicated by game id)." in game
    assert "Reads: `CFB_CORE.GAMES`, `CFB_META.LOAD_SLICES`" in game
    assert "| `GAME_ID` | CFBD game id |" in game
    assert "| `SEASON` | Season of the game |" in game


def test_a_function_shows_its_signature_and_a_table_does_not_read_anything() -> None:
    text = generated()
    function = text.split("## GREAT_CIRCLE_KM\n", 1)[1].split("\n## ", 1)[0]
    assert (
        "Signature: `GREAT_CIRCLE_KM (LAT1 IN DOUBLE, LON1 IN DOUBLE, LAT2 IN DOUBLE, "
        "LON2 IN DOUBLE) RETURN DOUBLE`" in function
    )
    assert "Great-circle distance in km between two WGS 84 points." in function
    era = text.split("## ERA\n", 1)[1].split("\n## ", 1)[0]
    assert "Reads:" not in era


def test_comments_are_escaped_for_a_markdown_table(tmp_path: Path) -> None:
    (tmp_path / "100_pipe.sql").write_text(
        "CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.PIPE (\n"
        "  X COMMENT IS 'a | b'\n"
        ') AS SELECT 1 FROM {CORE_SCHEMA}."GAMES"\n'
        "COMMENT IS 'One row per pipe.';\n"
    )
    assert "| `X` | a \\| b |" in generated(tmp_path)


def test_placeholders_in_comments_come_out_as_the_real_schema_names(tmp_path: Path) -> None:
    (tmp_path / "100_labels.sql").write_text(
        "CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.LABELS (\n"
        "  X COMMENT IS 'see {CORE_SCHEMA}.GAMES'\n"
        ') AS SELECT 1 FROM {CORE_SCHEMA}."GAMES"\n'
        "COMMENT IS 'One row per label.';\n"
    )
    text = generated(tmp_path)
    assert "see CFB_CORE.GAMES" in text
    assert "{" not in text


def test_the_output_is_deterministic_and_ends_with_a_newline() -> None:
    assert generated() == generated()
    assert generated().endswith("\n")
    assert not generated().endswith("\n\n")


def test_a_lint_problem_stops_the_generation() -> None:
    with pytest.raises(ModelLintError):
        generated(FIXTURES / "broken")


def test_an_empty_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(DeployError, match=r"no \.sql files"):
        generated(tmp_path)


def test_generate_works_on_objects_rendered_for_any_namespace() -> None:
    objects = sqlobjects.render(sqlobjects.load(VALID), Namespace("TEST_"))
    assert "TEST_CFB_CORE.GAMES" in dictionary.generate(objects)
