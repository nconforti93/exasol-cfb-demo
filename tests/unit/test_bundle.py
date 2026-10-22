import json
import subprocess
import sys
from pathlib import Path

import pytest

from vs import bundle
from vs.cfbd_vs.catalog import Catalog

ROOT = Path(__file__).resolve().parents[2]
CATALOG_TEXT = (ROOT / "vs" / "catalog" / "catalog.json").read_text(encoding="utf-8")
CATALOG = Catalog.from_json(CATALOG_TEXT)


@pytest.fixture(scope="module")
def built() -> bundle.Bundle:
    return bundle.build(CATALOG_TEXT)


def run_isolated(tmp_path: Path, body: str, driver: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "script.py"
    script.write_text(body + "\n" + driver, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-I", str(script)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
        check=False,
    )


def test_build_is_deterministic(built: bundle.Bundle) -> None:
    assert bundle.build(CATALOG_TEXT) == built


def test_build_embeds_the_compact_catalog_with_the_same_content(built: bundle.Bundle) -> None:
    compact = CATALOG.to_json(indent=None)
    assert repr(compact) in built.adapter_body
    assert repr(compact) in built.fetch_body
    assert len(compact) < len(CATALOG_TEXT)


def test_modules_are_registered_only_under_the_package_name(built: bundle.Bundle) -> None:
    for entry, body in (
        (bundle.ADAPTER_MODULE, built.adapter_body),
        (bundle.FETCH_MODULE, built.fetch_body),
    ):
        names = bundle.module_order(entry)
        assert "vs.cfbd_vs.http" in names
        assert all(name.startswith("vs.cfbd_vs.") for name in names)
        assert '\n    ("http", ' not in body
        assert all(f'\n    ("{name}", ' in body for name in names)


def test_adapter_bundle_runs_in_a_clean_interpreter(tmp_path: Path, built: bundle.Bundle) -> None:
    driver = (
        "import json\n"
        "caps = json.loads(adapter_call(json.dumps({'type': 'getCapabilities'})))\n"
        "created = json.loads(adapter_call(json.dumps({'type': 'createVirtualSchema', "
        "'schemaMetadataInfo': {'name': 'V', 'properties': {}}})))\n"
        "print(json.dumps({'caps': len(caps['capabilities']), "
        "'tables': len(created['schemaMetadata']['tables']), "
        "'stdlib_http': __import__('http').__name__, "
        "'http_is_stdlib': hasattr(__import__('http'), 'HTTPStatus')}))\n"
    )
    result = run_isolated(tmp_path, built.adapter_body, driver)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout.splitlines()[-1])
    assert summary["caps"] > 10
    assert summary["tables"] == len(CATALOG.tables)
    assert summary["http_is_stdlib"] is True


def test_fetch_bundle_runs_in_a_clean_interpreter(tmp_path: Path, built: bundle.Bundle) -> None:
    driver = f"""
from types import SimpleNamespace

class Ctx:
    TABLE_NAME = 'GAMES'
    SLICE_KEY = '/games?year=2024'
    CATALOG_HASH = {"0" * 64!r}
    FETCH_MODE = 'replay'
    CONNECTION_NAME = 'CFBD_API'
    REPLAY_PATH = '/buckets/bfsdefault/default/cfbd/replay'
    DOH_URL = 'off'
    MAX_PARALLEL_FETCHES = 2

    def next(self):
        return False

exa = SimpleNamespace(meta=SimpleNamespace(output_columns=[]))
try:
    run(Ctx())
except Exception as error:
    print(type(error).__name__ + ': ' + str(error))
"""
    result = run_isolated(tmp_path, built.fetch_body, driver)
    assert result.returncode == 0, result.stderr
    assert "different catalog versions" in result.stdout
    assert CATALOG.catalog_hash[:12] in result.stdout


