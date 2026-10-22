"""`loader/bundle.py`: the Lua script statements of the loader, bundled from stub Lua sources."""

import re
from pathlib import Path

import pytest

from loader import bundle
from vs.host.namespace import Namespace

REAL = Namespace()
TEST = Namespace("TEST_")
LOADLIB = "-- library\nfunction helper()\n  return 1\nend\n"
BACKFILL = "-- backfill entry\nlocal rows = helper()\nexit({{rows}}, 'A DECIMAL(9,0)')\n"
REFRESH = "-- refresh entry\nlocal rows = helper()\nexit({{rows}}, 'B DECIMAL(9,0)')\n"
RUN_LOCK = "-- run lock entry\nlocal rows = helper()\nexit({{rows}}, 'C DECIMAL(9,0)')\n"
LOADER_LUA_DIR = Path(__file__).resolve().parents[2] / "loader" / "lua"


def write_sources(directory: Path, **overrides: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        "loadlib.lua": LOADLIB,
        "backfill.lua": BACKFILL,
        "refresh_week.lua": REFRESH,
        "run_lock.lua": RUN_LOCK,
    }
    for name, text in {**files, **overrides}.items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory


@pytest.fixture
def lua_dir(tmp_path: Path) -> Path:
    return write_sources(tmp_path / "lua")


def script(name: str) -> bundle.LuaScript:
    return next(s for s in bundle.SCRIPTS if s.name == name)


def test_the_three_scripts_have_the_planned_signatures_and_files() -> None:
    assert [s.name for s in bundle.SCRIPTS] == ["BACKFILL", "REFRESH_WEEK", "RUN_LOCK"]
    assert script("BACKFILL").parameters == ("profile", "from_season", "max_calls")
    assert script("REFRESH_WEEK").parameters == ("season", "week", "season_type")
    assert script("RUN_LOCK").parameters == ("lock_action", "lock_word", "lock_run", "lock_text")
    assert script("BACKFILL").file_name == "loader_backfill.sql"
    assert script("REFRESH_WEEK").file_name == "loader_refresh_week.sql"
    assert script("RUN_LOCK").file_name == "loader_run_lock.sql"
    assert script("RUN_LOCK").entry_file == "run_lock.lua"


def test_the_statement_has_the_lua_script_header_and_terminator(lua_dir: Path) -> None:
    text = bundle.build_script(script("BACKFILL"), REAL, lua_dir)
    assert text.startswith(
        "CREATE OR REPLACE LUA SCRIPT CFB_INFRA.BACKFILL(profile, from_season, max_calls) "
        "RETURNS TABLE AS\n"
    )
    assert text.endswith("\n/")
    refresh = bundle.build_script(script("REFRESH_WEEK"), TEST, lua_dir)
    assert refresh.startswith(
        "CREATE OR REPLACE LUA SCRIPT TEST_CFB_INFRA.REFRESH_WEEK(season, week, season_type) "
        "RETURNS TABLE AS\n"
    )


def test_the_body_is_constants_then_the_library_then_the_entry(lua_dir: Path) -> None:
    body = bundle.script_body(bundle.build_script(script("BACKFILL"), REAL, lua_dir))
    constants = body.index("local QUOTED")
    library = body.index("function helper()")
    entry = body.index("-- backfill entry")
    assert 0 <= body.index("local PLAIN") < constants < library < entry
    assert "refresh entry" not in body


def test_output_is_deterministic(lua_dir: Path) -> None:
    first = [bundle.build_script(s, TEST, lua_dir) for s in bundle.SCRIPTS]
    second = [bundle.build_script(s, TEST, lua_dir) for s in bundle.SCRIPTS]
    assert first == second


def test_line_endings_are_normalized(tmp_path: Path) -> None:
    crlf = write_sources(tmp_path / "crlf", **{"loadlib.lua": LOADLIB.replace("\n", "\r\n")})
    plain = write_sources(tmp_path / "lf")
    assert bundle.build_script(script("BACKFILL"), REAL, crlf) == bundle.build_script(
        script("BACKFILL"), REAL, plain
    )


def test_no_placeholder_is_left_in_the_output(lua_dir: Path) -> None:
    for s in bundle.SCRIPTS:
        for namespace in (REAL, TEST):
            assert not re.search(r"\{[A-Z][A-Z0-9_]*\}", bundle.build_script(s, namespace, lua_dir))


def test_a_source_with_a_namespace_placeholder_is_rejected(tmp_path: Path) -> None:
    sources = write_sources(
        tmp_path / "lua",
        **{"backfill.lua": "query_text = [[SELECT 1 FROM {META_SCHEMA}.LOAD_RUN]]\n"},
    )
    with pytest.raises(bundle.BundleError, match=r"backfill\.lua.*\{META_SCHEMA\}"):
        bundle.build_script(script("BACKFILL"), REAL, sources)


