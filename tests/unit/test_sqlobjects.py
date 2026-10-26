from pathlib import Path

import pytest

from vs.host.metaddl import MetaDdlError
from vs.host.namespace import Namespace
from vs.host.sqlobjects import (
    MODEL_ALLOWED_REFS,
    SqlObject,
    lint,
    load,
    load_reserved_keywords,
    render,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sqlobjects"
RESERVED = load_reserved_keywords()


def lint_dir(directory: Path) -> list[str]:
    return lint(load(directory), allowed_refs=MODEL_ALLOWED_REFS, reserved=RESERVED)


def write_files(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


VIEW = """\
CREATE OR REPLACE FORCE VIEW {{MODEL_SCHEMA}}.{name} (
  X COMMENT IS 'x'
) AS
SELECT 1 FROM {{CORE_SCHEMA}}."GAMES"
COMMENT IS 'One row per {name}.';
"""


def view_file(name: str) -> str:
    return VIEW.format(name=name)


def by_name(objects: list[SqlObject]) -> dict[str, SqlObject]:
    return {o.name: o for o in objects}


# --- load --------------------------------------------------------------------------------------


def test_load_reads_files_in_name_order_and_ignores_subdirectories(tmp_path: Path) -> None:
    write_files(tmp_path, {"200_b.sql": view_file("B"), "100_a.sql": view_file("A")})
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "300_example.sql").write_text("SELECT 1;\n")
    (tmp_path / "notes.txt").write_text("not sql")
    assert [o.file for o in load(tmp_path)] == ["100_a.sql", "200_b.sql"]


def test_load_parses_a_reference_table_with_its_rows() -> None:
    era = by_name(load(FIXTURES / "valid"))["ERA"]
    assert (era.kind, era.schema, era.name) == ("TABLE", "{MODEL_SCHEMA}", "ERA")
    assert era.is_replace
    assert [(c.name, c.comment) for c in era.columns] == [
        ("ERA_KIND", "DIVISION or POSTSEASON; a ; and -- inside a comment"),
        ("ERA_CODE", "Era code, e.g. 'BCS'"),
        ("FIRST_SEASON", "First season of the era"),
    ]
    assert era.comment == "One row per era kind and season range; seeded from git."
    assert len(era.statements) == 2
    assert era.statements[0].startswith("CREATE OR REPLACE TABLE {MODEL_SCHEMA}.ERA (")
    assert era.statements[1].startswith('INSERT INTO {MODEL_SCHEMA}."ERA" VALUES')
    assert "('POSTSEASON', 'BCS', 1998)" in era.statements[1]
    assert "line comment" not in era.statements[1]
    assert not era.statements[1].endswith(";")
    assert era.references == ()
    assert era.problems == ()


def test_load_parses_a_function_with_its_comment_statement() -> None:
    fn = by_name(load(FIXTURES / "valid"))["GREAT_CIRCLE_KM"]
    assert (fn.kind, fn.schema, fn.name) == ("FUNCTION", "{MODEL_SCHEMA}", "GREAT_CIRCLE_KM")
    assert fn.comment == "Great-circle distance in km between two WGS 84 points."
    assert fn.columns == ()
    assert len(fn.statements) == 2
    body = fn.statements[0]
    assert "H := POWER(SIN(RADIANS(LAT2 - LAT1) / 2), 2);" in body
    assert body.rstrip().endswith("END GREAT_CIRCLE_KM;")
    assert "\n/" not in body
    assert fn.statements[1].startswith("COMMENT ON FUNCTION {MODEL_SCHEMA}.GREAT_CIRCLE_KM IS")
    assert fn.problems == ()


def test_load_parses_a_force_view_with_columns_comment_and_references() -> None:
    game = by_name(load(FIXTURES / "valid"))["GAME"]
    assert (game.kind, game.is_force, game.is_replace) == ("VIEW", True, True)
    assert [(c.name, c.comment) for c in game.columns] == [
        ("GAME_ID", "CFBD game id"),
        ("SEASON", "Season of the game"),
    ]
    assert game.comment == "One row per game (deduplicated by game id)."
    assert game.references == ("{CORE_SCHEMA}.GAMES", "{META_SCHEMA}.LOAD_SLICES")
    assert game.problems == ()


def test_references_ignore_strings_comments_and_aliases() -> None:
    season = by_name(load(FIXTURES / "valid"))["SEASON"]
    assert season.references == ("{MODEL_SCHEMA}.GREAT_CIRCLE_KM", "{MODEL_SCHEMA}.GAME")


def test_a_bare_schema_reference_is_found_after_from_and_join(tmp_path: Path) -> None:
    text = (
        "CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.V (X COMMENT IS 'x') AS\n"
        'SELECT 1 FROM "CFBD_VS"."GAMES" a JOIN SYS.EXA_ALL_OBJECTS b ON 1 = 1\n'
        "COMMENT IS 'One row per v.';\n"
    )
    write_files(tmp_path, {"100_v.sql": text})
    assert load(tmp_path)[0].references == ("CFBD_VS.GAMES", "SYS.EXA_ALL_OBJECTS")


def test_load_keeps_every_statement_of_a_file_without_failing_on_garbage(tmp_path: Path) -> None:
    write_files(tmp_path, {"100_x.sql": "SELECT 1;\nSELECT 2"})
    (obj,) = load(tmp_path)
    assert obj.kind == ""
    assert len(obj.statements) == 2
    assert any("no CREATE" in p for p in obj.problems)
    assert any("not terminated" in p for p in obj.problems)


# --- lint: the valid fixtures ------------------------------------------------------------------


def test_the_valid_fixtures_have_no_problem() -> None:
    assert lint_dir(FIXTURES / "valid") == []


# --- lint: the five problems of the deploy spec ------------------------------------------------


def test_every_problem_is_reported_not_only_the_first() -> None:
    problems = lint_dir(FIXTURES / "broken")
    assert len(problems) == 6, problems
    assert any(
        p.startswith("100_game_a.sql: GAME_A: comment") and "SEASON" in p for p in problems
    ), problems
    assert any(p.startswith("110_b.sql: B: grain") for p in problems), problems
    assert any(p.startswith("120_c.sql: C: reserved-word") and "RESULT" in p for p in problems), (
        problems
    )
    assert any(
        p.startswith("130_game_list.sql: game_list: ddl-name") and "quoted" in p for p in problems
    ), problems
    refs = [p for p in problems if p.startswith("140_d.sql: D: reference")]
    assert len(refs) == 2, problems
    assert any("CFBD_VS.GAMES" in p for p in refs)
    assert any("{VS_NAME}.TEAMS" in p for p in refs)


def test_every_problem_line_names_file_object_and_rule() -> None:
    for problem in lint_dir(FIXTURES / "broken"):
        file, obj, rest = problem.split(": ", 2)
        assert file.endswith(".sql")
        assert obj
        assert rest.split(":")[0].replace("-", "").isalpha(), problem


# --- lint: single rules ------------------------------------------------------------------------


def problems_of(tmp_path: Path, text: str, file: str = "100_v.sql") -> list[str]:
    write_files(tmp_path, {file: text})
    return lint_dir(tmp_path)


def test_a_table_column_without_a_comment_is_reported(tmp_path: Path) -> None:
    text = (
        "CREATE OR REPLACE TABLE {MODEL_SCHEMA}.T (\n"
        "  A DECIMAL(4,0) COMMENT IS 'a',\n"
        "  B DECIMAL(4,0)\n"
        ") COMMENT IS 'One row per t.';\n"
    )
    (problem,) = problems_of(tmp_path, text, "100_t.sql")
    assert problem.startswith("100_t.sql: T: comment") and "B" in problem


def test_an_empty_comment_counts_as_missing(tmp_path: Path) -> None:
    text = (
        "CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.V (X COMMENT IS '  ') AS\nSELECT 1\n"
        "COMMENT IS 'One row per v.';\n"
    )
    (problem,) = problems_of(tmp_path, text)
    assert "comment" in problem and "X" in problem


def test_an_object_without_a_comment_is_reported(tmp_path: Path) -> None:
    text = "CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.V (X COMMENT IS 'x') AS\nSELECT 1;\n"
    (problem,) = problems_of(tmp_path, text)
    assert problem.startswith("100_v.sql: V: comment")


def test_a_view_without_a_column_list_is_reported(tmp_path: Path) -> None:
    text = (
        "CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.V AS\nSELECT 1\nCOMMENT IS 'One row per v.';\n"
    )
    (problem,) = problems_of(tmp_path, text)
    assert problem.startswith("100_v.sql: V: columns")


def test_the_grain_must_start_the_comment_of_tables_and_views_only(tmp_path: Path) -> None:
    fn = (
        "CREATE OR REPLACE FUNCTION {MODEL_SCHEMA}.F (A IN DOUBLE) RETURN DOUBLE IS\n"
        "  V DOUBLE;\nBEGIN\n  V := A;\n  RETURN V;\nEND F;\n/\n"
        "COMMENT ON FUNCTION {MODEL_SCHEMA}.F IS 'Returns its argument.';\n"
    )
    assert problems_of(tmp_path, fn, "100_f.sql") == []
    bad = (
        "CREATE OR REPLACE TABLE {MODEL_SCHEMA}.T (A DECIMAL(1,0) COMMENT IS 'a')"
        " COMMENT IS 'one row per t';\n"
    )
    (problem,) = problems_of(tmp_path, bad, "110_t.sql")
    assert problem.startswith("110_t.sql: T: grain")


def test_a_comment_longer_than_2000_characters_is_reported(tmp_path: Path) -> None:
    long = "x" * 2001
    text = (
        f"CREATE OR REPLACE FORCE VIEW {{MODEL_SCHEMA}}.V (X COMMENT IS '{long}') AS\nSELECT 1\n"
        "COMMENT IS 'One row per v.';\n"
    )
    (problem,) = problems_of(tmp_path, text)
    assert "comment-length" in problem and "X" in problem


@pytest.mark.parametrize(
    ("name", "rule"),
    [
        ("game_list", "ddl-name"),
        ("Game", "ddl-name"),
        ("1GAME", "ddl-name"),
        ("STATE", "reserved-word"),
        ("STATE_CODE", None),
    ],
)
def test_unquoted_names_must_be_upper_case_identifiers(
    tmp_path: Path, name: str, rule: str | None
) -> None:
    text = view_file(name)
    write_files(tmp_path, {"100_x.sql": text})
    problems = [p for p in lint_dir(tmp_path) if "file-name" not in p]
    if rule is None:
        assert problems == []
    else:
        assert any(f": {rule}:" in p for p in problems), problems


def test_a_reserved_word_is_rejected_as_an_object_and_as_a_column(tmp_path: Path) -> None:
    text = (
        "CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.VALUES (\n  RESULT COMMENT IS 'r',\n"
        "  SELECT COMMENT IS 's'\n) AS\nSELECT 1, 2\nCOMMENT IS 'One row per v.';\n"
    )
    problems = problems_of(tmp_path, text, "100_values.sql")
    reserved = [p for p in problems if "reserved-word" in p]
    assert len(reserved) == 3, problems
    assert any("VALUES" in p for p in reserved)
    assert any("RESULT" in p for p in reserved)
    assert any("SELECT" in p for p in reserved)


def test_a_quoted_column_name_is_rejected_even_if_upper_case(tmp_path: Path) -> None:
    text = (
        "CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.V (\"X\" COMMENT IS 'x') AS\nSELECT 1\n"
        "COMMENT IS 'One row per v.';\n"
    )
    (problem,) = problems_of(tmp_path, text)
    assert ": ddl-name:" in problem and "quoted" in problem


def test_a_plain_view_is_rejected_because_every_view_is_force(tmp_path: Path) -> None:
    text = (
        "CREATE OR REPLACE VIEW {MODEL_SCHEMA}.V (X COMMENT IS 'x') AS\nSELECT 1\n"
        "COMMENT IS 'One row per v.';\n"
    )
    (problem,) = problems_of(tmp_path, text)
    assert problem.startswith("100_v.sql: V: force")


def test_create_without_or_replace_is_rejected(tmp_path: Path) -> None:
    text = (
        "CREATE TABLE {MODEL_SCHEMA}.T (A DECIMAL(1,0) COMMENT IS 'a')"
        " COMMENT IS 'One row per t.';\n"
    )
    (problem,) = problems_of(tmp_path, text, "100_t.sql")
    assert problem.startswith("100_t.sql: T: replace")


def test_an_object_outside_the_model_schema_is_rejected(tmp_path: Path) -> None:
    text = view_file("V").replace("{MODEL_SCHEMA}", "{META_SCHEMA}")
    (problem,) = problems_of(tmp_path, text)
    assert problem.startswith("100_v.sql: V: schema")


def test_the_file_name_must_carry_the_object_name(tmp_path: Path) -> None:
    (problem,) = problems_of(tmp_path, view_file("GAME"), "100_games.sql")
    assert problem.startswith("100_games.sql: GAME: file-name") and "100_game.sql" in problem


@pytest.mark.parametrize("file", ["game.sql", "10_game.sql", "100_Game.sql", "100-game.sql"])
def test_the_file_name_must_follow_the_numbered_pattern(tmp_path: Path, file: str) -> None:
    problems = problems_of(tmp_path, view_file("GAME"), file)
    assert any(": file-name:" in p and "NNN_" in p for p in problems), problems


def test_two_files_that_define_the_same_object_are_reported(tmp_path: Path) -> None:
    write_files(tmp_path, {"100_game.sql": view_file("GAME"), "200_game.sql": view_file("GAME")})
    problems = lint_dir(tmp_path)
    assert len(problems) == 1, problems
    assert problems[0].startswith("200_game.sql: GAME: duplicate") and "100_game.sql" in problems[0]


def test_an_unknown_placeholder_is_reported(tmp_path: Path) -> None:
    text = view_file("V").replace('{CORE_SCHEMA}."GAMES"', '{NOPE_SCHEMA}."GAMES"')
    problems = problems_of(tmp_path, text)
    assert any(": placeholder:" in p and "{NOPE_SCHEMA}" in p for p in problems), problems


def test_lower_case_braces_are_not_placeholders(tmp_path: Path) -> None:
    text = view_file("V").replace("SELECT 1", "SELECT '/games?year={season}'")
    assert problems_of(tmp_path, text) == []


def test_a_file_must_define_exactly_one_object(tmp_path: Path) -> None:
    text = view_file("V") + view_file("W")
    problems = problems_of(tmp_path, text)
    assert any(": statement:" in p and "second" in p for p in problems), problems


def test_a_statement_other_than_the_objects_rows_or_comment_is_reported(tmp_path: Path) -> None:
    text = view_file("V") + "DELETE FROM {MODEL_SCHEMA}.OTHER;\n"
    problems = problems_of(tmp_path, text)
    assert any(": statement:" in p and "DELETE" in p for p in problems), problems


def test_inserts_into_another_table_are_reported(tmp_path: Path) -> None:
    text = (
        "CREATE OR REPLACE TABLE {MODEL_SCHEMA}.T (A DECIMAL(1,0) COMMENT IS 'a')"
        " COMMENT IS 'One row per t.';\n"
        'INSERT INTO {MODEL_SCHEMA}."OTHER" VALUES (1);\n'
    )
    problems = problems_of(tmp_path, text, "100_t.sql")
    assert any(": statement:" in p and "OTHER" in p for p in problems), problems


def test_a_function_comment_must_be_given_by_comment_on_function(tmp_path: Path) -> None:
    fn = (
        "CREATE OR REPLACE FUNCTION {MODEL_SCHEMA}.F (A IN DOUBLE) RETURN DOUBLE IS\n"
        "  V DOUBLE;\nBEGIN\n  V := A;\n  RETURN V;\nEND F;\n/\n"
    )
    (problem,) = problems_of(tmp_path, fn, "100_f.sql")
    assert problem.startswith("100_f.sql: F: comment")


def test_allowed_references_accept_the_model_core_and_three_meta_views(tmp_path: Path) -> None:
    text = (
        "CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.V (X COMMENT IS 'x') AS\n"
        'SELECT 1 FROM {MODEL_SCHEMA}."A", {CORE_SCHEMA}."GAMES",\n'
        '  {META_SCHEMA}."LOAD_PLAN", {META_SCHEMA}."LOAD_PLAN_GAPS", {META_SCHEMA}."LOAD_SLICES"\n'
        "COMMENT IS 'One row per v.';\n"
    )
    assert problems_of(tmp_path, text) == []


def test_other_meta_objects_and_hardcoded_schemas_are_rejected(tmp_path: Path) -> None:
    text = (
        "CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.V (X COMMENT IS 'x') AS\n"
        'SELECT 1 FROM {META_SCHEMA}."FETCH_LOG", CFB_MODEL.OTHER\n'
        "COMMENT IS 'One row per v.';\n"
    )
    problems = [p for p in problems_of(tmp_path, text) if ": reference:" in p]
    assert len(problems) == 2, problems
    assert any("{META_SCHEMA}.FETCH_LOG" in p for p in problems)
    assert any("CFB_MODEL.OTHER" in p for p in problems)


def test_the_allowed_references_are_a_parameter(tmp_path: Path) -> None:
    write_files(tmp_path, {"100_v.sql": view_file("V")})
    objects = load(tmp_path)
    assert lint(objects, allowed_refs=["{CORE_SCHEMA}.*"], reserved=RESERVED) == []
    (problem,) = lint(objects, allowed_refs=["{MODEL_SCHEMA}.*"], reserved=RESERVED)
    assert ": reference:" in problem and "{CORE_SCHEMA}.GAMES" in problem


def test_reserved_keywords_are_loaded_from_the_pinned_list() -> None:
    assert {"SELECT", "RESULT", "STATE"} <= RESERVED
    assert "STATE_CODE" not in RESERVED
    assert len(RESERVED) == 468


# --- render ------------------------------------------------------------------------------------


def test_render_fills_the_placeholders_of_the_namespace_everywhere() -> None:
    objects = load(FIXTURES / "valid")
    rendered = by_name(render(objects, Namespace("TEST_")))
    game = rendered["GAME"]
    assert game.schema == "TEST_CFB_MODEL"
    assert game.statements[0].startswith("CREATE OR REPLACE FORCE VIEW TEST_CFB_MODEL.GAME (")
    assert 'FROM TEST_CFB_CORE."GAMES" g' in game.statements[0]
    assert 'JOIN TEST_CFB_META."LOAD_SLICES" s' in game.statements[0]
    assert game.references == ("TEST_CFB_CORE.GAMES", "TEST_CFB_META.LOAD_SLICES")
    assert all("{" not in s for o in rendered.values() for s in o.statements)
    assert rendered["ERA"].statements[1].startswith('INSERT INTO TEST_CFB_MODEL."ERA" VALUES')
    assert (
        rendered["GREAT_CIRCLE_KM"]
        .statements[1]
        .startswith("COMMENT ON FUNCTION TEST_CFB_MODEL.GREAT_CIRCLE_KM IS")
    )


def test_render_leaves_the_input_alone_and_keeps_names_and_comments() -> None:
    objects = load(FIXTURES / "valid")
    before = [o.statements for o in objects]
    rendered = render(objects, Namespace(""))
    assert [o.statements for o in objects] == before
    assert [o.name for o in rendered] == [o.name for o in objects]
    assert [o.comment for o in rendered] == [o.comment for o in objects]
    assert rendered[2].schema == "CFB_MODEL"


def test_render_fills_a_placeholder_inside_a_comment(tmp_path: Path) -> None:
    text = view_file("V").replace("One row per V.", "One row per V of {CORE_SCHEMA}.")
    write_files(tmp_path, {"100_v.sql": text})
    (rendered,) = render(load(tmp_path), Namespace("TEST_"))
    assert rendered.comment == "One row per V of TEST_CFB_CORE."


def test_render_rejects_an_unknown_placeholder(tmp_path: Path) -> None:
    write_files(tmp_path, {"100_v.sql": view_file("V").replace("{CORE_SCHEMA}", "{ODD}")})
    with pytest.raises(MetaDdlError, match=r"\{ODD\}"):
        render(load(tmp_path), Namespace(""))


def test_references_in_a_comma_list_with_aliases_and_clauses(tmp_path: Path) -> None:
    text = (
        "CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.V (X COMMENT IS 'x') AS\n"
        'SELECT 1 FROM {CORE_SCHEMA}."A" a, CFBD_VS.B AS b, OTHER.C c WHERE 1 = 1\n'
        "COMMENT IS 'One row per v.';\n"
    )
    write_files(tmp_path, {"100_v.sql": text})
    assert load(tmp_path)[0].references == ("{CORE_SCHEMA}.A", "CFBD_VS.B", "OTHER.C")


def test_block_comments_and_comment_markers_in_strings_are_handled(tmp_path: Path) -> None:
    text = (
        "/* header; with a semicolon */\n"
        "CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.V (X COMMENT IS 'x /* not a comment */ -- no')"
        " AS\nSELECT 1 /* inline */\nCOMMENT IS 'One row per v.';\n"
    )
    write_files(tmp_path, {"100_v.sql": text})
    (obj,) = load(tmp_path)
    assert obj.problems == ()
    assert obj.columns[0].comment == "x /* not a comment */ -- no"
    assert "inline" not in obj.statements[0]
    assert obj.statements[0].startswith("CREATE OR REPLACE FORCE VIEW")


def test_the_slash_line_of_a_function_is_optional(tmp_path: Path) -> None:
    text = (
        "CREATE OR REPLACE FUNCTION {MODEL_SCHEMA}.F (A IN DOUBLE) RETURN DOUBLE IS\n"
        "  V DOUBLE;\nBEGIN\n  V := A;\n  RETURN V;\nEND F;\n"
        "COMMENT ON FUNCTION {MODEL_SCHEMA}.F IS 'Returns its argument.';\n"
    )
    write_files(tmp_path, {"100_f.sql": text})
    (obj,) = load(tmp_path)
    assert obj.problems == ()
    assert len(obj.statements) == 2
    assert obj.statements[0].endswith("END F;")
    assert obj.comment == "Returns its argument."
    assert lint_dir(tmp_path) == []


def test_a_function_without_slash_and_without_comment_runs_to_the_end(tmp_path: Path) -> None:
    text = (
        "CREATE OR REPLACE FUNCTION {MODEL_SCHEMA}.F (A IN DOUBLE) RETURN DOUBLE IS\n"
        "  V DOUBLE;\nBEGIN\n  V := A;\n  RETURN V;\nEND F;\n"
    )
    write_files(tmp_path, {"100_f.sql": text})
    (problem,) = lint_dir(tmp_path)
    assert problem.startswith("100_f.sql: F: comment")


@pytest.mark.parametrize("target", ["TABLE", "COLUMN"])
def test_comment_on_a_view_is_rejected_because_only_inline_comments_work(
    tmp_path: Path, target: str
) -> None:
    name = "{MODEL_SCHEMA}.V" if target == "TABLE" else "{MODEL_SCHEMA}.V.X"
    text = view_file("V") + f"COMMENT ON {target} {name} IS 'One row per v.';\n"
    problems = [p for p in problems_of(tmp_path, text) if ": statement:" in p]
    assert len(problems) == 1, problems
    assert "cannot target a view" in problems[0]


def test_a_function_comment_over_2000_characters_is_reported(tmp_path: Path) -> None:
    long = "x" * 2001
    text = (
        "CREATE OR REPLACE FUNCTION {MODEL_SCHEMA}.F (A IN DOUBLE) RETURN DOUBLE IS\n"
        "  V DOUBLE;\nBEGIN\n  V := A;\n  RETURN V;\nEND F;\n/\n"
        f"COMMENT ON FUNCTION {{MODEL_SCHEMA}}.F IS '{long}';\n"
    )
    (problem,) = problems_of(tmp_path, text, "100_f.sql")
    assert "comment-length" in problem


def test_a_table_comment_over_2000_characters_is_reported(tmp_path: Path) -> None:
    long = "One row per t " + "x" * 2000
    text = (
        "CREATE OR REPLACE TABLE {MODEL_SCHEMA}.T (A DECIMAL(1,0) COMMENT IS 'a')"
        f" COMMENT IS '{long}';\n"
    )
    (problem,) = problems_of(tmp_path, text, "100_t.sql")
    assert "comment-length" in problem