def test_scripts_carry_the_specified_headers(built: bundle.Bundle) -> None:
    adapter = bundle.adapter_script(built.adapter_body, "CFB_INFRA")
    assert adapter.startswith(
        "CREATE OR REPLACE PYTHON3 ADAPTER SCRIPT CFB_INFRA.CFBD_ADAPTER AS\n"
    )
    assert adapter.endswith("\n/")
    fetch = bundle.fetch_script(built.fetch_body, "TEST_CFB_INFRA")
    assert fetch.startswith(
        "CREATE PYTHON3 SET SCRIPT TEST_CFB_INFRA.CFBD_FETCH(TABLE_NAME VARCHAR(128), "
        "SLICE_KEY VARCHAR(2000), CATALOG_HASH VARCHAR(64), FETCH_MODE VARCHAR(16), "
        "CONNECTION_NAME VARCHAR(128), REPLAY_PATH VARCHAR(1024), DOH_URL VARCHAR(1024), "
        "MAX_PARALLEL_FETCHES DECIMAL(2,0)) EMITS (...) AS\n"
    )
    assert fetch.endswith("\n/")


def test_fetch_header_has_no_or_replace_so_the_grant_is_kept_by_the_deployer() -> None:
    header = bundle.fetch_header("CFB_INFRA")
    assert "OR REPLACE" not in header
    assert bundle.fetch_header("CFB_INFRA", replace=True).startswith("CREATE OR REPLACE ")


def test_stored_body_comparison_form_matches_the_probe_normalization(
    built: bundle.Bundle,
) -> None:
    sent = bundle.fetch_script(built.fetch_body, "CFB_INFRA")
    stored = (
        'CREATE PYTHON3 SET SCRIPT "CFBD_FETCH" (...) EMITS (...) AS\n' + built.fetch_body + "\n"
    )
    assert bundle.script_body(stored) == bundle.stored_body(built.fetch_body)
    assert bundle.script_body(sent.removesuffix("/")) == bundle.stored_body(built.fetch_body)


def test_script_body_without_header_marker_is_rejected() -> None:
    with pytest.raises(bundle.BundleError):
        bundle.script_body("no header here")


def test_real_bundle_is_within_the_script_limit(built: bundle.Bundle) -> None:
    for text in (
        bundle.adapter_script(built.adapter_body, "CFB_INFRA"),
        bundle.fetch_script(built.fetch_body, "CFB_INFRA"),
    ):
        assert len(text) < bundle.SIZE_LIMIT


@pytest.mark.parametrize("size", [bundle.SIZE_LIMIT - 1, bundle.SIZE_LIMIT, 2_000_001])
def test_size_check_fails_at_the_limit_and_above(size: int) -> None:
    padding = "x" * (size - len(bundle.fetch_script("", "CFB_INFRA")))
    if size < bundle.SIZE_LIMIT:
        bundle.fetch_script(padding, "CFB_INFRA")
        return
    with pytest.raises(bundle.BundleError, match="1,990,000"):
        bundle.fetch_script(padding, "CFB_INFRA")


def test_write_bundle_creates_both_files_deterministically(tmp_path: Path) -> None:
    first = bundle.write_bundle(CATALOG_TEXT, tmp_path)
    contents = {path.name: path.read_bytes() for path in sorted(tmp_path.iterdir())}
    assert set(contents) == {"cfbd_adapter.sql", "cfbd_fetch.sql"}
    second = bundle.write_bundle(CATALOG_TEXT, tmp_path)
    assert first == second
    assert contents == {path.name: path.read_bytes() for path in sorted(tmp_path.iterdir())}
    assert first.adapter_chars == len(contents["cfbd_adapter.sql"].decode("utf-8"))


def test_module_order_is_dependency_first(built: bundle.Bundle) -> None:
    order = bundle.module_order("vs.cfbd_vs.adapter")
    assert order[-1] == "vs.cfbd_vs.adapter"
    assert order.index("vs.cfbd_vs.catalog") < order.index("vs.cfbd_vs.filters")
    assert order.index("vs.cfbd_vs.fetch") < order.index("vs.cfbd_vs.pushdown_sql")
    fetch_order = bundle.module_order("vs.cfbd_vs.fetch")
    assert "vs.cfbd_vs.adapter" not in fetch_order
    assert "vs.cfbd_vs.resolver" in fetch_order
