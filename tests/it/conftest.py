"""Fixtures and hooks of the integration tests (marker `it`, run by `make test-it`).

- Collection refuses to run when any file below `tests/it` refers to the real CFBD connection.
- Every test under `tests/it` gets the `it` marker.
- `harness` (session scope) builds the `TEST_` namespace once and drops it at the end.
- `clean_state` (autouse, per test) restores properties, tables and recordings, so a test leaves
  the database as it found it, also after a failure.
- `plan_variant` (module scope, autouse) deploys the loader plan a test module asks for with
  `PLAN_VARIANT = "<variant of tests/it/fixtures>"` and optionally `PLAN_CONFIG = {...}`
  (`LOADER_CONFIG` overrides); the default is the base plan. The per-test reset returns to it.
- Model tests (`PLAN_VARIANT = "model"`): `model_deployed` (module scope) makes sure
  `TEST_CFB_MODEL` holds the current `sql/model/` files. `league` (module scope) deploys the model
  and seeds the base league once for a module of read-only tests, marked
  `pytestmark = pytest.mark.model_readonly`: for those `clean_state` keeps the data, the loader plan
  and the clock, so a test of such a module must not change data. The module's teardown empties the
  data again. Modules that change data (variants, the refresh step) do not use `league`: they call
  `harness.seed_league(variant)` in every test, which empties and re-seeds, and the normal
  per-test reset cleans up.
- `snapshot_dir` (opt-in) is an empty private (mode 0700) directory below pytest's tmp path.
- `mock` (opt-in) resets the mock sidecar so a test counts its own hits and rules; a test that
  looks at the mock must request it.

Zero CFBD calls: the only connection the harness creates is `CFBD_API_MOCK`, pointing at the
mock sidecar. See `tests/it/harness.py` for the helper API.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.it import guards, loader_support
from tests.it.harness import Harness, Mock, build_harness
from tests.synthetic.league import League

IT_DIRECTORY = Path(__file__).parent
FAILED_START = 2
MODEL_READONLY = "model_readonly"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    problems = guards.forbidden_references(IT_DIRECTORY)
    if problems:
        raise pytest.UsageError(
            "integration tests must not reference the real CFBD connection:\n  "
            + "\n  ".join(problems)
        )
    for item in items:
        if IT_DIRECTORY in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.it)


@pytest.fixture(scope="session")
def harness() -> Iterator[Harness]:
    try:
        built = build_harness()
    except guards.GuardError as error:
        pytest.exit(str(error), returncode=FAILED_START)
    try:
        yield built
    finally:
        problems = built.close()
        if problems:
            pytest.fail(
                "test namespace cleanup incomplete:\n  " + "\n  ".join(problems), pytrace=False
            )


@pytest.fixture(autouse=True)
def clean_state(harness: Harness, request: pytest.FixtureRequest) -> Iterator[None]:
    keep_data = request.node.get_closest_marker(MODEL_READONLY) is not None
    try:
        yield
    finally:
        problems = harness.reset_state(keep_data=keep_data)
        if problems:
            pytest.fail("test cleanup incomplete:\n  " + "\n  ".join(problems), pytrace=False)


@pytest.fixture(scope="module", autouse=True)
def plan_variant(request: pytest.FixtureRequest, harness: Harness) -> str:
    variant = str(getattr(request.module, "PLAN_VARIANT", loader_support.BASE_VARIANT))
    harness.use_plan(variant, getattr(request.module, "PLAN_CONFIG", None))
    return variant


@pytest.fixture(scope="module")
def model_deployed(harness: Harness) -> None:
    harness.ensure_model()


@pytest.fixture(scope="module")
def league(harness: Harness, model_deployed: None) -> Iterator[League]:
    seeded = harness.seed_league()
    try:
        yield seeded
    finally:
        problems = harness.reset_state()
        if problems:
            pytest.fail("league cleanup incomplete:\n  " + "\n  ".join(problems), pytrace=False)


@pytest.fixture
def snapshot_dir(tmp_path: Path) -> Path:
    return loader_support.private_directory(tmp_path / "snapshots")


@pytest.fixture
def mock(harness: Harness) -> Iterator[Mock]:
    harness.arm_mock()
    try:
        yield harness.mock
    finally:
        harness.disarm_mock()