def test_a_placeholder_inside_a_comment_is_ignored(tmp_path: Path) -> None:
    sources = write_sources(
        tmp_path / "lua", **{"backfill.lua": "-- uses {META_SCHEMA} via QUOTED\nlocal a = 1\n"}
    )
    assert "local a = 1" in bundle.build_script(script("BACKFILL"), REAL, sources)


def test_every_namespace_name_is_a_double_quoted_constant(lua_dir: Path) -> None:
    body = bundle.script_body(bundle.build_script(script("BACKFILL"), TEST, lua_dir))
    expected = {
        "META_SCHEMA": "TEST_CFB_META",
        "CORE_SCHEMA": "TEST_CFB_CORE",
        "STAGE_SCHEMA": "TEST_CFB_STAGE",
        "RESTORE_SCHEMA": "TEST_CFB_RESTORE",
        "INFRA_SCHEMA": "TEST_CFB_INFRA",
        "VS_NAME": "TEST_CFBD_VS",
    }
    for key, name in expected.items():
        assert f'  {key} = "{name}",' in body
        assert f'  {key} = "\\"{name}\\"",' in body


def test_the_constants_evaluate_to_quoted_and_plain_names(lua_dir: Path) -> None:
    lupa = pytest.importorskip("lupa.lua54")
    runtime = lupa.LuaRuntime()
    body = bundle.script_body(bundle.build_script(script("BACKFILL"), TEST, lua_dir))
    constants = body[: body.index("function helper()")]
    result = runtime.execute(constants + "\nreturn QUOTED.META_SCHEMA, PLAIN.META_SCHEMA")
    assert tuple(result) == ('"TEST_CFB_META"', "TEST_CFB_META")


def test_the_script_size_stays_below_the_cap(lua_dir: Path) -> None:
    for s in bundle.SCRIPTS:
        assert len(bundle.build_script(s, REAL, lua_dir)) < bundle.SIZE_LIMIT


def test_a_script_of_the_size_limit_fails_naming_the_size(tmp_path: Path) -> None:
    huge = write_sources(tmp_path / "lua", **{"loadlib.lua": "-- " + "x" * bundle.SIZE_LIMIT})
    with pytest.raises(bundle.BundleError, match=r"BACKFILL is [\d,]+ characters"):
        bundle.build_script(script("BACKFILL"), REAL, huge)


@pytest.mark.parametrize(
    "line",
    [
        'query("SELECT 1")',
        "local r = query [[SELECT 1]]",
        "query'SELECT 1'",
        "local r = query(sql, binds)",
        "x = 1; query(sql)",
    ],
)
def test_a_bare_query_call_is_rejected(tmp_path: Path, line: str) -> None:
    sources = write_sources(tmp_path / "lua", **{"loadlib.lua": line + "\n"})
    with pytest.raises(bundle.BundleError, match=r"loadlib\.lua.*bare query"):
        bundle.build_script(script("BACKFILL"), REAL, sources)


@pytest.mark.parametrize(
    "line",
    [
        'local ok, r = pquery("SELECT 1")',
        "local ok, r = pquery(sql, binds)",
        "-- query(sql) would end the script on error",
        "--[[ query(sql) ]] local a = 1",
        'local text = "call query() here"',
        "local text = [[ query(sql) ]]",
        "local run = state.query(sql)",
        "local run = state:query(sql)",
        "local my_query = 1",
        "local query_count = query_total(1)",
    ],
)
def test_pquery_comments_strings_and_other_names_are_allowed(tmp_path: Path, line: str) -> None:
    sources = write_sources(tmp_path / "lua", **{"loadlib.lua": line + "\n"})
    assert bundle.build_script(script("BACKFILL"), REAL, sources).endswith("\n/")


@pytest.mark.parametrize(
    "line",
    [
        "local ok = pquery(\"ALTER VIRTUAL SCHEMA CFBD_VS SET READ_THROUGH = 'off'\")",
        "local sql = [[alter   virtual\n schema x refresh]]",
        "-- note\nlocal sql = 'ALTER VIRTUAL SCHEMA'",
    ],
)
def test_alter_virtual_schema_is_rejected(tmp_path: Path, line: str) -> None:
    sources = write_sources(tmp_path / "lua", **{"backfill.lua": line + "\n"})
    with pytest.raises(bundle.BundleError, match=r"backfill\.lua.*ALTER VIRTUAL SCHEMA"):
        bundle.build_script(script("BACKFILL"), REAL, sources)


