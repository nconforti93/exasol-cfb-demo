"""Fixtures and hooks of the integration tests (marker `it`, run by `make test-it`).

- Collection refuses to run when any file below `tests/it` refers to the real CFBD connection.
- Every test under `tests/it` gets the `it` marker.
- `harness` (session scope) builds the `TEST_` namespace once and drops it at the end.
- `clean_state` (autouse, per test) restores properties, tables and recordings, so a test leaves
  the database as it found it, also after a failure.
- `mock` (opt-in) resets the mock sidecar so a test counts its own hits and rules; a test that
  looks at the mock must request it.

Zero CFBD calls: the only connection the harness creates is `CFBD_API_MOCK`, pointing at the
mock sidecar. See `tests/it/harness.py` for the helper API.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.it import guards
from tests.it.harness import Harness, Mock, build_harness

IT_DIRECTORY = Path(__file__).parent
FAILED_START = 2


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
def clean_state(harness: Harness) -> Iterator[None]:
    try:
        yield
    finally:
        problems = harness.reset_state()
        if problems:
            pytest.fail("test cleanup incomplete:\n  " + "\n  ".join(problems), pytrace=False)


@pytest.fixture
def mock(harness: Harness) -> Iterator[Mock]:
    harness.arm_mock()
    try:
        yield harness.mock
    finally:
        harness.disarm_mock()
