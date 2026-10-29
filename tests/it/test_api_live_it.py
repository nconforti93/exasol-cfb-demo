"""Quota access is app-only; refresh and fetch views read stored metadata."""

from __future__ import annotations

import pyexasol
import pytest

from scripts import grants
from tests.it.harness import Harness, Mock
from tests.synthetic.league import League

pytestmark = pytest.mark.model_readonly
PLAN_VARIANT = "model"


def test_live_quota_is_app_only_and_costs_no_cfbd_calls(
    harness: Harness, league: League, mock: Mock
) -> None:
    report = harness.deploy_api()
    assert report.live.views == 1
    assert harness.mock_hits() == 0
    result = grants.apply(
        grants.PyexasolDatabase(harness.sys),
        harness.namespace,
        harness.namespace.default_connection,
    )
    assert result.exit_code == grants.EXIT_OK
    live = harness.namespace.api_live_schema
    with harness.role_session(harness.namespace.app_role) as session:
        quota = session.execute(f'SELECT "MONTHLY_LIMIT" FROM {live}."QUOTA"').fetchall()
        assert len(quota) == 1
    with (
        harness.role_session(harness.namespace.mcp_role) as chat,
        pytest.raises(pyexasol.ExaQueryError, match="insufficient privileges"),
    ):
        chat.execute(f'SELECT "MONTHLY_LIMIT" FROM {live}."QUOTA"').fetchall()
    assert harness.mock_hits("/info") == 1
    assert harness.mock_hits() == 1


def test_refresh_and_fetch_views_read_stored_metadata(harness: Harness, league: League) -> None:
    harness.deploy_api()
    api = harness.namespace.api_schema
    direct = harness.query(f'SELECT COUNT(*) FROM {harness.meta}."FETCH_LOG"')
    served = harness.query(f'SELECT COUNT(*) FROM {api}."FETCHES"')
    assert served == direct
    assert harness.query(f'SELECT COUNT(*) FROM {api}."REFRESH_TIMELINE"') == harness.query(
        f'SELECT COUNT(*) FROM {harness.meta}."REFRESH_LOG" s '
        f'JOIN {harness.meta}."LOAD_RUN" r ON r."RUN_ID" = s."RUN_ID" '
        "WHERE r.\"RUN_KIND\" = 'REFRESH_WEEK'"
    )