def test_alter_virtual_schema_in_a_comment_is_allowed(tmp_path: Path) -> None:
    sources = write_sources(
        tmp_path / "lua", **{"backfill.lua": "-- never ALTER VIRTUAL SCHEMA here\nlocal a = 1\n"}
    )
    assert bundle.build_script(script("BACKFILL"), REAL, sources).endswith("\n/")


def test_every_violation_of_a_source_is_listed(tmp_path: Path) -> None:
    sources = write_sources(
        tmp_path / "lua",
        **{
            "loadlib.lua": (
                'query("x")\nlocal s = "alter virtual schema"\nlocal t = [[{CORE_SCHEMA}]]\n'
            )
        },
    )
    with pytest.raises(bundle.BundleError) as raised:
        bundle.build_script(script("BACKFILL"), REAL, sources)
    text = str(raised.value)
    assert "bare query" in text and "ALTER VIRTUAL SCHEMA" in text and "{CORE_SCHEMA}" in text
    assert "loadlib.lua:1" in text and "loadlib.lua:2" in text and "loadlib.lua:3" in text


def test_a_missing_source_is_a_bundle_error(tmp_path: Path) -> None:
    sources = write_sources(tmp_path / "lua")
    (sources / "loadlib.lua").unlink()
    with pytest.raises(bundle.BundleError, match=r"loadlib\.lua"):
        bundle.build_script(script("BACKFILL"), REAL, sources)


def test_a_namespace_name_that_is_not_a_plain_identifier_is_rejected(lua_dir: Path) -> None:
    class Odd(Namespace):
        @property
        def meta_schema(self) -> str:
            return 'CFB_META"; DROP'

    with pytest.raises(bundle.BundleError, match="META_SCHEMA"):
        bundle.build_script(script("BACKFILL"), Odd(), lua_dir)


def test_the_bundled_lua_compiles(lua_dir: Path) -> None:
    lupa = pytest.importorskip("lupa.lua54")
    runtime = lupa.LuaRuntime()
    for s in bundle.SCRIPTS:
        body = bundle.script_body(bundle.build_script(s, TEST, lua_dir)).removesuffix("\n/")
        compiled = runtime.eval("load")(body)
        assert callable(compiled), (s.name, compiled)


def test_a_lua_syntax_error_does_not_compile(tmp_path: Path) -> None:
    lupa = pytest.importorskip("lupa.lua54")
    broken = write_sources(tmp_path / "lua", **{"backfill.lua": "local x = (\n"})
    body = bundle.script_body(bundle.build_script(script("BACKFILL"), TEST, broken))
    assert not callable(lupa.LuaRuntime().eval("load")(body.removesuffix("\n/")))


def test_write_bundle_writes_one_file_per_available_script(tmp_path: Path, lua_dir: Path) -> None:
    out = tmp_path / "out"
    result = bundle.write_bundle(out_dir=out, namespace=REAL, lua_dir=lua_dir)
    assert result.skipped == ()
    assert sorted(p.name for p in out.iterdir()) == [
        "loader_backfill.sql",
        "loader_refresh_week.sql",
        "loader_run_lock.sql",
    ]
    for s in bundle.SCRIPTS:
        written = (out / s.file_name).read_bytes().decode("utf-8")
        assert written == bundle.build_script(s, REAL, lua_dir)
        assert result.sizes[s.name] == len(written)


def test_write_bundle_skips_scripts_whose_sources_do_not_exist_yet(
    tmp_path: Path, lua_dir: Path
) -> None:
    (lua_dir / "refresh_week.lua").unlink()
    result = bundle.write_bundle(out_dir=tmp_path / "out", namespace=REAL, lua_dir=lua_dir)
    assert result.skipped == ("REFRESH_WEEK",)
    assert list(result.sizes) == ["BACKFILL", "RUN_LOCK"]
    assert not (tmp_path / "out" / "loader_refresh_week.sql").exists()


def test_write_bundle_skips_everything_while_the_library_is_missing(tmp_path: Path) -> None:
    empty = tmp_path / "lua"
    empty.mkdir()
    result = bundle.write_bundle(out_dir=tmp_path / "out", namespace=REAL, lua_dir=empty)
    assert result.skipped == ("BACKFILL", "REFRESH_WEEK", "RUN_LOCK")
    assert result.sizes == {}


def test_the_real_loader_sources_pass_the_source_checks() -> None:
    sources = sorted(LOADER_LUA_DIR.glob("*.lua"))
    problems = [
        p for source in sources for p in bundle.check_source(source.name, source.read_text("utf-8"))
    ]
    assert problems == []
